import json
import time
from datetime import datetime
from pathlib import Path

import click
from flask import abort, current_app, request
from werkzeug.datastructures import FileStorage

import buyer_settings
import lead_connectors as connectors
import lead_inquiries as leads
import lead_records as records
import lead_research as research
import lead_translations as translations
import local_translation
import research_agent
import workspace
from model_client import ModelUnavailable, available as model_available
from storage import get_db
from tables import rows_from_upload


def record(inquiry_id, include_deleted=False):
    row = get_db().execute("SELECT * FROM lead_inquiries WHERE id=?", (inquiry_id,)).fetchone()
    if (row is None or (row["deleted_at"] and not include_deleted) or
            (row["data_kind"] != "authorized" and row["source"] != "social"
             and not current_app.config["LEADS_ALLOW_SYNTHETIC"])):
        abort(404)
    return row


def brief(row):
    fields = row["fields"]
    declared = (row["public_research"] or {}).get("declared_company")
    return {key: row[key] for key in ("id", "source", "external_id", "data_kind", "owner", "status",
                                      "received_at", "analysis", "meeting_at", "next_action", "buyer_score", "deleted_at",
                                      "candidate_count")} | {
        "score": row["score"]["total"],
        "company": (row["identity"] or {}).get("company") or fields.get("company") or (f"{declared}（自述）" if declared else "") or fields.get("name") or "企业待核",
        "title": fields.get("title") or "",
        "origin": fields.get("source") or "",
        "country": fields.get("address") or "",
    }


def register(app, event_for):
    def event():
        configured = event_for(app)
        return {**configured, "date": configured["date"] or workspace.DEMO_FALLBACK}

    def imported(rows, kind, expected=None, export=False):
        counts = (records.import_export if export else leads.import_vendor)(rows, kind, event()["date"])
        report = {**counts, "checked_at": leads.stamp(), "expected": expected if expected is not None else counts["total"],
                  "source": kind}
        report["missing"] = max(0, report["expected"] - sum(report.get(key, 0)
                                for key in ("inserted", "updated", "duplicate", "deleted")))
        connectors.store_setting("lead_vendor_last", report)
        return report

    @app.route("/api/settings/inquiry-scoring", methods=["GET", "POST"])
    def inquiry_scoring_settings():
        if request.method == "POST":
            try:
                weights = buyer_settings.validate(request.get_json(silent=True), leads.WEIGHT_DEFAULTS)
            except ValueError as error:
                return workspace.error(str(error))
            with get_db():
                buyer_settings.save(weights, leads.WEIGHTS_KEY)
        return {"weights": leads.inquiry_weights(), "defaults": leads.WEIGHT_DEFAULTS,
                "dimensions": leads.WEIGHT_DIMENSIONS}

    @app.route("/api/settings/competitor-agent", methods=["GET", "POST"])
    def competitor_agent_settings():
        if request.method == "GET":
            return research_agent.settings()
        try:
            return research_agent.save_settings(request.get_json(silent=True))
        except ValueError as error:
            return workspace.error(str(error))

    @app.get("/api/lead-inquiries")
    def lead_list():
        rows = leads.listing(deleted=request.args.get("deleted") == "1", sort=request.args.get("sort", "priority"))
        q = request.args.get("q", "").strip().casefold()
        tier = request.args.get("tier")
        kind = request.args.get("kind")
        search = ("title", "company", "name", "mail", "address", "number")
        visible = [row for row in rows if (not kind or kind == row["data_kind"])
                   and (not tier or tier == row["analysis"]["tier"])
                   and (not q or any(q in row["fields"].get(key, "").casefold() for key in search)
                        or q in brief(row)["company"].casefold())]
        return {"items": [brief(row) for row in visible], "priorities": leads.PRIORITIES, "states": leads.LEAD_STATES,
                "relationships": research.CANDIDATE_LABELS, "fields": leads.FIELDS, "export_fields": records.EXPORT_FIELDS}

    @app.get("/api/lead-inquiries/expo")
    def lead_expo():
        rows = leads.listing()
        kind = request.args.get("kind")
        if kind:
            rows = [row for row in rows if row["data_kind"] == kind]
        shortlist = leads.event_shortlist(rows)
        groups = {key: [brief(row) for row in items] for key, items in shortlist.items()}
        return {"date": event()["date"], "groups": groups, "priorities": leads.PRIORITIES}

    @app.get("/api/lead-inquiries/status")
    def lead_status():
        db = get_db()
        snapshots = [dict(row) for row in db.execute("""SELECT id,state,receipt,updated_at FROM lead_notifications
                                                        ORDER BY id DESC LIMIT 12""")]
        return {"mailbox_ready": connectors.mailbox_ready(), "wecom_ready": bool(connectors.webhook()),
                "translation_ready": model_available(),
                "translation_local": local_translation.available(),
                "imap": connectors.setting("lead_imap_last", None),
                "vendor": connectors.setting("lead_vendor_last", None), "notifications": snapshots}

    @app.post("/api/lead-inquiries/sync")
    def lead_mail_sync():
        try:
            return connectors.sync_mail(event()["date"])
        except ValueError as error:
            return workspace.error(str(error))

    @app.post("/api/lead-inquiries/import")
    def lead_file_import():
        file = request.files.get("file")
        kind = request.form.get("data_kind", "authorized")
        if file is None or not file.filename:
            return workspace.error("请选择文件。")
        if kind != "authorized" and not (kind == "synthetic" and app.config["LEADS_ALLOW_SYNTHETIC"]):
            return workspace.error("请选择资料类型。")
        try:
            extension = Path(file.filename).suffix.lower()
            if extension == ".eml":
                mail = leads.parse_email(file.read())
                return leads.import_email(mail, kind, event()["date"], alert=False)
            if extension == ".json":
                rows = json.load(file)
                if not isinstance(rows, list) or len(rows) > 1000:
                    raise ValueError("历史 JSON 应为最多 1,000 条的记录列表。")
                return imported(rows, kind)
            return imported(rows_from_upload(file, (label.lower() for label in records.EXPORT_FIELDS.values()),
                                             excel=True), kind, export=True)
        except (UnicodeError, ValueError) as error:
            return workspace.error(str(error))

    @app.get("/api/lead-inquiries/<int:inquiry_id>")
    def lead_detail(inquiry_id):
        row = record(inquiry_id)
        info = leads.lead_view(row, leads.buyer_scores())
        dossier = info["dossier"]
        if (dossier and dossier.get("version") != 2) or (not dossier and info["public_research"]):
            info["dossier"] = research.build_dossier(row)
        messages = [{**dict(item), "attachments": json.loads(item["attachments"]), "analysis": json.loads(item["analysis"])}
                    for item in get_db().execute("""SELECT * FROM lead_messages WHERE inquiry_id=?
                                                   ORDER BY received_at DESC,id DESC""", (inquiry_id,))]
        scope = "data_kind='synthetic'" if row["data_kind"] == "synthetic" else "data_kind<>'synthetic'"
        companies = [dict(item) for item in get_db().execute(f"SELECT id,company FROM buyers WHERE {scope} ORDER BY company")]
        sources = {str(item["id"]): translations.source_hash(item["reply_text"]) for item in messages}
        sources["0"] = translations.source_hash(info["fields"]["content"])
        return {"inquiry": info, "messages": messages, "candidates": research.candidates(inquiry_id), "buyers": companies,
                "export_values": records.export_values(info["fields"]),
                "research_agent": research_agent.status(row),
                "translations": translations.saved(row, messages), "translation_sources": sources}

    @app.post("/api/lead-inquiries")
    def lead_create():
        try:
            return {"id": records.save(request.form, event()["date"])}
        except ValueError as error:
            return workspace.error(str(error))

    @app.post("/api/lead-inquiries/<int:inquiry_id>/edit")
    def lead_edit(inquiry_id):
        try:
            return {"id": records.save(request.form, event()["date"], record(inquiry_id))}
        except ValueError as error:
            return workspace.error(str(error))

    @app.post("/api/lead-inquiries/<int:inquiry_id>/delete")
    def lead_delete(inquiry_id):
        records.set_deleted(record(inquiry_id), True)
        return {"ok": True}

    @app.post("/api/lead-inquiries/<int:inquiry_id>/restore")
    def lead_restore(inquiry_id):
        records.set_deleted(record(inquiry_id, include_deleted=True), False)
        return {"ok": True}

    @app.post("/api/lead-inquiries/<int:inquiry_id>/translate")
    def lead_translate(inquiry_id):
        row = record(inquiry_id)
        target = request.form.get("target", type=int)
        mode = request.form.get("mode")
        if target is None or target < 0 or mode not in {"model", "browser", "manual", "local"}:
            return workspace.error("请选择要翻译的原文。")
        original = translations.source_for(row, target)
        if not original or request.form.get("source_hash") != translations.source_hash(original):
            return workspace.error("原文已更新或为空，请刷新后重试。")
        try:
            if mode == "model":
                return translations.translate(row, target, original)
            if mode == "local":
                return translations.save(row, target, original, local_translation.translate(original), mode)
            return translations.save(row, target, original, request.form.get("text"), mode)
        except (ValueError, ModelUnavailable) as error:
            return workspace.error(str(error))

    @app.post("/api/lead-inquiries/<int:inquiry_id>/follow")
    def lead_follow(inquiry_id):
        row = record(inquiry_id)
        data = request.form
        status = data.get("status", row["status"])
        if status not in leads.LEAD_STATES:
            return workspace.error("请选择跟进状态。")
        meeting = leads.plain(data.get("meeting_at", row["meeting_at"]), 60)
        if meeting:
            stamped = leads.date_value(meeting)
            if not stamped:
                return workspace.error("请选择可用的会面日期时间。")
            meeting = datetime.fromisoformat(stamped).astimezone(leads.ZONE).isoformat(timespec="minutes")
        buyer_id = data.get("buyer_id", type=int) if "buyer_id" in data else row["buyer_id"]
        db = get_db()
        if buyer_id:
            target = db.execute("SELECT data_kind FROM buyers WHERE id=?", (buyer_id,)).fetchone()
            if not target or (target["data_kind"] == "synthetic") != (row["data_kind"] == "synthetic"):
                return workspace.error("请选择同资料范围内的客户。")
        with db:
            db.execute("UPDATE lead_inquiries SET owner=?,status=?,meeting_at=?,next_action=?,buyer_id=? WHERE id=?",
                       (leads.plain(data.get("owner", row["owner"]), 120), status, meeting,
                        leads.plain(data.get("next_action", row["next_action"]), 500), buyer_id, inquiry_id))
        return {"ok": True}

    @app.post("/api/lead-inquiries/<int:inquiry_id>/dossier")
    def lead_dossier(inquiry_id):
        return research.prepare_dossier(record(inquiry_id))

    @app.post("/api/lead-inquiries/<int:inquiry_id>/identity")
    def lead_company_identity(inquiry_id):
        try:
            return research.save_identity(record(inquiry_id), request.form)
        except ValueError as error:
            return workspace.error(str(error))

    @app.post("/api/lead-inquiries/<int:inquiry_id>/candidates")
    def lead_candidate(inquiry_id):
        row = record(inquiry_id)
        if request.form.get("mode") == "discover":
            return research.discover(row)
        try:
            item = research.validate_candidate(request.form)
        except ValueError as error:
            return workspace.error(str(error))
        return {"added": research.add_candidate(inquiry_id, item)}

    @app.post("/api/lead-inquiries/<int:inquiry_id>/candidates/refresh")
    def lead_candidate_refresh(inquiry_id):
        return research_agent.refresh(record(inquiry_id), automatic=request.form.get("mode") == "auto")

    @app.post("/api/lead-inquiries/<int:inquiry_id>/candidates/<int:candidate_id>/<action>")
    def lead_candidate_action(inquiry_id, candidate_id, action):
        row = record(inquiry_id)
        db = get_db()
        candidate = db.execute("SELECT * FROM lead_candidates WHERE inquiry_id=? AND id=?", (inquiry_id, candidate_id)).fetchone()
        if candidate is None:
            abort(404)
        if action == "verify":
            item = research.loads_candidate(candidate)
            if row["data_kind"] != "synthetic" and not leads.http_link(item.get("source", "")):
                return workspace.error("请为实际候选补入可复核的公开来源。")
            if item["procurement"] in {"", "待核"} and item["channel"] in {"", "待核"}:
                return workspace.error("请核对候选的渠道或采购角色。")
            with db:
                db.execute("UPDATE lead_candidates SET verification='verified',checked_at=? WHERE id=?",
                           (leads.stamp(), candidate_id))
            return {"ok": True}
        if action == "edit":
            try:
                item = research.validate_candidate({**research.loads_candidate(candidate), **request.form})
            except ValueError as error:
                return workspace.error(str(error))
            with db:
                db.execute("UPDATE lead_candidates SET candidate=?,verification='pending',checked_at='' WHERE id=?",
                           (json.dumps(item, ensure_ascii=False), candidate_id))
            return {"ok": True}
        if action == "follow":
            try:
                return research.follow(row, candidate, event())
            except ValueError as error:
                return workspace.error(str(error))
        abort(404)

    @app.post("/api/lead-inquiries/notifications/<int:notice_id>/retry")
    def retry_lead_notice(notice_id):
        db = get_db()
        with db:
            changed = db.execute("""UPDATE lead_notifications SET state='pending',receipt='',updated_at=?
                                    WHERE id=? AND state='failed' AND NOT EXISTS (
                                        SELECT 1 FROM lead_inquiries i
                                        WHERE i.id=lead_notifications.inquiry_id AND i.deleted_at<>''
                                    )""", (leads.stamp(), notice_id)).rowcount
        return {"ok": True} if changed else workspace.error("仅可重试已明确失败的提醒。")

    @app.cli.command("import-inquiries")
    @click.argument("filename", type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @click.option("--kind", type=click.Choice(["synthetic", "authorized"]), default="authorized")
    @click.option("--expected", type=click.IntRange(min=1))
    def import_inquiries(filename, kind, expected):
        if kind != "authorized" and not app.config["LEADS_ALLOW_SYNTHETIC"]:
            raise click.ClickException("此环境仅接收实际询盘。")
        if filename.suffix.lower() in {".xlsx", ".csv"}:
            try:
                with filename.open("rb") as source:
                    rows = rows_from_upload(FileStorage(source, filename=filename.name),
                                            (label.lower() for label in records.EXPORT_FIELDS.values()), excel=True)
            except ValueError as error:
                raise click.ClickException(str(error)) from error
            click.echo(json.dumps(imported(rows, kind, expected, export=True), ensure_ascii=False))
            return
        with filename.open(encoding="utf-8-sig") as source:
            rows = json.load(source)
        if not isinstance(rows, list):
            raise click.ClickException("JSON 应为询盘记录列表。")
        click.echo(json.dumps(imported(rows, kind, expected), ensure_ascii=False))

    @app.cli.command("inquiry-worker")
    @click.option("--once", is_flag=True)
    def inquiry_worker(once):
        if not connectors.mailbox_ready() and not connectors.webhook():
            raise click.ClickException("请先设置邮箱或企业微信连接。")
        while True:
            if connectors.mailbox_ready():
                mail = connectors.sync_mail(event()["date"])
                click.echo("邮箱同步：" + ("失败" if mail.get("error") else f"新增 {mail['inserted']} 条"))
            connectors.queue_digest()
            sent = connectors.process_notifications()
            if sent:
                click.echo(f"已发送 {sent} 条企微提醒")
            if once:
                break
            time.sleep(60)
