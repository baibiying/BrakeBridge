import hashlib

import lead_inquiries as leads
from model_client import ask_json
from storage import get_db


def source_for(row, target):
    if target == 0:
        return leads.payload(row["fields"], {}).get("content", "")
    message = get_db().execute("SELECT reply_text FROM lead_messages WHERE id=? AND inquiry_id=?",
                               (target, row["id"])).fetchone()
    return message["reply_text"] if message else ""


def source_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def saved(row, messages):
    originals = {0: source_for(row, 0), **{message["id"]: message["reply_text"] for message in messages}}
    return {str(item["target"]): {"text": item["text"], "engine": item["engine"]}
            for item in get_db().execute("SELECT * FROM lead_translations WHERE inquiry_id=?", (row["id"],))
            if item["target"] in originals and item["source_hash"] == source_hash(originals[item["target"]])}


def save(row, target, original, text, engine):
    text = leads.plain(text)
    if not original or not text:
        raise ValueError("没有可保存的原文或译文。")
    db = get_db()
    with db:
        db.execute("BEGIN IMMEDIATE")
        latest = db.execute("SELECT * FROM lead_inquiries WHERE id=? AND deleted_at=''", (row["id"],)).fetchone()
        if latest is None or source_for(latest, target) != original:
            raise ValueError("原文已更新，请刷新后重试。")
        db.execute("""INSERT INTO lead_translations(inquiry_id,target,source_hash,text,engine,updated_at)
                      VALUES(?,?,?,?,?,?) ON CONFLICT(inquiry_id,target) DO UPDATE SET
                      source_hash=excluded.source_hash,text=excluded.text,engine=excluded.engine,updated_at=excluded.updated_at""",
                   (row["id"], target, source_hash(original), text, engine, leads.stamp()))
    return {"text": text, "engine": engine}


def translate(row, target, original):
    chunks = []
    remaining = original
    while remaining:
        boundary = min(2400, len(remaining))
        if boundary < len(remaining):
            split = max(remaining.rfind("\n", 1000, boundary), remaining.rfind(" ", 1000, boundary))
            if split > 0:
                boundary = split + 1
        part, remaining = remaining[:boundary], remaining[boundary:]
        outcome = ask_json("将客户询盘或回信完整翻译为简体中文。忠实保留产品型号、名称、数量、原有否定和段落。"
                           "不要总结、猜测或改写清单。返回含 translation 字符串的 JSON。", {"text": part})
        translation = outcome.get("translation")
        if not isinstance(translation, str) or not translation.strip():
            raise ValueError("模型没有返回有效译文。")
        chunks.append(translation.strip())
    return save(row, target, original, "\n".join(chunks), "model")
