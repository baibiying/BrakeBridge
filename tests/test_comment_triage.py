import unittest
import csv
import gc
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import create_app
from comment_desk import DEMO_EVENT, analyze as rule_analysis, demo_item, enrich
from comment_desk.policy import plan
from facebook import FacebookReplyUncertain
from inquiries import import_rows
from storage import get_db
from workspace import init_admin


class CommentTriageTests(unittest.TestCase):
    def test_exhibition_price_and_aftermarket_intents(self):
        for body in ("我们做售后市场，请报价 T850", "不需要样品，请报价 T850"):
            triage = enrich(demo_item("demo:test", "客户", body))["triage"]
            self.assertIn("quote", triage)
            self.assertNotIn("样品", triage["draft"])
        combined = enrich(demo_item("demo:test", "客户", "12月汽配展展位号？T850 能报价吗？"))["triage"]
        self.assertIn("1.1N59", combined["draft"])
        self.assertIn("价格", combined["draft"])

    def test_partial_inquiry_can_enter_quote_desk(self):
        for body, count in [("这款多少钱？", 0), ("How much?", 0), ("天锦KR，多少钱？", 4),
                            ("请报价：200 套 T850 前刹 10 孔，宽度 140mm。", 1)]:
            with self.subTest(body=body):
                item = enrich(demo_item("demo:test", "样本", body))
                self.assertEqual(len(item["triage"]["quote"]["options"]), count)
                self.assertTrue(item["triage"]["draft"])
        for body in ["你们参加 12 月的汽配展吗？", "Our T850 brake lining arrived damaged."]:
            self.assertNotIn("quote", enrich(demo_item("demo:test", "样本", body))["triage"])

    def test_catalog_candidate_requires_unique_explicit_evidence(self):
        comment = "Please quote 217 sets of T850 front brake linings, 140mm wide with 10 holes."
        analysis = rule_analysis(comment)
        self.assertEqual(analysis["fields"]["quantity"], "217")
        self.assertEqual(analysis["extraction"]["vehicle"], "")
        decision = plan(comment, analysis, "social_demo", "synthetic")
        self.assertEqual(decision["candidate"]["catalog_id"], "GY-DEMO-01")
        self.assertIn("verify fitment", decision["draft"])
        self.assertEqual(plan(comment, analysis, "facebook", "authorized")["mode"], "candidate")
        self.assertEqual(plan(comment, analysis, "email", "authorized")["draft"], "")

    def test_unclear_quantities_and_sensitive_comments(self):
        for body in ("Please quote T850x2.5 sets", "T850 front brake lining, 140mm wide, 10 holes and 12 holes, quote please"):
            result = plan(body, rule_analysis(body), "social_demo", "synthetic")
            self.assertEqual(result["mode"], "acknowledge")
            self.assertIsNone(result["candidate"])
        body = "Please quote T850, my number is +1 206 555 0123"
        self.assertEqual(plan(body, rule_analysis(body), "facebook", "authorized")["draft"], "")

    def test_source_and_intent_take_precedence_over_purchase_keywords(self):
        cases = {
            "请取消询价，我们现在不需要这批刹车片。": "skip",
            "Nice video!": "skip",
            "Our T850 brake lining arrived damaged.": "acknowledge",
            "Necesito cotización para T850": "manual",
            "We need a wholesale quote for floor mats": "skip",
        }
        for body, expected in cases.items():
            with self.subTest(body=body):
                self.assertEqual(plan(body, rule_analysis(body), "figeon:instagram", "authorized")["mode"], expected)

    def test_booth_question_gets_a_direct_answer_without_confirming_attendance(self):
        body = "你们参加 12 月的汽配展吗？能发一下展位号吗？"
        analysis = rule_analysis(body)
        decision = plan(body, analysis, "facebook", "synthetic", DEMO_EVENT)
        self.assertIn("1.1N59", decision["draft"])
        self.assertIn("预计哪天到展", decision["draft"])
        self.assertNotIn("已确认", decision["draft"])
        self.assertNotIn("1.1N59", plan(body, analysis, "facebook", "authorized")["draft"])

    def test_review_and_export_leave_original_comments_untouched(self):
        with TemporaryDirectory() as folder:
            app = create_app({"TESTING": True, "DATABASE": str(Path(folder) / "test.sqlite3")})
            with app.app_context():
                init_admin("test-password")
                body = "Please quote T850x2.5 sets."
                saved = import_rows([(1, {"body": body})], "synthetic", channel="facebook")
                record_id = saved["ids"][0]
                original = tuple(get_db().execute("SELECT * FROM interactions WHERE id=?", (record_id,)).fetchone())
            with app.test_client() as client:
                self.assertEqual(client.get("/api/comment-desk").status_code, 401)
                csrf = client.get("/api/session").json["csrf"]
                session = client.post("/api/login", json={"username": "admin", "password": "test-password"},
                                      headers={"X-CSRFToken": csrf})
                headers = {"X-CSRFToken": session.json["csrf"]}
                desk = client.get("/api/comment-desk?source=social").json["items"]
                self.assertEqual(len(desk), 1)
                self.assertEqual(desk[0]["body"], body)
                self.assertTrue(desk[0]["triage"]["extraction"]["quantity_note"])
                key = desk[0]["key"]
                self.assertEqual(client.post("/api/comment-desk/review", data={"key": key, "status": "review"},
                                             headers=headers).status_code, 400)
                self.assertEqual(client.post("/api/comment-desk/review", data={
                    "key": key, "status": "review", "reason": "确认数量",
                }, headers=headers).status_code, 200)
                returned = client.get("/api/comment-desk?source=social").json["items"][0]
                self.assertEqual(returned["review"]["reason"], "确认数量")
                exported = client.get("/api/comment-desk/export?source=social&state=review").data
                self.assertIn(body.encode(), exported)
                self.assertIn("确认数量".encode(), exported)
                quote = client.get("/api/comment-desk/quote.csv?key=demo:catalog")
                self.assertEqual(quote.status_code, 200)
                self.assertIn(b"GY-DEMO-01", quote.data)
                self.assertIn(b"200", quote.data)
                partial = client.get("/api/comment-desk/quote.csv?key=demo:vehicle&filter=ABS")
                self.assertEqual(partial.status_code, 200)
                rows = list(csv.DictReader(io.StringIO(partial.data.decode("utf-8-sig"))))
                self.assertEqual(len(rows), 1)
                self.assertIn("ABS", rows[0]["目录名称"])
                self.assertEqual(client.get("/api/comment-desk/quote.csv?key=demo:vehicle&filter=missing").status_code, 400)
                batch = client.get("/api/comment-desk/quote.csv?key=demo:batch")
                self.assertEqual(batch.status_code, 200)
                rows = list(csv.DictReader(io.StringIO(batch.data.decode("utf-8-sig"))))
                self.assertEqual([(r["客户零件号"],r["规格"],r["需求数量"]) for r in rows],
                                 [("19032","STD","150"),("19032","1+","100"),("19488","2+","100")])
                self.assertTrue(all(r["原文单位"] == "" and r["单价"] == "" for r in rows))
                self.assertEqual(client.get("/api/comment-desk/quote.csv?key=demo:ambiguous").status_code, 200)
                self.assertEqual(client.post("/api/comment-desk/reply", data={"key": key, "token": "fake"},
                                             headers=headers).status_code, 409)
            with app.app_context():
                db = get_db()
                self.assertEqual(tuple(db.execute("SELECT * FROM interactions WHERE id=?", (record_id,)).fetchone()), original)
                self.assertEqual(db.execute("SELECT count(*) FROM lead_inquiries").fetchone()[0], 0)
            # The shared app factory registers its teardown after initializing SQLite.
            # Release the bootstrap connection before Windows removes the temporary DB.
            gc.collect()

    def test_authorized_one_click_reply_saves_receipt_or_uncertainty_and_never_resends(self):
        for outcome in ("ok", "uncertain"):
            with self.subTest(outcome=outcome), TemporaryDirectory() as folder, patch.dict(
                "os.environ", {"COMMENT_DESK_FACEBOOK_SEND": "1"}
            ):
                app = create_app({"TESTING": True, "DATABASE": str(Path(folder) / "test.sqlite3"),
                                  "FACEBOOK_PAGE_ID": "101", "FACEBOOK_POST_ID": "101_202",
                                  "FACEBOOK_PAGE_TOKEN": "stub", "FACEBOOK_AUTO_REPLY": False})
                with app.app_context():
                    init_admin("test-password")
                    import_rows([(1, {"body": "Please quote T850 truck brake linings.",
                                     "account": "101", "post_id": "101_202", "comment_id": "101_999"})],
                                "authorized", transport="graph", channel="facebook")
                with app.test_client() as client:
                    csrf = client.get("/api/session").json["csrf"]
                    session = client.post("/api/login", json={"username": "admin", "password": "test-password"},
                                          headers={"X-CSRFToken": csrf})
                    headers = {"X-CSRFToken": session.json["csrf"]}
                    item = client.get("/api/comment-desk").json["items"][0]
                    values = {"key": item["key"], "token": item["send_token"]}
                    self.assertTrue(values["token"])
                    for status in ("done", "archive", "review"):
                        updated = client.post("/api/comment-desk/review", data={"key":item["key"], "status":status,
                                              "reason":"核对原帖"}, headers=headers)
                        self.assertEqual(updated.status_code, 200)
                        if status != "review":
                            self.assertEqual(client.post("/api/comment-desk/reply", data=values, headers=headers).status_code, 409)
                    with patch("facebook.verify_reply_target") as verify, patch("facebook.publish_reply") as publish:
                        publish.return_value = "101_555"
                        if outcome == "uncertain":
                            publish.side_effect = FacebookReplyUncertain("平台回执未知")
                        response = client.post("/api/comment-desk/reply", data=values, headers=headers)
                        self.assertEqual(response.json["state"], "sent" if outcome == "ok" else "uncertain")
                        self.assertEqual(client.post("/api/comment-desk/reply", data=values, headers=headers).status_code, 409)
                        verify.assert_called_once()
                        publish.assert_called_once()
                    saved = client.get("/api/comment-desk").json["items"][0]
                    self.assertEqual(saved["send_token"], "")
                    self.assertEqual(saved["reply"]["receipt"], "101_555" if outcome == "ok" else "")
