import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from flask import current_app, g
from social_schema import FIGEON_SCHEMA, SCHEMA as SOCIAL_SCHEMA


SCHEMA = """
CREATE TABLE IF NOT EXISTS buyers (
    id INTEGER PRIMARY KEY, entity_key TEXT NOT NULL UNIQUE,
    company TEXT NOT NULL, country TEXT NOT NULL, domain TEXT NOT NULL,
    data_kind TEXT NOT NULL,
    contact_email TEXT NOT NULL DEFAULT '', contact_name TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'new',
    follow_up TEXT NOT NULL DEFAULT '', draft TEXT NOT NULL DEFAULT '',
    override TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
    analysis TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY, buyer_id INTEGER NOT NULL REFERENCES buyers(id),
    fingerprint TEXT NOT NULL UNIQUE, source_type TEXT NOT NULL,
    source_ref TEXT NOT NULL, as_of TEXT NOT NULL, source_text TEXT NOT NULL,
    data_kind TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY, channel TEXT NOT NULL, account TEXT NOT NULL,
    external_id TEXT NOT NULL, post_id TEXT NOT NULL, thread_id TEXT NOT NULL,
    source_ref TEXT NOT NULL, received_at TEXT NOT NULL, body TEXT NOT NULL,
    company TEXT NOT NULL, data_kind TEXT NOT NULL, transport TEXT NOT NULL,
    analysis TEXT NOT NULL, buyer_id INTEGER REFERENCES buyers(id),
    owner TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'review',
    follow_up TEXT NOT NULL DEFAULT '', draft TEXT NOT NULL DEFAULT '',
    override TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
    UNIQUE(channel, account, external_id)
);
CREATE INDEX IF NOT EXISTS interaction_thread
ON interactions(channel, account, thread_id, data_kind);
CREATE TABLE IF NOT EXISTS facebook_replies (
    interaction_id INTEGER PRIMARY KEY REFERENCES interactions(id),
    stage TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
    reply_id TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reply_queue ON facebook_replies(stage, created_at);
CREATE TABLE IF NOT EXISTS sales_agent_results (
    scope TEXT NOT NULL CHECK (scope IN ('buyer','inquiry')),
    record_id INTEGER NOT NULL, task TEXT NOT NULL,
    fingerprint TEXT NOT NULL, data TEXT NOT NULL,
    PRIMARY KEY (scope, record_id, task)
);
"""

PROFILE_COLUMNS = {
    "contact_email": "TEXT NOT NULL DEFAULT ''",
    "contact_name": "TEXT NOT NULL DEFAULT ''",
    "customer_source": "TEXT NOT NULL DEFAULT ''",
    "importance": "INTEGER CHECK (importance BETWEEN 1 AND 5)",
    "address": "TEXT NOT NULL DEFAULT ''",
    "phone": "TEXT NOT NULL DEFAULT ''",
    "description": "TEXT NOT NULL DEFAULT ''",
}

LEAD_SCHEMA = (
    """CREATE TABLE lead_inquiries (
        id INTEGER PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL,
        data_kind TEXT NOT NULL CHECK (data_kind IN ('synthetic','authorized')),
        fields TEXT NOT NULL, sender TEXT NOT NULL DEFAULT '',
        buyer_id INTEGER REFERENCES buyers(id), owner TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','working','done','declined','archived')),
        received_at TEXT NOT NULL, analysis TEXT NOT NULL,
        meeting_at TEXT NOT NULL DEFAULT '', next_action TEXT NOT NULL DEFAULT '',
        dossier TEXT NOT NULL DEFAULT '', identity TEXT NOT NULL DEFAULT '', UNIQUE(source,external_id,data_kind)
    )""",
    "CREATE INDEX lead_sender ON lead_inquiries(data_kind,sender)",
    """CREATE TABLE lead_messages (
        id INTEGER PRIMARY KEY, inquiry_id INTEGER NOT NULL REFERENCES lead_inquiries(id),
        fingerprint TEXT NOT NULL, data_kind TEXT NOT NULL, message_ref TEXT NOT NULL DEFAULT '',
        sender TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL DEFAULT '',
        received_at TEXT NOT NULL, body TEXT NOT NULL, reply_text TEXT NOT NULL,
        attachments TEXT NOT NULL DEFAULT '[]', analysis TEXT NOT NULL, live INTEGER NOT NULL DEFAULT 0,
        UNIQUE(data_kind,fingerprint)
    )""",
    "CREATE INDEX lead_message_ref ON lead_messages(data_kind,message_ref)",
    """CREATE TABLE lead_candidates (
        id INTEGER PRIMARY KEY, inquiry_id INTEGER NOT NULL REFERENCES lead_inquiries(id),
        identity_key TEXT NOT NULL, candidate TEXT NOT NULL, verification TEXT NOT NULL DEFAULT 'pending',
        follow_up TEXT NOT NULL DEFAULT '', draft TEXT NOT NULL DEFAULT '', checked_at TEXT NOT NULL DEFAULT '',
        UNIQUE(inquiry_id,identity_key)
    )""",
    """CREATE TABLE lead_notifications (
        id INTEGER PRIMARY KEY, dedupe_key TEXT NOT NULL UNIQUE,
        inquiry_id INTEGER REFERENCES lead_inquiries(id), body TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending', receipt TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX lead_notice_queue ON lead_notifications(state,id)",
)


def default_path():
    base = Path(os.getenv("LOCALAPPDATA") or Path.home() / ".local" / "share")
    return str(base / "BrakeBridge" / "sales.sqlite3")


def init_app(app):
    path = Path(app.config["DATABASE"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with app.app_context():
        db = get_db()
        db.executescript(SCHEMA)
        with db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("PRAGMA user_version").fetchone()[0] < 1:
                columns = {row["name"] for row in db.execute("PRAGMA table_info(buyers)")}
                for name, definition in PROFILE_COLUMNS.items():
                    if name not in columns:
                        db.execute(f"ALTER TABLE buyers ADD COLUMN {name} {definition}")
                db.execute("""CREATE TABLE users (
                    id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""")
                db.execute("""CREATE TABLE event_tasks (
                    id INTEGER PRIMARY KEY, title TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '',
                    start_on TEXT NOT NULL, end_on TEXT NOT NULL CHECK (end_on >= start_on),
                    status TEXT NOT NULL CHECK (status IN ('open','doing','done')),
                    kind TEXT NOT NULL CHECK (kind IN ('preparation','invitation','milestone')),
                    data_kind TEXT NOT NULL CHECK (data_kind IN ('synthetic','authorized')),
                    seed_key TEXT UNIQUE
                )""")
                db.execute("CREATE INDEX event_tasks_dates ON event_tasks(start_on, end_on)")
                db.execute("PRAGMA user_version = 1")
            if db.execute("PRAGMA user_version").fetchone()[0] < 2:
                for name in ("social_facebook", "social_twitter", "social_linkedin", "tags"):
                    db.execute(f"ALTER TABLE buyers ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
                db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                db.execute("PRAGMA user_version = 2")
            if db.execute("PRAGMA user_version").fetchone()[0] < 3:
                for statement in LEAD_SCHEMA:
                    db.execute(statement)
                db.execute("PRAGMA user_version = 3")
            if db.execute("PRAGMA user_version").fetchone()[0] < 6:
                columns = {row["name"] for row in db.execute("PRAGMA table_info(lead_inquiries)")}
                for name in ("deleted_at", "vendor_key", "edited_at", "public_research"):
                    if name not in columns:
                        db.execute(f"ALTER TABLE lead_inquiries ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
                db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS lead_vendor_export_key
                              ON lead_inquiries(data_kind,vendor_key) WHERE vendor_key<>''""")
                db.execute("""CREATE TABLE IF NOT EXISTS lead_translations (
                    inquiry_id INTEGER NOT NULL REFERENCES lead_inquiries(id),
                    target INTEGER NOT NULL, source_hash TEXT NOT NULL,
                    text TEXT NOT NULL, engine TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY (inquiry_id,target)
                )""")
                db.execute("PRAGMA user_version = 6")
            if db.execute("PRAGMA user_version").fetchone()[0] < 7:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='social_jobs'").fetchone():
                    for statement in SOCIAL_SCHEMA:
                        db.execute(statement)
                columns = {row["name"] for row in db.execute("PRAGMA table_info(social_jobs)")}
                for name in ("comments_attempt_at", "comments_error"):
                    if name not in columns:
                        db.execute(f"ALTER TABLE social_jobs ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
                db.execute("PRAGMA user_version = 7")
            if db.execute("PRAGMA user_version").fetchone()[0] < 8:
                for statement in FIGEON_SCHEMA:
                    db.execute(statement)
                db.execute("PRAGMA user_version = 8")
            if db.execute("PRAGMA user_version").fetchone()[0] < 9:
                db.execute("ALTER TABLE figeon_batches ADD COLUMN request_key TEXT NOT NULL DEFAULT ''")
                db.execute("""CREATE UNIQUE INDEX figeon_release_key ON figeon_batches(request_key)
                              WHERE request_key<>''""")
                db.execute("PRAGMA user_version = 9")
            if db.execute("PRAGMA user_version").fetchone()[0] < 10:
                db.execute("""CREATE TABLE social_assets (
                    id INTEGER PRIMARY KEY, digest TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                    category TEXT NOT NULL, model TEXT NOT NULL, image BLOB NOT NULL
                )""")
                db.execute("ALTER TABLE social_versions ADD COLUMN asset_id INTEGER REFERENCES social_assets(id)")
                db.execute("PRAGMA user_version = 10")
    app.teardown_appcontext(close_db)


def get_db():
    if "db" not in g:
        connection = sqlite3.connect(current_app.config["DATABASE"], timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        g.db = connection
    return g.db


@contextmanager
def write_transaction(db):
    if db.in_transaction:
        yield
    else:
        with db:
            db.execute("BEGIN IMMEDIATE")
            yield


def close_db(_error):
    db = g.pop("db", None)
    if db is not None:
        db.close()
