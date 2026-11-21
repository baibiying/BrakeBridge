import json
import re
from datetime import datetime, timezone
from hashlib import sha256

import agent_scoring
import buyers
import lead_inquiries as leads
import lead_research
import research_agent
from storage import get_db

GUIDES = {
    "buyer": "评估该企业作为冠英商用车制动件采购方的匹配度。看主营产品、可信采购或进口证据、来华与到展线索。"
             "企业自述或官网能证明主营品类时，仍需独立采购证据才能判定采购能力。可联系不代表有需求。",
    "inquiry": "评估该条询盘对冠英商用车制动件的销售价值。看产品匹配、明确询价或采购行为、型号数量、到展和企业角色。"
               "泛目录咨询、销售推销、自动回复、品类不符和拒绝应低分。回复里的自述和数量不等于已下单。",
}
SCORING = (
    "只根据给定资料独立打分。0–19 为无关或拒绝，20–39 为信号弱，40–69 为值得补证，"
    "70–89 为有明确相关采购依据，90–100 为多项强信号。信息不足时降低评分并说明原因。"
    "输出 JSON 如 {\"score\":70,\"reason\":\"用中文说明两条决定评分的事实或线索\"}。分数为整数。"
)
BACKGROUND = (
    "你为冠英商用车制动件销售核实指定企业的公开资料。使用联网工具，核对企业名称、官网和经营市场，"
    "优先找企业官网、官方目录、公告。区分同名主体。只摘录能由来源证实的主营、匹配产品、公开采购或销售渠道。"
    "忽略个人信息、手机号和私人邮箱。找不到同一主体的证据就返回空数组。"
    "输出 JSON 格式 {\"facts\":[{\"label\":\"主营业务\",\"text\":\"中文事实和相关性\","
    "\"url\":\"工具找到的精确出处\"}]}，最多 5 条。"
)
MEETING = (
    "你负责给中国制动件销售准备商务会面。只基于给定资料，写明本次要推进的目标和 3 至 5 条具体行动。"
    "有型号、对比样品、数量或到展信号时结合它们。有待核事项时写成要向客户确认的问题。"
    "按核实采购主体、对齐规格、准备样品或报价、约定下一步的业务顺序写。"
    "不得编造产品适配、价格、采购资格、参展承诺或交易状态。"
    "输出 JSON 如 {\"goal\":\"中文会面目标\",\"questions\":[\"中文行动一\",\"中文行动二\",\"中文行动三\"]}。"
)


def inquiry_row(record_id):
    return get_db().execute("SELECT * FROM lead_inquiries WHERE id=? AND deleted_at=''", (record_id,)).fetchone()


def buyer_row(record_id):
    return get_db().execute("SELECT * FROM buyers WHERE id=?", (record_id,)).fetchone()


def score_context(scope, row):
    if scope == "inquiry":
        return leads.lead_score_context(row)
    return agent_scoring.buyer_context(row, lead_research.evidence_for(row["id"]))


def score(scope, row, force=False):
    record_id = row["id"]
    context = score_context(scope, row)
    cached, _ = agent_scoring.stored(scope, record_id, "score", context)
    if cached and not force:
        return {"score": cached["score"], "cached": True}
    assessed, _ = research_agent.ask(GUIDES[scope] + SCORING, context)
    result = agent_scoring.validate_score(assessed)
    db = get_db()
    with db:
        db.execute("BEGIN IMMEDIATE")
        current = inquiry_row(record_id) if scope == "inquiry" else buyer_row(record_id)
        if current is None or score_context(scope, current) != context:
            raise ValueError("资料已更新，请重新运行 AI 评分。")
        agent_scoring.save(scope, record_id, "score", context, result)
    return {"score": result["score"], "cached": False, "message": "AI 评分已更新"}


def background_context(scope, row):
    if scope == "buyer":
        name, domain, market = row["company"], row["domain"], row["country"]
    else:
        identity = leads.payload(row["identity"], {})
        fields = leads.payload(row["fields"], {})
        public = leads.payload(row["public_research"], {})
        declared = public.get("declared_company") if not public.get("source_hash") or (
            public["source_hash"] == sha256(fields.get("content", "").encode()).hexdigest()
        ) else ""
        name = identity.get("company") or fields.get("company") or declared
        domain = identity.get("domain", "")
        market = identity.get("market") or fields.get("address")
    return {"企业": agent_scoring.clean(name, 180), "官网域名": buyers.domain_name(domain),
            "所在市场": agent_scoring.clean(market, 120)}


def background_data(scope, row):
    return agent_scoring.stored(scope, row["id"], "background", background_context(scope, row))[0]


def collect_background(scope, row):
    context = background_context(scope, row)
    company, domain = context["企业"], context["官网域名"]
    if not company and not domain:
        raise ValueError("请先核对企业名称或官网，再抓取公开背调。")
    response, sources = research_agent.ask(BACKGROUND, context, web=True)
    findings = response.get("facts")
    if not isinstance(findings, list):
        raise ValueError("模型未返回有效背调。")
    facts = []
    for fact in findings[:5]:
        if not isinstance(fact, dict):
            continue
        url = research_agent.source_url(fact.get("url"))
        proof = sources.get(research_agent.source_key(url)) if url else None
        if not proof:
            continue
        host = buyers.domain_name(proof[0])
        if domain and host != domain and not host.endswith("." + domain):
            continue
        if not domain:
            name = re.sub(r"[\W_]+", "", company.casefold())
            source_name = re.sub(r"[\W_]+", "", (host + " " + proof[1]).casefold())
            if len(name) < 3 or name not in source_name:
                continue
        label, text = agent_scoring.clean(fact.get("label"), 32), agent_scoring.clean(fact.get("text"), 450)
        if label and text:
            facts.append({"label": label, "text": text, "url": proof[0]})
    if not facts:
        return {"message": "本次没有找到可对应同一企业的公开来源。"}
    db = get_db()
    with db:
        db.execute("BEGIN IMMEDIATE")
        current = inquiry_row(row["id"]) if scope == "inquiry" else buyer_row(row["id"])
        if current is None or background_context(scope, current) != context:
            raise ValueError("企业资料已更新，请重新抓取背调。")
        agent_scoring.save(scope, row["id"], "background", context,
                           {"facts": facts, "checked_at": datetime.now(timezone.utc).isoformat()})
    return {"message": f"已保存 {len(facts)} 条公开背调依据。"}


def meeting_context(row, base):
    return score_context("inquiry", row) | {
        "已核对资料": {label: agent_scoring.clean(fact) for label, fact in base["facts"].items()},
        "公开背调": [fact["text"] for fact in (background_data("inquiry", row) or {}).get("facts", [])],
        "会前准备建议": [agent_scoring.clean(item, 250) for item in base["questions"]],
    }


def meeting_card(row):
    base = lead_research.build_dossier(row)
    context = meeting_context(row, base)
    existing, _ = agent_scoring.stored("inquiry", row["id"], "meeting", context)
    return {**base, **(existing or {}), "engine": "agent" if existing else "rule"}


def prepare_meeting(row):
    if not research_agent.settings()["configured"]:
        lead_research.prepare_dossier(row)
        return {"message": "已生成中文准备卡"}
    base = lead_research.build_dossier(row)
    context = meeting_context(row, base)
    result, _ = research_agent.ask(MEETING, context)
    goal, questions = result.get("goal"), result.get("questions")
    if not isinstance(goal, str) or not isinstance(questions, list) or not (
        3 <= len(questions) <= 5 and all(isinstance(item, str) and re.search(r"[\u4e00-\u9fff]", item) for item in questions)
        and re.search(r"[\u4e00-\u9fff]", goal)
    ):
        raise ValueError("AI 未返回完整的中文会面计划。")
    saved = {"goal": agent_scoring.clean(goal, 300), "questions": [agent_scoring.clean(item, 280) for item in questions]}
    db = get_db()
    with db:
        db.execute("BEGIN IMMEDIATE")
        current = inquiry_row(row["id"])
        if current is None or meeting_context(current, lead_research.build_dossier(current)) != context:
            raise ValueError("询盘资料已更新，请重新生成准备卡。")
        agent_scoring.save("inquiry", row["id"], "meeting", context, saved)
        db.execute("UPDATE lead_inquiries SET dossier=? WHERE id=?",
                   (json.dumps(base, ensure_ascii=False), row["id"]))
    return {"message": "AI 会面准备卡已更新"}
