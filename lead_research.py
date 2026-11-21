import json
import re
from datetime import datetime
from hashlib import sha256

import buyers
import inquiries
import lead_inquiries as leads
from catalog import find_candidates
from storage import get_db

CANDIDATE_LABELS = {"competition": "竞争候选", "similar": "相似渠道", "affiliate": "关联企业",
                    "benchmark": "产品对标", "market": "市场参考"}
REFERENCE_RELATIONSHIPS = {"benchmark", "market"}
CANDIDATE_FIELDS = ("company", "market", "domain", "products", "channel", "procurement",
                    "contact", "email", "phone", "contact_source", "source", "quote", "relationship")
PHONE = re.compile(r"\+?[0-9(][0-9(). \-]{5,34}[0-9]")
MARKETS = {"usa": "美国", "us": "美国", "united states": "美国", "united states of america": "美国",
           "uae": "阿联酋", "united arab emirates": "阿联酋", "brazil": "巴西",
           "germany": "德国", "mexico": "墨西哥", "united kingdom": "英国", "uk": "英国",
           "netherlands": "荷兰", "iran": "伊朗", "nigeria": "尼日利亚", "uganda": "乌干达",
           "venezuela": "委内瑞拉", "bangladesh": "孟加拉", "dominica": "多米尼克", "morocco": "摩洛哥",
           "finland": "芬兰", "nepal": "尼泊尔", "peru": "秘鲁", "south africa": "南非"}
PRODUCTS = {
    "制动衬片": re.compile(r"brake lining|鼓式刹车片|鼓式刹车衬片|制动衬片|\bt850\b", re.I),
    "盘式刹车片": re.compile(r"disc (?:brake )?pads?|brake pads?|盘式刹车片|pastillas de freno(?: de disco)?", re.I),
    "制动蹄": re.compile(r"brake shoes?|制动蹄", re.I),
    "离合器面片": re.compile(r"clutch facings?|离合器面片", re.I),
}


def market_key(value):
    value = value.casefold().strip()
    if value in MARKETS:
        return MARKETS[value]
    return next((country for country in sorted(set(MARKETS.values()), key=len, reverse=True)
                 if value.startswith(country)), value)


def product_set(value):
    return {name for name, regex in PRODUCTS.items() if regex.search(value)}


def evidence_for(buyer_id):
    return get_db().execute("SELECT * FROM evidence WHERE buyer_id=? ORDER BY id", (buyer_id,)).fetchall()


def latest_text(row):
    mail = get_db().execute("""SELECT reply_text FROM lead_messages WHERE inquiry_id=?
                               AND json_extract(analysis,'$.tier')<>'archive'
                               ORDER BY received_at DESC,id DESC LIMIT 1""", (row["id"],)).fetchone()
    return mail["reply_text"] if mail else leads.current_reply(leads.payload(row["fields"], {}).get("content", ""))


def build_dossier(row):
    fields = leads.payload(row["fields"], {})
    identity = leads.payload(row["identity"], {})
    assessment = leads.payload(row["analysis"], {})
    public = leads.payload(row["public_research"], {})
    if public.get("source_hash") and public["source_hash"] != sha256(fields["content"].encode()).hexdigest():
        public = {}
    text = latest_text(row)
    parsed = inquiries.rule_analysis(text)["fields"]
    fits, _ = find_candidates(parsed)
    numbers = tuple(dict.fromkeys(match.group() for match in leads.PART_NUMBER.finditer(text)))[:5]
    products = sorted(product_set(text))
    quantity = (parsed.get("quantity", "") + " " + parsed.get("unit", "")).strip()
    quantity = re.sub(r"\bsets?\b", "套", quantity, flags=re.I)
    quantity = re.sub(r"\b(?:pcs?|pieces?)\b", "件", quantity, flags=re.I)
    demand = "、".join(filter(None, (*products, parsed.get("code") or "、".join(numbers), quantity)))
    evidence = []
    facts = {
        "市场线索": identity.get("market") or fields.get("address"),
        "询盘重点": (public.get("facts") or {}).get("询价规模") or demand,
        "到展意向": {"confirmed": "回信有明确到展或会面信号", "planned": "表达参展计划"}.get(assessment.get("show"), ""),
    }
    if identity:
        facts["企业主体依据"] = identity["quote"]
        evidence.append({"label": "企业主体", "quote": identity["quote"], "source": identity["source"]})
    if row["buyer_id"]:
        found = buyers.findings(evidence_for(row["buyer_id"]))
        for axis, label in (("product", "经营品类依据"), ("procurement", "采购资格"), ("china", "中国采购")):
            item = found.get(axis)
            if item and item["state"] == "supported":
                facts[label] = item["quote"]
                evidence.append({"label": label, "quote": item["quote"], "source": item["ref"]})
    if assessment.get("quote"):
        evidence.insert(0, {"label": "来信意向", "quote": assessment["quote"], "source": "当前会话"})
    if fields.get("page"):
        evidence.append({"label": "页面来源", "quote": fields["page"], "source": fields["page"]})
    if numbers:
        technical = "先把 " + "、".join(numbers[:3]) + " 和旧件号、车型、车轴及厚度对上，再给规格表和适配样品。"
    else:
        technical = "请对方给出首批采购的车系、旧件号或带尺寸的旧件照片，再准备匹配样品。"
    if assessment.get("show"):
        goal = "先确认到展和会面安排。用现有询盘聚焦一到两个产品，约定报价或样品的下一步。"
        timing = "提前确认到展日期、会面时间、地点及来访人员，会后约定样品或报价回复时间。"
    else:
        goal = "先确认最值得推进的一项采购需求，再约定明确的下一步。"
        timing = "确认首批数量、目标价格和实际收货市场，约定回图、核价或寄样的时间。"
    focus = list(public.get("next_steps") or []) or [
        "确认对方是进口商、经销商还是终端车队，问清实际采购主体和决策人。",
        technical,
        timing,
    ]
    return {"version": 2, "company": identity.get("company") or fields.get("company") or public.get("declared_company") or "",
            "goal": goal, "facts": {label: value for label, value in facts.items() if value}, "evidence": evidence,
            "samples": [{"name": item["source_name"], "width": item["width_mm"],
                         "source": f"{item['source_file']} PDF 第 {item['pdf_page']} 页"} for item in fits],
            "questions": focus, "prepared_at": leads.stamp()}


def prepare_dossier(row):
    dossier = build_dossier(row)
    db = get_db()
    with db:
        db.execute("UPDATE lead_inquiries SET dossier=? WHERE id=?", (json.dumps(dossier, ensure_ascii=False), row["id"]))
    return dossier


def candidates(row_id):
    return [{**dict(row), "candidate": loads_candidate(row)} for row in
            get_db().execute("SELECT * FROM lead_candidates WHERE inquiry_id=? ORDER BY verification='verified' DESC,id", (row_id,))]


def add_candidate(inquiry_id, item):
    domain = buyers.domain_name(item["domain"])
    key = domain or re.sub(r"\W+", "", item["company"].casefold())
    db = get_db()
    with db:
        inserted = db.execute("""INSERT INTO lead_candidates(inquiry_id,identity_key,candidate)
                                 VALUES(?,?,?) ON CONFLICT DO NOTHING""", (inquiry_id, key, json.dumps(item, ensure_ascii=False)))
    return int(bool(inserted.rowcount))


def discover(row):
    fields = leads.payload(row["fields"], {})
    identity = leads.payload(row["identity"], {})
    market = market_key(identity.get("market") or fields.get("address", ""))
    text = latest_text(row)
    text += "\n" + identity.get("quote", "")
    if row["buyer_id"]:
        text += "\n" + "\n".join(item["source_text"] for item in evidence_for(row["buyer_id"]))
    products = product_set(text)
    if not market or not products:
        return {"added": 0, "message": "客户市场或制动件品类待核，请先补证。"}
    db = get_db()
    real = row["data_kind"] != "synthetic"
    matched = []
    for buyer in db.execute("SELECT * FROM buyers ORDER BY company"):
        if (buyer["data_kind"] != "synthetic") != real or buyer["id"] == row["buyer_id"] or market_key(buyer["country"]) != market:
            continue
        if buyer["company"].casefold() == (identity.get("company") or fields.get("company", "")).casefold() or (row["sender"] and row["sender"] == buyer["contact_email"].casefold()):
            continue
        evidence = evidence_for(buyer["id"])
        origin = buyers.findings(evidence)
        product = origin["product"]
        overlap = products & product_set("\n".join(item["source_text"] for item in evidence))
        if not overlap or not product or product["state"] != "supported" or not leads.http_link(product["ref"]):
            continue
        matched.append({"company": buyer["company"], "market": buyer["country"], "domain": buyer["domain"],
                        "products": "、".join(sorted(overlap)), "channel": "待核",
                        "procurement": origin["procurement"]["quote"] if origin["procurement"] else "待核",
                        "source": product["ref"], "quote": product["quote"],
                        "relationship": "similar"})
    matched.sort(key=lambda item: item["procurement"] == "待核")
    return {"added": sum(add_candidate(row["id"], item) for item in matched[:12]),
            "message": f"共找到 {len(matched)} 家库内同市场同品类候选。"}


def validate_candidate(values):
    item = {key: leads.plain(values.get(key), 1500 if key in {"source", "quote", "contact_source"} else 350)
            for key in CANDIDATE_FIELDS}
    if not item["company"] or not item["market"] or not item["products"] or not item["quote"] or not leads.http_link(item["source"]):
        raise ValueError("请填写候选企业、市场、产品重叠、来源链接和证据摘录。")
    if item["relationship"] not in CANDIDATE_LABELS:
        raise ValueError("请选择候选关系。")
    if item["email"] and not buyers.contact_email(item["email"]):
        raise ValueError("请填写有效的商务邮箱。")
    if item["phone"] and not business_phone(item["phone"]):
        raise ValueError("请填写有效的商务电话，可包含国家区号。")
    if item["contact_source"] and not leads.http_link(item["contact_source"]):
        raise ValueError("请使用有效的联系方式来源链接。")
    return item


def business_phone(value):
    return value if PHONE.fullmatch(value) and 7 <= sum(char.isdigit() for char in value) <= 18 else ""


def save_identity(row, values):
    keys = ("company", "domain", "market", "source", "quote")
    identity = {key: leads.plain(values.get(key), 1400 if key in {"source", "quote"} else 350) for key in keys}
    if not identity["company"] or not identity["quote"] or not leads.http_link(identity["source"]):
        raise ValueError("请填写核对后的公司名、公开链接和证据。")
    db = get_db()
    with db:
        db.execute("UPDATE lead_inquiries SET identity=?,dossier='' WHERE id=?",
                   (json.dumps(identity, ensure_ascii=False), row["id"]))
    return identity


def follow(row, candidate, event):
    if row["status"] != "declined":
        raise ValueError("将原客户标记为暂无合作后，可为候选生成跟进草稿。")
    if candidate["verification"] != "verified":
        raise ValueError("请先核对候选企业的来源和产品。")
    item = loads_candidate(candidate)
    if item["relationship"] in REFERENCE_RELATIONSHIPS:
        raise ValueError("这条候选用于市场和产品参考。")
    template = buyers.invite({"company": item["company"], "contact_name": ""}, {"product": None, "attendance": None}, event)
    db = get_db()
    with db:
        db.execute("""UPDATE lead_candidates SET follow_up=COALESCE(NULLIF(follow_up,''),?),
                       draft=COALESCE(NULLIF(draft,''),?) WHERE id=?""",
                   (datetime.now(leads.ZONE).date().isoformat(), template, candidate["id"]))
    return {"draft": candidate["draft"] or template}


def loads_candidate(row):
    item = json.loads(row["candidate"])
    previous = item.get("contact", "")
    item["email"] = buyers.contact_email(item.get("email", "") or previous)
    item["phone"] = business_phone(item.get("phone", "") or previous)
    item["contact_source"] = leads.http_link(item.get("contact_source", ""))
    if item["email"] == previous or item["phone"] == previous:
        item["contact"] = ""
    return item
