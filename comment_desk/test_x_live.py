import sqlite3
import unittest
from unittest.mock import patch

from flask import Flask
from . import x_live as x


class XDemoTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.secret_key = "test-only"
        app.register_blueprint(x.bp)
        self.client = app.test_client()
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        for p in (patch.object(x, "get_db", return_value=db), patch.object(x, "credential", return_value="fixture"),
                  patch.dict("os.environ", {"X_BRAND_AUTOREPLY_APPROVED":"1"})):
            p.start()
            self.addCleanup(p.stop)
        self.posts = [self.post("81", "10", "请自动回复：请发目录"), self.post("82", "20", "自定义需求")]
        self.writes = []

    def post(self, cid, uid, text, parent=x.ROOT):
        return dict(id=cid, author_id=uid, text=text, referenced_tweets=[dict(type="replied_to",id=parent)])

    def platform(self, method, path, **kwargs):
        if path == "users/me":
            return {"data":{"id":"44","username":x.ACCOUNT}}
        if path == "tweets/"+x.ROOT:
            return {"data":{"author_id":"44","id":x.ROOT,"conversation_id":x.ROOT}}
        if path == "tweets/search/recent":
            return {"data":self.posts}
        self.assertEqual((method,path),("POST","tweets"))
        self.writes.append(kwargs["json"])
        return {"data":{"id":"91"}}

    def sync(self, publish):
        with patch.object(x,"call",side_effect=self.platform):
            return self.client.post("/api/comment-desk/x/sync",data={"publish":publish})

    def test_preview_then_send_then_deduplicate(self):
        self.assertEqual(self.sync("no").json["items"][0]["state"],"可自动回复")
        self.assertFalse(self.writes)
        self.assertEqual(self.sync("yes").json["items"][0]["receipt"],"91")
        self.sync("yes")
        self.assertEqual(len(self.writes),1)
        self.assertEqual(self.writes[0]["reply"]["in_reply_to_tweet_id"],"81")

    def test_catalog_request_alone_only_previews(self):
        self.posts = [self.post("83", "10", "请发目录")]
        self.assertEqual(self.sync("yes").json["items"][0]["state"], "仅预览草稿")
        self.assertFalse(self.writes)

    def test_stop_nested_own_existing(self):
        self.posts.extend([self.post("83","10","STOP","81"), self.post("84","44","already","82")])
        rows=self.sync("yes").json["items"]
        self.assertEqual([r["state"] for r in rows],["已退订","作者已回复"])
        self.assertFalse(self.writes)

    def test_approval_and_missing_credentials(self):
        with patch.dict("os.environ",{"X_BRAND_AUTOREPLY_APPROVED":"0"}):
            self.assertEqual(self.sync("yes").status_code,409)
        with patch.object(x,"credential",return_value=""):
            self.assertEqual(self.sync("no").status_code,409)
