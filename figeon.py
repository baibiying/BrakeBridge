"""Integration with the endpoints used by the Figeon Social web application."""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import requests

import inquiries
import social
from social_providers import ProviderError
from storage import get_db

ORIGIN = "https://market.hqsmartcloud.com"
ENDPOINTS = {"accounts": "/admin/SocialMedia/getUserOauth", "posts": "/admin/SocialMedia/getDynamic",
             "publish": "/admin/Ayrshare/post", "comments": "/admin/Ayrshare/get_comment",
             "binding": "/admin/Ayrshare/generateJwt"}
PLATFORMS = {"facebook": "Facebook", "reddit": "Reddit", "telegram": "Telegram", "twitter": "X",
             "linkedin": "LinkedIn", "youtube": "YouTube", "tiktok": "TikTok", "gmb": "Google 商家",
             "pinterest": "Pinterest", "instagram": "Instagram"}
COMMENTS = {"facebook", "instagram"}
VIDEO = {".mp4", ".mov", ".avi", ".m4v", ".quicktime"}
MEDIA_PLATFORMS = {"youtube": "video", "tiktok": "video", "instagram": "media", "pinterest": "image"}


def call(operation, token, fields=None):
    writing = operation == "publish"
    for _ in range(1 if writing else 2):
        try:
            response = requests.post(ORIGIN + ENDPOINTS[operation], data={**(fields or {}), "token": token},
                                     allow_redirects=False, timeout=(8, 22))
            break
        except requests.RequestException:
            continue
    else:
        raise ProviderError("飞鸽连接中断，请先核对远端记录。" if writing else "飞鸽连接中断，请稍后同步。",
                            uncertain=writing) from None
    if response.status_code in {401, 403}:
        raise ProviderError("飞鸽授权已失效，请重新连接。")
    if not 200 <= response.status_code < 300:
        raise ProviderError("飞鸽返回异常响应，请核对发送记录。", uncertain=writing)
    try:
        document = response.json()
    except ValueError:
        document = {}
    if not isinstance(document, dict) or document.get("status") not in (1, 2):
        message = str(document.get("msg", "")) if isinstance(document, dict) else ""
        if "授权" in message and ("过期" in message or "重新" in message):
            raise ProviderError("飞鸽授权已失效，请重新连接。")
        raise ProviderError("飞鸽未返回可确认的结果，请核对远端记录。" if writing else "飞鸽未返回可读取的记录。",
                            uncertain=writing)
    return document


def account_list(token):
    rows = call("accounts", token).get("data")
    if not isinstance(rows, list):
        raise ProviderError("无法读取飞鸽帐号。")
    return [{"platform": item["platform"], "name": str(item.get("name") or "")[:140],
             "supported": item["platform"] in PLATFORMS}
            for item in rows if isinstance(item, dict) and item.get("status") == "link"
            and isinstance(item.get("platform"), str)]


def connection():
    return get_db().execute("SELECT * FROM figeon_connection WHERE id=1").fetchone()


def access(row):
    if row is None:
        raise ValueError("请先连接飞鸽。")
    return social.credential({"active": True, "credential": row["credential"]})


def binding_url():
    data = call("binding", access(connection())).get("data")
    value = data.get("url") if isinstance(data, dict) else ""
    try:
        parsed = urlsplit(social.public_url(value if isinstance(value, str) else ""))
    except ValueError:
        parsed = urlsplit("")
    if parsed.hostname != "profile.ayrshare.com":
        raise ProviderError("飞鸽未返回可验证的帐号绑定页面。")
    return parsed.geturl()


def connect(token):
    if not re.fullmatch(r"[A-Za-z0-9._~+/-]{16,1024}={0,2}", token):
        raise ValueError("请输入当前飞鸽会话的授权令牌。")
    encrypted = social.cipher().encrypt(token.encode()).decode()
    accounts = account_list(token)
    db = get_db()
    with db:
        db.execute("BEGIN IMMEDIATE")
        previous = connection()
        revision = (previous["revision"] + 1) if previous else 1
        if previous:
            try:
                if access(previous) == token:
                    revision = previous["revision"]
            except ValueError:
                pass
        db.execute("""INSERT INTO figeon_connection VALUES(1,?,?,?,?)
                      ON CONFLICT(id) DO UPDATE SET credential=excluded.credential,revision=excluded.revision,
                      accounts=excluded.accounts,checked_at=excluded.checked_at""",
                   (encrypted, revision, json.dumps(accounts, ensure_ascii=False), social.utc_stamp()))
        db.execute("""UPDATE figeon_batches SET state='cancelled',note='飞鸽会话已更新，请按新帐号重新审核'
                      WHERE state='scheduled' AND connection_revision<>?""", (revision,))
    return len(accounts)


def check():
    connected = connection()
    if connected is None:
        return {"state": "disconnected", "message": "飞鸽尚未连接。"}
    try:
        accounts = account_list(access(connected))
    except (ProviderError, ValueError) as error:
        return {"state": "unavailable", "message": str(error)}
    with get_db() as db:
        db.execute("UPDATE figeon_connection SET accounts=?,checked_at=? WHERE revision=?",
                   (json.dumps(accounts, ensure_ascii=False), social.utc_stamp(), connected["revision"]))
    return {"state": "connected", "message": f"飞鸽已连通，读取到 {len(accounts)} 个已绑定帐号。"}


def date_text(value):
    if isinstance(value, (float, int)) and 0 < value < 4102444800000:
        return social.utc_stamp(datetime.fromtimestamp(value / (1000 if value > 4102444800 else 1), timezone.utc))
    return str(value or "")


def identity(value):
    value = str(value or "")
    return int(value) if value.isascii() and value.isdecimal() and len(value) <= 15 else None


def platform_link(value):
    try:
        url = social.public_url(str(value or ""))
    except ValueError:
        return ""
    host = urlsplit(url).hostname or ""
    roots = {"facebook.com", "instagram.com", "linkedin.com", "youtube.com", "youtu.be", "x.com", "twitter.com",
             "reddit.com", "redd.it", "t.me", "telegram.me", "tiktok.com", "pinterest.com", "pin.it",
             "g.page", "share.google", "maps.google.com", "business.google.com"}
    if any(host == root or host.endswith("." + root) for root in roots):
        return url
    return ""


def post_values(item):
    targets = item.get("platforms")
    results = item.get("result")
    if isinstance(results, list):
        results = {result.get("platform"): result for result in results if isinstance(result, dict)}
    if not isinstance(results, dict):
        results = {}
    receipts = []
    for target in targets if isinstance(targets, list) else []:
        platform = target.get("platform") if isinstance(target, dict) else None
        if not isinstance(platform, str):
            continue
        result = results.get(platform)
        result = result if isinstance(result, dict) else {}
        status = str(result.get("status") or "").lower()
        link = platform_link(result.get("postUrl"))
        state = ("sent" if status in {"success", "1"} else "failed" if status in {"error", "failed", "2"} else "pending")
        receipts.append({"platform": platform, "name": str(target.get("platform_user") or "")[:140], "state": state,
                         "url": link, "comments": platform in COMMENTS and state == "sent"})
    return receipts


def sync():
    connected = connection()
    token = access(connected)
    revision = connected["revision"]
    accounts = account_list(token)
    document = call("posts", token, {"p": 1, "size": 100}).get("data")
    if not isinstance(document, dict) or not isinstance(document.get("data"), list):
        raise ProviderError("飞鸽文章记录格式异常。")
    db = get_db()
    count = 0
    with db:
        db.execute("BEGIN IMMEDIATE")
        if connection()["revision"] != revision:
            raise ValueError("飞鸽帐号刚刚更新，请重新同步。")
        for item in document["data"]:
            remote = identity(item.get("id")) if isinstance(item, dict) else None
            if remote is None:
                continue
            status = identity(item.get("status")) or 0
            receipts = post_values(item)
            title, body = str(item.get("title") or "")[:500], str(item.get("content") or "")[:12000]
            db.execute("""INSERT INTO figeon_posts VALUES(?,?,?,?,?,?,?) ON CONFLICT(connection_revision,remote_id)
                          DO UPDATE SET title=excluded.title,content=excluded.content,created_at=excluded.created_at,
                          status=excluded.status,targets=excluded.targets""",
                       (revision, remote, title, body, date_text(item.get("create_at")), status,
                        json.dumps(receipts, ensure_ascii=False)))
            db.execute("""UPDATE figeon_batches SET state=?,note='',updated_at=?
                          WHERE connection_revision=? AND remote_id=? AND state IN ('submitted','sent','partial','failed')""",
                       (receipt_state(receipts, status), social.utc_stamp(), revision, remote))
            count += 1
        db.execute("UPDATE figeon_connection SET accounts=?,checked_at=? WHERE revision=?",
                   (json.dumps(accounts, ensure_ascii=False), social.utc_stamp(), revision))
    return count


def receipt_state(receipts, status):
    states = {item["state"] for item in receipts}
    if status == 3 or "pending" in states:
        return "submitted"
    if states == {"sent"}:
        return "sent"
    if states == {"failed", "sent"}:
        return "partial"
    return "failed" if status == 2 or states == {"failed"} else "submitted"


def snapshot():
    connected = connection()
    platforms = [{"key": key, "label": name, "media": MEDIA_PLATFORMS.get(key, "")}
                 for key, name in PLATFORMS.items()]
    if connected is None:
        return {"connected": False, "platforms": platforms, "accounts": [], "batches": [], "posts": []}
    revision = connected["revision"]
    db = get_db()
    posts = [{**dict(row), "targets": json.loads(row["targets"])} for row in db.execute(
        """SELECT remote_id,title,content,created_at,status,targets FROM figeon_posts
           WHERE connection_revision=? ORDER BY created_at DESC,remote_id DESC LIMIT 100""", (revision,))]
    batches = [{**dict(row), "platforms": json.loads(row["platforms"])} for row in db.execute(
        """SELECT b.id,b.platforms,b.run_at,b.state,b.note,b.remote_id,v.title,v.revision
           FROM figeon_batches b JOIN social_versions v ON v.id=b.version_id
           WHERE connection_revision=? ORDER BY b.id DESC LIMIT 100""", (revision,))]
    return {"connected": True, "checked_at": connected["checked_at"], "platforms": platforms,
            "accounts": json.loads(connected["accounts"]),
            "batches": batches, "posts": posts}


def schedule(version_id, platforms, content_key, media, visibility, when, request_key=""):
    db = get_db()
    run_at = social.schedule_time(when)
    media = social.public_url(social.text(media, 2000, "媒体链接"))
    if not platforms or any(key not in PLATFORMS for key in platforms):
        raise ValueError("请选择已绑定且支持的飞鸽帐号。")
    video = urlsplit(media).path.lower().endswith(tuple(VIDEO))
    if "youtube" in platforms and (not video or visibility not in {"public", "unlisted", "private"}):
        raise ValueError("YouTube 需要公开可读取的视频链接和可见性设置。")
    if "tiktok" in platforms and not video:
        raise ValueError("TikTok 需要公开可读取的 HTTPS 视频链接。")
    if "instagram" in platforms and not media:
        raise ValueError("Instagram 需要图片或视频。")
    if "pinterest" in platforms and (not media or video):
        raise ValueError("Pinterest 请使用 HTTPS 图片链接。")
    if content_key not in social.PLATFORMS:
        raise ValueError("请选择一个已审核的平台文案。")
    with social.write_transaction(db):
        connected = connection()
        if connected is None:
            raise ValueError("请先连接飞鸽。")
        linked = {account["platform"] for account in json.loads(connected["accounts"])}
        if not set(platforms) <= linked:
            raise ValueError("部分平台尚未绑定，请先同步帐号。")
        version = social.approved_version(version_id)
        if not version["approved_at"] or not version[content_key]:
            raise ValueError("请批准稿件并补齐所选文案。")
        content = version[content_key] + ("\n\n" + version["url"] if version["url"] else "")
        if version["image"] and not media:
            raise ValueError("此版本含配图，请为飞鸽选择可公开读取的 HTTPS 媒体链接。")
        result = db.execute("""INSERT INTO figeon_batches(version_id,connection_revision,platforms,content,media,
                               visibility,run_at,state,updated_at,request_key) VALUES(?,?,?,?,?,?,?,'scheduled',?,?)
                               ON CONFLICT(version_id) DO NOTHING""",
                            (version_id, connected["revision"], json.dumps(sorted(set(platforms))), content, media,
                             visibility if "youtube" in platforms else "", run_at, social.utc_stamp(), request_key))
    return result.rowcount


def release(draft_id, request_key, title, body, selected, platforms, media, visibility, when):
    if not re.fullmatch(r"[0-9a-f-]{36}", request_key):
        raise ValueError("请刷新工作台再提交发布。")
    db = get_db()
    with social.write_transaction(db):
        previous = db.execute("""SELECT b.id,v.draft_id FROM figeon_batches b
                              JOIN social_versions v ON v.id=b.version_id WHERE b.request_key=?""",
                              (request_key,)).fetchone()
        if previous:
            return {"batch_id": previous["id"], "draft_id": previous["draft_id"], "duplicate": True}
        detail = social.draft_detail(draft_id) if draft_id else None
        current_refs = detail["refs"] if detail else ()
        refs = social.sources(selected, current_refs)
        draft = social.save_draft(draft_id, title, body, body, "", refs, ("", None), False)
        social.approve(draft["version_id"])
        schedule(draft["version_id"], platforms, "facebook", media, visibility, when, request_key)
        row = db.execute("SELECT id FROM figeon_batches WHERE request_key=?", (request_key,)).fetchone()
        return {"batch_id": row["id"], "draft_id": draft["id"], "duplicate": False}


def cancel(batch_id):
    db = get_db()
    with db:
        count = db.execute("UPDATE figeon_batches SET state='cancelled',updated_at=? WHERE id=? AND state='scheduled'",
                           (social.utc_stamp(), batch_id)).rowcount
    if not count:
        raise ValueError("只有待发送的飞鸽批次可以取消。")


def run_due(limit=5):
    db = get_db()
    count = 0
    with db:
        db.execute("""UPDATE figeon_batches SET state='uncertain',note='发送中断，请核对飞鸽文章',
                      updated_at=? WHERE state='sending' AND updated_at<?""",
                   (social.utc_stamp(), social.utc_stamp(datetime.now(timezone.utc) - timedelta(minutes=3))))
    for _ in range(limit):
        with db:
            db.execute("BEGIN IMMEDIATE")
            batch = db.execute("""SELECT b.*,v.title FROM figeon_batches b JOIN social_versions v ON v.id=b.version_id
                                 WHERE b.state='scheduled' AND b.run_at<=? ORDER BY b.run_at,b.id LIMIT 1""",
                               (social.utc_stamp(),)).fetchone()
            if batch is None:
                break
            db.execute("UPDATE figeon_batches SET state='sending',updated_at=? WHERE id=?", (social.utc_stamp(), batch["id"]))
        note, state, remote = "", "submitted", None
        try:
            connected = connection()
            if connected is None or connected["revision"] != batch["connection_revision"]:
                raise ValueError("飞鸽帐号已经更新，请重新安排发布。")
            social.approved_version(batch["version_id"])
            token = access(connected)
            platforms = json.loads(batch["platforms"])
            if not set(platforms) <= {row["platform"] for row in account_list(token)}:
                raise ValueError("部分目的帐号在飞鸽已解绑，请核对。")
            response = call("publish", token, {"platform": ",".join(platforms), "title": batch["title"],
                            "content": batch["content"], "media": batch["media"], "thumb": "",
                            "visibility": batch["visibility"]})
            remote = identity(response.get("id"))
            if not remote:
                raise ProviderError("飞鸽未返回任务编号，请先检查文章管理。", uncertain=True)
            count += 1
        except (ProviderError, ValueError) as error:
            note, state = str(error), "uncertain" if getattr(error, "uncertain", False) else "failed"
        with db:
            db.execute("""UPDATE figeon_batches SET state=?,remote_id=?,note=?,updated_at=?
                          WHERE id=? AND state='sending'""", (state, remote, note, social.utc_stamp(), batch["id"]))
    return count


def refresh_due(force=False):
    connected = connection()
    if connected is None:
        return
    threshold = social.utc_stamp(datetime.now(timezone.utc) - timedelta(minutes=2))
    if not force and connected["checked_at"] >= threshold:
        return
    pending = get_db().execute("""SELECT 1 FROM figeon_batches WHERE connection_revision=? AND state='submitted'
                                 AND updated_at>? LIMIT 1""",
                              (connected["revision"], social.utc_stamp(datetime.now(timezone.utc) - timedelta(days=2)))).fetchone()
    if pending:
        try:
            sync()
        except (ProviderError, ValueError):
            with get_db() as db:
                db.execute("UPDATE figeon_connection SET checked_at=? WHERE revision=?",
                           (social.utc_stamp(), connected["revision"]))


def comment_context(remote, platform):
    connected = connection()
    if platform not in COMMENTS or connected is None:
        raise ValueError("该飞鸽平台当前没有评论接口。")
    post = get_db().execute("SELECT * FROM figeon_posts WHERE connection_revision=? AND remote_id=?",
                            (connected["revision"], remote)).fetchone()
    target = next((row for row in json.loads(post["targets"]) if row["platform"] == platform and row["comments"]), None) if post else None
    if target is None:
        raise ValueError("请先同步该平台已成功发布的文章。")
    return connected, post, target


def sync_comments(remote, platform):
    connected, _, target = comment_context(remote, platform)
    data = call("comments", access(connected), {"id": remote, "platform": platform}).get("data")
    rows = data.get(platform) if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ProviderError("飞鸽没有返回此平台的评论。")
    records = []
    for item in rows:
        if not isinstance(item, dict) or not isinstance(item.get("comment"), str):
            continue
        body = item["comment"]
        author = item.get("from") if isinstance(item.get("from"), dict) else {}
        date = date_text(item.get("created"))
        external = item.get("id") or item.get("commentId") or hashlib.sha256(
            json.dumps([author.get("id"), author.get("name"), date, body]).encode()).hexdigest()
        records.append((len(records) + 1, {"body": body, "company": str(author.get("name") or "")[:140],
                        "comment_id": str(external), "received_at": date, "post_id": str(remote),
                        "account": "figeon-" + str(connected["revision"]), "source_ref": target["url"]}))
    return inquiries.import_rows(records, "authorized", transport="figeon", channel="figeon:" + platform)
