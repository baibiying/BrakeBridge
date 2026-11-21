"""Independent review queue for existing comments and inquiries."""

import csv
import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path

from flask import Blueprint, Response, abort, current_app, request, send_from_directory

import facebook
import inquiries
from lead_inquiries import payload
from scoring import comment_score
from storage import get_db
from tables import csv_bytes
from workspace import current_user, error

from .extraction import build_card
from .policy import PRICE_REQUEST, plan

bp = Blueprint("comment_desk", __name__)
SOURCES = {"social": "社媒评论", "inquiry": "已有询盘", "demo": "演示样本"}
STATES = {"pending": "待处理", "review": "待复核", "done": "已跟进", "archive": "留档"}
MODES = {"candidate": "目录候选", "acknowledge": "可先回复", "manual": "人工核对", "skip": "无需回复"}
LIMIT = 150
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
    return {
        "key": f"social:{row['id']}", "title": row["company"] or "评论者",
        "body": row["body"], "received_at": row["received_at"], "channel": row["channel"],
        "data_kind": row["data_kind"], "source_url": row["source_ref"],
        "existing_id": row["id"] if row["channel"] == "facebook" else None,
        "owner": row["owner"], "source_kind": row["transport"],
    }


def from_inquiry(row):
    fields = payload(row["fields"], {})
    if not isinstance(fields, dict):
        fields = {}
    return {
        "key": f"inquiry:{row['id']}", "title": fields.get("company") or fields.get("title") or "已有询盘",
        "body": str(fields.get("content") or ""), "received_at": row["received_at"],
        "channel": "inquiry", "data_kind": row["data_kind"], "source_url": "", "existing_id": row["id"],
        "owner": row["owner"], "source_kind": row["source"], "has_files": bool(fields.get("files")),
    }


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
        positions = set(re.findall(r"front|rear|前刹|后刹", body.lower()))
        matches, _ = find_candidates({**fields, "code": "新T850" if re.search(r"(?:新|new)\s*T850", body, re.I) else "T850",
                                      "position": fields.get("position") if len(positions) <= 1 else ""})
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


def enrich(item):
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
    buying = (analysis["intent"] == "询价" or extraction["vehicle"] or analysis["fields"].get("code")
              or PRICE_REQUEST.search(item["body"]))
    if (buying or lines) and decision["mode"] != "skip" and not decision["reason"].startswith("售后"):
        options = quote_options(item["body"], analysis)
        zh = analysis["language"] == "中文"
        if item.get("has_files"):
            question = "我们会先核对您已附的资料。" if zh else "We will review the attachment you already provided."
        elif not options:
            question = ("方便发一张旧件或包装标签的照片吗？我们可以先帮您查。"
                        if zh else "Could you send a photo of the old part or its label? We can start the lookup for you.")
        elif len(options) > 1 and any("前" in o["name"] for o in options) and any("后" in o["name"] for o in options) and not analysis["fields"].get("position"):
            question = "您这次要换的是前桥还是后桥的刹车片？" if zh else "Is the lining for the front or rear axle?"
        else:
            question = "方便发一张旧件照片，让我们核对规格吗？" if zh else "Could you share a photo of the old part so we can check the specification?"
        decision["quote"] = {"options": options, "quantity": extraction["quantity"], "unit": extraction["unit"],
                             "lines": lines,
                             "question": question, "missing": ["核实旧件或实际车桥", "内部核算重量、配方和附件成本", "确认正式单价和币种"]}
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
    item.update(intent=intent, score=analysis["score"], triage=decision,
                language=analysis["language"], reply_target=target)
    return item


def can_send(row, decision):
    cfg = current_app.config
    page, post = facebook.page_settings()
    return bool(os.getenv("COMMENT_DESK_FACEBOOK_SEND") == "1" and not cfg["FACEBOOK_AUTO_REPLY"]
                and page and cfg["FACEBOOK_PAGE_TOKEN"] and row["channel"] == "facebook"
                and row["data_kind"] == "authorized" and row["transport"] in {"graph", "webhook"}
                and row["account"] == page and row["post_id"] == post and decision["draft"])


def raw_item(key):
    source, _, item_id = key.partition(":")
    if source == "demo":
        demo = demo_rows().get(key)
        return (demo_item(key, *demo), None) if demo else (None, None)
    if source not in {"social", "inquiry"} or not re.fullmatch(r"[1-9][0-9]{0,17}", item_id):
        return None, None
    db = get_db()
    if source == "social":
        row = db.execute("SELECT * FROM interactions WHERE id=? AND channel<>'social_demo'", (item_id,)).fetchone()
        return (from_comment(row), row) if row else (None, None)
    row = db.execute("SELECT * FROM lead_inquiries WHERE id=? AND deleted_at=''", (item_id,)).fetchone()
    return (from_inquiry(row), None) if row else (None, None)


def feed(source):
    db = get_db()
    if source == "social":
        raw = [from_comment(row) for row in db.execute(
            "SELECT * FROM interactions WHERE channel<>'social_demo' ORDER BY received_at DESC,id DESC LIMIT ?", (LIMIT,)
        )]
    elif source == "inquiry":
        raw = [from_inquiry(row) for row in db.execute(
            "SELECT * FROM lead_inquiries WHERE deleted_at='' ORDER BY received_at DESC,id DESC LIMIT ?", (LIMIT,)
        )]
    else:
        raw = [demo_item(key, title, body) for key, (title, body) in demo_rows().items()]
    review_table(db)
    keys = [item["key"] for item in raw]
    reviews = {}
    replies = {}
    if keys:
        placeholders = ",".join("?" for _ in keys)
        reviews = {row["item_key"]: dict(row) for row in db.execute(
            f"SELECT * FROM comment_desk_reviews WHERE item_key IN ({placeholders})", keys
        )}
        replies = {row["item_key"]: dict(row) for row in db.execute(
            f"SELECT item_key,state,updated_at,receipt,note FROM comment_desk_replies WHERE item_key IN ({placeholders})", keys
        )}
    for item in raw:
        enrich(item)
        key = item["key"]
        item.update(review=reviews.get(key), reply=replies.get(key), send_token="")
        if item["channel"] == "facebook" and item["data_kind"] == "authorized" and not closed_review(item["review"]):
            row = db.execute("SELECT * FROM interactions WHERE id=?", (item["existing_id"],)).fetchone()
            legacy = db.execute("SELECT stage FROM facebook_replies WHERE interaction_id=?", (row["id"],)).fetchone()
            if not legacy and not item["reply"] and can_send(row, item["triage"]):
                item["send_token"] = reply_token(key, item["triage"]["draft"])
    return sorted(raw, key=lambda row: (row["score"]["total"], row["received_at"]), reverse=True)


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
    return {"items": feed(source), "limit": LIMIT}


@bp.get("/api/comment-desk/quote.csv")
def quote_sheet():
    item, _ = raw_item(request.args.get("key", ""))
    if not item:
        abort(404)
    triage = enrich(item)["triage"]
    quote = triage.get("quote")
    if not quote:
        return error("这条记录未识别到询价需求，请核对原文。", 409)
    identity = (item["key"], item["title"], item["channel"], item["owner"], item["body"])
    base = ("线索编号", "客户", "渠道", "负责人", "询价原文")
    if quote["lines"]:
        content = csv_bytes(base + ("客户零件号", "规格", "需求数量", "原文单位", "单价", "币种"),
                            [identity + (r["part"], r["size"], r["quantity"], r["unit"], "", "") for r in quote["lines"]])
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
          ON CONFLICT(item_key) DO NOTHING""",
                              (key, draft, current_user()["username"], inquiries.now(), key, row["id"]))
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
    source, _, item_id = key.partition(":")
    status = request.form.get("status", "")
    reason = request.form.get("reason", "").strip()
    if status not in STATES or not reason or len(reason) > 600:
        return error("请选择状态并填写 1–600 字的处理说明。")
    db = get_db()
    if source == "demo":
        exists = key in demo_rows()
    elif source in {"social", "inquiry"} and item_id.isascii() and item_id.isdecimal() and len(item_id) <= 18:
        table = "interactions" if source == "social" else "lead_inquiries"
        condition = "channel<>'social_demo'" if source == "social" else "deleted_at=''"
        exists = db.execute(f"SELECT 1 FROM {table} WHERE id=? AND {condition}", (int(item_id),)).fetchone()
        key = f"{source}:{int(item_id)}"
    else:
        exists = False
    if not exists:
        abort(404)
    review_table(db)
    values = (key, status, reason, current_user()["username"], inquiries.now())
    with db:
        db.execute("""INSERT INTO comment_desk_reviews VALUES (?,?,?,?,?)
                      ON CONFLICT(item_key) DO UPDATE SET status=excluded.status,reason=excluded.reason,
                      reviewed_by=excluded.reviewed_by,updated_at=excluded.updated_at""", values)
    return {"ok": True}


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
        rows.append((item["key"], SOURCES[source], item["data_kind"], item["received_at"], item["body"],
                     item["score"]["total"], MODES[triage["mode"]], triage["reason"], extraction["vehicle"],
                     extraction["quantity"], extraction["unit"], extraction["quantity_note"], triage["draft"],
                     STATES[status], review.get("reason", ""), review.get("reviewed_by", "")))
    return Response(csv_bytes(
        ("记录", "来源", "数据类型", "时间", "原文", "意向分", "建议", "依据", "车型", "数量", "单位",
         "数量备注", "首回复草稿", "复核状态", "处理说明", "处理人"), rows
    ), mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="comment-desk-{source}.csv"'})
