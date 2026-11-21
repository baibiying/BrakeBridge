import csv
import hashlib
import hmac
import io
import json
import re
import sqlite3
from email.message import EmailMessage
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from openpyxl import Workbook

from app import DATA, create_app
from facebook import FacebookReplyUncertain
import facebook_replies
from inquiries import catalog_for_thread, rule_analysis
from storage import SCHEMA, get_db
from workspace import init_admin
import lead_inquiries as leads
import lead_connectors
from lead_records import EXPORT_FIELDS


class SalesWorkflowTest(unittest.TestCase):
    def test_catalog_rejects_unknown_width_without_treating_measurement_as_code(self):
        for width, expected in [(150, ["新T850前刹"]), (145, [])]:
            fields = rule_analysis(f"T850前刹，10孔，宽度{width}mm")["fields"]
            candidates, question = catalog_for_thread(fields, set())
            self.assertEqual([row["source_name"] for row in candidates], expected)
            self.assertTrue(question)
        self.assertNotIn("code", rule_analysis("The old lining is 140mm wide.")["fields"])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = {
            "TESTING": True,
            "LEADS_ALLOW_SYNTHETIC": True,
            "DATABASE": str(Path(self.directory.name) / "test.sqlite3"),
            "VUE_DIST": Path(self.directory.name) / "unbuilt",
            "B1X_BASE_URL": "",
            "B1X_API_KEY": "",
            "B1X_MODEL": "",
            "FACEBOOK_PAGE_ID": "123",
            "FACEBOOK_POST_ID": "123_456",
            "FACEBOOK_PAGE_TOKEN": "fixture-only",
            "FACEBOOK_APP_SECRET": "fixture-secret",
            "FACEBOOK_VERIFY_TOKEN": "fixture-verify",
        }
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        with self.app.app_context():
            init_admin("123456")
        self.login(self.client)
        page = self.client.get("/").get_data(as_text=True)
        self.csrf = re.search(r'name="csrf" value="([^"]+)"', page)[1]

    def login(self, client):
        token = client.get("/api/session").json["csrf"]
        response = client.post("/api/login", json={"username": "admin", "password": "123456"},
                               headers={"X-CSRFToken": token})
        self.assertEqual(response.status_code, 200)
        return response.json["csrf"]

    def post(self, path, **fields):
        response = self.client.post(path, data={"csrf": self.csrf, **fields}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def query(self, sql):
        with self.app.app_context():
            return get_db().execute(sql).fetchall()

    def test_chinese_examples_keep_buyer_and_comment_qualification(self):
        self.post("/buyers/import", file=(io.BytesIO((DATA / "demo-buyers.csv").read_bytes()), "buyers.csv"))
        priority = self.client.get("/?tier=focus").get_data(as_text=True)
        self.assertIn("海湾重卡配件", priority)
        self.assertNotIn("安第斯乘用车配件", priority)
        self.post("/inbox/import", file=(io.BytesIO((DATA / "demo-comments.csv").read_bytes()), "comments.csv"))
        messages = {row["external_id"]: row for row in self.query("SELECT * FROM interactions")}
        self.assertEqual(messages["M01"]["status"], "new")
        self.assertEqual(messages["M04"]["status"], "archive")
        first = json.loads(messages["M01"]["analysis"])["fields"]
        updated = json.loads(messages["M02"]["analysis"])["fields"]
        self.assertEqual((first["quantity"], first["unit"], first["position"]), ("200", "套", "front"))
        self.assertEqual(updated["width_mm"], "140")
        self.assertEqual(self.client.post("/inbox/add", data={"body": "没有表单令牌"}).status_code, 400)

    def test_sales_workflow_imports_drafts_persists_and_exports(self):
        for _ in range(2):
            self.post("/buyers/import",
                      file=(io.BytesIO((DATA / "demo-buyers.csv").read_bytes()), "buyers.csv"))
        self.assertEqual(len(self.query("SELECT * FROM buyers")), 4)
        self.assertEqual(len(self.query("SELECT * FROM evidence")), 5)
        self.post("/buyers/2/invite")
        self.assertIn("Are you planning", self.query("SELECT draft FROM buyers WHERE id=2")[0]["draft"])
        self.post("/buyers/2/save", owner="Anna", status="contacted", follow_up="2026-11-25",
                  draft="Hi from Kooying.", override="priority", reason="Called verified purchasing team.")
        result = self.post("/inbox/add", body="Please quote 217 sets of T850 front brake linings, 10 holes.",
                           comment_id="test-one", thread_id="inquiry-one", data_kind="synthetic")
        self.assertIn("高意向", result)
        self.assertIn("140 or 150 mm", result)
        self.post("/inbox/add", body="The old lining is 140 mm wide.", comment_id="test-two",
                  thread_id="inquiry-one", data_kind="synthetic")
        draft_view = self.post("/inbox/1/reply")
        self.assertIn("待核价单", draft_view)
        self.assertIn("Could you confirm the old lining dimensions", draft_view)
        self.post("/inbox/1/save", owner="Chen", status="done", follow_up="",
                  draft="Could you confirm the axle model?", buyer_id="2")
        self.assertIn("关联的 Facebook 询盘", self.client.get("/?buyer=2").get_data(as_text=True))
        self.post("/inbox/add", body="Great video!", comment_id="test-three", data_kind="synthetic")
        rows = self.query("SELECT * FROM interactions ORDER BY id")
        self.assertEqual([row["status"] for row in rows], ["done", "review", "archive"])
        quote = self.client.get("/inbox/1/quote.csv").data.decode("utf-8-sig")
        entries = list(csv.DictReader(io.StringIO(quote)))
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]["width_mm"], entries[0]["quantity"], entries[0]["unit_price"]), ("140", "217", ""))
        restarted = create_app(self.config).test_client()
        self.login(restarted)
        self.assertIn("Anna", restarted.get("/export/buyers.csv").get_data(as_text=True))
        self.assertIn("Chen", restarted.get("/export/inbox.csv").get_data(as_text=True))

    def test_excel_import_and_facebook_delivery_deduplication(self):
        workbook = Workbook()
        workbook.active.append(["company", "source_text", "country", "domain", "data_kind"])
        workbook.active.append(["Company Example", "Heavy truck brake parts", "US", "example.com", "public"])
        data = io.BytesIO()
        workbook.save(data)
        data.seek(0)
        self.post("/buyers/import", file=(data, "buyers.xlsx"))
        self.assertEqual(len(self.query("SELECT * FROM buyers")), 1)
        page_response = Mock(ok=True)
        page_response.json.return_value = {"data": [
            {"id": "123_789", "message": "Please quote 12 sets of brake pads.",
             "created_time": "2026-11-21T06:40:00+0000"}
        ]}
        with patch("facebook.requests.get", return_value=page_response) as get:
            self.post("/inbox/sync")
            self.post("/inbox/sync")
            self.assertEqual(get.call_count, 2)
        new_comment = {"object": "page", "entry": [{"id": "123", "changes": [
            {"field": "feed", "value": {"item": "comment", "verb": "add", "post_id": "123_456",
                                       "comment_id": "123_789", "message": "Please quote 12 sets of brake pads."}},
            {"field": "feed", "value": {"item": "comment", "verb": "add", "post_id": "123_999",
                                       "comment_id": "123_9999", "message": "Other post."}}
        ]}]}
        payload = json.dumps(new_comment).encode()
        sig = hmac.new(b"fixture-secret", payload, hashlib.sha256).hexdigest()
        response = self.client.post("/webhooks/facebook", data=payload, content_type="application/json",
                                    headers={"X-Hub-Signature-256": "sha256=" + sig})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post("/webhooks/facebook", data=payload).status_code, 403)
        verify = self.client.get("/webhooks/facebook?hub.mode=subscribe&hub.verify_token=fixture-verify&hub.challenge=17")
        self.assertEqual(verify.get_data(as_text=True), "17")
        self.assertEqual(len(self.query("SELECT * FROM interactions")), 1)

    def test_ai_failure_preserves_comment_and_rejects_ungrounded_quote(self):
        self.app.config.update(B1X_BASE_URL="https://example.test/v1", B1X_MODEL="test", B1X_API_KEY="fixture-only")
        with patch("inquiries.ask_json", return_value={"tier": "high", "intent": "询价",
                   "quote": "invented evidence", "fields": {}}):
            result = self.post("/inbox/add", body="Could you send your catalogue?", comment_id="uncertain")
        self.assertIn("评论已保存", result)
        self.assertEqual(len(self.query("SELECT * FROM interactions")), 1)
        self.assertEqual(self.query("SELECT status FROM interactions")[0]["status"], "review")

    def test_scoring_uses_original_signals_and_non_target_cap(self):
        examples = [
            ("我们经销卡车制动件。想订 200 套 T850 鼓式刹车片，请报价。", "high"),
            ("请问有没有卡车盘式刹车片？能发目录吗？", "medium"),
            ("Looking for passenger car floor mats and seat covers.", "low"),
        ]
        for body, expected in examples:
            analysis = rule_analysis(body)
            score = analysis["score"]
            self.assertEqual(score["tier"], expected)
            self.assertEqual(sum(part["max"] for part in score["parts"]), 100)
            self.assertLessEqual(score["total"], score["raw"])
        self.assertEqual(rule_analysis(examples[-1][0])["score"]["total"], 10)

    def test_invite_is_prepared_for_contact_and_repeat_import_keeps_edits(self):
        csv_rows = ("company,country,source_type,source_text,contact_name,contact_email\n"
                    "海湾客户,阿联酋,trade,收货人从中国采购卡车鼓式刹车片,采购部,buyer@a.example\n").encode()
        self.app.config.update(EVENT_NAME="Test trade show", EVENT_BOOTH="B-12")
        html = self.post("/buyers/import", file=(io.BytesIO(csv_rows), "buyers.csv"))
        self.assertIn("已为 1 家优先买家准备邀请函", html)
        self.assertIn("mailto:buyer@a.example?", html)
        draft = self.query("SELECT draft, contact_email FROM buyers")[0]
        self.assertEqual(draft["contact_email"], "buyer@a.example")
        self.assertIn("booth B-12", draft["draft"])
        self.assertIn("Subject: Invitation", self.client.get("/buyers/1/invitation.txt").get_data(as_text=True))
        self.post("/buyers/1/save", owner="王丽", status="new", draft="已编辑邀请函")
        self.post("/buyers/import", file=(io.BytesIO(csv_rows), "buyers.csv"))
        self.assertEqual(self.query("SELECT draft FROM buyers")[0]["draft"], "已编辑邀请函")

    def test_facebook_auto_reply_queues_only_new_trusted_comments_and_sends_once(self):
        self.app.config.update(FACEBOOK_AUTO_REPLY=True, B1X_BASE_URL="https://example.test/v1",
                               B1X_MODEL="fixture", B1X_API_KEY="fixture-only")
        body = "想订 200 套 T850 前刹的鼓式刹车片，请报价。"
        with patch("inquiries.ask_json", return_value={"tier": "high", "intent": "询价",
                   "quote": "请报价", "language": "中文", "fields": {}}):
            self.post("/inbox/add", data_kind="synthetic", body=body)
        self.assertEqual(len(self.query("SELECT * FROM facebook_replies")), 0)
        event = {"object": "page", "entry": [{"id": "123", "changes": [
            {"field": "feed", "value": {"item": "comment", "verb": "add", "post_id": "123_456",
                                      "comment_id": "123_789", "message": body,
                                      "created_time": int(datetime.now(timezone.utc).timestamp())}}
        ]}]}
        payload = json.dumps(event).encode()
        sig = hmac.new(b"fixture-secret", payload, hashlib.sha256).hexdigest()
        for _ in range(2):
            response = self.client.post("/webhooks/facebook", data=payload, content_type="application/json",
                                        headers={"X-Hub-Signature-256": "sha256=" + sig})
            self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.query("SELECT * FROM facebook_replies")), 1)
        with self.app.app_context(), patch("facebook.verify_reply_target") as verify, \
                patch("facebook_replies.ask_json", return_value={"question": "dimensions"}), \
                patch("facebook.publish_reply", return_value="123_790") as publish:
            self.assertTrue(facebook_replies.process_one())
            self.assertFalse(facebook_replies.process_one())
            verify.assert_called_once_with("123_789", body, "123")
            publish.assert_called_once()
            self.assertIn("车桥型号", publish.call_args.args[1])
        status = self.client.get("/inbox/2/reply-status").get_json()
        self.assertEqual((status["stage"], status["reply_id"]), ("sent", "123_790"))
        event["entry"][0]["changes"][0]["value"]["comment_id"] = "123_791"
        payload = json.dumps(event).encode()
        sig = hmac.new(b"fixture-secret", payload, hashlib.sha256).hexdigest()
        self.assertEqual(self.client.post("/webhooks/facebook", data=payload, content_type="application/json",
                        headers={"X-Hub-Signature-256": "sha256=" + sig}).status_code, 200)
        with self.app.app_context(), patch("facebook.verify_reply_target"), \
                patch("facebook_replies.ask_json", return_value={"question": "dimensions"}), \
                patch("facebook.publish_reply", side_effect=FacebookReplyUncertain("请查看原帖")) as publish:
            self.assertTrue(facebook_replies.process_one())
            self.assertFalse(facebook_replies.process_one())
            self.assertEqual(publish.call_count, 1)
        self.assertEqual(self.client.get("/inbox/3/reply-status").get_json()["stage"], "uncertain")

    def test_vue_api_login_profile_and_dashboard_persist(self):
        visitor = self.app.test_client()
        self.assertEqual(visitor.get("/api/buyers").status_code, 401)
        self.assertEqual(visitor.get("/export/buyers.csv").status_code, 302)
        self.assertEqual(visitor.post("/api/login", json={"username": "admin", "password": "123456"}).status_code, 400)
        token = self.login(visitor)
        headers = {"X-CSRFToken": token}
        runner = self.app.test_cli_runner()
        for _ in range(2):
            result = runner.invoke(args=["seed", "demo"])
            self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(len(self.query("SELECT * FROM event_tasks")), 6)
        self.assertEqual(len(self.query("SELECT * FROM buyers")), 30)
        self.assertEqual(len(self.query("SELECT DISTINCT country FROM buyers")), 29)
        self.assertTrue(all(row["phone"] and row["contact_email"] and row["address"] and row["importance"]
                            for row in self.query("SELECT * FROM buyers")))
        self.assertNotEqual(self.query("SELECT password_hash FROM users")[0]["password_hash"], "123456")
        listing = visitor.get("/api/buyers?tier=focus").json["items"]
        self.assertEqual(len(listing), 14)
        buyer_id = next(row["buyer"]["id"] for row in listing if row["buyer"]["company"] == "海湾商用车制动配件")
        detail = visitor.get(f"/api/buyers/{buyer_id}").json
        self.assertEqual(detail["score"]["total"], 100)
        self.assertTrue(detail["buyer"]["draft"])
        self.assertFalse(detail["mailto"])
        invalid = visitor.post("/api/buyers", data={"company": "测试客户"}, headers=headers)
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("phone", invalid.json["fields"])
        follow = visitor.post(f"/api/buyers/{buyer_id}/save", data={"owner": "外贸一组", "status": "waiting",
                              "follow_up": "2026-11-28", "draft": "Follow-up confirmation"}, headers=headers)
        self.assertEqual(follow.status_code, 200)
        today = visitor.get("/api/dashboard?start=2026-11-27&end=2026-12-05").json
        updated = next(task for task in today["tasks"] if task["resource"] == "buyer" and task["id"] == buyer_id)
        self.assertEqual(updated["start_on"], "2026-11-28")
        self.assertEqual(updated["status"], "doing")
        invalid_task = visitor.post("/api/event-tasks", data={"title": "倒置日期", "status": "open", "kind": "preparation",
                                    "data_kind": "synthetic", "start_on": "2026-12-03", "end_on": "2026-12-01"},
                                    headers=headers)
        self.assertIn("end_on", invalid_task.json["fields"])
        self.assertEqual(visitor.post("/api/logout", data={}, headers=headers).status_code, 200)
        self.assertEqual(visitor.get("/api/dashboard").status_code, 401)

    def test_migrating_an_existing_database_preserves_follow_up(self):
        path = str(Path(self.directory.name) / "old.sqlite3")
        db = sqlite3.connect(path)
        with db:
            db.executescript(SCHEMA)
            db.execute("""INSERT INTO buyers(entity_key,company,country,domain,data_kind,owner,draft)
                          VALUES('prior','原有买家','巴西','legacy.example','synthetic','外贸组','Saved invitation')""")
        db.close()
        upgraded = create_app({**self.config, "DATABASE": path})
        with upgraded.app_context():
            saved = get_db().execute("SELECT owner,draft,phone,importance FROM buyers").fetchone()
            self.assertEqual(tuple(saved), ("外贸组", "Saved invitation", "", None))
            self.assertEqual(get_db().execute("PRAGMA user_version").fetchone()[0], 9)

    def test_inquiry_history_import_and_alternative_draft(self):
        token = self.client.get("/api/session").json["csrf"]
        headers = {"X-CSRFToken": token}
        root = "/api/lead-inquiries"
        source = (DATA / "demo-lead-inquiries.json").read_bytes()
        self.assertEqual(self.app.test_client().get(root).status_code, 401)
        for expected in (4, 0):
            response = self.client.post(root + "/import", data={"file": (io.BytesIO(source), "hq.json")},
                                        headers=headers)
            self.assertEqual(response.json["inserted"], expected)
            self.assertEqual(response.json["missing"], 0)
        shortlist = self.client.get(root + "/expo").json["groups"]
        self.assertEqual(len(shortlist["meetings"]), 1)
        self.assertEqual(len(shortlist["show"]), 1)
        inquiry = shortlist["meetings"][0]
        path = f"{root}/{inquiry['id']}"
        record = self.client.get(path).json["inquiry"]
        self.assertEqual(record["fields"]["meta"]["关注产品"], "T850")
        self.assertEqual(record["fields"]["first_page"], "https://example.com/catalog")
        self.assertEqual(record["analysis"]["tier"], "hot")
        self.assertEqual(self.client.post(path + "/dossier", headers=headers).status_code, 200)
        alternative = {"company": "备选商用车零件", "market": "阿联酋", "domain": "rival.example",
                       "products": "重卡制动衬片", "channel": "重卡售后分销", "relationship": "competition",
                       "source": "https://rival.example/catalog", "quote": "Commercial vehicle brake linings.",
                       "contact": "sales@rival.example", "phone": "+971 4 555 0123",
                       "contact_source": "https://rival.example/contact"}
        self.assertEqual(self.client.post(path + "/candidates", data={**alternative, "phone": "待核"},
                                          headers=headers).status_code, 400)
        self.assertEqual(self.client.post(path + "/candidates", data=alternative, headers=headers).json["added"], 1)
        rival = self.client.get(path).json["candidates"][0]
        action = f"{path}/candidates/{rival['id']}"
        self.assertEqual(rival["candidate"]["email"], "sales@rival.example")
        self.assertEqual(rival["candidate"]["phone"], "+971 4 555 0123")
        self.assertEqual(rival["candidate"]["contact_source"], "https://rival.example/contact")
        self.assertEqual(self.client.post(action + "/edit", data={"channel": "卡车制动件经销"}, headers=headers).status_code, 200)
        self.assertEqual(self.client.get(path).json["candidates"][0]["candidate"]["phone"], "+971 4 555 0123")
        self.assertEqual(self.client.post(action + "/verify", headers=headers).status_code, 200)
        self.assertEqual(self.client.post(action + "/follow", headers=headers).status_code, 400)
        self.assertEqual(self.client.post(path + "/follow", data={"status": "declined"}, headers=headers).status_code, 200)
        self.assertIn("备选商用车零件", self.client.post(action + "/follow", headers=headers).json["draft"])

    def test_reply_threads_quotes_and_notice_dedupe(self):
        def raw(body, reference, reply=None, auto=False):
            mail = EmailMessage()
            mail["From"] = "Buyer <team@buyer.example>"
            mail["To"] = "sales@demo.example"
            mail["Subject"] = "Re: catalogue"
            mail["Date"] = "Sat, 21 Nov 2026 19:00:00 +0800"
            mail["Message-ID"] = reference
            if reply:
                mail["In-Reply-To"] = reply
            if auto:
                mail["Auto-Submitted"] = "auto-replied"
            mail.set_content(body)
            return mail.as_bytes()

        with self.app.app_context():
            first = leads.parse_email(raw("Thank you.\n\nOn Friday you wrote:\n> I will attend the show.", "<first@buyer.example>"))
            self.assertEqual(first["reply_text"], "Thank you.")
            item = leads.import_email(first, "authorized")
            self.assertEqual(leads.listing()[0]["score"]["total"], 0)
            second = leads.parse_email(raw("Please quote 200 sets of T850 brake linings.", "<second@buyer.example>", "<first@buyer.example>"))
            upgraded = leads.import_email(second, "authorized")
            self.assertEqual(item["id"], upgraded["id"])
            self.assertTrue(leads.import_email(second, "authorized")["duplicate"])
            auto = leads.parse_email(raw("I will attend.", "<third@buyer.example>", "<second@buyer.example>", auto=True))
            leads.import_email(auto, "authorized")
            self.assertEqual(leads.listing()[0]["analysis"]["tier"], "hot")
            self.assertGreater(leads.listing()[0]["score"]["total"], 50)
            self.assertEqual(get_db().execute("SELECT count(*) FROM lead_notifications").fetchone()[0], 1)
            self.app.config["LEADS_WECOM_WEBHOOK"] = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fixture"
            response = Mock(ok=True)
            response.json.return_value = {"errcode": 0}
            with patch("lead_connectors.requests.post", return_value=response) as send:
                self.assertEqual(lead_connectors.process_notifications(), 1)
                self.assertEqual(lead_connectors.process_notifications(), 0)
                self.assertNotIn("team@buyer.example", str(send.call_args.kwargs["json"]))
            self.assertEqual(leads.intent("Please send your catalogue.")["tier"], "review")
            self.assertEqual(leads.intent("No longer interested.")["tier"], "defer")

    def test_inquiry_scoring_and_sorting_with_public_competitors(self):
        root = "/api/lead-inquiries"
        headers = {"X-CSRFToken": self.client.get("/api/session").json["csrf"]}

        def create(text):
            response = self.client.post(root, data={"content": text}, headers=headers)
            return response.json["id"]

        researched = create("Could you send a catalogue?")
        higher_score = create("Please send current prices for automotive brake parts.")
        meeting = create("We will attend the exhibition.")
        before = self.client.get(root + "?tier=review").json["items"]
        self.assertEqual([item["id"] for item in before], [higher_score, researched])
        self.assertGreater(before[0]["score"], before[1]["score"])
        evidence = {"company": "参考渠道", "market": "乌干达", "products": "重卡制动件", "relationship": "similar",
                    "source": "https://channel.example/catalog", "quote": "Commercial brake products."}
        path = f"{root}/{researched}"
        self.assertEqual(self.client.post(path + "/candidates", data=evidence, headers=headers).status_code, 200)
        data = self.client.get(root).json["items"]
        self.assertEqual([item["id"] for item in data], [meeting, researched, higher_score])
        self.assertEqual(data[1]["candidate_count"], 1)
        self.assertEqual(data[1]["score"], before[1]["score"])
        for mode, expected in (("score_asc", [researched, meeting, higher_score]),
                               ("score_desc", [higher_score, meeting, researched])):
            ordered = self.client.get(root + "?sort=" + mode).json["items"]
            self.assertEqual([item["id"] for item in ordered], expected)
        record = self.client.get(path).json["inquiry"]
        self.assertEqual(record["score"]["total"], data[1]["score"])
        self.assertEqual(sum(part["max"] for part in record["score"]["parts"]), 100)
        self.assertEqual(record["analysis"]["tier"], "review")
        rfq_table = "Please send prices for truck brake linings. Part No. QTY/Set: 19486 100. TOTAL 100"
        quote = leads.score_inquiry(rfq_table, leads.intent(rfq_table), False)
        self.assertTrue(any(part["points"] == 20 and "TOTAL 100" in part["evidence"] for part in quote["parts"]))
        settings = "/api/settings/inquiry-scoring"
        self.assertEqual(self.client.get(settings).json["weights"], leads.WEIGHT_DEFAULTS)
        self.assertEqual(self.client.post(settings, json={"action": 100}, headers=headers).status_code, 400)
        weights = dict.fromkeys(leads.WEIGHT_DEFAULTS, 0) | {"action": 100}
        self.assertEqual(self.client.post(settings, json=weights, headers=headers).json["weights"], weights)
        changed = self.client.get(root + "?tier=review").json["items"]
        self.assertEqual([(row["id"], row["score"]) for row in changed], [(researched, 40), (higher_score, 80)])
        self.assertEqual(self.client.get(path).json["inquiry"]["score"]["total"], 40)

    def test_competitor_agent_sources_credentials_and_retry(self):
        headers = {"X-CSRFToken": self.client.get("/api/session").json["csrf"]}
        url = "/api/settings/competitor-agent"
        defaults = self.client.get(url).json
        self.assertEqual(defaults["model"], "deepseek-flash")
        secret = "agent-fixture-key"
        config = self.client.post(url, json={"provider": "deepseek", "api_key": secret}, headers=headers).json
        self.assertTrue(config["configured"])
        self.assertNotIn(secret, json.dumps(config))
        with self.app.app_context():
            stored = get_db().execute("SELECT value FROM settings WHERE key='competitor_agent_config'").fetchone()[0]
            self.assertNotIn(secret, stored)
        root = "/api/lead-inquiries"
        create = self.client.post(root, headers=headers, data={"address": "伊朗德黑兰", "name": "Confidential contact",
            "mail": "hidden@example.net", "content": "Please send your catalogue for truck brake linings WVA 19486."})
        path = f"{root}/{create.json['id']}"
        detail = self.client.get(path).json
        self.assertTrue(detail["research_agent"]["automatic"])
        source = "https://example-parts.test/brakes"
        good = {"company": "制动件参考商", "market": "伊朗", "products": "重卡制动衬片", "relationship": "similar",
                "source": source, "quote": "公开目录包含重卡制动衬片。", "email": "team@example-parts.test", "phone": "+98 21 1111 2222"}
        response = Mock(status_code=200)
        response.json.return_value = {"choices": [{"message": {"content": json.dumps({"items": [
            good, dict(good, company="来源无效", source="https://elsewhere.test/brakes"),
        ]}), "server_tool_calls": [{"calls": [{"status": "completed",
            "sources": [{"url": source, "snippet": "Truck brake lining. Sales: team@example-parts.test"}]}]}]}}]}
        with patch("research_agent.requests.post", return_value=response) as post:
            fetched = self.client.post(path + "/candidates/refresh", headers=headers, data={"mode": "auto"}).json
            self.assertEqual(fetched["added"], 1)
            payload = post.call_args.kwargs["json"]
            self.assertNotIn("hidden@example.net", json.dumps(payload))
            self.assertNotIn("Confidential contact", json.dumps(payload))
            self.assertIn("19486", payload["messages"][1]["content"])
            self.assertEqual(self.client.post(path + "/candidates/refresh", headers=headers, data={"mode": "auto"}).json["skipped"], True)
            self.assertEqual(post.call_count, 1)
            self.assertEqual(self.client.post(path + "/candidates/refresh", headers=headers).json["added"], 0)
        candidate = self.client.get(path).json["candidates"][0]["candidate"]
        self.assertEqual(candidate["email"], "team@example-parts.test")
        self.assertEqual(candidate["phone"], "")
        with patch("research_agent.requests.post", return_value=Mock(status_code=401)):
            self.assertTrue(self.client.post(path + "/candidates/refresh", headers=headers).json["error"])
        self.assertEqual(len(self.client.get(path).json["candidates"]), 1)
        other = self.client.post(url, json={"provider": "openai", "api_key": "openai-fixture-key"}, headers=headers).json
        self.assertEqual(other["model"], "gpt-5-mini")
        output = Mock(status_code=200)
        output.json.return_value = {"output": [
            {"type": "web_search_call", "status": "completed", "action": {"sources": [{"url": source}]}},
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps({"items": [good]}),
                                             "annotations": [{"type": "url_citation", "url": source}]}]},
        ]}
        with patch("research_agent.requests.post", return_value=output) as post:
            self.assertEqual(self.client.post(path + "/candidates/refresh", headers=headers).json["added"], 0)
            self.assertEqual(post.call_args.args[0], "https://api.openai.com/v1/responses")
        self.assertEqual(len(self.client.get(path).json["candidates"]), 1)

    def test_real_export_contract_edit_translation_and_recycle(self):
        root = "/api/lead-inquiries"
        headers = {"X-CSRFToken": self.client.get("/api/session").json["csrf"]}
        self.app.config["LEADS_ALLOW_SYNTHETIC"] = False
        original = "Please quote 100 sets of WVA 19486 brake linings."
        export = Workbook()
        export.active.append(list(EXPORT_FIELDS.values()))
        export.active.append(["QA", original, "", "", "RFQ", "", "荷兰", "2026-11-16 16:53:23",
                              "产品页", "https://example.com/rfq", "", "外贸组", "PC"])
        xlsx = io.BytesIO()
        export.save(xlsx)

        def upload(content, filename):
            return self.client.post(root + "/import", data={"file": (io.BytesIO(content), filename)}, headers=headers)

        self.assertEqual(upload(b"title,content\nDemo,Test", "wrong.csv").status_code, 400)
        with self.app.app_context():
            leads.import_vendor([{"number": "fixture", "content": "仅演示"}], "synthetic")
            leads.import_vendor([{"number": "legacy-1", "name": "QA", "title": "RFQ", "content": original,
                                  "addtime": "2026-11-16 16:53:23", "phone": "legacy"}], "authorized")
        initial = self.client.get(root).json["items"]
        self.assertEqual(len(initial), 1)
        path = f"{root}/{initial[0]['id']}"
        report = upload(xlsx.getvalue(), "inquiry.xlsx").json
        self.assertEqual((report["inserted"], report["updated"], report["missing"]), (0, 1, 0))
        detail = self.client.get(path).json
        self.assertEqual(detail["inquiry"]["fields"]["meta"]["分配员工"], "外贸组")
        self.assertEqual(detail["inquiry"]["fields"]["phone"], "")
        source_hash = detail["translation_sources"]["0"]
        translation = {"mode": "manual", "target": 0, "source_hash": source_hash, "text": "请报 100 套的价格。"}
        self.assertEqual(self.client.post(path + "/translate", data=translation, headers=headers).status_code, 200)
        self.assertEqual(self.client.get(path).json["translations"]["0"]["text"], translation["text"])
        self.assertEqual(self.client.post(path + "/follow", data={"owner": "采购组"}, headers=headers).status_code, 200)
        self.assertEqual(self.client.post(path + "/edit", data={"content": "Please send the catalogue."}, headers=headers).status_code, 200)
        self.assertEqual(self.client.post(path + "/translate", data=translation, headers=headers).status_code, 400)
        self.assertEqual(upload(xlsx.getvalue(), "inquiry.xlsx").json["duplicate"], 1)
        detail = self.client.get(path).json
        self.assertEqual(detail["inquiry"]["owner"], "采购组")
        self.assertEqual(detail["inquiry"]["fields"]["content"], "Please send the catalogue.")
        self.assertFalse(detail["translations"])
        self.assertEqual(self.client.post(path + "/delete", headers=headers).status_code, 200)
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get(root).json["items"], [])
        self.assertEqual(len(self.client.get(root + "?deleted=1").json["items"]), 1)
        self.assertEqual(upload(xlsx.getvalue(), "inquiry.xlsx").json["deleted"], 1)
        self.assertEqual(self.client.post(path + "/restore", headers=headers).status_code, 200)
        self.assertEqual(self.client.get(path).status_code, 200)
        created = self.client.post(root, data={"content": original}, headers=headers).json
        self.assertEqual(self.client.get(f"{root}/{created['id']}").json["inquiry"]["source"], "manual")

    def test_imap_polls_readonly_and_persists_cursor_without_alerting_backfill(self):
        self.app.config.update(LEADS_IMAP_HOST="imap.demo.example", LEADS_IMAP_USER="inbox@demo.example",
                               LEADS_IMAP_PASSWORD="fixture-only")
        message = EmailMessage()
        message["From"] = "Buyer <team@buyer.example>"
        message["Subject"] = "Re: catalogue"
        message["Date"] = "Sat, 21 Nov 2020 12:10:00 +0800"
        message["Message-ID"] = "<imap-fixture@buyer.example>"
        message.set_content("Please quote 200 sets of T850 brake linings.")
        mailbox = Mock()
        mailbox.select.return_value = ("OK", [b"1"])
        mailbox.response.return_value = ("UIDVALIDITY", [b"121"])
        mailbox.uid.side_effect = lambda name, *_: (
            ("OK", [b"71"]) if name == "search" else ("OK", [(b"71", message.as_bytes())])
        )
        with self.app.app_context(), patch("lead_connectors.imaplib.IMAP4_SSL") as connect:
            connect.return_value.__enter__.return_value = mailbox
            first = lead_connectors.sync_mail()
            second = lead_connectors.sync_mail()
            self.assertEqual((first["inserted"], second["inserted"]), (1, 0))
            mailbox.select.assert_called_with("INBOX", readonly=True)
            self.assertEqual(get_db().execute("SELECT count(*) FROM lead_notifications").fetchone()[0], 0)
            self.assertEqual(lead_connectors.setting("lead_imap_cursor", {})["uid"], 71)

    def test_customer_fields_and_weights_are_applied_to_import_and_scoring(self):
        token = self.client.get("/api/session").json["csrf"]
        headers = {"X-CSRFToken": token}
        profile_csv = ("客户名称,客户联系人,客户来源,客户重要程度,客户区域,具体地址,客户邮箱,客户电话\n"
                       "转单云示例,采购部,展会登记,3 星,日本,演示园区,team@lead.example,2025550111\n").encode()
        uploaded = self.client.post("/api/buyers/import", data={"file": (io.BytesIO(profile_csv), "profiles.csv")}, headers=headers)
        self.assertEqual(uploaded.status_code, 200, uploaded.json)
        self.assertEqual(uploaded.json["buyers"], 1)
        self.assertEqual(uploaded.json["new"], 0)
        profile = self.query("SELECT * FROM buyers")[0]
        self.assertEqual((profile["customer_source"], profile["domain"], profile["importance"]), ("展会登记", "", 3))
        self.assertFalse(self.query("SELECT * FROM evidence"))
        self.assertEqual(self.app.test_client().get("/api/settings/buyer-scoring").status_code, 401)
        self.assertEqual(self.app.test_cli_runner().invoke(args=["seed", "demo"]).exit_code, 0)
        before = self.client.get("/api/buyers?q=金桥").json["items"][0]
        path = f"/api/buyers/{before['buyer']['id']}"
        self.assertEqual((before["tier"], before["score"]), ("priority", 85))
        endpoint = "/api/settings/buyer-scoring"
        original = self.client.get(endpoint).json["weights"]
        self.assertEqual(sum(original.values()), 100)
        self.assertEqual(self.client.post(endpoint, json={**original, "show": 0}, headers=headers).status_code, 400)
        custom = {"product": 20, "procurement": 20, "china": 10, "show": 45, "contact": 5}
        self.assertEqual(self.client.post(endpoint, json=custom, headers=headers).status_code, 200)
        after = self.client.get(path).json
        self.assertEqual((after["tier"], after["score"]["total"]), ("review", 55))
        self.assertEqual(after["buyer"]["draft"], before["buyer"]["draft"])
        restarted = create_app(self.config).test_client()
        self.login(restarted)
        self.assertEqual(restarted.get(endpoint).json["weights"], custom)
