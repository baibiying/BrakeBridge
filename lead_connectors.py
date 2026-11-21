import imaplib
import json
import ssl
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import requests
from flask import current_app

import lead_inquiries as leads
from storage import get_db


def setting(key, default):
    row = get_db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def store_setting(key, value):
    db = get_db()
    with db:
        db.execute("""INSERT INTO settings(key,value) VALUES(?,?)
                      ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (key, json.dumps(value, ensure_ascii=False)))


def mailbox_ready():
    return all(current_app.config.get("LEADS_IMAP_" + name) for name in ("HOST", "USER", "PASSWORD"))


def sync_mail(event_date=""):
    cfg = current_app.config
    if not mailbox_ready():
        raise ValueError("请先配置实际收件邮箱。")
    outcome = {"checked_at": leads.stamp(), "inserted": 0, "duplicate": 0, "skipped": 0, "failed": 0}
    account = cfg["LEADS_IMAP_HOST"] + ":" + cfg["LEADS_IMAP_USER"] + ":" + cfg["LEADS_IMAP_FOLDER"]
    try:
        with imaplib.IMAP4_SSL(cfg["LEADS_IMAP_HOST"], ssl_context=ssl.create_default_context(), timeout=12) as client:
            client.login(cfg["LEADS_IMAP_USER"], cfg["LEADS_IMAP_PASSWORD"])
            status, _ = client.select(cfg["LEADS_IMAP_FOLDER"], readonly=True)
            if status != "OK":
                raise imaplib.IMAP4.error("Mailbox unavailable")
            _, values = client.response("UIDVALIDITY")
            validity = values[0].decode() if values and values[0] else ""
            old = setting("lead_imap_cursor", {})
            last = old.get("uid", 0) if old.get("account") == account and old.get("validity") == validity else 0
            since = (datetime.now(leads.ZONE).date() - timedelta(days=7)).strftime("%d-%b-%Y")
            criteria = ("UID", f"{last + 1}:*") if last else ("SINCE", since)
            status, data = client.uid("search", None, *criteria)
            if status != "OK":
                raise imaplib.IMAP4.error("Search failed")
            new_ids = sorted({int(uid) for uid in data[0].split() if uid.isdigit() and int(uid) > last}) if data and data[0] else []
            for uid in new_ids[:100]:
                status, fetched = client.uid("fetch", str(uid), "(BODY.PEEK[]<0.5242881>)")
                raw = next((part[1] for part in fetched or [] if isinstance(part, tuple)), None)
                if status != "OK" or raw is None:
                    raise imaplib.IMAP4.error("Fetch failed")
                try:
                    message = leads.parse_email(raw)
                    if message["refs"] or message["subject"].lower().startswith(("re:", "回复:", "回复：")) or message["auto"]:
                        received = datetime.fromisoformat(message["received_at"])
                        current = datetime.now(timezone.utc)
                        live = current - timedelta(days=1) <= received <= current + timedelta(hours=1)
                        inserted = leads.import_email(message, "authorized", event_date, alert=live)
                        outcome["duplicate" if inserted["duplicate"] else "inserted"] += 1
                    else:
                        outcome["skipped"] += 1
                except ValueError:
                    outcome["failed"] += 1
                store_setting("lead_imap_cursor", {"account": account, "validity": validity, "uid": uid})
            outcome["remaining"] = max(0, len(new_ids) - 100)
    except (imaplib.IMAP4.error, OSError, ValueError):
        outcome["error"] = "邮箱连接或读取失败，请检查授权、IMAP 和收件文件夹。"
    store_setting("lead_imap_last", outcome)
    return outcome


def webhook():
    value = current_app.config.get("LEADS_WECOM_WEBHOOK", "")
    try:
        endpoint = urlsplit(value)
        key = parse_qs(endpoint.query).get("key", [])
        if (endpoint.scheme == "https" and endpoint.netloc.lower() == "qyapi.weixin.qq.com"
                and endpoint.path == "/cgi-bin/webhook/send" and len(key) == 1 and not endpoint.fragment):
            return value
    except ValueError:
        pass
    return ""


def queue_digest():
    if not webhook():
        return 0
    now = datetime.now(leads.ZONE)
    if now.hour < 9:
        return 0
    eligible = {row["inquiry_id"] for row in get_db().execute("SELECT DISTINCT inquiry_id FROM lead_messages WHERE live=1")}
    shortlist = leads.event_shortlist([row for row in leads.listing() if row["data_kind"] == "authorized" and row["id"] in eligible])
    ordered = [*shortlist["meetings"], *shortlist["show"], *shortlist["follow"]]
    if not ordered:
        return 0
    lines = [f"【展会重点询盘】{now.date().isoformat()} · 共 {len(ordered)} 条"]
    for item in ordered[:10]:
        analysis = item["analysis"]
        company = leads.safe_company(item["fields"], item["id"])
        lines.append(f"{company}｜{leads.PRIORITIES[analysis['tier']]}｜{analysis['next_action']}（#{item['id']}）")
    db = get_db()
    with db:
        return leads.queue_notice(f"digest:{now.date().isoformat()}", None, "\n".join(lines))


def process_notifications(limit=10):
    endpoint = webhook()
    if not endpoint:
        return 0
    db = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    with db:
        db.execute("""UPDATE lead_notifications SET state='uncertain',receipt='发送结果待人工核对'
                      WHERE state='sending' AND updated_at<?""", (cutoff,))
    sent = 0
    for _ in range(limit):
        with db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT n.* FROM lead_notifications n WHERE n.state='pending'
                                AND NOT EXISTS (SELECT 1 FROM lead_inquiries i
                                                WHERE i.id=n.inquiry_id AND i.deleted_at<>'')
                                ORDER BY n.id LIMIT 1""").fetchone()
            if row is None:
                break
            db.execute("UPDATE lead_notifications SET state='sending',updated_at=? WHERE id=?", (leads.stamp(), row["id"]))
        base = current_app.config.get("LEADS_PUBLIC_URL", "").rstrip("/")
        link = ""
        if base.startswith("https://") and leads.http_link(base):
            link = base + "/lead-inquiries" + (f"?id={row['inquiry_id']}" if row["inquiry_id"] else "")
        body = (row["body"] + f"\n打开工作台：{link}" if link else row["body"])
        body = body.encode("utf-8")[:2048].decode("utf-8", errors="ignore")
        try:
            response = requests.post(endpoint, json={"msgtype": "text", "text": {"content": body}},
                                     timeout=12, allow_redirects=False)
            details = response.json() if response.ok else {}
            if response.ok and isinstance(details, dict) and details.get("errcode") == 0:
                state, receipt = "sent", "企业微信已接收"
                sent += 1
            elif response.status_code >= 500:
                state, receipt = "uncertain", "发送结果待人工核对"
            else:
                state, receipt = "failed", "企业微信拒绝，请检查机器人配置"
        except (requests.RequestException, ValueError):
            state, receipt = "uncertain", "发送结果待人工核对"
        with db:
            db.execute("UPDATE lead_notifications SET state=?,receipt=?,updated_at=? WHERE id=?",
                       (state, receipt, leads.stamp(), row["id"]))
    return sent
