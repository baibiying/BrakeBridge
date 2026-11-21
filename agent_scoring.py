import hashlib
import json
import re
from datetime import datetime, timezone

import buyer_settings
from storage import get_db

DEFAULTS = {"rule": 50, "ai": 50}
SCOPES = {"buyer", "inquiry"}
CONTACT = re.compile(
    r"https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b(?:\d{1,3}\.){3}\d{1,3}\b|"
    r"(?<![\w.])\+?\d[\d ().-]{6,}\d(?!\w)", re.I)


def clean(value, limit=1400):
    return CONTACT.sub("[联系方式]", str(value or ""))[:limit].strip()


def weights(scope):
    return buyer_settings.weights("score_blend:" + scope, DEFAULTS)


def save_weights(scope, values):
    buyer_settings.validate(values, DEFAULTS)
    with get_db():
        buyer_settings.save(values, "score_blend:" + scope)


def fingerprint(context):
    return hashlib.sha256(json.dumps(context, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def stored(scope, record_id, task, context):
    entry = get_db().execute("""SELECT fingerprint,data FROM sales_agent_results
                               WHERE scope=? AND record_id=? AND task=?""", (scope, record_id, task)).fetchone()
    if not entry:
        return None, False
    if entry["fingerprint"] != fingerprint(context):
        return None, True
    return json.loads(entry["data"]), False


def save(scope, record_id, task, context, result):
    get_db().execute("""INSERT INTO sales_agent_results(scope,record_id,task,fingerprint,data)
                        VALUES(?,?,?,?,?) ON CONFLICT(scope,record_id,task) DO UPDATE SET
                        fingerprint=excluded.fingerprint,data=excluded.data""",
                     (scope, record_id, task, fingerprint(context), json.dumps(result, ensure_ascii=False)))


def buyer_context(buyer, evidence):
    return {
        "市场": clean(buyer["country"], 120),
        "业务简介": clean(buyer["description"], 1200),
        "已有商务邮箱": bool(buyer["contact_email"]),
        "客户来源": clean(buyer["customer_source"], 100),
        "证据": [{"类型": item["source_type"], "文字": clean(item["source_text"])}
                 for item in evidence[-8:] if item["source_text"]],
    }


def inquiry_context(text, fields, identity):
    return {"来信": clean(text, 5500), "市场": clean(identity.get("market") or fields.get("address"), 120),
            "已记录企业主体": bool(identity or fields.get("company"))}


def combine(rule, scope, record_id, context, distribution=None):
    config = distribution if distribution is not None else weights(scope)
    ai, stale = stored(scope, record_id, "score", context)
    baseline = rule["total"]
    if config["ai"] == 0:
        total, state = baseline, "rule"
    elif ai:
        total, state = (baseline * config["rule"] + ai["score"] * config["ai"]) / 100, "combined"
    elif config["rule"]:
        total, state = baseline, "provisional"
    else:
        total, state = None, "pending"
    return {**rule, "total": total, "mix": {"rule": baseline, "ai": ai["score"] if ai else None,
            "weights": config, "state": state, "stale": stale,
            "reason": ai["reason"] if ai else "", "checked_at": ai["checked_at"] if ai else "",
            "source": ai.get("source", "agent") if ai else ""}}


def validate_score(response):
    value = response.get("score")
    explanation = response.get("reason")
    reason = clean(explanation, 400) if isinstance(explanation, str) else ""
    if type(value) is not int or not 0 <= value <= 100 or not reason:
        raise ValueError("AI 未返回有效的 0 至 100 分及理由。")
    return {"score": value, "reason": reason, "checked_at": datetime.now(timezone.utc).isoformat()}
