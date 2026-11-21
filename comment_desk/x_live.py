"""Single-post X demonstration using the official OAuth 2.0 and Posts APIs."""
import base64
import hashlib
import os
import re
import secrets
import time
from threading import Lock
from urllib.parse import urlencode

import requests
from flask import Blueprint, redirect, request, session

from storage import get_db
from workspace import error

bp = Blueprint("x_live", __name__)
ROOT = "2124107958841753962"
ACCOUNT = "qsl8880"
CALLBACK = "http://127.0.0.1:8099/comment-desk/x/callback"
FIELDS = "author_id,conversation_id,referenced_tweets,created_at,possibly_sensitive"
lock = Lock()
tokens = {}


class PlatformError(Exception):
    pass


def call(method, path, **kwargs):
    try:
        response = requests.request(method, "https://api.x.com/2/" + path, timeout=(4, 10),
                                    allow_redirects=False, **kwargs)
        if not 200 <= response.status_code < 300:
            labels = {401: "授权已失效，请重新连接", 402: "请检查开发者账户余额", 403: "请检查读写权限与平台限制",
                      404: "帖子或评论已不可访问", 429: "已达到限流，请稍后手动恢复"}
            raise PlatformError(f"X 返回 {response.status_code}，{labels.get(response.status_code, '请核对平台状态')}。")
        data = response.json()
        if not isinstance(data, dict) or data.get("errors"):
            raise PlatformError("X 返回不完整结果，请稍后核对。")
        return data
    except (requests.RequestException, ValueError) as exc:
        raise PlatformError("未取得有效的 X 回执，请核对网络及原帖。") from exc


def credential():
    now = time.time()
    with lock:
        for key in list(tokens):
            if tokens[key][1] <= now:
                del tokens[key]
        return tokens.get(session.get("x_connection"), ("", 0))[0]


@bp.post("/api/comment-desk/x/connect")
def connect():
    client = os.getenv("X_CLIENT_ID")
    if not client:
        return error("请先在 X 开发者应用启用 OAuth 2.0 并在本机配置 X_CLIENT_ID。", 409)
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(32)
    session["x_login"] = (state, verifier, time.time())
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    scope = "tweet.read users.read"
    if os.getenv("X_BRAND_AUTOREPLY_APPROVED") == "1":
        scope += " tweet.write"
    return {"url": "https://x.com/i/oauth2/authorize?" + urlencode(dict(
        response_type="code", client_id=client, redirect_uri=CALLBACK, scope=scope,
        state=state, code_challenge=challenge, code_challenge_method="S256"))}


@bp.get("/comment-desk/x/callback")
def callback():
    login = session.pop("x_login", None)
    if not login or not secrets.compare_digest(login[0], request.args.get("state", "")) or time.time()-login[2] > 600:
        return "授权请求已过期，请返回线索承接重新连接。", 400
    if not request.args.get("code"):
        return redirect("/comment-desk?panel=x")
    client = os.getenv("X_CLIENT_ID", "")
    secret = os.getenv("X_CLIENT_SECRET")
    try:
        data = call("POST", "oauth2/token", auth=(client, secret) if secret else None, data=dict(
            grant_type="authorization_code", client_id=client, code=request.args["code"],
            redirect_uri=CALLBACK, code_verifier=login[1]))
        token = data.get("access_token")
        if not token:
            raise PlatformError("授权返回中缺少访问令牌。")
        owner({"Authorization": "Bearer " + token})
    except PlatformError as exc:
        return str(exc), 409
    key = secrets.token_urlsafe(32)
    with lock:
        tokens.pop(session.get("x_connection"), None)
        tokens[key] = (token, time.time() + int(data.get("expires_in", 7200)))
    session["x_connection"] = key
    return redirect("/comment-desk?panel=x")


def owner(headers):
    me = call("GET", "users/me", headers=headers).get("data", {})
    if str(me.get("username", "")).lower() != ACCOUNT or not me.get("id"):
        raise PlatformError("请用指定原帖的作者 @qsl8880 授权。")
    return me["id"]


def parent(post):
    return next((r.get("id") for r in post.get("referenced_tweets", []) if r.get("type") == "replied_to"), None)


def comments(headers, author):
    root = call("GET", "tweets/" + ROOT, headers=headers, params={"tweet.fields":FIELDS}).get("data", {})
    if root.get("author_id") != author or root.get("id") != ROOT:
        raise PlatformError("原帖归属校验失败。")
    params = {"query":"conversation_id:" + root["conversation_id"], "tweet.fields":FIELDS, "max_results":100}
    posts = []
    for _ in range(5):
        page = call("GET", "tweets/search/recent", headers=headers, params=params)
        posts.extend(page.get("data", []))
        cursor = page.get("meta", {}).get("next_token")
        if not cursor:
            return posts
        params["next_token"] = cursor
    raise PlatformError("会话超过本次 500 条读取上限，已暂停回复，请到原帖核对。")


def init_tables(db):
    db.execute("CREATE TABLE IF NOT EXISTS comment_desk_x_receipts (comment_id TEXT PRIMARY KEY, state TEXT NOT NULL, receipt TEXT DEFAULT '')")
    db.execute("CREATE TABLE IF NOT EXISTS comment_desk_x_stops (author TEXT PRIMARY KEY)")
    db.execute("CREATE TABLE IF NOT EXISTS comment_desk_x_clock (id INTEGER PRIMARY KEY, attempted REAL NOT NULL)")


def plain(text):
    return re.sub(r"^@qsl8880\s+", "", text.strip(), flags=re.I).strip().casefold().strip("。！？!? .")


def draft(text):
    phrase = plain(text)
    part = re.fullmatch(r"(?:请报价\s*[:：]?\s*|(?:please )?quote\s+)((?:wva|fmsi)\s*[-:#]?\s*\d{4,5})", phrase)
    if part:
        code = part[1].upper()
        if phrase.isascii():
            return f"We can check {code}. How many sets do you need? Reply STOP to opt out."
        return f"收到，我们可以先查 {code}。您预计需要多少套？回复 STOP 可停止自动回复。"
    templates = [
        ({"请发目录","有产品目录吗","产品目录","catalog please","can i get a catalog"},
         "可以。您想看鼓式衬片、盘式刹车片，还是制动蹄总成？", "Which catalog would help: drum linings, disc pads, or brake shoe assemblies?"),
        ({"这款多少钱","请报价","可以报价吗","price please","can you quote"},
         "可以帮您查价。您手边有旧件编号或包装标签吗？有其中一项就可以开始查。", "We can start checking a quote. Do you have an old part number or the label on the box?")]
    for words, zh, en in templates:
        if phrase in words:
            return (zh + " 回复 STOP 可停止自动回复。" if not phrase.isascii() else en + " Reply STOP to opt out.")
    return ""


AUTO_REPLY_REQUEST = re.compile(r"^(?:请自动回复|please auto-reply)\s*[:：]\s*", re.I)


@bp.get("/api/comment-desk/x")
def status():
    return {"configured": bool(os.getenv("X_CLIENT_ID")), "connected": bool(credential()),
            "approved": os.getenv("X_BRAND_AUTOREPLY_APPROVED") == "1", "callback": CALLBACK}


@bp.post("/api/comment-desk/x/sync")
def sync():
    token = credential()
    if not token:
        return error("请先连接 X 账号。", 409)
    send = request.form.get("publish") == "yes"
    if send and os.getenv("X_BRAND_AUTOREPLY_APPROVED") != "1":
        return error("此品牌活动尚未配置 X 审批。可先同步并预览草稿。", 409)
    headers = {"Authorization":"Bearer " + token}
    try:
        author = owner(headers)
        posts = comments(headers, author)
    except PlatformError as exc:
        return error(str(exc), 409)
    db = get_db()
    init_tables(db)
    replied = {parent(p):p["id"] for p in posts if p.get("author_id") == author and parent(p)}
    with db:
        for cid, rid in replied.items():
            db.execute("""UPDATE comment_desk_x_receipts SET state='已在原帖查到作者回复', receipt=?
                        WHERE comment_id=? AND state LIKE '发送待核对%'""", (rid,cid))
        for p in posts:
            if p.get("author_id") and plain(p.get("text", "")) in {"stop","unsubscribe","退订","停止","停止自动回复"}:
                db.execute("INSERT OR IGNORE INTO comment_desk_x_stops VALUES (?)",(p["author_id"],))
    rows = []
    sent_this_round = bool(db.execute("SELECT 1 FROM comment_desk_x_receipts WHERE state LIKE '发送待核对%' LIMIT 1").fetchone())
    seen = set()
    for p in posts:
        cid, uid = p.get("id"), p.get("author_id")
        if not cid or not cid.isdigit() or not uid or uid == author or parent(p) != ROOT or cid in seen:
            continue
        seen.add(cid)
        text = p.get("text","")
        consent = AUTO_REPLY_REQUEST.match(text.lstrip())
        reply = "" if p.get("possibly_sensitive") else draft(AUTO_REPLY_REQUEST.sub("", text.lstrip(), count=1))
        saved = db.execute("SELECT state,receipt FROM comment_desk_x_receipts WHERE comment_id=?", (cid,)).fetchone()
        stopped = db.execute("SELECT 1 FROM comment_desk_x_stops WHERE author=?", (uid,)).fetchone()
        state, receipt = "待人工判断", ""
        if saved:
            state, receipt = saved
        elif cid in replied:
            state = "作者已回复"
        elif stopped:
            state = "已退订"
        elif reply:
            state = "可自动回复" if consent else "仅预览草稿"
            if consent and send and not sent_this_round:
                with db:
                    claimed = db.execute("""INSERT OR IGNORE INTO comment_desk_x_receipts
                        SELECT ?, '发送待核对','' WHERE NOT EXISTS
                        (SELECT 1 FROM comment_desk_x_clock WHERE attempted>?) AND NOT EXISTS
                        (SELECT 1 FROM comment_desk_x_stops WHERE author=?) AND NOT EXISTS
                        (SELECT 1 FROM comment_desk_x_receipts WHERE state LIKE '发送待核对%')""",
                        (cid,time.time()-60,uid)).rowcount
                    if claimed:
                        db.execute("INSERT OR REPLACE INTO comment_desk_x_clock VALUES (1,?)", (time.time(),))
                if claimed:
                    sent_this_round = True
                    try:
                        result = call("POST", "tweets", headers=headers, json={"text":reply,"reply":{"in_reply_to_tweet_id":cid}})
                        receipt = str(result.get("data",{}).get("id",""))
                        state = "平台已确认" if receipt.isdigit() else "发送待核对"
                    except PlatformError as exc:
                        state = "发送待核对：" + str(exc)
                    with db:
                        db.execute("UPDATE comment_desk_x_receipts SET state=?, receipt=? WHERE comment_id=?", (state,receipt,cid))
                else:
                    state = "暂未发送，请重新同步核对"
        rows.append(dict(id=cid, text=text, draft=reply if state in {"可自动回复", "仅预览草稿"} or receipt else "", state=state, receipt=receipt))
    unresolved = db.execute("SELECT 1 FROM comment_desk_x_receipts WHERE state LIKE '发送待核对%' LIMIT 1").fetchone()
    return {"items":rows, "halt": bool(unresolved)}
