import hashlib
import json
import re
from datetime import datetime, timezone

from buyers import KINDS
from catalog import find_candidates
from model_client import ModelUnavailable, ask_json
from scoring import ORDER, comment_score
from storage import get_db

REQUIRED = {"body"}
LABELS = {"high": "高意向", "medium": "中意向", "low": "普通互动", "review": "待确认"}
STATES = {"new": "待回复", "review": "待复核", "archive": "留档", "done": "已跟进"}
RFQ = re.compile(r"\b(?:quot(?:e|ation)|pricing|price list|how much|bulk order|rfq|minimum order|moq)\b|报价|询价|报.{0,4}价格|起订量|cotiza|orçamento", re.I)
TRADE_SHOW = re.compile(r"\b(?:booth|trade show|trade fair|exhibition|automechanika)\b|展位|展会|汽配展", re.I)
MEETING = re.compile(r"\b(?:meeting|meet|book|appoint\w*|registered)\b|预约|约见", re.I)
INTEREST = re.compile(r"catalog|brochure|specification|availability|looking for|do you (?:have|stock)|目录|规格|有货|想了解|想找", re.I)
SOCIAL = re.compile(r"^(?:great|nice|cool|thanks?|thank you|congratulations|awesome|well done)\b|^(?:不错|点赞|谢谢|视频很清楚)", re.I)
PART = re.compile(r"\bbrake\s+(?:lining|pad|shoe)s?\b|鼓式刹车片|鼓式刹车衬片|盘式刹车片|刹车片|制动蹄", re.I)
CODE = re.compile(r"(?<![A-Z0-9])(?:新\s*|new\s*)?T850(?![A-Z0-9])|"
                  r"\b(?:[A-Z]{1,4}-?\d{3,6}[A-Z0-9]*|\d{3,6}(?!(?:mm|cm|kg|pcs?)\b)[A-Z]{1,4})\b", re.I)
FIELDS = ("product", "code", "quantity", "unit", "position", "width_mm", "holes", "company", "country",
          "axle", "material", "destination")
WIDTH = re.compile(r"(?:(?:width|宽度|宽)\s*(?:is|:|：|=|为)?\s*)(\d+(?:\.\d+)?)\s*(?:mm|毫米)|"
                   r"(\d+(?:\.\d+)?)\s*(?:mm|毫米)\s*(?:wide|宽)", re.I)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_date(value):
    if not value:
        return now()
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return value
    if stamp.tzinfo:
        stamp = stamp.astimezone(timezone.utc)
    return stamp.isoformat(timespec="seconds")


def rule_analysis(body):
    fields = {}
    product = PART.search(body)
    if product:
        fields["product"] = product.group()
    code = CODE.search(body)
    if code:
        fields["code"] = code.group().strip()
    quantity = re.search(r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(sets?\b|pcs?\b|pieces?\b|pairs?\b|套|件|对)", body, re.I)
    if quantity:
        fields.update(quantity=quantity[1], unit=quantity[2])
    position = re.search(r"\b(front|rear)\b|前刹|后刹", body, re.I)
    if position:
        fields["position"] = {"前刹": "front", "后刹": "rear"}.get(position.group(), position.group().lower())
    width = WIDTH.search(body)
    if width:
        fields["width_mm"] = width[1] or width[2]
    holes = re.search(r"(\d+)\s*(?:holes?\b|孔)", body, re.I)
    if holes:
        fields["holes"] = holes[1]

    rfq = RFQ.search(body) or ORDER.search(body)
    show = TRADE_SHOW.search(body)
    meeting = MEETING.search(body)
    interest = INTEREST.search(body)
    social = SOCIAL.search(body)
    if rfq:
        intent, signal = "询价", rfq
    elif show and meeting:
        intent, signal = "展会约见", meeting
    elif show:
        intent, signal = "展会咨询", show
    elif interest or product or code:
        intent, signal = "产品咨询", interest or product or code
    elif social:
        intent, signal = "普通互动", social
    else:
        intent, signal = "待确认", None
    score = comment_score(body, fields, rfq.group() if rfq else "",
                          signal.group() if signal and (interest or show) else "")
    tier = score["tier"]
    if tier == "low" and not (intent == "普通互动" or score["raw"] > score["total"] and score["total"] == 10):
        tier = "review"
    language = "中文" if re.search(r"[\u4e00-\u9fff]", body) else "待识别"
    if language == "待识别" and re.search(r"\b(?:you|the|please|our|for|what|great|can)\b", body, re.I):
        language = "英语"
    return {"tier": tier, "intent": intent, "fields": fields, "score": score,
            "quote": signal.group() if signal else "", "mode": "rules", "language": language}


def ai_analysis(body):
    baseline = rule_analysis(body)
    data = ask_json(
        "从 Facebook 评论识别采购意向。输出字段 tier, intent, quote, language, fields。"
        "tier 只取 high 明确商用车报价/约见, medium 索目录或产品咨询, low 普通互动, review 待确认。"
        "intent 只取 询价、展会约见、展会咨询、产品咨询、普通互动、待确认 中之一。"
        "quote 为支撑结论的逐字原文。fields 可包含 product,code,quantity,unit,position,width_mm,holes,"
        "company,country,axle,material,destination，每个值都必须逐字出现在评论中。未知省略。",
        {"comment": body[:6000]},
    )
    tier = data.get("tier")
    intent = data.get("intent")
    quote = data.get("quote")
    fields = data.get("fields")
    valid_intents = {"询价", "展会约见", "展会咨询", "产品咨询", "普通互动", "待确认"}
    if (not isinstance(tier, str) or tier not in LABELS or not isinstance(intent, str)
            or intent not in valid_intents or not isinstance(quote, str)
            or not isinstance(fields, dict) or (quote and quote not in body)
            or (tier in {"medium", "high"} and not quote.strip())
            or (tier == "high" and intent not in {"询价", "展会约见"})):
        raise ModelUnavailable("模型判断缺少可核验的原文。")
    for key in FIELDS:
        value = fields.get(key)
        if isinstance(value, str) and value.strip() and value in body:
            if key == "position":
                value = {"前刹": "front", "后刹": "rear"}.get(value, value.casefold())
                if value not in {"front", "rear"}:
                    continue
            if key in {"width_mm", "holes", "quantity"} and not re.fullmatch(r"\d[\d,]*(?:\.\d+)?", value):
                continue
            baseline["fields"].setdefault(key, value)
    language = data.get("language")
    baseline.update(intent=intent, quote=quote, mode="ai",
                    language=language.strip()[:24] if isinstance(language, str) and language.strip() else baseline["language"])
    return baseline


def default_status(tier):
    return {"high": "new", "low": "archive"}.get(tier, "review")


def read_analysis(message):
    saved = json.loads(message["analysis"])
    if "score" not in saved:
        current = rule_analysis(message["body"])
        saved.update(score=current["score"], tier=current["tier"])
    return saved


def import_rows(records, default_kind, page_id="", transport="csv", event=None, on_import=None, channel="facebook"):
    db = get_db()
    counts = {"new": 0, "duplicate": 0, "errors": [], "ids": []}
    with db:
        for line, record in records:
            body = record.get("body", "").strip()
            kind = (record.get("data_kind") or default_kind).lower()
            if not body or len(body) > 12000 or kind not in KINDS:
                counts["errors"].append(f"第 {line} 行需要有效原文与数据类型。")
                continue
            account = record.get("account") or record.get("page_id") or (
                "demo-page" if kind == "synthetic" else page_id or "manual-page"
            )
            ref = record.get("source_ref") or record.get("source_url", "")
            post = record.get("post_id", "")
            provided_date = record.get("received_at") or record.get("created_time", "")
            external = record.get("message_id") or record.get("comment_id") or ("manual-" + hashlib.sha256(
                json.dumps([kind, ref, post, body, provided_date], ensure_ascii=False).encode()
            ).hexdigest()[:24])
            thread = record.get("thread_id") or external
            parsed = rule_analysis(body)
            draft = ""
            if parsed["tier"] == "high":
                _, question = find_candidates(parsed["fields"])
                draft = reply(parsed["fields"], parsed["intent"], event or {"name": "", "booth": ""}, question)
            result = db.execute(
                """INSERT INTO interactions (channel, account, external_id, post_id, thread_id, source_ref,
                       received_at, body, company, data_kind, transport, analysis, status, draft)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(channel, account, external_id) DO NOTHING""",
                (channel, account, external, post, thread, ref, canonical_date(provided_date), body,
                 record.get("company", ""), kind, transport,
                 json.dumps(parsed, ensure_ascii=False), default_status(parsed["tier"]), draft),
            )
            if result.rowcount:
                counts["new"] += 1
                counts["ids"].append(result.lastrowid)
            else:
                counts["duplicate"] += 1
        if on_import:
            on_import(counts["ids"])
    return counts


def thread_fields(messages):
    combined = {}
    conflict = set()
    for message in messages:
        parsed = json.loads(message["analysis"])
        for key, value in parsed["fields"].items():
            if key in combined and str(combined[key]).casefold() != str(value).casefold():
                conflict.add(key)
            combined[key] = value
    return combined, conflict


def reply(fields, intent, event, question):
    if intent.startswith("展会"):
        if event["name"]:
            answer = f"Thank you for reaching out about {event['name']}."
            if event["booth"]:
                answer += f" You can find us at booth {event['booth']}."
        else:
            answer = "Thanks for your interest in meeting the Kooying team."
        return answer + " Are you planning to attend, and what time would work for you?"
    product = fields.get("product", "")
    english_products = {"鼓式刹车片": "drum brake linings", "鼓式刹车衬片": "drum brake linings",
                        "盘式刹车片": "disc brake pads", "刹车片": "brake pads and linings", "制动蹄": "brake shoes"}
    product = english_products.get(product) or (product if product.isascii() else "") or "commercial vehicle brake parts"
    lines = [f"Thank you for your interest in {product}."]
    if question:
        lines.append(question)
    elif not fields.get("code"):
        lines.append("Could you share the part number or axle model so we can identify the right options?")
    if not fields.get("quantity"):
        lines.append("What quantity are you looking to order?")
    if not fields.get("company"):
        lines.append("Could you also share your company name?")
    return " ".join(lines)


def catalog_for_thread(fields, conflicts):
    if conflicts.intersection({"code", "position", "width_mm", "holes"}):
        return [], "Could you confirm the part number and current measurements? We received different specifications."
    return find_candidates(fields)
