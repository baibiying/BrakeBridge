import re
from datetime import date, datetime, timedelta, timezone
from collections import Counter
from pathlib import Path

from flask import abort, current_app, g, jsonify, redirect, request, send_from_directory, session, url_for
from flask_wtf.csrf import generate_csrf
from werkzeug.security import check_password_hash, generate_password_hash

import buyers
import lead_inquiries as leads
from storage import get_db

DEMO_FALLBACK = "2026-12-02"
TASK_STATES = {"open": "待开始", "doing": "进行中", "done": "已完成"}
TASK_TYPES = {"preparation": "展会筹备", "invitation": "邀约窗口", "milestone": "展会节点"}
PROFILE_LABELS = {
    "company": "客户名称", "contact_name": "联系人", "customer_source": "客户来源",
    "importance": "重要程度", "country": "国家或地区", "address": "具体地址",
    "contact_email": "邮箱", "phone": "电话",
}
PROFILE_OPTIONAL = {"domain": "客户官网", "description": "客户描述", "social_facebook": "Facebook",
                    "social_twitter": "Twitter", "social_linkedin": "LinkedIn", "owner": "负责人", "tags": "标签"}
PROFILE_HEADERS = {label.lower(): key for key, label in {**PROFILE_LABELS, **PROFILE_OPTIONAL}.items()}
PROFILE_HEADERS.update({"客户联系人": "contact_name", "客户重要程度": "importance", "客户区域": "country",
                        "国家": "country", "客户邮箱": "contact_email", "客户电话": "phone", "标签：": "tags",
                        "来源原文": "source_text"})


def error(message, status=400, fields=None):
    return jsonify(message=message, fields=fields or {}), status


def register(app):
    dist = Path(app.config["VUE_DIST"])
    public = {"session_status", "login_api", "login_page", "vue_assets", "static", "facebook_hook",
              "inquiry.inquiry_home", "sourcing.sourcing_home"}

    @app.before_request
    def require_user():
        if request.endpoint is None or request.endpoint in public:
            return
        if current_user() is not None:
            return
        if request.path.startswith("/api/"):
            return error("请先登录。", 401)
        return redirect(url_for("login_page", next=request.full_path.rstrip("?")))

    @app.after_request
    def prevent_business_cache(response):
        if request.endpoint not in {"vue_assets", "static", "facebook_hook"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    def frontend_shell():
        if not (dist / "index.html").is_file():
            return "请先在 frontend 执行 npm ci 和 npm run build。", 503
        return send_from_directory(dist, "index.html")

    @app.get("/assets/<path:filename>")
    def vue_assets(filename):
        return send_from_directory(dist / "assets", filename)

    @app.get("/login")
    def login_page():
        return frontend_shell()

    @app.get("/dashboard")
    @app.get("/buyers")
    @app.get("/settings")
    @app.get("/lead-inquiries")
    @app.get("/fitment")
    @app.get("/social")
    @app.get("/social/scheduled")
    def workspace_page():
        return frontend_shell()

    @app.get("/api/session")
    def session_status():
        return session_data()

    @app.post("/api/login")
    def login_api():
        values = request.get_json(silent=True)
        if not isinstance(values, dict):
            return error("请填写用户名和密码。")
        username = values.get("username")
        password = values.get("password")
        if not isinstance(username, str) or not isinstance(password, str) or len(password) > 256:
            return error("请填写有效的用户名和密码。")
        row = get_db().execute("SELECT * FROM users WHERE username=? AND active=1", (username.strip(),)).fetchone()
        if row is None or not check_password_hash(row["password_hash"], password):
            return error("用户名或密码错误。", 401)
        session.clear()
        session["user_id"] = row["id"]
        g.pop("workspace_user", None)
        return session_data()

    @app.post("/api/logout")
    def logout_api():
        session.clear()
        return {"ok": True}


def current_user():
    if "workspace_user" not in g:
        user_id = session.get("user_id")
        g.workspace_user = (
            get_db().execute("SELECT id, username FROM users WHERE id=? AND active=1", (user_id,)).fetchone()
            if user_id else None
        )
    return g.workspace_user


def session_data():
    user = current_user()
    return {"user": dict(user) if user else None, "csrf": generate_csrf()}


def init_admin(password):
    db = get_db()
    if not db.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
        hashed = generate_password_hash(password)
        with db:
            db.execute("INSERT OR IGNORE INTO users(username, password_hash) VALUES ('admin',?)", (hashed,))


def profile_values(values, required=False):
    clean = {}
    issues = {}
    limits = {"company": 200, "country": 100, "domain": 253, "contact_name": 120,
              "contact_email": 254, "customer_source": 100, "address": 300, "phone": 32,
              "description": 3000, "social_facebook": 500, "social_twitter": 500, "social_linkedin": 500,
              "owner": 120, "tags": 500}
    for key, limit in limits.items():
        value = values.get(key, "").strip()
        if len(value) > limit:
            issues[key] = f"最多 {limit} 字。"
        clean[key] = value
    importance = values.get("importance", "").strip().removesuffix("星").strip()
    clean["importance"] = int(importance) if importance in {"1", "2", "3", "4", "5"} else None
    if importance and clean["importance"] is None:
        issues["importance"] = "请选择 1 到 5 星。"
    if clean["contact_email"] and not buyers.contact_email(clean["contact_email"]):
        issues["contact_email"] = "请输入有效的邮箱。"
    if clean["phone"] and (not re.fullmatch(r"\+?[\d ().-]{5,32}", clean["phone"], flags=re.ASCII)
                           or sum(character.isdecimal() for character in clean["phone"]) < 5):
        issues["phone"] = "请输入有效的电话号码。"
    if clean["domain"]:
        clean["domain"] = buyers.domain_name(clean["domain"])
        if not clean["domain"]:
            issues["domain"] = "请输入有效的域名。"
    for key, label in PROFILE_LABELS.items():
        if (required or key == "company") and not clean[key]:
            issues.setdefault(key, f"请填写{label}。")
    return clean, issues


def local_today():
    return datetime.now(timezone(timedelta(hours=8))).date()


def task_window(args):
    today = local_today()
    start = date.fromisoformat(args["start"]) if args.get("start") else today - timedelta(days=7)
    end = date.fromisoformat(args["end"]) if args.get("end") else today + timedelta(days=14)
    if end < start or (end - start).days > 92:
        raise ValueError("请选择不超过 93 天的有效日期范围。")
    return today.isoformat(), start.isoformat(), end.isoformat()


def inquiry_overview(rows, today):
    by_date = Counter(leads.intake_day(row) for row in rows)
    by_source = Counter(row["source"] for row in rows)
    by_status = Counter(row["status"] for row in rows)
    calendar = date.fromisoformat(today)
    recent = [(calendar - timedelta(days=offset)).isoformat() for offset in range(59, -1, -1)]
    return {"inquiries_count": len(rows), "inquiry_sources": dict(by_source),
            "inquiry_states": {key: {"label": label, "count": by_status[key]} for key, label in leads.LEAD_STATES.items()},
            "inquiry_daily": [{"day": day, "count": by_date[day]} for day in recent]}


def dashboard(entries, messages, args):
    today, start, end = task_window(args)
    scope = args.get("kind")
    entries = [item for item in entries if not scope or item["buyer"]["data_kind"] == scope]
    messages = [row for row in messages if not scope or row["data_kind"] == scope]
    tiers = {key: sum(item["tier"] == key for item in entries) for key in buyers.TIER_LABELS}
    inbox = {key: sum(row["status"] == key for row in messages) for key in ("new", "review", "done", "archive")}
    inquiries = get_db().execute("""SELECT id,fields,source,received_at,meeting_at,identity,status,owner,data_kind
        FROM lead_inquiries WHERE deleted_at='' AND (data_kind='authorized' OR ?) AND (? IS NULL OR data_kind=?)""",
        (current_app.config["LEADS_ALLOW_SYNTHETIC"], scope or None, scope)).fetchall()
    events = [dict(row) for row in get_db().execute("SELECT * FROM event_tasks ORDER BY start_on, id")
              if not scope or row["data_kind"] == scope]
    for event in events:
        event["resource"] = "event"
    tasks = events[:]
    for resource, rows, label in (("buyer", [entry["buyer"] for entry in entries], "客户跟进"),
                                  ("inbox", messages, "询盘跟进")):
        for row in rows:
            if not row["follow_up"] or row["status"] == "archive":
                continue
            tasks.append({"id": row["id"], "resource": resource, "title": row["company"] or f"社媒评论 #{row['id']}",
                          "owner": row["owner"], "status": {"done": "done", "contacted": "doing", "waiting": "doing"}.get(row["status"], "open"),
                          "kind": label, "start_on": row["follow_up"], "end_on": row["follow_up"],
                          "data_kind": row["data_kind"]})
    for row in inquiries:
        meeting = leads.date_value(row["meeting_at"])
        if meeting and row["status"] != "archived":
            day = datetime.fromisoformat(meeting).astimezone(leads.ZONE).date().isoformat()
            fields = leads.payload(row["fields"], {})
            title = leads.payload(row["identity"], {}).get("company") or fields.get("company") or f"询盘会面 #{row['id']}"
            status = {"working": "doing", "done": "done", "declined": "done"}.get(row["status"], "open")
            tasks.append({"id": row["id"], "resource": "lead", "title": title, "owner": row["owner"],
                          "status": status, "kind": "询盘会面", "start_on": day, "end_on": day,
                          "data_kind": row["data_kind"]})
    for task in tasks:
        task["overdue"] = task["status"] != "done" and task["end_on"] < today
    due = [task for task in tasks if task["status"] != "done" and task["end_on"] <= today]
    due.sort(key=lambda task: (task["end_on"], task["title"]))
    window = [task for task in tasks if task["start_on"] <= end and task["end_on"] >= start
              and (not args.get("owner") or task["owner"] == args["owner"])
              and (not args.get("status") or task["status"] == args["status"])]
    window.sort(key=lambda task: (task["resource"] != "event", task["start_on"], task["title"]))
    return {"today": today, "start": start, "end": end, "tasks": window, "due": due,
            "tiers": tiers, "inbox": inbox, "owners": sorted({task["owner"] for task in tasks if task["owner"]}),
            "buyers_count": len(entries), **inquiry_overview(inquiries, today)}


def persist_event(values, task_id=None):
    title = values.get("title", "").strip()
    owner = values.get("owner", "").strip()
    kind = values.get("kind")
    status = values.get("status")
    scope = values.get("data_kind")
    issues = {}
    if not title or len(title) > 160:
        issues["title"] = "请填写任务名称，最多 160 字。"
    if len(owner) > 120:
        issues["owner"] = "负责人最多 120 字。"
    if kind not in TASK_TYPES:
        issues["kind"] = "请选择任务类型。"
    if status not in TASK_STATES:
        issues["status"] = "请选择状态。"
    if scope not in {"synthetic", "authorized"}:
        issues["data_kind"] = "请选择资料类型。"
    dates = {}
    for key in ("start_on", "end_on"):
        try:
            dates[key] = date.fromisoformat(values.get(key, "")).isoformat()
        except ValueError:
            issues[key] = "请选择有效日期。"
    if len(dates) == 2 and dates["end_on"] < dates["start_on"]:
        issues["end_on"] = "结束日期不能早于开始日期。"
    if issues:
        return error("请检查任务信息。", fields=issues)
    db = get_db()
    parts = (title, owner, dates["start_on"], dates["end_on"], status, kind, scope)
    with db:
        if task_id is None:
            task_id = db.execute("""INSERT INTO event_tasks(title, owner, start_on, end_on, status, kind, data_kind)
                                    VALUES(?,?,?,?,?,?,?)""", parts).lastrowid
        elif not db.execute("""UPDATE event_tasks SET title=?, owner=?, start_on=?, end_on=?, status=?,
                                kind=?, data_kind=? WHERE id=?""", (*parts, task_id)).rowcount:
            abort(404)
    return {"id": task_id}


def seed_timeline(event):
    expo = date.fromisoformat(event["date"] or DEMO_FALLBACK)
    tasks = [
        ("catalog", "样品与目录准备", "产品组", -12, -7, "done", "preparation"),
        ("shortlist", "重点采购商复核", "外贸一组", -11, -8, "doing", "preparation"),
        ("invitation", "买家邀约窗口", "外贸一组", -10, -2, "doing", "invitation"),
        ("appointments", "确认到展与会面时间", "外贸二组", -6, -1, "open", "preparation"),
        ("show", event["name"] or f"冠英展会 · {expo.month} 月 {expo.day} 日", "展会组", 0, 0, "open", "milestone"),
        ("recap", "展会线索回访", "外贸二组", 1, 3, "open", "preparation"),
    ]
    db = get_db()
    with db:
        inserted = db.executemany("""INSERT OR IGNORE INTO event_tasks
                          (seed_key,title,owner,start_on,end_on,status,kind,data_kind)
                          VALUES (?,?,?,?,?,?,?,'synthetic')""",
                       [(key, title, owner, (expo + timedelta(days=start)).isoformat(),
                         (expo + timedelta(days=end)).isoformat(), status, kind)
                        for key, title, owner, start, end, status, kind in tasks])
        if inserted.rowcount:
            db.executemany("""UPDATE buyers SET owner=?, follow_up=?
                              WHERE domain=? AND data_kind='synthetic' AND follow_up='' AND owner=''""",
                           [(owner, (expo + timedelta(days=offset)).isoformat(), domain)
                            for owner, offset, domain in [
                                ("外贸一组", -12, "gulf-brakes.example"),
                                ("外贸一组", -7, "golden-bridge-brakes.example"),
                                ("外贸二组", -4, "southridge-parts.example"),
                            ]])
