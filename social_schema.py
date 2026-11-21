SCHEMA = (
    """CREATE TABLE social_accounts (
        id INTEGER PRIMARY KEY, platform TEXT NOT NULL, external_id TEXT NOT NULL,
        name TEXT NOT NULL, mode TEXT NOT NULL CHECK (mode IN ('demo','live')),
        credential TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
        publish_check TEXT NOT NULL DEFAULT 'unverified', comment_check TEXT NOT NULL DEFAULT 'unverified',
        checked_at TEXT NOT NULL, UNIQUE(platform,external_id,mode)
    )""",
    """CREATE TABLE social_news (
        id INTEGER PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
        published_on TEXT NOT NULL, summary TEXT NOT NULL
    )""",
    """CREATE TABLE social_drafts (
        id INTEGER PRIMARY KEY, title TEXT NOT NULL, current_version INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE social_versions (
        id INTEGER PRIMARY KEY, draft_id INTEGER NOT NULL REFERENCES social_drafts(id), revision INTEGER NOT NULL,
        title TEXT NOT NULL, facebook TEXT NOT NULL, linkedin TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
        image_type TEXT NOT NULL DEFAULT '', image BLOB, refs TEXT NOT NULL DEFAULT '[]',
        approved_at TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, UNIQUE(draft_id,revision)
    )""",
    """CREATE TABLE social_jobs (
        id INTEGER PRIMARY KEY, version_id INTEGER NOT NULL REFERENCES social_versions(id),
        account_id INTEGER NOT NULL REFERENCES social_accounts(id),
        run_at TEXT NOT NULL, state TEXT NOT NULL, remote_id TEXT NOT NULL DEFAULT '',
        remote_url TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL, comments_at TEXT NOT NULL DEFAULT '',
        comments_attempt_at TEXT NOT NULL DEFAULT '', comments_error TEXT NOT NULL DEFAULT '',
        UNIQUE(version_id,account_id)
    )""",
    "CREATE INDEX social_due_jobs ON social_jobs(state,run_at,id)",
)

FIGEON_SCHEMA = (
    """CREATE TABLE figeon_connection (
        id INTEGER PRIMARY KEY CHECK(id=1), credential TEXT NOT NULL,
        revision INTEGER NOT NULL, accounts TEXT NOT NULL, checked_at TEXT NOT NULL
    )""",
    """CREATE TABLE figeon_batches (
        id INTEGER PRIMARY KEY, version_id INTEGER NOT NULL UNIQUE REFERENCES social_versions(id),
        connection_revision INTEGER NOT NULL, platforms TEXT NOT NULL, content TEXT NOT NULL,
        media TEXT NOT NULL, visibility TEXT NOT NULL, run_at TEXT NOT NULL, state TEXT NOT NULL,
        remote_id INTEGER, note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE figeon_posts (
        connection_revision INTEGER NOT NULL, remote_id INTEGER NOT NULL, title TEXT NOT NULL,
        content TEXT NOT NULL, created_at TEXT NOT NULL, status INTEGER NOT NULL, targets TEXT NOT NULL,
        PRIMARY KEY (connection_revision,remote_id)
    )""",
    "CREATE INDEX figeon_due ON figeon_batches(state,run_at)",
)
