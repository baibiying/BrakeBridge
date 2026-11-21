import json
import sqlite3
from contextlib import closing
from hashlib import sha256
from pathlib import Path

import agent_scoring
import lead_inquiries
import lead_records
import social_assets
from storage import get_db, write_transaction

BUNDLE = Path(__file__).parent / "data" / "company-seed.sqlite3"
FORMAT_VERSION = 1


def install(path, event_date):
    if not path.is_file():
        raise ValueError("找不到企业初始数据库。")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as seed:
        seed.execute("PRAGMA trusted_schema=OFF")
        if seed.execute("PRAGMA user_version").fetchone()[0] != FORMAT_VERSION:
            raise ValueError("企业初始数据库格式不兼容。")
        inquiries = [(line, json.loads(fields)) for line, fields in
                     seed.execute("SELECT line,fields FROM inquiries ORDER BY line")]
        photos = seed.execute("SELECT title,category,model,image FROM photos ORDER BY id").fetchall()
        scores = seed.execute("SELECT vendor_key,fingerprint,data,identity FROM inquiry_scores").fetchall()

    db = get_db()
    with write_transaction(db):
        identities = {key: identity for key, _, _, identity in scores if identity}
        report = lead_records.import_export(inquiries, "authorized", event_date, identities=identities)
        if report["errors"]:
            raise ValueError("；".join(report["errors"]))
        added_photos = 0
        for title, category, model, image in photos:
            digest = sha256(image).hexdigest()
            if db.execute("SELECT 1 FROM social_assets WHERE digest=?", (digest,)).fetchone():
                continue
            social_assets.store(title, category, model, image)
            added_photos += 1
        added_scores = restore_scores(db, scores)
    return {"inquiries": report["inserted"], "retained": report["updated"] + report["duplicate"] + report["deleted"],
            "photos": added_photos, "scores": added_scores}


def restore_scores(db, scores):
    rows = {row["vendor_key"]: row for row in db.execute(
        """SELECT i.* FROM lead_inquiries i WHERE i.source='hqsmartcloud' AND i.data_kind='authorized'
           AND i.vendor_key<>'' AND i.deleted_at='' AND NOT EXISTS
               (SELECT 1 FROM sales_agent_results r WHERE r.scope='inquiry' AND r.record_id=i.id AND r.task='score')""")}
    added = 0
    for key, expected_fingerprint, payload, _ in scores:
        row = rows.get(key)
        if row is None:
            continue
        context = lead_inquiries.lead_score_context(row)
        if agent_scoring.fingerprint(context) != expected_fingerprint:
            continue
        original = json.loads(payload)
        value = agent_scoring.validate_score(original)
        value.update(checked_at=original["checked_at"], source=original.get("source", "agent"))
        agent_scoring.save("inquiry", row["id"], "score", context, value)
        added += 1
    return added
