"""002_digest_subscribers: Create the digest subscription table.

Stores email digest subscribers with per-tenant isolation, frequency
preferences, meeting body and topic filters, double opt-in tokens,
and one-click unsubscribe tokens.
"""

DESCRIPTION = "Create digest_subscribers table"


def up(db):
    serial = db.serial_type
    bool_type = db.bool_type

    db.execute(f"""
        CREATE TABLE IF NOT EXISTS digest_subscribers (
            id                  TEXT PRIMARY KEY,
            tenant_id           TEXT NOT NULL,
            email               TEXT NOT NULL,
            frequency           TEXT NOT NULL DEFAULT 'weekly',
            meeting_bodies      TEXT NOT NULL DEFAULT '[]',
            topics              TEXT NOT NULL DEFAULT '[]',
            active              {bool_type} NOT NULL DEFAULT 1,
            confirmed           {bool_type} NOT NULL DEFAULT 0,
            confirm_token       TEXT,
            unsubscribe_token   TEXT,
            last_digest_at      TEXT,
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL,
            UNIQUE(tenant_id, email)
        )
    """)

    db.execute("CREATE INDEX IF NOT EXISTS idx_digest_tenant ON digest_subscribers(tenant_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_digest_email ON digest_subscribers(email)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_digest_frequency ON digest_subscribers(tenant_id, frequency)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_digest_unsub_token ON digest_subscribers(unsubscribe_token)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_digest_active ON digest_subscribers(tenant_id, active, confirmed)")


def down(db):
    db.execute("DROP TABLE IF EXISTS digest_subscribers")
