from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app import create_app
import lead_inquiries as leads
from storage import get_db
from workspace import init_admin


class FitmentWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory(ignore_cleanup_errors=True)
        cls.addClassCleanup(cls.temp.cleanup)
        cls.app = create_app({"TESTING": True, "SECRET_KEY": "test-fitment",
                              "DATABASE": Path(cls.temp.name) / "fitment.sqlite3"})
        with cls.app.app_context():
            init_admin("123456")

    def setUp(self):
        self.client = self.app.test_client()
        csrf = self.client.get("/api/session").json["csrf"]
        login = self.client.post("/api/login", json={"username": "admin", "password": "123456"},
                                 headers={"X-CSRFToken": csrf})
        self.assertEqual(login.status_code, 200)
        self.csrf = {"X-CSRFToken": login.json["csrf"]}

    def test_catalog_access_and_aliases(self):
        self.assertEqual(self.app.test_client().get("/api/fitment").status_code, 401)
        self.assertEqual(self.app.test_client().get("/fitment").status_code, 302)
        result = self.client.get("/api/fitment", query_string={"code": "DF-PAD-188A"}).json
        other = self.client.get("/api/fitment", query_string={"code": "DF-PAD-188B"}).json
        self.assertEqual(result["catalog_count"], 153)
        self.assertEqual(result["result"]["candidates"][0]["master_id"], "PAD-TJ-KR-FA")
        self.assertEqual(result["result"]["candidates"][0]["id"], other["result"]["candidates"][0]["id"])

    def test_vehicle_and_quote_require_review(self):
        data = {"q": "天锦KR", "qty": "20"}
        match = self.client.get("/api/fitment", query_string={"q": data["q"]}).json["result"]
        self.assertFalse(match["assert_unique"])
        first = match["candidates"][0]
        created = self.client.post("/api/fitment/quote", data={**data, "candidate_id": first["id"]}, headers=self.csrf)
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json["qty"], 20)
        self.assertTrue(created.json["needs_confirmation"])
        self.assertEqual(created.json["unit_price"], "")
        self.assertTrue(any(item["label"] == "目录证据" for item in created.json["basis"]))
        refused = self.client.post("/api/fitment/quote", data={**data, "candidate_id": "missing"}, headers=self.csrf)
        self.assertEqual(refused.status_code, 400)


    def test_inquiry_prefills_catalog_code_and_explicit_measurements(self):
        with self.app.app_context():
            leads.import_vendor([{"number": "fitment-inquiry", "title": "Brake lining enquiry",
                                      "content": "请报价DF-PAD-188A，150 mm wide，10孔。"}], "authorized")
            row = get_db().execute("SELECT id FROM lead_inquiries WHERE external_id='fitment-inquiry'").fetchone()
        result = self.client.get(f"/api/fitment/inquiries/{row['id']}").json
        self.assertEqual(result["codes"], ["DF-PAD-188A"])
        self.assertEqual(result["width"], "150")
        self.assertEqual(result["holes"], "10")


if __name__ == "__main__":
    unittest.main()
