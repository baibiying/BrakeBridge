import hashlib
import json
from uuid import uuid4

import lead_inquiries as leads
from storage import get_db

EXPORT_FIELDS = {
    "name": "姓名", "content": "内容", "phone": "手机", "mail": "邮箱", "title": "标题",
    "ip": "ip", "address": "地区", "added_at": "添加时间", "source": "来源页面",
    "page": "页面地址", "first_page": "首次访问", "assignee": "分配员工", "device": "设备",
}
META_FIELDS = {"assignee", "device"}


def export_values(fields):
    return {key: fields.get("meta", {}).get(label, "") if key in META_FIELDS else fields.get(key, "")
            for key, label in EXPORT_FIELDS.items()}


def fingerprint(fields):
    added = leads.date_value(fields.get("added_at") or fields.get("addtime"))
    values = [added, fields["mail"].casefold(), fields["title"], fields["name"],
              " ".join(fields["content"].split())]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest() if added else ""


def merge_export(original, values):
    fields = {**original, **{key: value for key, value in values.items() if key not in META_FIELDS}}
    fields["meta"] = {**original["meta"], **{EXPORT_FIELDS[key]: values[key] for key in META_FIELDS if key in values}}
    return fields


def store_fields(row, fields, event_date):
    db = get_db()
    sender = leads.buyers.contact_email(fields["mail"]).casefold()
    buyer_id = (row["buyer_id"] if row["sender"] == sender else None) or leads.buyer_match(sender, row["data_kind"])
    received = leads.date_value(fields["added_at"] or fields["addtime"]) or row["received_at"]
    reply = db.execute("""SELECT 1 FROM lead_messages WHERE inquiry_id=?
                          AND json_extract(analysis,'$.tier')<>'archive' LIMIT 1""", (row["id"],)).fetchone()
    analysis = leads.payload(row["analysis"], {}) if reply else leads.intent(
        leads.current_reply(fields["content"]), event_date=event_date, known_product=leads.buyer_product(buyer_id))
    next_action = analysis["next_action"] if row["next_action"] == leads.payload(row["analysis"], {}).get("next_action") else row["next_action"]
    db.execute("""UPDATE lead_inquiries SET fields=?,sender=?,buyer_id=?,received_at=?,analysis=?,next_action=?,
                  dossier=CASE WHEN json_extract(fields,'$.content')<>? THEN '' ELSE dossier END
                  WHERE id=?""", (json.dumps(fields, ensure_ascii=False), sender, buyer_id,
                                  row["received_at"] if reply else received, json.dumps(analysis, ensure_ascii=False),
                                  next_action, fields["content"], row["id"]))


def import_export(indexed_rows, kind, event_date):
    db = get_db()
    counts = {"total": len(indexed_rows), "inserted": 0, "updated": 0, "duplicate": 0, "deleted": 0, "errors": []}
    with db:
        db.execute("BEGIN IMMEDIATE")
        known = {}
        ambiguous = set()
        for row in db.execute("SELECT * FROM lead_inquiries WHERE source='hqsmartcloud' AND data_kind=?", (kind,)):
            key = row["vendor_key"] or fingerprint(leads.payload(row["fields"], {}))
            if key in known:
                ambiguous.add(key)
            elif key:
                known[key] = row
        for line, record in indexed_rows:
            values = {key: leads.plain(record.get(label.lower()), leads.MAX_BODY if key == "content" else 1500)
                      for key, label in EXPORT_FIELDS.items()}
            fields = merge_export(leads.vendor_fields({}), values)
            key = fingerprint(fields)
            if not key or not any(fields[key] for key in ("content", "mail", "title", "name")):
                counts["errors"].append(f"第 {line} 行需有有效的添加时间和可识别的询盘内容。")
                continue
            if key in ambiguous:
                counts["errors"].append(f"第 {line} 行匹配多条历史询盘，需要人工核对。")
                continue
            previous = known.get(key)
            if previous is not None:
                if not previous["vendor_key"]:
                    db.execute("UPDATE lead_inquiries SET vendor_key=? WHERE id=?", (key, previous["id"]))
                    if not previous["edited_at"] and not previous["deleted_at"]:
                        merged = merge_export(leads.payload(previous["fields"], {}), values)
                        store_fields(previous, merged, event_date)
                        counts["updated"] += 1
                        known[key] = db.execute("SELECT * FROM lead_inquiries WHERE id=?", (previous["id"],)).fetchone()
                        continue
                counts["deleted" if previous["deleted_at"] else "duplicate"] += 1
                continue
            fields["number"] = "HQ-" + key[:20]
            sender = leads.buyers.contact_email(fields["mail"]).casefold()
            buyer_id = leads.buyer_match(sender, kind)
            analysis = leads.intent(leads.current_reply(fields["content"]), event_date=event_date, known_product=leads.buyer_product(buyer_id))
            inserted = db.execute("""INSERT INTO lead_inquiries(source,external_id,data_kind,fields,sender,buyer_id,
                                      received_at,analysis,meeting_at,next_action,vendor_key)
                                      VALUES ('hqsmartcloud',?,?,?,?,?,?,?,?,?,?)""",
                                  (fields["number"], kind, json.dumps(fields, ensure_ascii=False), sender, buyer_id,
                                   leads.date_value(fields["added_at"]), json.dumps(analysis, ensure_ascii=False),
                                   analysis["meeting_at"], analysis["next_action"], key))
            known[key] = db.execute("SELECT * FROM lead_inquiries WHERE id=?", (inserted.lastrowid,)).fetchone()
            counts["inserted"] += 1
    return counts


def save(values, event_date, row=None):
    original = leads.payload(row["fields"], {}) if row else leads.vendor_fields({})
    updates = {key: leads.plain(values.get(key), leads.MAX_BODY if key == "content" else 1500)
               for key in EXPORT_FIELDS if key in values}
    fields = merge_export(original, updates)
    if not fields["content"] and not (row and original["content"] == fields["content"]):
        raise ValueError("请填写询盘内容。")
    if fields["added_at"] and not leads.date_value(fields["added_at"]):
        raise ValueError("添加时间请填写有效日期和时间。")
    db = get_db()
    with db:
        if row:
            # Remember the imported identity before changing an original field.
            key = row["vendor_key"] or (fingerprint(original) if row["source"] == "hqsmartcloud" else "")
            store_fields(row, fields, event_date)
            db.execute("UPDATE lead_inquiries SET edited_at=?,vendor_key=? WHERE id=?", (leads.stamp(), key, row["id"]))
            return row["id"]
        now = leads.stamp()
        fields["added_at"] = fields["added_at"] or leads.date_value(now)
        fields["number"] = "MAN-" + uuid4().hex[:20]
        sender = leads.buyers.contact_email(fields["mail"]).casefold()
        buyer_id = leads.buyer_match(sender, "authorized")
        analysis = leads.intent(leads.current_reply(fields["content"]), event_date=event_date, known_product=leads.buyer_product(buyer_id))
        return db.execute("""INSERT INTO lead_inquiries(source,external_id,data_kind,fields,sender,buyer_id,
                             received_at,analysis,meeting_at,next_action,edited_at)
                             VALUES ('manual',?,'authorized',?,?,?,?,?,?,?,?)""",
                          (fields["number"], json.dumps(fields, ensure_ascii=False), sender, buyer_id,
                           leads.date_value(fields["added_at"]), json.dumps(analysis, ensure_ascii=False),
                           analysis["meeting_at"], analysis["next_action"], now)).lastrowid


def set_deleted(row, deleted):
    db = get_db()
    now = leads.stamp()
    with db:
        db.execute("UPDATE lead_inquiries SET deleted_at=? WHERE id=?", (now if deleted else "", row["id"]))
        if deleted:
            db.execute("""UPDATE lead_notifications SET state='cancelled',receipt='询盘已移入回收站',updated_at=?
                          WHERE inquiry_id=? AND state IN ('pending','failed')""", (now, row["id"]))
