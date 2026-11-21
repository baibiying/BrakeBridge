import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import sleep
from unittest.mock import patch
from uuid import uuid4

from cryptography.fernet import Fernet

from app import create_app
import figeon
import social
from social_providers import ProviderError
from storage import get_db
from workspace import init_admin


class SocialWorkflowTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.remove_database_directory, temp)
        self.app = create_app({"TESTING": True, "FACEBOOK_AUTO_REPLY": False, "DATABASE": str(Path(temp.name) / "social.db"),
                               "SOCIAL_TOKEN_KEY": Fernet.generate_key().decode()})
        with self.app.app_context():
            init_admin("123456")
        self.client = self.app.test_client()
        token = self.client.get("/api/session").json["csrf"]
        session = self.client.post("/api/login", json={"username": "admin", "password": "123456"},
                                   headers={"X-CSRFToken": token})
        self.csrf = session.json["csrf"]
        self.now = datetime.now(timezone.utc).isoformat()

    @staticmethod
    def remove_database_directory(directory):
        for attempt in range(5):
            try:
                directory.cleanup()
                return
            except OSError as error:
                if getattr(error, "winerror", None) not in (32, 267) or attempt == 4:
                    raise
                # File indexing can briefly hold the SQLite test fixture on Windows.
                sleep(.1)

    def post(self, endpoint, status=200, **values):
        result = self.client.post("/api/social" + endpoint, data=values, headers={"X-CSRFToken": self.csrf})
        self.assertEqual(result.status_code, status, result.json)
        return result.json

    def draft(self):
        return self.post("/drafts", title="冠英制动件", facebook="欢迎采购商交流。", linkedin="欢迎经销商提供采购需求。")

    def test_demo_comments_are_separate_ranked_and_promote_once(self):
        endpoint = "/comments/demo"
        items = self.post(endpoint)["items"]
        self.assertEqual(self.post(endpoint)["items"], items)
        self.assertEqual(len(items), 30)
        self.assertTrue(all(row["data_kind"] == "synthetic" and not row["source_url"] for row in items))
        self.assertEqual({row["analysis"]["tier"] for row in items}, {"high", "medium", "low", "review"})
        self.assertEqual(self.client.get("/api/social/comments/history").json["items"], [])
        self.assertEqual(items[0]["analysis"]["tier"], "high")
        self.post(endpoint + f"/promote/{items[-1]['id']}", status=400)
        match = endpoint + f"/promote/{items[0]['id']}"
        lead = self.post(match)
        self.assertEqual(self.post(match), {**lead, "duplicate": True})
        saved = self.client.get(f"/api/lead-inquiries/{lead['id']}").json["inquiry"]
        self.assertEqual((saved["source"], saved["data_kind"]), ("social", "synthetic"))

    def test_figeon_publish_catalog_and_bound_targets(self):
        discovery = self.client.get("/api/social/figeon").json
        self.assertEqual(len(discovery["platforms"]), 10)
        self.assertEqual(discovery["accounts"], [])
        with patch("figeon.call", side_effect=self.vendor()):
            self.post("/figeon/connect", token="fixture-explicit-auth-token")
            self.post("/figeon/release", status=400, request_key=str(uuid4()),
                      title="冠英产品", body="欢迎产品交流。", platforms=["reddit"])
        self.assertEqual(self.client.get("/api/social/figeon").json["batches"], [])
        self.assertEqual(self.client.post("/api/social/accounts", headers={"X-CSRFToken": self.csrf}).status_code, 404)

    def vendor(self, case="partial"):
        accounts = [{"platform": platform, "name": "演示测试", "status": "link"}
                    for platform in ("youtube", "twitter", "facebook")]
        published = {"id": 123, "title": "冠英制动件", "content": "欢迎采购商交流。", "status": 1, "create_at": 1795261776,
                     "platforms": [{"platform": platform} for platform in ("youtube", "twitter", "facebook")],
                     "result": [{"platform": "twitter", "status": "success", "postUrl": "https://x.com/page/status/123"},
                                {"platform": "youtube", "status": "error"},
                                {"platform": "facebook", "status": "success"}]}

        def service(op, _, fields=None):
            if op == "accounts":
                return {"data": accounts}
            if op == "posts":
                return {"data": {"data": [published]}}
            if op == "comments":
                return {"data": {"facebook": [{"id": "id-1", "comment": "我们是卡车配件进口商，想订 200 套 T850 鼓式刹车片，请报价。"}]}}
            self.assertEqual(fields["platform"], "facebook,twitter,youtube")
            self.assertEqual(fields["visibility"], "unlisted")
            if case == "uncertain":
                raise ProviderError("未确认", uncertain=True)
            return {"id": 123}
        return service

    def schedule_figeon(self):
        self.post("/figeon/connect", token="fixture-explicit-auth-token")
        draft = self.draft()
        base = f"/versions/{draft['version_id']}"
        self.post(base + "/approve")
        form = {"platforms": ["youtube", "twitter", "facebook"], "content_key": "facebook",
                "run_at": self.now, "visibility": "unlisted", "media": "https://example.test/company.mp4"}
        self.post(base + "/figeon-schedule", status=400, **{**form, "media": ""})
        self.assertEqual(self.post(base + "/figeon-schedule", **form)["count"], 1)
        self.assertEqual(self.post(base + "/figeon-schedule", **form)["count"], 0)
        return draft

    def test_figeon_batch_and_per_platform_receipts(self):
        with patch("figeon.call", side_effect=self.vendor()) as service:
            self.schedule_figeon()
            with self.app.app_context():
                stored = get_db().execute("SELECT credential FROM figeon_connection").fetchone()[0]
                self.assertNotIn("fixture-explicit-auth-token", stored)
                self.assertEqual(figeon.run_due(), 1)
                self.assertEqual(figeon.run_due(), 0)
            self.assertEqual([call.args[0] for call in service.call_args_list].count("publish"), 1)
            self.post("/figeon/sync")
            data = self.client.get("/api/social/figeon").json
            self.assertEqual(data["batches"][0]["state"], "partial")
            self.assertEqual(data["posts"][0]["targets"][1]["url"], "https://x.com/page/status/123")
            self.post("/figeon/posts/123/twitter/sync", status=400)
            self.assertEqual(self.post("/figeon/posts/123/facebook/sync")["new"], 1)
            self.assertEqual(self.post("/figeon/posts/123/facebook/sync")["new"], 0)

    def test_figeon_uncertain_result_is_never_resent(self):
        with patch("figeon.call", side_effect=self.vendor("uncertain")) as service:
            self.schedule_figeon()
            with self.app.app_context():
                self.assertEqual(figeon.run_due(), 0)
                self.assertEqual(figeon.run_due(), 0)
            batch = self.client.get("/api/social/figeon").json["batches"][0]
            self.assertEqual(batch["state"], "uncertain")
            self.assertEqual([call.args[0] for call in service.call_args_list].count("publish"), 1)

    def test_edit_cancels_scheduled_figeon_batch(self):
        with patch("figeon.call", side_effect=self.vendor()):
            draft = self.schedule_figeon()
            self.post(f"/drafts/{draft['id']}", title="调整文案", facebook="新版")
            with self.app.app_context():
                self.assertEqual(figeon.run_due(), 0)
                self.assertEqual(social.drafts()[0]["approved_at"], "")
            self.assertEqual(self.client.get("/api/social/figeon").json["batches"][0]["state"], "cancelled")

    def test_figeon_quick_release_retains_edits_and_retry_is_idempotent(self):
        with patch("figeon.call", side_effect=self.vendor()):
            self.post("/figeon/connect", token="fixture-explicit-auth-token")
            self.assertEqual(self.post("/figeon/check")["state"], "connected")
            form = {"title": "采购交流（已编辑）", "body": "欢迎提供零件号以核对产品目录。",
                    "request_key": str(uuid4()), "platforms": ["youtube"]}
            self.post("/figeon/release", status=400, **form)
            self.assertEqual(self.client.get("/api/social").json["drafts"], [])
            form["platforms"] = ["twitter"]
            published = self.post("/figeon/release", **form)
            repeated = self.post("/figeon/release", **form)
            self.assertEqual(repeated, {**published, "duplicate": True})
            detail = self.client.get(f"/api/social/drafts/{published['draft_id']}").json
            self.assertEqual((detail["title"], detail["facebook"]), (form["title"], form["body"]))
            self.assertTrue(detail["approved_at"])
            self.assertEqual(len(self.client.get("/api/social/figeon").json["batches"]), 1)
            future = datetime.now(timezone.utc) + timedelta(hours=3)
            scheduled = self.post("/figeon/release", **{**form, "request_key": str(uuid4()),
                                                       "run_at": future.isoformat(), "title": "定时测试"})
            row = self.client.get("/api/social/figeon").json["batches"][0]
            self.assertEqual((row["id"], row["state"], row["run_at"]),
                             (scheduled["batch_id"], "scheduled", social.utc_stamp(future)))

    def test_saved_vendor_account_exposes_only_mask_and_enables_verification(self):
        result = self.post("/verification/credentials", username="12345678901", password="fixture-password")
        self.assertEqual(result, {"configured": True, "account": "123•••901"})
        state = self.client.get("/api/social/verification").json
        self.assertNotIn("fixture-password", str(state))
        with self.app.app_context():
            setting = get_db().execute("SELECT value FROM settings WHERE key='figeon_vendor_login'").fetchone()[0]
            self.assertNotIn("fixture-password", setting)

    def test_generate_uses_preset_or_available_model(self):
        preset = self.post("/generate-article", topic="catalog", language="en", title="", body="")
        self.assertEqual(preset["engine"], "preset")
        self.assertIn("Kooying", preset["body"])
        with patch("social.available", return_value=True), patch("social.ask_json", return_value={"title": "AI 标题", "body": "润色后正文"}):
            generated = self.post("/generate-article", language="zh", title="手写标题", body="手写正文")
        self.assertEqual((generated["title"], generated["body"], generated["engine"]), ("AI 标题", "润色后正文", "ai"))
