import gc
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import sleep
from unittest.mock import Mock, patch
from uuid import uuid4

from cryptography.fernet import Fernet

from app import create_app
import figeon
import research_agent
import social
import social_assets
import social_verification
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
        for attempt in range(8):
            try:
                directory.cleanup()
                return
            except OSError as error:
                if getattr(error, "winerror", None) not in (32, 267) or attempt == 7:
                    raise
                # Release cached SQLite cursors and allow Windows indexing handles to close.
                gc.collect()
                sleep(.1 * (attempt + 1))

    def post(self, endpoint, status=200, **values):
        result = self.client.post("/api/social" + endpoint, data=values, headers={"X-CSRFToken": self.csrf})
        self.assertEqual(result.status_code, status, result.json)
        return result.json

    def draft(self):
        return self.post("/drafts", title="冠英制动件", facebook="欢迎采购商交流。", linkedin="欢迎经销商提供采购需求。")

    def test_company_seed_loads_deployable_data_and_preserves_changes(self):
        runner = self.app.test_cli_runner()
        initial = runner.invoke(args=["seed", "demo"])
        self.assertEqual(initial.exit_code, 0, initial.output)
        root = "/api/lead-inquiries"
        rows = self.client.get(root + "?kind=authorized").json["items"]
        self.assertEqual(len(rows), 61)
        self.assertTrue(all(row["ai_scored"] for row in rows))
        assets = self.client.get("/api/social").json["assets"]
        self.assertEqual(len(assets), 40)
        photo = assets[0]["id"]
        image = self.client.get(f"/api/social/assets/{photo}/image").data
        self.assertTrue(image.startswith(bytes.fromhex("ffd8ff")))
        self.assertEqual(self.app.test_client().get(f"/api/social/assets/{photo}/image").status_code, 401)
        self.assertFalse(self.client.get("/api/social/figeon").json["connected"])
        self.assertFalse(self.client.get("/api/settings/competitor-agent").json["editable_key"])

        headers = {"X-CSRFToken": self.csrf}
        edited, removed = (row["id"] for row in rows[:2])
        self.assertEqual(self.client.post(f"{root}/{edited}/edit", headers=headers, data={"title": "更新后标题"}).status_code, 200)
        self.assertEqual(self.client.post(f"{root}/{removed}/delete", headers=headers).status_code, 200)
        repeat = runner.invoke(args=["seed", "company"])
        self.assertEqual(repeat.exit_code, 0, repeat.output)
        self.assertIn("0 inquiries, 0 product images", repeat.output)
        self.assertEqual(self.client.get(f"{root}/{edited}").json["inquiry"]["fields"]["title"], "更新后标题")
        self.assertEqual([row["id"] for row in self.client.get(root + "?deleted=1").json["items"]], [removed])

        with patch("figeon.call", return_value={"data": [{"platform": "facebook", "name": "Test", "status": "link"}]}):
            self.post("/figeon/connect", token="fixture-explicit-auth-token")
            future = datetime.now(timezone.utc) + timedelta(hours=1)
            self.post("/figeon/release", request_key=str(uuid4()), title="产品图文测试", body="图文预览",
                      asset_id=photo, platforms=["facebook"], run_at=future.isoformat())
        tasks = self.client.get("/api/social/figeon/scheduled").json["items"]
        self.assertTrue(tasks[0]["has_image"])
        self.assertEqual(self.client.get(f"/api/social/versions/{tasks[0]['version_id']}/image").data, image)

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
            self.assertEqual([record["key"] for record in data["records"]], ["receipt-123"])
            self.assertEqual(data["records"][0]["state"], "partial")
            self.post("/figeon/posts/123/twitter/sync", status=400)
            self.assertEqual(self.post("/figeon/posts/123/facebook/sync")["new"], 1)
            self.assertEqual(self.post("/figeon/posts/123/facebook/sync")["new"], 0)
            with self.app.app_context(), get_db():
                get_db().execute("""INSERT INTO figeon_posts
                    SELECT connection_revision+1,remote_id,title,content,created_at,status,targets
                    FROM figeon_posts WHERE remote_id=123""")
                get_db().execute("DELETE FROM figeon_connection")
        offline = self.client.get("/api/social/figeon").json
        self.assertFalse(offline["connected"])
        self.assertEqual([record["key"] for record in offline["records"]], ["receipt-123"])
        self.assertEqual(offline["records"][0]["targets"][1]["url"], "https://x.com/page/status/123")
        self.assertFalse(any(target["comments"] for target in offline["records"][0]["targets"]))

    def test_figeon_uncertain_result_is_never_resent(self):
        with patch("figeon.call", side_effect=self.vendor("uncertain")) as service:
            self.schedule_figeon()
            with self.app.app_context():
                self.assertEqual(figeon.run_due(), 0)
                self.assertEqual(figeon.run_due(), 0)
            batch = self.client.get("/api/social/figeon").json["batches"][0]
            self.assertEqual(batch["state"], "uncertain")
            self.assertEqual([call.args[0] for call in service.call_args_list].count("publish"), 1)

    def test_scheduled_figure_is_uploaded_on_publish_and_failure_stops_post(self):
        image = bytes.fromhex("ffd8ff") + b"test-image"
        with self.app.app_context(), get_db():
            social_assets.store("29125", "盘式刹车片", "29125", image)
            asset_id = social_assets.catalog()[0]["id"]
        accounts = {"data": [{"platform": "facebook", "name": "Test", "status": "link"}]}
        def vendor(operation, token, fields=None):
            return accounts if operation == "accounts" else {"id": 456}
        response = Mock(status_code=200)
        response.json.return_value = {"status": 1, "data": {"img_url": ["/product/abc.jpg"]}}
        form = {"title": "带图试稿", "body": "请核对产品。", "asset_id": asset_id, "platforms": ["facebook"]}
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        with patch("figeon.call", side_effect=vendor) as service, patch("figeon.requests.post", return_value=response) as upload:
            self.post("/figeon/connect", token="fixture-explicit-auth-token")
            batch = self.post("/figeon/release", **form, run_at=future.isoformat(), request_key=str(uuid4()))
            items = self.client.get("/api/social/figeon/scheduled").json["items"]
            self.assertEqual([item["id"] for item in items], [batch["batch_id"]])
            self.assertTrue(items[0]["has_image"])
            self.assertEqual(self.client.get(f"/api/social/versions/{items[0]['version_id']}/image").data, image)
            with self.app.app_context():
                self.assertEqual(figeon.run_due(), 0)
                upload.assert_not_called()
                with get_db():
                    past = social.utc_stamp(datetime.now(timezone.utc) - timedelta(seconds=2))
                    get_db().execute("UPDATE figeon_batches SET run_at=? WHERE id=?", (past, batch["batch_id"]))
                self.assertEqual(figeon.run_due(), 1)
            self.assertEqual(upload.call_args.args[0], figeon.UPLOAD)
            self.assertEqual(upload.call_args.kwargs["data"]["platform"], "facebook")
            self.assertEqual(upload.call_args.kwargs["files"]["file"][1], image)
            self.assertEqual(service.call_args.args[2]["media"], "https://hqcdn.hqsmartcloud.com/product/abc.jpg")
            self.assertEqual(service.call_args.args[2]["content"], form["body"])
            self.assertEqual(self.client.get("/api/social/figeon/scheduled").json["items"], [])
            response.json.return_value = {"status": 0, "msg": "无法上传"}
            self.post("/figeon/release", **form, request_key=str(uuid4()))
            with self.app.app_context():
                self.assertEqual(figeon.run_due(), 0)
            self.assertEqual([call.args[0] for call in service.call_args_list].count("publish"), 1)
            self.assertEqual(self.client.get("/api/social/figeon").json["batches"][0]["state"], "failed")

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

    def test_managed_credentials_can_be_read_after_restart_with_no_environment_key(self):
        self.app.config["SOCIAL_TOKEN_KEY"] = ""
        self.post("/verification/credentials", username="12345678901", password="fixture-password")
        with patch("figeon.call", side_effect=self.vendor()):
            self.post("/figeon/connect", token="fixture-explicit-auth-token")
        self.post("/figeon/keepalive", enabled="1")
        restarted = create_app({"TESTING": True, "DATABASE": self.app.config["DATABASE"], "SOCIAL_TOKEN_KEY": ""})
        with restarted.app_context():
            saved = social_verification.login_record()
            self.assertEqual(social.credential({"active": True, "credential": saved["password"]}), "fixture-password")
            self.assertEqual(figeon.access(figeon.connection()), "fixture-explicit-auth-token")
            self.assertTrue(figeon.keepalive_status()["enabled"])

    def test_keepalive_is_opt_in_throttled_and_stops_after_expiry(self):
        current = datetime.now(timezone.utc)
        with patch("figeon.call", side_effect=self.vendor()) as service:
            self.post("/figeon/connect", token="fixture-explicit-auth-token")
            with self.app.app_context():
                self.assertFalse(figeon.check_keepalive(current))
            self.assertEqual(self.post("/figeon/keepalive", enabled="1")["state"], "waiting")
            with self.app.app_context():
                self.assertTrue(figeon.check_keepalive(current))
                self.assertFalse(figeon.check_keepalive(current + timedelta(minutes=5)))
            self.assertEqual(service.call_count, 2)
            service.side_effect = figeon.AuthorizationExpired("已过期")
            with self.app.app_context():
                self.assertTrue(figeon.check_keepalive(current + timedelta(minutes=10)))
                self.assertFalse(figeon.check_keepalive(current + timedelta(minutes=30)))
            self.assertEqual(service.call_count, 3)
            state = self.client.get("/api/social/figeon").json["keep_alive"]
            self.assertEqual(state["state"], "expired")
            self.assertFalse(self.post("/figeon/keepalive", enabled="0")["enabled"])
            with self.app.app_context():
                self.assertFalse(figeon.check_keepalive(current + timedelta(minutes=40)))
            self.assertEqual(service.call_count, 3)

    def test_generate_uses_preset_or_available_model(self):
        preset = self.post("/generate-article", topic="catalog", language="en", title="", body="")
        self.assertEqual(preset["engine"], "preset")
        self.assertIn("Kooying", preset["body"])
        with patch("social.available", return_value=True), patch("social.ask_json", return_value={"title": "AI 标题", "body": "润色后正文"}):
            generated = self.post("/generate-article", language="zh", title="手写标题", body="手写正文")
        self.assertEqual((generated["title"], generated["body"], generated["engine"]), ("AI 标题", "润色后正文", "ai"))

    def test_sales_agent_grounding_photo_opt_in_and_saved_draft(self):
        image = bytes.fromhex("ffd8ff") + b"test-image"
        with self.app.app_context(), get_db():
            social_assets.store("29125 · 盘式刹车片", "盘式刹车片", "29125", image)
            asset_id = social_assets.catalog()[0]["id"]
        url = "https://www.example.org/news/event"
        response = {"title": "展前采购", "body": "欢迎发送零件号以便核对目录（来源：主办方新闻稿）。\n资料来源：展会公告", "asset_id": asset_id}
        response["news"] = [{"title": "展会动态", "url": url, "summary": "主办方公布议程",
                             "published_on": datetime.now(social.leads.ZONE).date().isoformat()}]
        cited = {research_agent.source_key(url): (url, "主办方公布议程")}
        with patch("sales_agent.research_agent.settings", return_value={"configured": True}), \
             patch("social.research_agent.ask", return_value=(response, cited)) as ask:
            generated = self.post("/generate-article", live_news="1", use_images="1", platforms=["twitter"], language="zh")
            self.assertTrue(ask.call_args.kwargs["web"])
            self.assertIn("正文最多 110 个字符", ask.call_args.args[0])
            self.assertIn("正式、克制的商务语气", ask.call_args.args[0])
            self.assertEqual(generated["body"], "欢迎发送零件号以便核对目录。")
            self.assertIn("2026 年浙江冠英企业介绍", [item["title"] for item in ask.call_args.args[1]["sources"]])
            self.assertEqual(generated["asset_id"], asset_id)
            self.assertIn(url, [item["url"] for item in generated["refs"]])
            self.assertIsNone(self.post("/generate-article")["asset_id"])
            self.assertEqual(ask.call_args.args[1]["素材"], [])
            ask.return_value = (response, {})
            self.post("/generate-article", live_news="1", status=400)
        saved = self.post("/drafts", title=generated["title"], facebook=generated["body"], asset_id=asset_id)
        self.assertEqual(self.client.get(f"/api/social/drafts/{saved['id']}").json["asset_id"], asset_id)
        self.assertEqual(self.client.get(f"/api/social/versions/{saved['version_id']}/image").data, image)
        self.assertEqual(self.app.test_client().get(f"/api/social/assets/{asset_id}/image").status_code, 401)
