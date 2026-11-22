"""Independent review queue for existing comments and inquiries."""

import csv
import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode

from flask import Blueprint, Response, abort, current_app, request, send_from_directory

import facebook
import inquiries
import lead_inquiries
from features.fitment.match import match as match_fitment
from lead_inquiries import payload
from lead_routes import brief as inquiry_brief
from scoring import comment_score
from social import DEMO_ACCOUNT, DEMO_CHANNEL, DEMO_POST, comment_key
from storage import get_db
from tables import csv_bytes
from workspace import current_user, error

from .extraction import build_card
from .policy import PRICE_REQUEST, plan

bp = Blueprint("comment_desk", __name__)
from . import x_live
bp.register_blueprint(x_live.bp)
SOURCES = {"social": "社媒营销", "inquiry": "询盘转化", "demo": "演示样本"}
STATES = {"pending": "待处理", "review": "待复核", "waiting": "待客户补充",
          "pricing": "已交核价", "done": "已跟进", "archive": "留档"}
MODES = {"candidate": "目录候选", "acknowledge": "可先回复", "manual": "人工核对", "skip": "无需回复"}
BEIJING = timezone(timedelta(hours=8))
DEMO_EVENT = {"name": "上海法兰克福汽配展", "date": "12 月 2 日至 5 日",
              "venue": "上海国家会展中心（虹桥）", "booth": "1.1N59"}
SHANGHAI_EVENT = re.compile(r"上海|虹桥|\bshanghai\b|(?:12\s*月|december|\bdec\b).{0,20}(?:汽配展|show|exhibition|automechanika)|汽配展.{0,12}12\s*月", re.I)
OTHER_EVENT = re.compile(r"广交会|canton fair|法兰克福德国|法兰克福展.{0,6}德国|dubai|迪拜|las vegas|拉斯维加斯", re.I)


@lru_cache(maxsize=1)
def aliases():
    return json.loads(Path(__file__).with_name("vehicle_aliases.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def demo_rows():
    fixture = Path(__file__).resolve().parent.parent / "data" / "social-demo-comments.csv"
    with fixture.open(encoding="utf-8", newline="") as source:
        return {
            **{f"demo:{row['id']}": (row["author"], row["body"]) for row in csv.DictReader(source)},
            "demo:catalog": ("选型样本", "请报价：200 套 T850 鼓式刹车片，旧件是前刹 10 孔，宽度 140mm。"),
            "demo:ambiguous": ("数量样本", "Please quote T850 front brake lining. T850x2.5 sets."),
            "demo:expo": ("展位咨询样本", "你们参加 12 月的汽配展吗？能发一下展位号吗？"),
            "demo:vehicle": ("车型询价样本", "天锦KR，多少钱？我不知道旧件编号。"),
            "demo:price": ("首次问价样本", "这款多少钱？"),
            "demo:unclear": ("语义不清样本", "这个呢？"),
            "demo:batch": ("多行询价样本", "请报价，PART NUM / SIZE / QTY\n19032 STD 150\n19032 1+ 100\n19488 2+ 100"),
        }


def analyze(body):
    analysis = inquiries.rule_analysis(body)
    fields = analysis["fields"]
    card = build_card(body, aliases())
    fields.pop("quantity", None)
    fields.pop("unit", None)
    if card["qty"] is not None and not card["need_confirm"]:
        quantity = card["qty_candidates"][0]
        fields.update(quantity=str(card["qty"]), unit=quantity["unit"])
    analysis["extraction"] = {
        "vehicle": card["vehicle"], "vehicle_note": card["vehicle_note"],
        "quantity": fields.get("quantity", ""), "unit": fields.get("unit", ""),
        "quantity_note": card["need_confirm_reason"],
    }
    analysis["score"] = comment_score(body, fields, analysis["quote"] if analysis["intent"] == "询价" else "",
                                      analysis["quote"] if analysis["intent"] in {"展会咨询", "展会约见"} else "")
    return analysis


def from_comment(row):
    lead = get_db().execute("""SELECT id FROM lead_inquiries WHERE source='social'
                              AND external_id=? AND data_kind=? AND deleted_at=''""",
                            (comment_key(row), row["data_kind"])).fetchone()
    return {
        "key": f"social:{row['id']}", "title": row["company"] or "评论者",
        "body": row["body"], "received_at": row["received_at"], "channel": row["channel"],
        "data_kind": row["data_kind"], "source_url": row["source_ref"],
        "existing_id": row["id"] if row["channel"] == "facebook" else None,
        "buyer_id": row["buyer_id"], "record_id": row["id"], "external_id": row["external_id"],
        "source_score": inquiries.read_analysis(row)["score"], "score_label": "社媒营销评分",
        "linked_inquiry": lead["id"] if lead else None,
        "owner": row["owner"], "source_kind": row["transport"],
    }


def from_inquiry(row):
    fields = payload(row["fields"], {})
    if not isinstance(fields, dict):
        fields = {}
    return {
        "key": f"inquiry:{row['id']}", "title": fields.get("company") or fields.get("title") or "已有询盘",
        "body": str(fields.get("content") or ""), "received_at": row["received_at"],
        "channel": "inquiry", "data_kind": row["data_kind"], "source_url": str(fields.get("page") or ""),
        "existing_id": row["id"], "buyer_id": row["buyer_id"],
        "owner": row["owner"], "source_kind": row["source"], "has_files": bool(fields.get("files")),
    }


def inquiry_from_listing(row):
    item = from_inquiry(row)
    original = inquiry_brief(row)
    item.update(title=original["company"], source_score=row["score"], score_state=original["score_state"],
                score_label="询盘转化评分", original_status=original["status"])
    return item


def review_table(db):
    db.execute("""CREATE TABLE IF NOT EXISTS comment_desk_reviews (
        item_key TEXT PRIMARY KEY, status TEXT NOT NULL, reason TEXT NOT NULL,
        reviewed_by TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS comment_desk_replies (
        item_key TEXT PRIMARY KEY, state TEXT NOT NULL, draft TEXT NOT NULL,
        actor TEXT NOT NULL, updated_at TEXT NOT NULL, receipt TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT ''
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS comment_desk_followups (
        item_key TEXT PRIMARY KEY, owner TEXT NOT NULL, due TEXT NOT NULL,
        supplement TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS comment_desk_history (
        id INTEGER PRIMARY KEY, item_key TEXT NOT NULL, status TEXT NOT NULL,
        reason TEXT NOT NULL, actor TEXT NOT NULL, updated_at TEXT NOT NULL,
        owner TEXT NOT NULL, due TEXT NOT NULL, supplement TEXT NOT NULL
    )""")


def demo_item(key, title, body):
    return {"key": key, "title": title, "body": body, "received_at": "", "channel": "social_demo",
            "data_kind": "synthetic", "source_url": "", "existing_id": None, "owner": "", "source_kind": "demo"}


def event_for(item):
    body = item["body"]
    if OTHER_EVENT.search(body):
        return None
    if item["data_kind"] == "synthetic":
        return DEMO_EVENT if SHANGHAI_EVENT.search(body) else None
    event = {key: current_app.config["EVENT_" + key.upper()] for key in DEMO_EVENT}
    name = event["name"].casefold()
    if name and (name in body.casefold() or "上海" in name and SHANGHAI_EVENT.search(body)):
        return event
    return None


@lru_cache(maxsize=1)
def pilot_catalog():
    path = Path(__file__).resolve().parent.parent / "试点_适配表_153条.csv"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def quote_options(body, analysis):
    from catalog import find_candidates
    fields = analysis["fields"]
    options = []
    vehicle = analysis["extraction"]["vehicle"]
    if re.search(r"T850", body, re.I):
        # Never pick one of two conflicting positions in the same request.
        positions = {"front" if term in {"front", "前刹", "前桥"} else "rear"
                     for term in re.findall(r"\bfront\b|\brear\b|前刹|后刹|前桥|后桥", body.lower())}
        if (len(positions) > 1 or len(set(re.findall(r"(\d+(?:\.\d+)?)\s*(?:mm|毫米)", body, re.I))) > 1
                or len(set(re.findall(r"(\d+)\s*(?:holes?\b|孔)", body, re.I))) > 1):
            return []
        matches, _ = find_candidates({**fields, "code": "新T850" if re.search(r"(?:新|new)\s*T850", body, re.I) else "T850",
                                      "position": next(iter(positions), fields.get("position"))})
        for row in matches:
            options.append({"sku": row["catalog_id"], "name": row["source_name"],
                            "spec": f'{row["width_mm"]} mm 宽 · {row["holes"]} 孔',
                            "ref": f'{row["source_file"]} PDF 第 {row["pdf_page"]} 页'})
    elif vehicle:
        for row in pilot_catalog():
            name = row["vehicle"].replace("⻛", "风")
            if vehicle.casefold() not in name.casefold():
                continue
            if fields.get("position") == "front" and "后" in name or fields.get("position") == "rear" and "前" in name:
                continue
            filename = "kooying catalog.pdf" if row["file"] == "kooying" else "冠隆产品册.pdf"
            options.append({"sku": row["oe"], "name": name, "spec": row["size"],
                            "ref": f'{filename} 第 {row["page"]} 页，第 {row["col"]} 列'})
    return options


RFQ_LINE = re.compile(r"^\s*(?:\d{1,3}[\s,;|]+)?([A-Z]{0,4}\d{4,16})[\s,;|]+(STD|\d+\+)[\s,;|]+(\d{1,9})\s*(套|件|sets?|pcs)?\s*$", re.I)


def order_lines(body):
    rows = []
    for line in body.splitlines():
        match = RFQ_LINE.fullmatch(line)
        if match:
            part, size, quantity, unit = match.groups()
            rows.append({"part": part, "size": size.upper(), "quantity": quantity, "unit": unit or ""})
    return rows


def unparsed_order_rows(body):
    return sum(1 for line in body.splitlines()
               if re.match(r"^\s*(?:\d{1,3}[\s,;|]+)?[A-Z]{0,4}\d{4,16}\b", line, re.I)
               and not RFQ_LINE.fullmatch(line))


def fitment_href(item, body, analysis, line=None):
    fields = analysis["fields"]
    extraction = analysis["extraction"]
    positions = {"front" if term.lower() in {"front", "前刹", "前桥"} else "rear"
                 for term in re.findall(r"\bfront\b|\brear\b|前刹|后刹|前桥|后桥", body, re.I)}
    if not positions and fields.get("position"):
        positions.add(fields["position"])
    explicit_code = re.search(r"\b(?:WVA|FMSI|OE)\s*[:#-]?\s*(\d{4,16})", body, re.I)
    code = explicit_code[1] if explicit_code else fields.get("code", "")
    vehicle = extraction["vehicle"]
    if code and "t850" in code.lower():
        vehicle = vehicle or code
        code = ""
    holes = set(re.findall(r"(\d+)\s*(?:holes?\b|孔)", body, re.I))
    widths = set(re.findall(r"(\d+(?:\.\d+)?)\s*(?:mm|毫米)", body, re.I))
    if line:
        params = {"code": line["part"]}
    else:
        params = {"q": vehicle, "code": code,
                  "position": {"front": "前", "rear": "后"}.get(next(iter(positions)), "") if len(positions) == 1 else "",
                  "width": fields.get("width_mm") if len(widths) <= 1 else "",
                  "holes": fields.get("holes") if len(holes) == 1 else ""}
    qty = line or extraction
    quantity = qty["quantity"]
    if qty["unit"].casefold() in {"套", "set", "sets"} and re.fullmatch(r"[1-9][0-9]{0,8}", quantity):
        params["qty"] = quantity
    if item["key"].startswith("social:"):
        params["from"] = "inquiry:" + str(item["record_id"])
    query = urlencode({key: value for key, value in params.items() if value})
    return "/fitment?" + query if query else "/fitment"


def fitment_choices(url):
    from urllib.parse import parse_qsl, urlsplit

    params = dict(parse_qsl(urlsplit(url).query))
    if not params.get("qty") or not (params.get("q") or params.get("code")):
        return []
    result = match_fitment(q=params.get("q", ""), code=params.get("code"),
                           position=params.get("position"),
                           width=float(params["width"]) if params.get("width") else None,
                           holes=int(params["holes"]) if params.get("holes") else None)
    return [{"name": card.get("vehicle_raw") or card["id"], "spec": card.get("size_raw") or "",
             "url": "/fitment?" + urlencode(params | {"pick": card["id"]}) + "#quote-workspace"}
            for card in result.get("candidates", [])]


def enrich(item, followup=None):
    analysis = analyze(item["body"])
    lines = order_lines(item["body"])
    if lines:
        analysis["intent"] = "询价"
    event = event_for(item)
    decision = plan(item["body"], analysis, item["channel"], item["data_kind"], event)
    intent = analysis["intent"]
    if decision["reason"].startswith("客户咨询展会") and not intent.startswith("展会"):
        intent = "展会咨询"
    if event and item["data_kind"] == "synthetic":
        decision["event_source"] = "团队群提供的参展资料 · 演示"
    elif event:
        decision["event_source"] = "工作台配置的展会资料"
    extraction = decision["extraction"]
    supplement = (followup or {}).get("supplement", "")
    quote_body = item["body"] + ("\n" + supplement if supplement else "")
    quote_analysis = analyze(quote_body) if supplement else analysis
    buying = (analysis["intent"] == "询价" or extraction["vehicle"] or analysis["fields"].get("code")
              or PRICE_REQUEST.search(item["body"]))
    supplement_buying = supplement and (quote_analysis["intent"] == "询价" or quote_analysis["extraction"]["vehicle"]
                                        or quote_analysis["fields"].get("code"))
    if (buying or lines or supplement_buying) and decision["mode"] != "skip" and not decision["reason"].startswith("售后"):
        options = quote_options(quote_body, quote_analysis)
        quote_fields = quote_analysis["extraction"]
        zh = analysis["language"] == "中文"
        if item.get("has_files"):
            question = "我们会先核对您已附的资料。" if zh else "We will review the attachment you already provided."
        elif quote_fields["quantity_note"] and not lines:
            question = "请确认实际采购数量及单位。" if zh else "Could you confirm the order quantity and unit?"
        elif not options:
            question = ("方便发一张旧件或包装标签的照片吗？我们可以先帮您查。"
                        if zh else "Could you send a photo of the old part or its label? We can start the lookup for you.")
        elif len(options) > 1 and any("前" in o["name"] for o in options) and any("后" in o["name"] for o in options) and not analysis["fields"].get("position"):
            question = "您这次要换的是前桥还是后桥的刹车片？" if zh else "Is the lining for the front or rear axle?"
        else:
            question = "方便发一张旧件照片，让我们核对规格吗？" if zh else "Could you share a photo of the old part so we can check the specification?"
        decision["quote"] = {"options": options, "quantity": quote_fields["quantity"], "unit": quote_fields["unit"],
                             "quantity_note": quote_fields["quantity_note"],
                             "lines": lines, "unparsed_rows": unparsed_order_rows(item["body"]) if lines else 0,
                             "question": question, "missing": ["核实旧件或实际车桥", "内部核算重量、配方和附件成本", "确认正式单价和币种"]}
        decision["quote"]["fitment_url"] = fitment_href(item, quote_body, quote_analysis) if not lines else ""
        decision["quote"]["fitment_choices"] = fitment_choices(decision["quote"]["fitment_url"])
        decision["quote"]["fitment_note"] = (
            "速配目录的 T850 入口目前收录新 T850。请对照原规格和目录页码，再选择候选。"
            if not lines and re.search(r"\bT850\b", quote_body, re.I) else ""
        )
        for line in lines:
            line["fitment_url"] = fitment_href(item, quote_body, quote_analysis, line)
        if not decision["candidate"] and decision["reason"] == "由销售确认规格或采购需求":
            intent = "询价" if PRICE_REQUEST.search(item["body"]) else intent
            decision["action"] = "先进入报价工作台登记需求，再根据已有线索缩小候选范围。"
            if not extraction["quantity_note"] and not re.search(r"@|https?://|投诉|退款", item["body"]) and (decision["draft"] or decision["reason"] == "采购意图或语言待确认"):
                decision["draft"] = ("可以，我先为您准备报价。" if zh else "We can start preparing your quote. ") + question
        if lines:
            decision["quote"].update(options=[], quantity="", unit="", question=(
                "我们会按原单逐行核对型号、规格、数量及单位。" if zh else
                "We will check each part number, size, quantity and unit against your original request."))
            if decision["mode"] == "candidate" or decision["reason"] in {"由销售确认规格或采购需求", "采购数量有歧义，先确认原文", "采购意图或语言待确认"}:
                decision.update(mode="acknowledge", candidate=None, reason="已识别分行采购明细",
                                action="对照原文核对每行及未识别内容，再交销售核价。",
                                draft=decision["quote"]["question"])
            intent = "询价"
        elif decision["reason"].startswith("客户咨询展会") and PRICE_REQUEST.search(item["body"]):
            decision["draft"] += (" 您提到的价格我们也会核对后回复。" if zh else " We will also check the pricing you requested.")
    target = "原询盘或邮件线程" if item["channel"] == "inquiry" else "原帖评论区"
    if item["data_kind"] == "synthetic":
        target = "演示记录"
    item.update(intent=intent, score=item.get("source_score", analysis["score"]), triage=decision,
                language=analysis["language"], reply_target=target)
    return item


def can_send(row, decision):
    cfg = current_app.config
    page, post = facebook.page_settings()
    return bool(os.getenv("COMMENT_DESK_FACEBOOK_SEND") == "1" and not cfg["FACEBOOK_AUTO_REPLY"]
                and page and cfg["FACEBOOK_PAGE_TOKEN"] and row["channel"] == "facebook"
                and row["data_kind"] == "authorized" and row["transport"] in {"graph", "webhook"}
                and row["account"] == page and row["post_id"] == post and decision["draft"]
                and decision["mode"] in {"candidate", "acknowledge"}
                and not decision["reason"].startswith("售后"))


def raw_item(key):
    source, _, item_id = key.partition(":")
    if source == "demo":
        demo = demo_rows().get(key)
        return (demo_item(key, *demo), None) if demo else (None, None)
    if source not in {"social", "inquiry"} or not re.fullmatch(r"[1-9][0-9]{0,17}", item_id):
        return None, None
    db = get_db()
    if source == "social":
        row = db.execute("SELECT * FROM interactions WHERE id=?", (item_id,)).fetchone()
        return (from_comment(row), row) if row else (None, None)
    row = db.execute("""SELECT * FROM lead_inquiries WHERE id=? AND deleted_at=''
                        AND (data_kind='authorized' OR ?)""",
                     (item_id, current_app.config["LEADS_ALLOW_SYNTHETIC"])).fetchone()
    return (from_inquiry(row), None) if row else (None, None)


def feed(source):
    db = get_db()
    if source == "social":
        raw = [from_comment(row) for row in db.execute("""
            WITH facebook_history AS (
                SELECT * FROM interactions WHERE channel='facebook'
                ORDER BY received_at DESC,id DESC LIMIT 300
            )
            SELECT * FROM facebook_history
            UNION ALL
            SELECT * FROM interactions WHERE channel LIKE 'figeon:%'
                OR (channel=? AND account=? AND post_id=?)
            ORDER BY received_at DESC,id DESC
            """, (DEMO_CHANNEL, DEMO_ACCOUNT, DEMO_POST))]
    elif source == "inquiry":
        raw = [inquiry_from_listing(row) for row in lead_inquiries.listing()]
    else:
        raw = [demo_item(key, title, body) for key, (title, body) in demo_rows().items()]
    review_table(db)
    reviews = {}
    replies = {}
    followups = {}
    for offset in range(0, len(raw), 400):
        keys = [item["key"] for item in raw[offset:offset+400]]
        placeholders = ",".join("?" for _ in keys)
        reviews.update((row["item_key"], dict(row)) for row in db.execute(
            f"SELECT * FROM comment_desk_reviews WHERE item_key IN ({placeholders})", keys
        ))
        replies.update((row["item_key"], dict(row)) for row in db.execute(
            f"SELECT item_key,state,updated_at,receipt,note FROM comment_desk_replies WHERE item_key IN ({placeholders})", keys
        ))
        followups.update((row["item_key"], dict(row)) for row in db.execute(
            f"SELECT * FROM comment_desk_followups WHERE item_key IN ({placeholders})", keys
        ))
    today = datetime.now(BEIJING).date().isoformat()
    for item in raw:
        key = item["key"]
        followup = followups.get(key)
        enrich(item, followup)
        item.update(review=reviews.get(key), reply=replies.get(key), followup=followup, send_token="")
        if followup and followup["owner"]:
            item["owner"] = followup["owner"]
        item["overdue"] = bool(followup and followup["due"] and followup["due"] < today and not closed_review(item["review"]))
        if (item["channel"] == "facebook" and item["data_kind"] == "authorized" and not closed_review(item["review"])
                and not (followup and followup["supplement"])):
            row = db.execute("SELECT * FROM interactions WHERE id=?", (item["existing_id"],)).fetchone()
            legacy = db.execute("SELECT stage FROM facebook_replies WHERE interaction_id=?", (row["id"],)).fetchone()
            if not legacy and not item["reply"] and can_send(row, item["triage"]):
                item["send_token"] = reply_token(key, item["triage"]["draft"])
    return sorted(raw, key=lambda row: (not closed_review(row["review"]),
                  row["triage"]["reason"].startswith("售后"), row["overdue"],
                  row["score"]["total"] if row["score"]["total"] is not None else -1, row["received_at"]), reverse=True)


@bp.get("/comment-desk")
def workspace_page():
    dist = Path(current_app.config["VUE_DIST"])
    if not (dist / "index.html").is_file():
        return "请先在 frontend 构建工作台。", 503
    return send_from_directory(dist, "index.html")


@bp.get("/api/comment-desk")
def list_items():
    source = request.args.get("source", "social")
    if source not in SOURCES:
        return error("请选择有效的数据来源。")
    return {"items": feed(source)}


@bp.get("/api/comment-desk/quote.csv")
def quote_sheet():
    item, _ = raw_item(request.args.get("key", ""))
    if not item:
        abort(404)
    db = get_db()
    review_table(db)
    saved = db.execute("SELECT * FROM comment_desk_followups WHERE item_key=?", (item["key"],)).fetchone()
    followup = dict(saved) if saved else {}
    triage = enrich(item, followup)["triage"]
    quote = triage.get("quote")
    if not quote:
        return error("这条记录未识别到询价需求，请核对原文。", 409)
    identity = (item["key"], item["title"], item["channel"], followup.get("owner") or item["owner"],
                item["body"], followup.get("supplement", ""), followup.get("due", ""), item["source_url"])
    base = ("线索编号", "客户", "渠道", "负责人", "询价原文", "补充查件资料", "跟进日期", "原渠道链接")
    if quote["lines"]:
        warning = (f"另有 {quote['unparsed_rows']} 行疑似明细未结构化，请核对原文。" if quote["unparsed_rows"] else "")
        content = csv_bytes(base + ("客户零件号", "规格", "需求数量", "原文单位", "核对提醒", "单价", "币种"),
                            [identity + (r["part"], r["size"], r["quantity"], r["unit"], warning, "", "") for r in quote["lines"]])
        return quote_download(content, item["key"])
    options = quote["options"] or [{"sku": "", "name": "", "spec": "", "ref": ""}]
    keyword = request.args.get("filter", "").strip().casefold()
    if keyword:
        options = [o for o in options if keyword in " ".join(str(o[k] or "") for k in ("name","spec","sku","ref")).casefold()]
        if not options:
            return error("筛选后没有候选，请调整关键词。", 400)
    content = csv_bytes(base + ("候选记录", "目录名称", "规格", "出处", "整单需求量（仅首行）", "单位", "下一问", "内部核价事项", "单价", "币种"),
        [identity + (opt["sku"], opt["name"], opt["spec"], opt["ref"], quote["quantity"] if i == 0 else "", quote["unit"] if i == 0 else "",
          quote["question"], "；".join(quote["missing"]), "", "") for i,opt in enumerate(options)])
    return quote_download(content, item["key"])


def quote_download(content, key):
    filename = "quote-" + key.replace(":", "-") + ".csv"
    return Response(content, mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def closed_review(review):
    return review and review["status"] in {"archive", "done"}


def reply_token(key, draft):
    return hashlib.sha256(f"{key}\n{draft}".encode()).hexdigest()


@bp.post("/api/comment-desk/reply")
def send_reply():
    key = request.form.get("key", "")
    item, row = raw_item(key)
    if not item or row is None:
        return error("该来源需要在原渠道回复。", 409)
    decision = enrich(item)["triage"]
    draft = decision["draft"]
    if not can_send(row, decision) or request.form.get("token") != reply_token(key, draft):
        return error("草稿或回复授权已变化，请刷新后核对。", 409)
    db = get_db()
    review_table(db)
    if closed_review(db.execute("SELECT status FROM comment_desk_reviews WHERE item_key=?", (key,)).fetchone()):
        return error("这条记录已跟进或留档，请重新打开后再回复。", 409)
    if db.execute("SELECT 1 FROM comment_desk_followups WHERE item_key=? AND supplement<>''", (key,)).fetchone():
        return error("这条线索已补充沟通资料，请在原渠道按最新上下文回复。", 409)
    if db.execute("SELECT 1 FROM comment_desk_replies WHERE item_key=?", (key,)).fetchone():
        return error("已有发送记录，请打开原帖核对，避免重复回复。", 409)
    if db.execute("SELECT 1 FROM facebook_replies WHERE interaction_id=?", (row["id"],)).fetchone():
        return error("原工作台已有回复任务，请先到原记录核对。", 409)
    # Verify permission and comment identity before claiming an external write.
    try:
        facebook.verify_reply_target(row["external_id"], row["body"], row["account"])
    except facebook.FacebookUnavailable as exc:
        return error(str(exc), 409)
    with db:
        inserted = db.execute("""INSERT INTO comment_desk_replies(item_key,state,draft,actor,updated_at)
          SELECT ?,'sending',?,?,? WHERE NOT EXISTS
          (SELECT 1 FROM comment_desk_reviews WHERE item_key=? AND status IN ('archive','done'))
          AND NOT EXISTS (SELECT 1 FROM facebook_replies WHERE interaction_id=?)
          AND NOT EXISTS (SELECT 1 FROM comment_desk_followups WHERE item_key=? AND supplement<>'')
          ON CONFLICT(item_key) DO NOTHING""",
                              (key, draft, current_user()["username"], inquiries.now(), key, row["id"], key))
    if not inserted.rowcount:
        return error("已有发送记录，请打开原帖核对，避免重复回复。", 409)
    receipt, note = "", ""
    try:
        receipt = facebook.publish_reply(row["external_id"], draft)
        state = "sent"
    except facebook.FacebookReplyUncertain as exc:
        state, note = "uncertain", str(exc)
    except facebook.FacebookUnavailable as exc:
        state, note = "failed", str(exc)
    with db:
        db.execute("UPDATE comment_desk_replies SET state=?,receipt=?,note=?,updated_at=? WHERE item_key=?",
                   (state, receipt, note, inquiries.now(), key))
    return {"state": state, "receipt": receipt, "note": note}


@bp.post("/api/comment-desk/review")
def save_review():
    key = request.form.get("key", "").strip()
    status = request.form.get("status", "")
    reason = request.form.get("reason", "").strip()
    if status not in STATES or not reason or len(reason) > 600:
        return error("请选择状态并填写 1–600 字的处理说明。")
    item, _ = raw_item(key)
    if not item:
        abort(404)
    db = get_db()
    review_table(db)
    previous = db.execute("SELECT * FROM comment_desk_followups WHERE item_key=?", (key,)).fetchone()
    followup = {name: request.form.get(name, previous[name] if previous else "").strip()
                for name in ("owner", "due", "supplement")}
    owner, due, supplement = (followup[name] for name in ("owner", "due", "supplement"))
    if len(owner) > 120 or len(supplement) > 4000:
        return error("负责人最多 120 字，补充查件资料最多 4000 字。")
    if due:
        try:
            if date.fromisoformat(due).isoformat() != due:
                raise ValueError
        except ValueError:
            return error("请填写有效的跟进日期。")
    if status in {"waiting", "pricing"} and (not (owner or item["owner"]) or not due):
        return error("待客户补充或交核价需要明确负责人和跟进日期。")
    if status == "pricing" and not enrich(item, followup)["triage"].get("quote"):
        return error("请先确认采购需求，再交核价。")
    logged_reply = request.form.get("logged_reply") == "yes"
    if logged_reply and item["data_kind"] == "synthetic":
        return error("演示样本只能保存处理记录。")
    values = (key, status, reason, current_user()["username"], inquiries.now())
    with db:
        db.execute("""INSERT INTO comment_desk_reviews VALUES (?,?,?,?,?)
                      ON CONFLICT(item_key) DO UPDATE SET status=excluded.status,reason=excluded.reason,
                      reviewed_by=excluded.reviewed_by,updated_at=excluded.updated_at""", values)
        db.execute("""INSERT INTO comment_desk_followups VALUES (?,?,?,?)
                      ON CONFLICT(item_key) DO UPDATE SET owner=excluded.owner,due=excluded.due,
                      supplement=excluded.supplement""", (key, owner, due, supplement))
        db.execute("""INSERT INTO comment_desk_history(item_key,status,reason,actor,updated_at,owner,due,supplement)
                      VALUES (?,?,?,?,?,?,?,?)""", (*values, owner or item["owner"], due, supplement))
        if logged_reply:
            db.execute("""INSERT INTO comment_desk_replies(item_key,state,draft,actor,updated_at,note)
                          VALUES (?,'sent','',?,?,'销售已记录在原渠道回复')
                          ON CONFLICT(item_key) DO NOTHING""", (key, values[3], values[4]))
    return {"ok": True}


@bp.get("/api/comment-desk/history")
def history():
    key = request.args.get("key", "")
    item, _ = raw_item(key)
    if not item:
        abort(404)
    db = get_db()
    review_table(db)
    return {"items": [dict(row) for row in db.execute(
        """SELECT status,reason,actor,updated_at,owner,due,supplement
             FROM comment_desk_history WHERE item_key=? ORDER BY id DESC LIMIT 30""", (key,))]}


@bp.get("/api/comment-desk/export")
def export():
    source = request.args.get("source", "social")
    mode = request.args.get("mode", "")
    state = request.args.get("state", "")
    if source not in SOURCES or (mode and mode not in MODES) or (state and state not in STATES):
        return error("筛选条件无效。")
    rows = []
    for item in feed(source):
        triage = item["triage"]
        review = item["review"] or {}
        status = review.get("status", "pending")
        if (mode and mode != triage["mode"]) or (state and state != status):
            continue
        extraction = triage["extraction"]
        followup = item["followup"] or {}
        rows.append((item["key"], SOURCES[source], item["data_kind"], item["received_at"], item["body"],
                     item["score"]["total"], item.get("score_label", "演示规则评分"), MODES[triage["mode"]], triage["reason"], extraction["vehicle"],
                     extraction["quantity"], extraction["unit"], extraction["quantity_note"], triage["draft"],
                     STATES[status], review.get("reason", ""), review.get("reviewed_by", ""),
                     item["owner"], followup.get("due", ""), "逾期" if item["overdue"] else "",
                     followup.get("supplement", ""), item["source_url"]))
    return Response(csv_bytes(
        ("记录", "来源", "数据类型", "时间", "原文", "来源评分", "评分口径", "建议", "依据", "车型", "数量", "单位",
         "数量备注", "首回复草稿", "复核状态", "处理说明", "处理人", "跟进负责人", "跟进日期", "时效",
         "补充查件资料", "原渠道链接"), rows
    ), mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="comment-desk-{source}.csv"'})
