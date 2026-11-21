import hashlib
import json
import re
from urllib.parse import urlsplit

from buyer_settings import weights as current_weights
from model_client import ModelUnavailable, ask_json
from scoring import buyer_score
from storage import get_db

REQUIRED = {"company", "source_text"}
KINDS = {"synthetic", "public", "authorized"}
AXES = {
    "product": "商用车制动品类",
    "procurement": "采购或进口能力",
    "china": "中国采购记录",
    "travel": "来华计划",
    "attendance": "到展意向",
}
TIER_LABELS = {"hot": "火热 · 立即联系", "priority": "高潜 · 重点跟进", "review": "有戏 · 补证核实",
               "nurture": "慢热 · 持续观察", "defer": "冷却 · 暂缓联系"}
FOCUS_TIERS = {"hot", "priority"}
BRAKES = re.compile(r"brak(?:e|ing)|制动|刹车|freio|freno", re.I)
TRUCK = re.compile(r"truck|trailer|heavy.duty|commercial vehicle|卡车|商用车|重卡|camión|cami[oó]n|caminh", re.I)
DENIAL = re.compile(r"passenger.car.{0,45}(?:only|exclusively)|(?:only|exclusively).{0,45}passenger.car|"
                    r"(?:no|not).{0,25}(?:heavy.duty|commercial vehicle|truck)|仅经营乘用车", re.I)
BUYING = re.compile(r"\b(?:consignee|importer|imported|purchased|buying department)\b|采购方|收货人|进口商|已采购", re.I)
ORIGIN = re.compile(r"from china|china.origin|made in china|自中国进口|从中国采购|原产国.?中国", re.I)
TRAVEL = re.compile(r"\b(?:in china now|will (?:visit|travel to|be in) china|arrived in china)\b|已到中国|计划来华|已抵达中国", re.I)
ATTENDANCE = re.compile(r"registered for|confirmed to attend|plan to attend|will attend|已报名|确认参展|确认到展", re.I)
EMAIL = re.compile(r"[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+")


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def domain_name(value):
    try:
        parts = urlsplit(value if "://" in value else "//" + value)
        return (parts.hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def contact_email(value):
    clean = value.strip()
    return clean if len(clean) <= 254 and EMAIL.fullmatch(clean) else ""


def entity_key(kind, domain, company, country):
    scope = "synthetic" if kind == "synthetic" else "real"
    normalized_name = " ".join(company.casefold().split())
    return digest(f"{scope}|{domain or normalized_name}|{country.casefold()}")


def import_rows(records, default_kind, event=None, allow_profile_only=False):
    db = get_db()
    counts = {"buyers": 0, "new": 0, "duplicate": 0, "invites": 0, "errors": []}
    touched = set()
    with db:
        for line, record in records:
            company = record.get("company", "")
            body = record.get("source_text", "")
            source_type = record.get("source_type", "website").lower() or "website"
            kind = (record.get("data_kind") or default_kind).lower()
            if not company or (not body and not allow_profile_only) or kind not in KINDS or len(body) > 12000:
                counts["errors"].append(f"第 {line} 行客户名称、资料类型或原文长度无效。")
                continue
            country = record.get("country", "")
            domain = domain_name(record.get("domain", ""))
            supplied_email = record.get("contact_email") or record.get("email", "")
            email = contact_email(supplied_email)
            name = record.get("contact_name", "")[:120]
            if supplied_email and not email:
                counts["errors"].append(f"第 {line} 行的邮箱格式无效，已保留买家证据和其他字段。")
            key = entity_key(kind, domain, company, country)
            importance = record.get("importance", "")
            profile = (record.get("customer_source", "")[:100], int(importance) if importance in {"1", "2", "3", "4", "5"} else None,
                       record.get("address", "")[:300], record.get("phone", "")[:32], record.get("description", "")[:3000],
                       *(record.get(key, "")[:500] for key in ("social_facebook", "social_twitter", "social_linkedin", "tags")),
                       record.get("owner", "")[:120])
            ref = record.get("source_ref", "")
            fingerprint = digest(json.dumps([
                key, source_type, ref, record.get("record_id", ""), body,
                record.get("as_of", ""), kind,
            ], ensure_ascii=False))
            inserted = db.execute(
                """INSERT INTO buyers (entity_key, company, country, domain, data_kind, contact_email, contact_name,
                                       customer_source, importance, address, phone, description,
                                       social_facebook, social_twitter, social_linkedin, tags, owner)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(entity_key) DO NOTHING""",
                (key, company, country, domain, kind, email, name, *profile),
            )
            counts["buyers"] += inserted.rowcount
            buyer = db.execute("SELECT id FROM buyers WHERE entity_key = ?", (key,)).fetchone()
            if not inserted.rowcount and (email or name or any(profile)):
                db.execute("""UPDATE buyers SET contact_email=COALESCE(NULLIF(contact_email,''),?),
                              contact_name=COALESCE(NULLIF(contact_name,''),?),
                              customer_source=COALESCE(NULLIF(customer_source,''),?), importance=COALESCE(importance,?),
                              address=COALESCE(NULLIF(address,''),?), phone=COALESCE(NULLIF(phone,''),?),
                              description=COALESCE(NULLIF(description,''),?),
                              social_facebook=COALESCE(NULLIF(social_facebook,''),?),
                              social_twitter=COALESCE(NULLIF(social_twitter,''),?),
                              social_linkedin=COALESCE(NULLIF(social_linkedin,''),?),
                              tags=COALESCE(NULLIF(tags,''),?), owner=COALESCE(NULLIF(owner,''),?)
                              WHERE id=?""", (email, name, *profile, buyer["id"]))
            touched.add(buyer["id"])
            if not body:
                counts["duplicate"] += int(not inserted.rowcount)
                continue
            evidence = db.execute(
                """INSERT INTO evidence
                   (buyer_id, fingerprint, source_type, source_ref, as_of, source_text, data_kind)
                   VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(fingerprint) DO NOTHING""",
                (buyer["id"], fingerprint, source_type, ref, record.get("as_of", ""), body, kind),
            )
            if evidence.rowcount:
                db.execute("UPDATE buyers SET analysis = '' WHERE id = ?", (buyer["id"],))
                counts["new"] += 1
            else:
                counts["duplicate"] += 1
        if event is not None:
            weights = current_weights()
            for record_id in touched:
                counts["invites"] += prepare_invite(record_id, event, weights)
    return counts


def prepare_invite(buyer_id, event, weights=None):
    db = get_db()
    buyer = db.execute("SELECT * FROM buyers WHERE id=?", (buyer_id,)).fetchone()
    if buyer["draft"] or buyer["status"] != "new":
        return 0
    evidence = db.execute("SELECT * FROM evidence WHERE buyer_id=? ORDER BY id", (buyer_id,)).fetchall()
    tier, axes, _ = evaluate(buyer, evidence, weights)
    if tier not in FOCUS_TIERS:
        return 0
    return db.execute("UPDATE buyers SET draft=? WHERE id=? AND draft=''", (invite(buyer, axes, event), buyer_id)).rowcount


def make_finding(row, quote, state):
    return {"id": row["id"], "quote": quote, "state": state,
            "ref": row["source_ref"], "date": row["as_of"], "kind": row["data_kind"]}


def findings(evidence):
    result = {axis: None for axis in AXES}
    for row in evidence:
        body = row["source_text"]
        source = row["source_type"]
        for sentence in re.split(r"(?<=[.!?。！？])\s+|[\r\n]+", body):
            text = sentence.strip()
            if not text:
                continue
            if DENIAL.search(text):
                prior = result["product"]
                state = "conflict" if prior and prior["state"] in {"supported", "conflict"} else "excluded"
                result["product"] = make_finding(row, text, state)
            elif BRAKES.search(text) and TRUCK.search(text):
                prior = result["product"]
                state = "conflict" if prior and prior["state"] in {"excluded", "conflict"} else "supported"
                result["product"] = make_finding(row, text, state)
            if source in {"trade", "crm"} and BUYING.search(text):
                result["procurement"] = make_finding(row, text, "supported")
                if ORIGIN.search(text):
                    result["china"] = make_finding(row, text, "supported")
            if source == "message":
                if TRAVEL.search(text):
                    result["travel"] = make_finding(row, text, "declared")
                if ATTENDANCE.search(text):
                    result["attendance"] = make_finding(row, text, "declared")
    return result


def evaluate(buyer, evidence, weights=None):
    result = findings(evidence)
    if buyer["analysis"]:
        for axis, finding in json.loads(buyer["analysis"]).items():
            if result[axis] is None:
                result[axis] = finding
    score = buyer_score(result, buyer["contact_email"], weights if weights is not None else current_weights())
    return buyer["override"] or score["tier"], result, score


def assess(buyer, evidence):
    tier, axes, _ = evaluate(buyer, evidence)
    return tier, axes


def ai_assess(evidence):
    by_id = {row["id"]: row for row in evidence}
    result = ask_json(
        "判断商用车制动产品相关性。返回 product 为空或对象 {id,quote}。"
        "仅在同一原句明确出现商用车辆用途和制动件时填写。quote 必须逐字引用，不推断采购能力。",
        [{"id": row["id"], "source": row["source_text"][:2500]} for row in evidence[:12]],
    )
    item = result.get("product")
    if item is None:
        return {}
    if not isinstance(item, dict) or type(item.get("id")) is not int:
        raise ModelUnavailable("模型引用无法核验。")
    row = by_id.get(item["id"])
    quote = item.get("quote")
    if not row or not isinstance(quote, str) or not quote.strip() or quote not in row["source_text"]:
        raise ModelUnavailable("模型引用无法核验。")
    return {"product": make_finding(row, quote, "supported")}


def invite(buyer, axes, event):
    name = buyer["company"]
    intro = "I’m reaching out from Kooying about commercial vehicle brake linings and pads."
    if axes["product"] and axes["product"]["state"] == "supported":
        intro += " I noticed that your range includes commercial vehicle braking products."
    greeting = f"Hello {buyer['contact_name']}," if buyer["contact_name"] else f"Hello {name} team,"
    lines = [greeting, intro]
    if event["name"]:
        date = f" ({event['date']})" if event["date"] else ""
        place = f" at {event['venue']}" if event["venue"] else ""
        lines.append(f"We’d like to invite you to meet us at {event['name']}{date}{place}.")
        if event["booth"]:
            lines.append(f"You can find us at booth {event['booth']}.")
    else:
        lines.append("Are you planning to attend the upcoming auto parts trade show in China?")
    if axes["attendance"]:
        lines.append("What time would suit you for a brief meeting at the show?")
    else:
        lines.append("If you are attending, I would be happy to arrange a brief meeting.")
    lines.append("Best regards,\nKooying sales team")
    return "\n\n".join(lines)


def similar(buyer, buyers, evidence_by_id):
    origin = findings(evidence_by_id[buyer["id"]])
    if not buyer["country"] or not origin["product"] or origin["product"]["state"] != "supported":
        return []
    matches = []
    for candidate in buyers:
        if candidate["id"] == buyer["id"] or candidate["country"].casefold() != buyer["country"].casefold():
            continue
        if (buyer["data_kind"] == "synthetic") != (candidate["data_kind"] == "synthetic"):
            continue
        candidate_findings = findings(evidence_by_id[candidate["id"]])
        if candidate_findings["product"] and candidate_findings["product"]["state"] == "supported":
            matches.append((candidate, candidate_findings["product"], bool(candidate_findings["procurement"])))
    matches.sort(key=lambda item: (not item[2], item[0]["company"].casefold()))
    return matches[:3]
