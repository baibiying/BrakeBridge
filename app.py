import csv
import hmac
import json
import os
import secrets
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import click
from flask import Flask, Response, abort, current_app, flash, get_flashed_messages, jsonify, redirect, render_template, request, send_from_directory, url_for
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.exceptions import HTTPException

import agent_scoring
import buyers
import buyer_settings
import company_seed
from comment_desk import bp as comment_desk
import facebook
import facebook_replies
import inquiries
import lead_routes
import lead_inquiries
import research_agent
import sales_agent
import social_routes
from features.fitment import fitment_bp
from features.inquiry import inquiry_bp
from features.sourcing import sourcing_bp
import workspace
from forms import BuyerImportForm, CommentForm, CommentImportForm
from model_client import ModelUnavailable, ask_json, available as model_available
from storage import default_path, get_db, init_app
from tables import csv_bytes, normalize, rows_from_upload

DATA = Path(__file__).parent / "data"
KINDS = {"synthetic": "演示数据", "public": "公开资料", "authorized": "授权资料"}
BUYER_STATES = {"new": "待联系", "contacted": "已联系", "waiting": "等待回复", "done": "已跟进"}
FINDING_LABELS = {"supported": "有依据", "declared": "客户自述", "excluded": "明确不符", "conflict": "证据待复核"}
TRANSPORTS = {"manual": "人工录入", "csv": "CSV 导入", "graph": "Facebook 同步", "webhook": "Facebook 实时推送"}
FIELD_LABELS = {"product": "产品", "code": "产品编号", "quantity": "数量", "unit": "单位",
                "position": "位置", "width_mm": "宽度 (mm)", "holes": "孔数", "company": "自述公司",
                "country": "自述国家", "axle": "车桥", "material": "材质", "destination": "目的地"}


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("BRAKEBRIDGE_SECRET") or secrets.token_hex(32),
        DATABASE=os.getenv("BRAKEBRIDGE_DB") or default_path(),
        VUE_DIST=Path(__file__).parent / "frontend" / "dist",
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        WTF_CSRF_FIELD_NAME="csrf",
        FACEBOOK_AUTO_REPLY=os.getenv("FACEBOOK_AUTO_REPLY") == "1",
        SOCIAL_FIGEON_ENABLED=os.getenv("SOCIAL_FIGEON_ENABLED") == "1",
        LEADS_ALLOW_SYNTHETIC=os.getenv("LEADS_ALLOW_SYNTHETIC") == "1",
        LEADS_TRANSLATION_MODEL=os.getenv("LEADS_TRANSLATION_MODEL") or
                                str(Path(default_path()).parent / "translation" / "translate-en_zh-1_9"),
    )
    for key in ("B1X_BASE_URL", "B1X_MODEL", "B1X_API_KEY", "FACEBOOK_PAGE_ID", "FACEBOOK_POST_ID",
                "FACEBOOK_PAGE_TOKEN", "FACEBOOK_APP_SECRET", "FACEBOOK_VERIFY_TOKEN",
                "EVENT_NAME", "EVENT_DATE", "EVENT_VENUE", "EVENT_BOOTH", "SOCIAL_TOKEN_KEY",
                "SOCIAL_NEWS_FEEDS", "SOCIAL_FIGEON_USER", "SOCIAL_FIGEON_PASSWORD", "SOCIAL_FIGEON_CHROME"):
        app.config[key] = os.getenv(key, "").strip()
    app.config["FACEBOOK_GRAPH_VERSION"] = os.getenv("FACEBOOK_GRAPH_VERSION", "v27.0")
    app.config["SOCIAL_LINKEDIN_VERSION"] = os.getenv("SOCIAL_LINKEDIN_VERSION", "202611").strip()
    for key in ("LEADS_IMAP_HOST", "LEADS_IMAP_USER", "LEADS_IMAP_PASSWORD", "LEADS_WECOM_WEBHOOK", "LEADS_PUBLIC_URL"):
        app.config[key] = os.getenv(key, "").strip()
    app.config["LEADS_IMAP_FOLDER"] = os.getenv("LEADS_IMAP_FOLDER", "INBOX").strip()
    app.config.update(config or {})
    app.json.sort_keys = False
    init_app(app)
    workspace.register(app)
    register_routes(app)
    lead_routes.register(app, event_details)
    social_routes.register(app, event_details)
    app.register_blueprint(fitment_bp)
    app.register_blueprint(comment_desk)
    app.register_blueprint(inquiry_bp)
    app.register_blueprint(sourcing_bp)
    return app


def register_routes(app):
    csrf = CSRFProtect(app)

    @app.errorhandler(CSRFError)
    def csrf_error(_error):
        if api_request():
            return jsonify(message="表单会话已过期，请刷新后重试。", fields={}, code="csrf_expired"), 400
        return "表单会话已过期，请刷新工作台后重试。", 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        if api_request():
            messages = {404: "记录不存在。", 413: "上传文件不能超过 5 MB。", 405: "不支持该请求。"}
            return workspace.error(messages.get(error.code, "请求失败。"), error.code)
        return error

    @app.context_processor
    def template_data():
        return {"kinds": KINDS, "buyer_states": BUYER_STATES, "axes": buyers.AXES,
                "tiers": buyers.TIER_LABELS, "findings": FINDING_LABELS, "intents": inquiries.LABELS,
                "inbox_states": inquiries.STATES, "transports": TRANSPORTS, "fields_labels": FIELD_LABELS,
                "model_online": model_available(), "safe_link": safe_link, "display_time": display_time,
                "facebook_ready": bool(facebook.page_settings()[0] and app.config["FACEBOOK_PAGE_TOKEN"]),
                "auto_reply_ready": facebook_replies.ready(), "reply_stages": facebook_replies.STAGES,
                "auto_reply_enabled": app.config["FACEBOOK_AUTO_REPLY"]}

    @app.get("/api/meta")
    def meta():
        return {key: value for key, value in template_data().items() if not callable(value)}

    @app.get("/api/dashboard")
    def dashboard_api():
        try:
            history = get_db().execute("SELECT * FROM interactions WHERE channel='facebook'").fetchall()
            return workspace.dashboard(buyer_entries(*buyer_data()), history, request.args)
        except ValueError:
            return workspace.error("请选择不超过 93 天的有效日期范围。")

    @app.route("/api/settings/buyer-scoring", methods=["GET", "POST"])
    def buyer_scoring_settings():
        if request.method == "GET":
            return buyer_settings.describe()
        try:
            weights = buyer_settings.validate(request.get_json(silent=True))
        except ValueError as error:
            return workspace.error(str(error))
        db = get_db()
        with db:
            buyer_settings.save(weights)
            candidates = db.execute("SELECT id FROM buyers WHERE status='new' AND draft=''").fetchall()
            invites = sum(buyers.prepare_invite(row["id"], event_details(app), weights) for row in candidates)
        return {**buyer_settings.describe(), "invites": invites}

    @app.post("/api/event-tasks")
    @app.post("/api/event-tasks/<int:task_id>")
    def event_task(task_id=None):
        return workspace.persist_event(request.form, task_id)

    @app.get("/api/buyers")
    @app.get("/api/buyers/<int:record_id>")
    @app.get("/")
    def buyer_list(record_id=None):
        if not api_request() and vue_built(app):
            if set(request.args) & {"buyer", "q", "tier", "kind", "view"}:
                return redirect("/buyers?" + urlencode(list(request.args.items(multi=True))))
            return redirect("/dashboard")
        query = request.args.get("q", "").strip().casefold()
        tier_filter = request.args.get("tier", "")
        kind_filter = request.args.get("kind", "")
        selected = record_id or request.args.get("buyer", type=int)
        records, sources = buyer_data()
        display = buyer_entries(records, sources)
        matching = [entry for entry in display if (not kind_filter or entry["buyer"]["data_kind"] == kind_filter) and (
            not query or any(query in entry["buyer"][key].casefold() for key in ("company", "country", "domain"))
        )]
        accepted = buyers.FOCUS_TIERS if tier_filter == "focus" else {tier_filter}
        visible = [entry for entry in matching if not tier_filter or entry["tier"] in accepted]
        if api_request() and record_id is None:
            return {"items": [{"buyer": dict(entry["buyer"]), "tier": entry["tier"],
                               "score": entry["score"]["total"], "score_state": entry["score"]["mix"]["state"],
                               "ai_scored": entry["score"]["mix"]["ai"] is not None} for entry in visible],
                    "agent_ready": research_agent.settings()["configured"]}
        if record_id is not None:
            visible = [entry for entry in display if entry["buyer"]["id"] == record_id]
            if not visible:
                abort(404)
        detail = next((entry for entry in visible if entry["buyer"]["id"] == selected), None)
        detail = detail or (visible[0] if visible else None)
        evidence_groups, evidence_refs = group_findings(detail["axes"]) if detail else ([], {})
        similar = buyers.similar(detail["buyer"], records, sources) if detail else []
        linked = get_db().execute(
            "SELECT id, body, status, received_at FROM interactions WHERE buyer_id=? ORDER BY received_at DESC, id DESC",
            (detail["buyer"]["id"],),
        ).fetchall() if detail else []
        if api_request():
            entry = {**detail, "buyer": dict(detail["buyer"]), "evidence": [dict(row) for row in detail["evidence"]]}
            related = [{"id": row["id"], "company": row["company"], "evidence": finding, "procurement": has_purchase}
                       for row, finding, has_purchase in similar]
            return {**entry, "related": related, "linked": [dict(row) for row in linked],
                    "agent_ready": research_agent.settings()["configured"],
                    "agent_background": sales_agent.background_data("buyer", detail["buyer"]),
                    "mailto": invitation_mailto(detail["buyer"], event_details(app))}
        return render_template("buyers.html", entries=visible, all_entries=matching, detail=detail,
                               related=similar, linked=linked, evidence_groups=evidence_groups,
                               evidence_refs=evidence_refs, query=query, tier_filter=tier_filter,
                               kind_filter=kind_filter, follow_tab=request.args.get("view") == "follow",
                               mailto=invitation_mailto(detail["buyer"], event_details(app)) if detail else "")

    @app.post("/api/buyers")
    def create_buyer():
        return persist_profile(request.form)

    @app.post("/api/buyers/import")
    @app.post("/buyers/import")
    def import_buyers():
        form = BuyerImportForm()
        if not form.validate_on_submit():
            flash_form_errors(form)
            return action_result("buyer_list")
        try:
            profiles = api_request() and request.form.get("mode", "profile") != "evidence"
            required = workspace.PROFILE_LABELS if profiles else buyers.REQUIRED
            rows = rows_from_upload(form.file.data, required, excel=True, aliases=workspace.PROFILE_HEADERS)
            skipped = []
            if profiles:
                checked = []
                for line, row in rows:
                    fields, issues = workspace.profile_values(row, required=True)
                    if issues:
                        skipped.append(f"第 {line} 行：{' '.join(issues.values())}")
                    else:
                        checked.append((line, {**row, **fields, "importance": str(fields["importance"])}))
                rows = checked
            counts = buyers.import_rows(rows, form.data_kind.data, event_details(app), allow_profile_only=profiles)
            counts["errors"] = skipped + counts["errors"]
            if api_request():
                return counts
            announce(counts, "条买家证据")
            if counts["invites"]:
                flash(f"已为 {counts['invites']} 家优先买家准备邀请函。")
        except ValueError as error:
            flash(str(error), "error")
        return action_result("buyer_list")

    @app.post("/api/buyers/<int:buyer_id>/<action>")
    @app.post("/buyers/<int:buyer_id>/<action>")
    def buyer_action(buyer_id, action):
        db = get_db()
        buyer = get_record("buyers", buyer_id)
        evidence = db.execute("SELECT * FROM evidence WHERE buyer_id = ? ORDER BY id", (buyer_id,)).fetchall()
        try:
            if action == "profile" and api_request():
                return persist_profile(request.form, buyer)
            if action == "ai-score" and api_request():
                return sales_agent.score("buyer", buyer, force=request.form.get("force") == "1")
            if action == "agent-background" and api_request():
                return sales_agent.collect_background("buyer", buyer)
            if action == "assess":
                analysis = buyers.ai_assess(evidence)
                with db:
                    db.execute("UPDATE buyers SET analysis = ? WHERE id = ?", (json.dumps(analysis, ensure_ascii=False), buyer_id))
                    if buyers.prepare_invite(buyer_id, event_details(app)):
                        flash("该买家已达到优先联系，邀请函已备好。")
                flash("AI 已检查品类证据。")
            elif action == "invite":
                _, axes = buyers.assess(buyer, evidence)
                event = event_details(app)
                draft = buyers.invite(buyer, axes, event)
                draft = polish(draft, event)
                with db:
                    db.execute("UPDATE buyers SET draft = ? WHERE id = ?", (draft, buyer_id))
                flash("邀约草稿已生成。")
            elif action == "save":
                fields = follow_up_fields(BUYER_STATES, buyers.TIER_LABELS, buyer["override"])
                value = request.form.get("contact_email", buyer["contact_email"]).strip()
                email = buyers.contact_email(value)
                if value and not email:
                    raise ValueError("请填写有效的联系邮箱。")
                name = request.form.get("contact_name", buyer["contact_name"]).strip()[:120]
                with db:
                    db.execute("""UPDATE buyers SET owner=?, status=?, follow_up=?, draft=?, override=?, reason=?,
                                  contact_email=?, contact_name=? WHERE id=?""", (*fields, email, name, buyer_id))
                    if request.form.get("override") in buyers.FOCUS_TIERS and buyer["override"] != request.form.get("override"):
                        if buyers.prepare_invite(buyer_id, event_details(app)):
                            flash("邀请函已备好。")
                flash("买家跟进已保存。")
            else:
                abort(404)
        except research_agent.ResearchUnavailable as error:
            return workspace.error(str(error))
        except (ValueError, ModelUnavailable) as error:
            if api_request() and action in {"ai-score", "agent-background"}:
                return workspace.error(str(error))
            flash(str(error), "error")
        return action_result("buyer_list", buyer=buyer_id, view="follow" if action in {"invite", "save"} else None)

    @app.get("/api/buyers/<int:buyer_id>/invitation.txt")
    @app.get("/buyers/<int:buyer_id>/invitation.txt")
    def invitation(buyer_id):
        buyer = get_record("buyers", buyer_id)
        if not buyer["draft"]:
            abort(404)
        content = f"Subject: {invitation_subject(event_details(app))}\n\n{buyer['draft']}\n"
        return Response(content, mimetype="text/plain",
                        headers={"Content-Disposition": f'attachment; filename="invitation-{buyer_id}.txt"'})

    @app.get("/api/inbox")
    @app.get("/api/inbox/<int:record_id>")
    @app.get("/inbox")
    def inbox_list(record_id=None):
        if not api_request() and vue_built(app):
            filters = {item: request.args[item] for item in ("status", "kind", "message") if item in request.args}
            return redirect("/social?" + urlencode({"tab": "comments", "source": "history", **filters}))
        db = get_db()
        rows = db.execute("""SELECT i.*, r.stage AS reply_stage, r.body AS sent_body, r.reply_id,
                              r.note AS reply_note, r.updated_at AS reply_updated
                              FROM interactions i LEFT JOIN facebook_replies r ON i.id=r.interaction_id
                              WHERE i.channel='facebook'
                              ORDER BY i.received_at DESC, i.id DESC""").fetchall()
        status = request.args.get("status", "")
        kind = request.args.get("kind", "")
        query = request.args.get("q", "").strip().casefold()
        matching = [row for row in rows if (not kind or row["data_kind"] == kind) and
                    (not query or any(query in row[key].casefold() for key in ("company", "body", "external_id")))]
        assessments = {row["id"]: inquiries.read_analysis(row) for row in matching}
        visible = [row for row in matching if not status or row["status"] == status]
        visible.sort(key=lambda row: (row["status"] in {"done", "archive"}, -assessments[row["id"]]["score"]["total"]))
        if api_request() and record_id is None:
            return {"items": [{**dict(row), "tier": row["override"] or assessments[row["id"]]["tier"],
                               "intent": assessments[row["id"]]["intent"],
                               "score": assessments[row["id"]]["score"]["total"]} for row in visible]}
        if record_id is not None:
            visible = [row for row in rows if row["id"] == record_id]
            if not visible:
                abort(404)
        selected = record_id or request.args.get("message", type=int)
        detail = next((row for row in visible if row["id"] == selected), None)
        detail = detail or (visible[0] if visible else None)
        thread, analysis, combined, conflicts, candidates, question = [], {}, {}, set(), [], ""
        if detail:
            thread, analysis, combined, conflicts, candidates, question = message_details(detail)
        linked_buyers = db.execute("SELECT id, company, data_kind FROM buyers ORDER BY company").fetchall()
        if api_request():
            return {"message": dict(detail), "analysis": analysis, "thread": [dict(row) for row in thread],
                    "fields": combined, "conflicts": sorted(conflicts), "candidates": candidates, "question": question,
                    "buyers": [dict(row) for row in linked_buyers if
                               (row["data_kind"] == "synthetic") == (detail["data_kind"] == "synthetic")]}
        return render_template(
            "inbox.html", messages=visible, all_messages=matching, detail=detail, status=status, query=query,
            thread=thread, analysis=analysis, combined=combined, conflicts=conflicts, candidates=candidates,
            question=question, linked_buyers=linked_buyers, assessments=assessments,
            follow_tab=request.args.get("view") == "follow",
        )

    @app.get("/api/inbox/<int:message_id>/reply-status")
    @app.get("/inbox/<int:message_id>/reply-status")
    def reply_status(message_id):
        job = get_db().execute("SELECT * FROM facebook_replies WHERE interaction_id=?", (message_id,)).fetchone()
        if not job:
            abort(404)
        return jsonify(stage=job["stage"], label=facebook_replies.STAGES[job["stage"]],
                       note=job["note"], body=job["body"], reply_id=job["reply_id"],
                       updated_at=display_time(job["updated_at"]))

    @app.post("/api/inbox/add")
    @app.post("/inbox/add")
    def add_message():
        form = CommentForm()
        if not form.validate_on_submit():
            flash_form_errors(form)
            return action_result("inbox_list")
        record = {
            key: (form[key].data or "").strip()
            for key in ("body", "post_id", "comment_id", "source_ref", "data_kind", "company", "thread_id")
        }
        counts = inquiries.import_rows([(2, record)], "authorized", facebook.page_settings()[0], "manual", event_details(app))
        announce(counts, "条评论")
        selected = counts["ids"][0] if counts["ids"] else None
        if selected and model_available():
            try:
                analyze_message(get_record("interactions", selected))
            except ModelUnavailable:
                flash("AI 暂时不可用。评论已保存，当前显示规则判断。", "error")
        if api_request():
            notices = [message for category, message in get_flashed_messages(with_categories=True) if category == "error"]
            return {**counts, "warnings": notices}
        return redirect(url_for("inbox_list", message=selected))

    @app.post("/api/inbox/import")
    @app.post("/inbox/import")
    def import_messages():
        form = CommentImportForm()
        if not form.validate_on_submit():
            flash_form_errors(form)
            return action_result("inbox_list")
        try:
            rows = rows_from_upload(form.file.data, inquiries.REQUIRED)
            counts = inquiries.import_rows(rows, form.data_kind.data, facebook.page_settings()[0], event=event_details(app))
            if api_request():
                return counts
            announce(counts, "条评论")
        except ValueError as error:
            flash(str(error), "error")
        return action_result("inbox_list")

    @app.post("/api/inbox/sync")
    @app.post("/inbox/sync")
    def sync_comments():
        try:
            rows, capped = facebook.comments()
            counts = inquiries.import_rows(rows, "authorized", facebook.page_settings()[0], "graph",
                                          event_details(app), facebook_replies.enqueue)
            if api_request():
                return {**counts, "capped": capped}
            announce(counts, "条 Facebook 评论")
            if capped:
                flash("本次已读取前 1,000 条，请用更小的产品帖完成核对。")
        except facebook.FacebookUnavailable as error:
            flash(str(error), "error")
        return action_result("inbox_list")

    @app.route("/webhooks/facebook", methods=["GET", "POST"])
    @csrf.exempt
    def facebook_hook():
        token = app.config["FACEBOOK_VERIFY_TOKEN"]
        if request.method == "GET":
            provided = request.args.get("hub.verify_token", "")
            if (token and provided.isascii() and request.args.get("hub.mode") == "subscribe"
                    and hmac.compare_digest(provided, token)):
                return Response(request.args.get("hub.challenge", ""), mimetype="text/plain")
            abort(403)
        if not facebook.verify_signature(request.get_data(), request.headers.get("X-Hub-Signature-256")):
            abort(403)
        payload = request.get_json(silent=True)
        try:
            rows = facebook.webhook_rows(payload)
            inquiries.import_rows(rows, "authorized", facebook.page_settings()[0], "webhook",
                                  event_details(app), facebook_replies.enqueue)
        except (AttributeError, TypeError, ValueError, OverflowError, OSError):
            abort(400, "无效的 Facebook 事件。")
        return "", 200

    @app.post("/api/inbox/<int:message_id>/<action>")
    @app.post("/inbox/<int:message_id>/<action>")
    def message_action(message_id, action):
        db = get_db()
        message = get_record("interactions", message_id)
        try:
            if action == "analyze":
                analyze_message(message)
                flash("AI 已分析评论。")
            elif action == "reply":
                _, info, fields, _, _, question = message_details(message)
                event = event_details(app)
                draft = polish(inquiries.reply(fields, info["intent"], event, question), event)
                with db:
                    db.execute("UPDATE interactions SET draft = ? WHERE id = ?", (draft, message_id))
                flash("英文回复草稿已生成。")
            elif action == "save":
                fields = follow_up_fields(inquiries.STATES, inquiries.LABELS, message["override"])
                buyer_id = request.form.get("buyer_id", type=int)
                if buyer_id:
                    buyer = get_record("buyers", buyer_id)
                    if (buyer["data_kind"] == "synthetic") != (message["data_kind"] == "synthetic"):
                        raise ValueError("请选择与互动记录数据类型一致的买家。")
                with db:
                    db.execute("""UPDATE interactions SET owner=?, status=?, follow_up=?, draft=?, override=?,
                                  reason=?, buyer_id=? WHERE id=?""", (*fields, buyer_id, message_id))
                flash("询盘跟进已保存。")
            else:
                abort(404)
        except (ValueError, ModelUnavailable) as error:
            flash(str(error), "error")
        return action_result("inbox_list", message=message_id, view="follow" if action in {"reply", "save"} else None)

    @app.get("/export/<kind>.csv")
    def export(kind):
        if kind == "buyers":
            records, sources = buyer_data()
            fields = ["company", "country", "domain", "tier", "score", "contact_name", "contact_email",
                      "customer_source", "importance", "address", "phone", "description", "social_facebook",
                      "social_twitter", "social_linkedin", "tags",
                      "data_kind", "owner", "status", "follow_up", "override_reason", "draft", "sources"]
            rows = []
            weights = buyer_settings.weights()
            for row in records:
                evidence = sources[row["id"]]
                tier, _, score = buyers.evaluate(row, evidence, weights)
                rows.append((row["company"], row["country"], row["domain"], buyers.TIER_LABELS[tier],
                             score["total"], row["contact_name"], row["contact_email"],
                             row["customer_source"], row["importance"], row["address"], row["phone"], row["description"],
                             row["social_facebook"], row["social_twitter"], row["social_linkedin"], row["tags"],
                             row["data_kind"], row["owner"], BUYER_STATES[row["status"]], row["follow_up"], row["reason"],
                             row["draft"], "\n".join(e["source_ref"] + " " + e["source_text"] for e in evidence)))
        elif kind == "inbox":
            records = get_db().execute(
                """SELECT i.*, b.company AS linked_company, r.stage AS reply_stage, r.reply_id
                   FROM interactions i LEFT JOIN buyers b ON i.buyer_id=b.id
                   LEFT JOIN facebook_replies r ON r.interaction_id=i.id WHERE i.channel='facebook' ORDER BY i.id"""
            ).fetchall()
            fields = ["company", "comment_id", "body", "intent", "tier", "score", "owner", "status", "follow_up",
                      "data_kind", "source", "reason", "draft", "reply_stage", "facebook_reply_id"]
            rows = []
            for row in records:
                assessment = inquiries.read_analysis(row)
                rows.append((row["linked_company"] or row["company"], row["external_id"], row["body"],
                             assessment["intent"], row["override"] or assessment["tier"], assessment["score"]["total"],
                             row["owner"], row["status"], row["follow_up"], row["data_kind"], row["source_ref"],
                             row["reason"], row["draft"], row["reply_stage"], row["reply_id"]))
        else:
            abort(404)
        return download(kind + ".csv", fields, rows)

    @app.get("/api/inbox/<int:message_id>/quote.csv")
    @app.get("/inbox/<int:message_id>/quote.csv")
    def export_quote(message_id):
        message = get_record("interactions", message_id)
        _, _, fields, conflicts, candidates, question = message_details(message)
        headers = ["comment_id", "company", "body", "product", "source_page", "width_mm", "holes", "quantity", "unit",
                   "material", "destination", "owner", "pending", "unit_price", "total_price", "data_kind"]
        missing = [
            label for key, label in {"code": "原件编号", "quantity": "数量", "material": "材质", "destination": "目的地"}.items()
            if not fields.get(key)
        ]
        buyer = get_record("buyers", message["buyer_id"]) if message["buyer_id"] else None
        rows = [
            (message["external_id"], buyer["company"] if buyer else message["company"], message["body"],
             row.get("source_name") or fields.get("product", ""),
             f"{row['source_file']} PDF {row['pdf_page']} / 印刷页 {row['printed_page']}" if row else "",
             row.get("width_mm", fields.get("width_mm", "")), row.get("holes", fields.get("holes", "")),
             fields.get("quantity", ""), fields.get("unit", ""), fields.get("material", ""),
             fields.get("destination", ""), message["owner"],
             " | ".join(["待核价", question, *missing, *sorted(conflicts)]).strip(" |"), "", "", message["data_kind"])
            for row in candidates or [{}]
        ]
        return download(f"quote-{message_id}.csv", headers, rows)

    @app.get("/templates/<kind>.csv")
    def template_download(kind):
        if kind == "customer-profiles":
            return download("customer-profiles.csv", [*workspace.PROFILE_LABELS.values(),
                            *workspace.PROFILE_OPTIONAL.values(), "来源原文"], [])
        if kind == "buyers":
            return download("buyers-template.csv", ["record_id", "company", "country", "domain", "source_type",
                           "source_ref", "as_of", "source_text", "data_kind", "contact_name", "contact_email",
                           "customer_source", "importance", "address", "phone", "description"], [])
        if kind == "comments":
            return download("comments-template.csv", ["comment_id", "page_id", "post_id", "thread_id", "company",
                           "received_at", "body", "source_ref", "data_kind"], [])
        abort(404)

    @app.cli.command("seed")
    @click.argument("dataset", type=click.Choice(["demo", "public", "company"]))
    def seed(dataset):
        if dataset == "company":
            seed_company()
            return
        filename = "hqsmartcloud-demo-buyers.csv" if dataset == "demo" else "public-buyers.csv"
        with (DATA / filename).open(encoding="utf-8-sig", newline="") as source:
            counts = buyers.import_rows(list(enumerate(csv.DictReader(source), 2)),
                                        "public" if dataset == "public" else "synthetic", event_details(app))
        click.echo(f"Buyers: {counts['buyers']}. Evidence: {counts['new']}. Duplicates: {counts['duplicate']}.")
        if dataset == "demo":
            workspace.init_admin(os.getenv("BRAKEBRIDGE_ADMIN_PASSWORD") or "123456")
            workspace.seed_timeline(event_details(app))
            with (DATA / "demo-comments.csv").open(encoding="utf-8-sig", newline="") as source:
                results = inquiries.import_rows(list(enumerate(csv.DictReader(source), 2)), "synthetic", event=event_details(app))
            if results["ids"]:
                db = get_db()
                with db:
                    db.executemany("""UPDATE interactions SET buyer_id = (
                                         SELECT buyers.id FROM buyers WHERE buyers.company=interactions.company
                                         AND buyers.data_kind='synthetic' ORDER BY buyers.id LIMIT 1
                                      ) WHERE id=?""", [(record_id,) for record_id in results["ids"]])
            click.echo(f"Comments: {results['new']}. Duplicates: {results['duplicate']}.")
            if app.config["LEADS_ALLOW_SYNTHETIC"]:
                with (DATA / "demo-lead-inquiries.json").open(encoding="utf-8") as source:
                    counts = lead_inquiries.import_vendor(json.load(source), "synthetic",
                                                         event_details(app)["date"] or workspace.DEMO_FALLBACK)
                click.echo(f"Inquiries: {counts['inserted']}. Duplicates: {counts['duplicate']}.")
            if company_seed.BUNDLE.is_file():
                seed_company()

    def seed_company():
        try:
            result = company_seed.install(company_seed.BUNDLE, event_details(app)["date"] or workspace.DEMO_FALLBACK)
        except (OSError, sqlite3.Error, ValueError) as error:
            raise click.ClickException(str(error)) from error
        click.echo(f"Company seed: {result['inquiries']} inquiries, {result['photos']} product images, "
                   f"{result['scores']} restored AI scores. Retained inquiries: {result['retained']}.")


def safe_link(value):
    try:
        link = urlsplit(value)
        return value if link.scheme in {"http", "https"} and link.hostname else ""
    except ValueError:
        return ""


def display_time(value):
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return value
    if stamp.tzinfo:
        stamp = stamp.astimezone(timezone(timedelta(hours=8)))
    return stamp.strftime("%Y-%m-%d %H:%M")


def event_details(app):
    return {key: app.config["EVENT_" + key.upper()] for key in ("name", "date", "venue", "booth")}


def invitation_subject(event):
    return "Invitation to meet Kooying" + (f" at {event['name']}" if event["name"] else " at the upcoming auto parts show")


def invitation_mailto(buyer, event):
    if not buyer["draft"] or not buyer["contact_email"] or buyer["data_kind"] == "synthetic":
        return ""
    address = quote(buyer["contact_email"], safe="@")
    return f"mailto:{address}?subject={quote(invitation_subject(event), safe='')}&body={quote(buyer['draft'], safe='')}"


def get_record(table, record_id):
    if table not in {"buyers", "interactions"}:
        raise ValueError("Unknown record type")
    result = get_db().execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
    if result is None:
        abort(404)
    return result


def buyer_data():
    db = get_db()
    records = db.execute("SELECT * FROM buyers ORDER BY id").fetchall()
    sources = defaultdict(list)
    for row in db.execute("SELECT * FROM evidence ORDER BY id"):
        sources[row["buyer_id"]].append(row)
    return records, sources


def buyer_entries(records, sources):
    entries = []
    weights = buyer_settings.weights()
    blend = agent_scoring.weights("buyer")
    for buyer in records:
        tier, axes, score = buyers.evaluate(buyer, sources[buyer["id"]], weights, blend)
        entries.append({"buyer": buyer, "tier": tier, "axes": axes, "score": score, "evidence": sources[buyer["id"]]})
    return sorted(entries, key=lambda entry: (list(buyers.TIER_LABELS).index(entry["tier"]),
                                              entry["score"]["total"] is None, -(entry["score"]["total"] or 0),
                                              entry["buyer"]["company"].casefold()))


def persist_profile(values, buyer=None):
    provided = {key: normalize(buyer[key]) for key in buyer.keys()} if buyer else {}
    provided.update(values)
    profile, issues = workspace.profile_values(provided, required=True)
    kind = buyer["data_kind"] if buyer else values.get("data_kind") or "authorized"
    if kind not in KINDS:
        issues["data_kind"] = "请选择资料类型。"
    if issues:
        return workspace.error("请检查客户资料。", fields=issues)
    key = buyers.entity_key(kind, profile["domain"], profile["company"], profile["country"])
    args = tuple(profile[name] for name in ("company", "country", "domain", "contact_name", "contact_email",
                                           "customer_source", "importance", "address", "phone", "description",
                                           "social_facebook", "social_twitter", "social_linkedin", "tags", "owner"))
    db = get_db()
    try:
        with db:
            if buyer:
                db.execute("""UPDATE buyers SET entity_key=?, company=?, country=?, domain=?, contact_name=?,
                              contact_email=?, customer_source=?, importance=?, address=?, phone=?, description=?,
                              social_facebook=?, social_twitter=?, social_linkedin=?, tags=?, owner=?
                              WHERE id=?""", (key, *args, buyer["id"]))
                buyers.prepare_invite(buyer["id"], event_details(current_app))
                buyer_id = buyer["id"]
            else:
                buyer_id = db.execute("""INSERT INTO buyers(entity_key, data_kind, company, country, domain,
                                         contact_name, contact_email, customer_source, importance, address, phone, description,
                                         social_facebook, social_twitter, social_linkedin, tags, owner)
                                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (key, kind, *args)).lastrowid
    except sqlite3.IntegrityError:
        return workspace.error("已存在同一地区的相同客户，请先查找后补充资料。", 409, {"company": "可能为重复客户。"})
    return {"id": buyer_id}


def group_findings(findings):
    groups = {}
    refs = {}
    for axis, item in findings.items():
        if item:
            key = (item["id"], item["quote"])
            if key not in groups:
                groups[key] = {"number": len(groups) + 1, **item}
            refs[axis] = groups[key]["number"]
    return list(groups.values()), refs


def message_details(message):
    thread = get_db().execute(
        """SELECT * FROM interactions WHERE channel=? AND account=? AND thread_id=? AND data_kind=?
           ORDER BY received_at, id""",
        (message["channel"], message["account"], message["thread_id"], message["data_kind"]),
    ).fetchall()
    combined, conflicts = inquiries.thread_fields(thread)
    candidates, question = inquiries.catalog_for_thread(combined, conflicts)
    return thread, inquiries.read_analysis(message), combined, conflicts, candidates, question


def analyze_message(message):
    result = inquiries.ai_analysis(message["body"])
    db = get_db()
    with db:
        db.execute(
            """UPDATE interactions SET analysis = ?, status = CASE
               WHEN status = ? AND owner = '' AND follow_up = '' AND draft = '' AND reason = '' AND override = ''
               THEN ? ELSE status END WHERE id = ?""",
            (json.dumps(result, ensure_ascii=False), inquiries.default_status(inquiries.read_analysis(message)["tier"]),
             inquiries.default_status(result["tier"]), message["id"]),
        )


def polish(draft, event):
    if not model_available():
        return draft
    try:
        output = ask_json(
            "润色一份英文商务草稿。返回 {draft: string}。保留全部问题和全部已知事实。"
            "只使用原稿已有信息，不增加价格、交期、型号适配、技术承诺、展位、链接或第三方联系人。",
            {"draft": draft, "confirmed_event": event},
        ).get("draft")
        if isinstance(output, str) and 0 < len(output.strip()) <= 2500:
            return output.strip()
    except ModelUnavailable:
        pass
    flash("当前使用规则草稿，可编辑后保存。")
    return draft


def follow_up_fields(statuses, overrides, previous_override):
    owner = request.form.get("owner", "").strip()[:120]
    status = request.form.get("status", "")
    follow_up = request.form.get("follow_up", "").strip()
    draft = request.form.get("draft", "").strip()[:6000]
    override = request.form.get("override", "")
    reason = request.form.get("reason", "").strip()[:1000]
    if status not in statuses or (override and override not in overrides):
        raise ValueError("请选择有效的跟进状态。")
    if (override or previous_override != override) and not reason:
        raise ValueError("改判时请记录原因。")
    if follow_up:
        try:
            follow_up = date.fromisoformat(follow_up).isoformat()
        except ValueError as error:
            raise ValueError("请选择有效的跟进日期。") from error
    return owner, status, follow_up, draft, override, reason


def announce(counts, label):
    summary = f"新增 {counts['new']} {label}，重复 {counts['duplicate']} 条"
    if "buyers" in counts:
        summary += f"，新增 {counts['buyers']} 家企业"
    flash(summary + "。")
    if counts["errors"]:
        flash(" ".join(counts["errors"][:4]), "error")


def flash_form_errors(form):
    errors = [error for field in form.errors.values() for error in field]
    flash(" ".join(errors), "error")


def api_request():
    return request.path.startswith("/api/")


def action_result(endpoint, **values):
    if not api_request():
        return redirect(url_for(endpoint, **values))
    messages = get_flashed_messages(with_categories=True)
    errors = [message for category, message in messages if category == "error"]
    if errors:
        return workspace.error(" ".join(errors))
    return {"ok": True, "messages": [message for _, message in messages]}


def vue_built(app):
    return (Path(app.config["VUE_DIST"]) / "index.html").is_file()


def send_workspace(app):
    return send_from_directory(app.config["VUE_DIST"], "index.html")


def download(name, headers, rows):
    return Response(csv_bytes(headers, rows), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


app = create_app()
