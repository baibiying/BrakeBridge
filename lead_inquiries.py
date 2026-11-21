import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit
from flask import current_app

import agent_scoring
import buyer_settings
import buyers
import inquiries
import scoring
from storage import get_db

FIELDS = {
    "company": "公司", "name": "姓名", "phone": "电话", "mail": "邮箱", "title": "标题", "content": "内容",
    "social": "社交账号", "address": "国家/地区", "ip": "IP", "page": "页面来源",
    "first_page": "首次访问", "addtime": "提交时间", "files": "附件",
}
HEADERS = {"询盘单号": "number", "来源": "source", "是否回复": "replied", "添加时间": "added_at",
           "动态附加字段": "meta", "手机": "phone", "地区": "address", "页面地址": "page",
           "来源页面": "source", **{label: key for key, label in FIELDS.items()}}
LEAD_STATES = {"open": "待跟进", "working": "跟进中", "done": "已跟进", "declined": "暂无合作", "archived": "留档"}
PRIORITIES = {**buyers.TIER_LABELS, "archive": "系统留档"}
WEIGHTS_KEY = "inquiry_weights"
WEIGHT_DEFAULTS = {"product": 30, "action": 30, "specifics": 20, "show": 15, "company": 5}
WEIGHT_DIMENSIONS = {"product": "产品匹配", "action": "采购动作", "specifics": "询价明细",
                     "show": "展会意向", "company": "企业线索"}
OPEN = {"open", "working"}
ZONE = timezone(timedelta(hours=8))
MAX_BODY = 24000
MAX_FILE = 5 * 1024 * 1024
MESSAGE_ID = re.compile(r"<[^<>\s]+>")
AUTO = re.compile(r"out of office|automatic reply|undeliverable|delivery status notification|自动回复|邮件退信", re.I)
NO = re.compile(r"unsubscribe|remove me|not interested|not looking to|will not be attending|won.t be attending|"
                r"already have a supplier|退订|不感兴趣|暂无合作|不考虑|不参加|取消询价", re.I)
CONFIRMED = re.compile(r"\b(?:will attend|registered for|see you at|confirm(?:ed)? (?:our|the) meeting)\b|"
                       r"已报名|确认(?:参展|到展|参加|约见)|展会见|会在展会见", re.I)
PLAN = re.compile(r"plan(?:ning)? to attend|planning on attending|intend to attend|准备参展|计划(?:参展|到展|参加)|打算参加", re.I)
SLOT = re.compile(r"(?:(?:meet(?:ing)?|见面|会面|约见).{0,38})(?:\b\d{1,2}:\d{2}\b|\d{1,2}\s*[ap]\.?m\.?|[上下]午.{0,4}\d{1,2}点)", re.I)
MEET = re.compile(r"\bmeet(?:ing)?\b|见面|会面|约见", re.I)
UNCONFIRMED = re.compile(r"not (?:yet )?registered|not confirmed|尚未确认|还没确认|尚未报名", re.I)
RFQ = re.compile(r"quot(?:e|ation)|pric(?:e|ing)|lead time|sample|询价|报价|价格|样品|交期|订购|想订", re.I)
CHECK = re.compile(r"catalog(?:ue)?|brochure|booth|stand number|目录|展位|规格|适配", re.I)
SPEC = re.compile(r"\bt850\b|\bwva[- ]?\d{4,5}\b|\d+\s*(?:套|件|片|sets?\b|pcs\b|pieces?\b)", re.I)
PART_NUMBER = re.compile(r"(?<![a-z0-9-])(?:T850|(?:WVA|OEM)[ :\-]*\d{4,8}|(?:19|29)\d{3})(?![a-z0-9])", re.I)
QUANTITY = re.compile(r"(?<![\d.])\d[\d,]*\s*(?:套|件|片|sets?\b|pcs?\b|pieces?\b)", re.I)
QUANTITY_HEADER = re.compile(r"\bqty\s*/\s*(?:sets?|pcs)\b", re.I)
QUANTITY_TOTAL = re.compile(r"\btotal\s*:?\s*\d[\d,]*", re.I)
TECHNICAL_SPEC = re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*(?:mm\b|holes?\b|inches?\b|毫米|孔)", re.I)
EXPO = re.compile(r"\b(?:expo|exhibition|trade show|automechanika|booth)\b|展会|参展|到展|展位", re.I)
TRADE_ROLE = re.compile(r"\b(?:distribuidor(?:a|es)?|importador(?:a|es)?|distributeur|importateur|(?:leading|top) sellers?)\b", re.I)
DATE_SLOT = re.compile(r"(?:(\d{4})[-/])?(\d{1,2})[-/月](\d{1,2})日?\s*(?:(?:下午|上午)\s*)?(\d{1,2})[:：](\d{2})")
TRAIL = re.compile(r"^\s*(?:>+|On .{1,130} wrote:\s*$|[- ]{2,}Original Message[- ]*|"
                   r"_{4,}\s*$|[- ]{2,}Forwarded message|(?:From|发件人|原始邮件)[:：])", re.I)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def intake_day(row):
    fields = payload(row["fields"], {})
    value = date_value(fields.get("added_at") or fields.get("addtime")) or date_value(row["received_at"])
    return datetime.fromisoformat(value).astimezone(ZONE).date().isoformat() if value else ""


def date_value(value):
    if not value:
        return ""
    if isinstance(value, str) and value.isdecimal() and len(value) in (10, 13):
        try:
            return datetime.fromtimestamp(int(value) / (1000 if len(value) == 13 else 1), timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            return ""
    try:
        result = datetime.fromisoformat(str(value).strip())
    except ValueError:
        try:
            result = parsedate_to_datetime(str(value))
        except (ValueError, TypeError, OverflowError):
            return ""
    return (result if result.tzinfo else result.replace(tzinfo=ZONE)).astimezone(timezone.utc).isoformat()


def http_link(value):
    try:
        link = urlsplit(value)
        return value if link.scheme in {"http", "https"} and link.hostname and not link.username else ""
    except ValueError:
        return ""


def plain(value, size=MAX_BODY):
    return str(value if value is not None else "").strip()[:size]


def payload(value, default):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value if value is not None else default


def vendor_fields(record):
    row = {HEADERS.get(key, key): value for key, value in record.items()}
    fields = {key: plain(row.get(key), MAX_BODY if key == "content" else 1500) for key in FIELDS if key not in {"social", "files"}}
    social = payload(row.get("social"), {})
    fields["social"] = {key: plain(value, 800) for key, value in social.items() if isinstance(key, str)} if isinstance(social, dict) else {}
    for field, name in (("fb", "Facebook"), ("tw", "Twitter"), ("ln", "LinkedIn")):
        if row.get(field):
            fields["social"][name] = plain(row[field], 800)
    if not fields["social"] and isinstance(row.get("social"), str):
        fields["social"] = {"原值": plain(row["social"], 800)}
    files = payload(row.get("files"), [])
    fields["files"] = [{"name": plain(item.get("name") or item.get("title"), 300),
                        "url": http_link(plain(item.get("url"), 1500))} if isinstance(item, dict)
                       else {"name": plain(item, 300), "url": ""} for item in files[:40]] if isinstance(files, list) else []
    if not fields["files"] and isinstance(row.get("files"), str):
        fields["files"] = [{"name": plain(row["files"], 300), "url": ""}]
    extra = payload(row.get("meta"), {})
    if isinstance(extra, list):
        extra = {item["key"]: item.get("value", "") for item in extra if isinstance(item, dict) and isinstance(item.get("key"), str)}
    fields["meta"] = {plain(key, 100): plain(value, 1500) for key, value in list(extra.items())[:60]} if isinstance(extra, dict) else {}
    fields.update({key: plain(row.get(key), 1500) for key in ("number", "source", "replied", "added_at")})
    fields["number"] = fields["number"] or plain(row.get("id"), 100)
    if not fields["number"] and (fields["added_at"] or fields["addtime"]) and (fields["mail"] or fields["content"]):
        raw = "|".join((fields["mail"], fields["added_at"] or fields["addtime"], fields["title"],
                        fields["content"], fields["company"]))
        fields["number"] = "HQ-" + hashlib.sha256(raw.encode()).hexdigest()[:20]
    for key in ("设备", "分配员工", "所属业务员"):
        if record.get(key):
            fields["meta"][key] = plain(record[key], 500)
    return fields


class PlainHTML(HTMLParser):
    def __init__(self, exclude_quotes=False):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = []
        self.skipped = {"script", "style", "head"}
        if exclude_quotes:
            self.skipped.add("blockquote")

    def handle_starttag(self, tag, attrs):
        if tag in self.skipped:
            self.hidden.append(tag)
        elif not self.hidden and tag in {"p", "li", "div", "tr", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.hidden and tag == self.hidden[-1]:
            self.hidden.pop()
        elif not self.hidden and tag in {"p", "li", "div", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def current_reply(body):
    lines = []
    for line in body.replace("\r", "").split("\n"):
        if TRAIL.match(line):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def parse_email(raw):
    if not raw or len(raw) > MAX_FILE:
        raise ValueError("请选择 5 MB 以内的邮件文件。")
    mail = BytesParser(policy=policy.default).parsebytes(raw)
    name, address = parseaddr(str(mail.get("From", "")))
    address = buyers.contact_email(address).casefold()
    if not address:
        raise ValueError("邮件缺少有效发件邮箱。")
    body_part = mail.get_body(preferencelist=("plain", "html"))
    try:
        body = body_part.get_content() if body_part else ""
    except (LookupError, UnicodeError):
        body = (body_part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
    body = plain(body)
    reply = body
    if body_part and body_part.get_content_type() == "text/html":
        html = PlainHTML()
        new_text = PlainHTML(exclude_quotes=True)
        html.feed(body)
        new_text.feed(body)
        body = plain("".join(html.parts))
        reply = plain("".join(new_text.parts))
    subject = plain(mail.get("Subject"), 300)
    received = date_value(str(mail.get("Date", "")))
    reference = plain(mail.get("Message-ID"), 350)
    refs = MESSAGE_ID.findall(f"{mail.get('References', '')} {mail.get('In-Reply-To', '')}")[-25:]
    auto = str(mail.get("Auto-Submitted", "no")).lower() != "no" or mail.get_content_type() == "multipart/report" or bool(AUTO.search(subject))
    attachments = [plain(part.get_filename(), 260) for part in mail.iter_attachments() if part.get_filename()][:40]
    digest = hashlib.sha256((reference or json.dumps([address, subject, received, body], ensure_ascii=False)).encode()).hexdigest()
    return {"name": name, "sender": address, "subject": subject, "received_at": received or stamp(),
            "body": body, "reply_text": current_reply(reply), "ref": reference, "refs": refs,
            "attachments": attachments, "fingerprint": digest, "auto": auto}


def intent(text, *, auto=False, event_date="", known_product=False):
    match = lambda regex: regex.search(text)
    matched_product = bool(re.search(r"\bt850\b", text, re.I) or
                           (match(scoring.BRAKE) and (match(scoring.COMMERCIAL) or known_product)))
    confirmed = None if match(UNCONFIRMED) else match(CONFIRMED)
    slot = match(SLOT) or (match(DATE_SLOT) if match(MEET) else None)
    planned = match(PLAN)
    request = match(RFQ)
    detail = match(SPEC)
    wait = match(CHECK)
    if auto:
        return result("archive", "自动回复或退信", "留档核对邮箱", "")
    if (declined := match(NO)) or (declined := match(scoring.NEGATION)):
        return result("defer", "已明确拒绝或要求停止联系", "关闭当前跟进，查看备选买家", declined.group())
    if match(scoring.OTHER_PART) and not matched_product or (match(scoring.UNRELATED) and not matched_product):
        return result("defer", "来信品类与冠英目标不符", "暂缓联系", match(scoring.UNRELATED).group())
    if confirmed or slot:
        found = confirmed or slot
        return result("hot", "客户明确到展或提出会面", "确认双方会面时间与地点", found.group(), "confirmed",
                      meeting_time(text, event_date) if slot else "")
    if request and detail and matched_product:
        return result("hot", "目标制动件询价含型号或数量", "核对规格并安排核价", request.group() + " · " + detail.group())
    if planned:
        return result("priority", "客户表达参展计划", "核实到展日期及会面时间", planned.group(), "planned")
    if request and matched_product:
        return result("priority", "客户询问目标品类的价格、样品或交期", "确认产品规格和采购数量", request.group())
    if wait or request:
        found = wait or request
        return result("review", "收到目录、展位或待核品类咨询", "补问目标品类或参展安排", found.group())
    return result("nurture", "暂无明确采购或到展动作", "观察后续回复", "")


def result(tier, why, action, quote, show="", meeting=""):
    return {"tier": tier, "reason": why, "next_action": action, "quote": quote, "show": show, "meeting_at": meeting}


def inquiry_weights():
    return buyer_settings.weights(WEIGHTS_KEY, WEIGHT_DEFAULTS)


def score_inquiry(text, analysis, has_company, weights=WEIGHT_DEFAULTS):
    def find(pattern):
        return scoring.text_match(pattern, text)

    brake, vehicle, part_code = find(scoring.BRAKE), find(scoring.COMMERCIAL), find(PART_NUMBER)
    order, rfq, consultation = find(scoring.ORDER), find(RFQ), find(CHECK)
    quantity, specification = find(QUANTITY), find(TECHNICAL_SPEC)
    role = find(scoring.B2B) or find(TRADE_ROLE)
    expo = find(EXPO)
    show = analysis.get("show")
    explicit_part = part_code.upper().startswith(("T850", "WVA", "OEM"))
    if not (vehicle or brake or explicit_part):
        part_code = ""
    if not quantity and part_code and find(QUANTITY_HEADER):
        quantity = find(QUANTITY_TOTAL)
    full_match = (vehicle and (brake or part_code)) or part_code.upper() == "T850"
    product_points = 30 if full_match else 12 if brake or part_code else 0
    show_points = 15 if show == "confirmed" else 10 if show == "planned" else 5 if expo and find(MEET) else 0
    specifications = [value for value in (part_code, quantity, specification) if value]
    signals = {
        "product": (product_points, " · ".join(filter(None, (brake, vehicle, part_code))) if product_points else ""),
        "action": (30 if order else 24 if rfq else 12 if consultation else 0, order or rfq or consultation),
        "specifics": (min(20, 10 * len(specifications)), " · ".join(specifications)),
        "show": (show_points, (expo or "来信提到到展或会面") if show_points else ""),
        "company": (5 if role or has_company else 0, role or ("已有企业名称" if has_company else "")),
    }
    parts = []
    for key, (earned, evidence) in signals.items():
        maximum = weights[key]
        baseline = WEIGHT_DEFAULTS[key]
        points = (earned * maximum + baseline // 2) // baseline
        parts.append(scoring.part(WEIGHT_DIMENSIONS[key], maximum, points, evidence))
    cap, note = {
        "archive": (0, "自动回复或退信计 0 分。"),
        "defer": (10, "明确拒绝或品类不匹配，评分最高 10 分。"),
    }.get(analysis.get("tier"), (100, ""))
    breakdown = scoring.summarize(parts, cap, note)
    del breakdown["tier"]
    return breakdown


def meeting_time(text, event_date):
    match = DATE_SLOT.search(text)
    if not match or not event_date:
        return ""
    year, month, day, hour, minute = match.groups()
    try:
        event = date.fromisoformat(event_date)
        hour_value = int(hour)
        if "下午" in match.group() and hour_value < 12:
            hour_value += 12
        proposed = datetime(int(year or event.year), int(month), int(day), hour_value, int(minute), tzinfo=ZONE)
        return proposed.isoformat(timespec="minutes")
    except ValueError:
        return ""


def buyer_match(sender, kind):
    if not sender:
        return None
    scope = "data_kind='synthetic'" if kind == "synthetic" else "data_kind<>'synthetic'"
    hits = get_db().execute(f"SELECT id FROM buyers WHERE {scope} AND lower(contact_email)=? LIMIT 2", (sender,)).fetchall()
    return hits[0]["id"] if len(hits) == 1 else None


def buyer_product(buyer_id):
    if not buyer_id:
        return False
    evidence = get_db().execute("SELECT * FROM evidence WHERE buyer_id=?", (buyer_id,)).fetchall()
    product = buyers.findings(evidence).get("product")
    return bool(product and product["state"] == "supported")


def import_vendor(rows, kind, event_date=""):
    db = get_db()
    counts = {"total": len(rows), "inserted": 0, "duplicate": 0, "errors": []}
    with db:
        for index, record in enumerate(rows, 1):
            if not isinstance(record, dict):
                counts["errors"].append(f"第 {index} 条的格式无效。")
                continue
            fields = vendor_fields(record)
            number = fields["number"]
            if not number:
                counts["errors"].append(f"第 {index} 条缺少询盘单号。")
                continue
            sender = buyers.contact_email(fields["mail"]).casefold()
            linked = buyer_match(sender, kind)
            received = date_value(fields["addtime"] or fields["added_at"])
            analysis = intent(current_reply(fields["content"]), event_date=event_date, known_product=buyer_product(linked))
            item = db.execute("""INSERT INTO lead_inquiries(source,external_id,data_kind,fields,sender,buyer_id,
                                 received_at,analysis,meeting_at,next_action)
                                 VALUES ('hqsmartcloud',?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
                              (number, kind, json.dumps(fields, ensure_ascii=False), sender, linked, received,
                               json.dumps(analysis, ensure_ascii=False), analysis["meeting_at"], analysis["next_action"]))
            counts["inserted"] += int(bool(item.rowcount))
            counts["duplicate"] += int(not item.rowcount)
    return counts


def find_thread(mail, kind, recover_sender=True):
    db = get_db()
    for reference in reversed(mail["refs"]):
        row = db.execute("""SELECT i.* FROM lead_messages m JOIN lead_inquiries i ON i.id=m.inquiry_id
                            WHERE m.data_kind=? AND m.message_ref=? AND i.deleted_at='' LIMIT 2""", (kind, reference)).fetchall()
        if len(row) == 1 and row[0]["sender"] == mail["sender"]:
            return row[0]
    if not recover_sender:
        return None
    results = db.execute("SELECT * FROM lead_inquiries WHERE data_kind=? AND sender=? AND status<>'archived' AND deleted_at='' LIMIT 2",
                         (kind, mail["sender"])).fetchall()
    return results[0] if len(results) == 1 else None


def import_email(mail, kind, event_date="", alert=True):
    db = get_db()
    with db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT inquiry_id FROM lead_messages WHERE data_kind=? AND fingerprint=?",
                              (kind, mail["fingerprint"])).fetchone()
        if existing:
            deleted = db.execute("SELECT deleted_at FROM lead_inquiries WHERE id=?", (existing["inquiry_id"],)).fetchone()
            return {"duplicate": True, "deleted": True} if deleted["deleted_at"] else {"id": existing["inquiry_id"], "duplicate": True}
        row = find_thread(mail, kind, recover_sender=not mail["auto"])
        linked = (row["buyer_id"] if row else None) or buyer_match(mail["sender"], kind)
        analysis = intent(mail["reply_text"], auto=mail["auto"], event_date=event_date, known_product=buyer_product(linked))
        serialized = json.dumps(analysis, ensure_ascii=False)
        if row is None:
            fields = vendor_fields({"name": mail["name"], "mail": mail["sender"], "title": mail["subject"],
                                    "content": mail["body"], "source": "客户回信", "addtime": mail["received_at"],
                                    "files": mail["attachments"]})
            if linked:
                customer = db.execute("SELECT company,country,phone FROM buyers WHERE id=?", (linked,)).fetchone()
                fields.update(company=customer["company"], address=customer["country"], phone=customer["phone"])
            inquiry_id = db.execute("""INSERT INTO lead_inquiries(source,external_id,data_kind,fields,sender,buyer_id,
                                        received_at,analysis,meeting_at,next_action,status)
                                        VALUES ('email',?,?,?,?,?,?,?,?,?,?)""",
                                     (mail["fingerprint"], kind, json.dumps(fields, ensure_ascii=False), mail["sender"], linked,
                                      mail["received_at"], serialized, analysis["meeting_at"], analysis["next_action"],
                                      "archived" if mail["auto"] else "open")).lastrowid
        else:
            inquiry_id = row["id"]
            if not mail["auto"] and mail["received_at"] >= row["received_at"]:
                db.execute("""UPDATE lead_inquiries SET received_at=?, analysis=?, buyer_id=?,
                              meeting_at=COALESCE(NULLIF(?,''),meeting_at),
                              next_action=CASE WHEN status='open' THEN ? ELSE next_action END,
                              status=CASE WHEN status IN ('done','declined','archived') THEN 'open' ELSE status END
                              WHERE id=?""", (mail["received_at"], serialized, linked, analysis["meeting_at"],
                                             analysis["next_action"], inquiry_id))
        db.execute("""INSERT INTO lead_messages(inquiry_id,fingerprint,data_kind,message_ref,sender,subject,received_at,
                       body,reply_text,attachments,analysis,live) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (inquiry_id, mail["fingerprint"], kind, mail["ref"], mail["sender"], mail["subject"], mail["received_at"],
                    mail["body"], mail["reply_text"], json.dumps(mail["attachments"], ensure_ascii=False), serialized,
                    int(alert and kind == "authorized" and not mail["auto"])))
        promoted = row is None or payload(row["analysis"], {}).get("tier") != "hot"
        if alert and kind == "authorized" and promoted and analysis["tier"] == "hot" and (row is None or mail["received_at"] >= row["received_at"]):
            queue_hot(inquiry_id, analysis)
    return {"id": inquiry_id, "duplicate": False}


def safe_company(fields, inquiry_id):
    company = plain(fields.get("company"), 70)
    if not company or scoring.SENSITIVE.search(company):
        return f"询盘 #{inquiry_id}"
    return " ".join(company.split())


def queue_hot(inquiry_id, analysis):
    row = get_db().execute("SELECT fields FROM lead_inquiries WHERE id=?", (inquiry_id,)).fetchone()
    company = safe_company(payload(row["fields"], {}), inquiry_id)
    body = f"【火热 · 立即联系】{company}\n{analysis['reason']}\n下一步：{analysis['next_action']}"
    queue_notice(f"hot:{inquiry_id}", inquiry_id, body)


def queue_notice(key, inquiry_id, body):
    now = stamp()
    return get_db().execute("""INSERT INTO lead_notifications(dedupe_key,inquiry_id,body,created_at,updated_at)
                               SELECT ?,?,?,?,? WHERE ? IS NULL OR EXISTS (
                                   SELECT 1 FROM lead_inquiries WHERE id=? AND deleted_at=''
                               ) ON CONFLICT DO NOTHING""", (key, inquiry_id, body, now, now, inquiry_id, inquiry_id)).rowcount


SCORE_SIGNALS = """
    (SELECT m.reply_text FROM lead_messages m
     WHERE m.inquiry_id=i.id AND m.received_at=i.received_at AND json_extract(m.analysis,'$.tier')<>'archive'
     ORDER BY m.id DESC LIMIT 1) AS score_reply,
    (SELECT count(*) FROM lead_candidates c
     WHERE c.inquiry_id=i.id AND trim(json_extract(c.candidate,'$.company'))<>''
     AND (json_extract(c.candidate,'$.source') LIKE 'https://_%'
          OR json_extract(c.candidate,'$.source') LIKE 'http://_%')) AS candidate_count
"""


def score_context(row, reply):
    fields = payload(row["fields"], {})
    identity = payload(row["identity"], {})
    return agent_scoring.inquiry_context(
        reply if reply is not None else current_reply(fields.get("content", "")), fields, identity)


def lead_score_context(row):
    reply = get_db().execute(f"SELECT {SCORE_SIGNALS} FROM lead_inquiries i WHERE i.id=?", (row["id"],)).fetchone()
    return score_context(row, reply["score_reply"])


def lead_view(row, scores, weights=None, blend=None):
    info = {**dict(row), "fields": payload(row["fields"], {}), "analysis": payload(row["analysis"], {}),
            "dossier": payload(row["dossier"], None), "identity": payload(row["identity"], None),
            "public_research": payload(row["public_research"], None)}
    info["buyer_score"] = scores.get(row["buyer_id"])
    if "candidate_count" not in info:
        info.update(dict(get_db().execute(f"SELECT {SCORE_SIGNALS} FROM lead_inquiries i WHERE i.id=?",
                                          (row["id"],)).fetchone()))
    reply = info.pop("score_reply")
    text = reply if reply is not None else current_reply(info["fields"]["content"])
    rules = score_inquiry(text, info["analysis"], bool(info["identity"] or info["fields"].get("company")),
                          weights if weights is not None else inquiry_weights())
    info["score"] = agent_scoring.combine(rules, "inquiry", row["id"], score_context(row, reply), blend)
    if info["public_research"] and info["public_research"].get("source_hash"):
        info["public_research"]["stale"] = info["public_research"]["source_hash"] != hashlib.sha256(
            info["fields"]["content"].encode()).hexdigest()
    return info


def buyer_scores():
    db = get_db()
    weights = buyer_settings.weights()
    blend = agent_scoring.weights("buyer")
    scores = {}
    for buyer in db.execute("SELECT * FROM buyers WHERE id IN (SELECT buyer_id FROM lead_inquiries WHERE buyer_id IS NOT NULL)"):
        evidence = db.execute("SELECT * FROM evidence WHERE buyer_id=?", (buyer["id"],)).fetchall()
        _, _, score = buyers.evaluate(buyer, evidence, weights, blend)
        scores[buyer["id"]] = score["total"]
    return scores


def listing(deleted=False, sort=None):
    scores = buyer_scores()
    weights = inquiry_weights()
    blend = agent_scoring.weights("inquiry")
    rows = [lead_view(row, scores, weights, blend) for row in get_db().execute(f"""
        SELECT i.*, {SCORE_SIGNALS} FROM lead_inquiries i WHERE (deleted_at<>'')=?
        AND (data_kind='authorized' OR ?) ORDER BY received_at DESC,id DESC
    """, (deleted, current_app.config["LEADS_ALLOW_SYNTHETIC"]))]
    if sort in {"score_desc", "score_asc"}:
        def numeric(row):
            total = row["score"]["total"]
            return total is None, (-1 if sort == "score_desc" else 1) * (total or 0)

        return sorted(rows, key=numeric)
    order = list(PRIORITIES)
    if sort is not None:
        return sorted(rows, key=lambda row: (
            order.index(row["analysis"]["tier"]),
            row["analysis"]["tier"] == "review" and not row["candidate_count"],
            row["score"]["total"] is None,
            -(row["score"]["total"] or 0),
            -(row["buyer_score"] or 0),
        ))
    return sorted(rows, key=lambda row: (order.index(row["analysis"]["tier"]),
                                          row["meeting_at"] or "9999", -(row["buyer_score"] or 0)))


def event_shortlist(rows):
    active = [row for row in rows if row["status"] in OPEN and row["analysis"]["tier"] in buyers.FOCUS_TIERS]
    return {"meetings": [row for row in active if row["meeting_at"]],
            "show": [row for row in active if not row["meeting_at"] and row["analysis"]["show"]],
            "follow": [row for row in active if not row["meeting_at"] and not row["analysis"]["show"]]}
