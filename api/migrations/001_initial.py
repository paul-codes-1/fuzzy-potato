"""001_initial: Create all CivicLens SaaS tables.

Consolidates the schemas previously spread across individual SQLite databases
(tenants.db, analytics.db, audit.db, webhooks.db, scheduler.db, sso.db,
tracker.db, push.db, branding.db, health.db, exports.db) into a single
unified schema suitable for both SQLite and PostgreSQL.

Differences from the per-module SQLite originals:
- All tables live in one database with a shared migration history.
- INTEGER PRIMARY KEY AUTOINCREMENT is expressed as SERIAL (PostgreSQL)
  or INTEGER PRIMARY KEY AUTOINCREMENT (SQLite) via the ``serial_type``
  helper provided at runtime by the migration runner.
- REAL columns become DOUBLE PRECISION on PostgreSQL.
- TEXT columns are TEXT on both engines (fine for Postgres).
- Boolean columns use INTEGER 0/1 on SQLite, BOOLEAN on PostgreSQL.
"""

DESCRIPTION = "Create all CivicLens SaaS tables"


def up(db):
    """Apply migration: create every table and index.

    ``db`` is a DatabaseManager instance.  Call ``db.execute(sql, params)`` or
    ``db.executescript(sql)`` to run DDL.  The manager handles dialect
    differences (e.g. placeholder style).
    """
    serial = db.serial_type       # "SERIAL" or "INTEGER PRIMARY KEY AUTOINCREMENT"
    real = db.real_type            # "DOUBLE PRECISION" or "REAL"
    bool_type = db.bool_type      # "BOOLEAN" or "INTEGER"
    now_func = db.now_func        # "NOW()" or "datetime('now')"
    conflict = db.conflict_clause  # "ON CONFLICT" vs PostgreSQL upsert syntax

    # ------------------------------------------------------------------
    # tenants  (from auth.py -- tenants.db)
    # ------------------------------------------------------------------
    db.execute("""
        CREATE TABLE IF NOT EXISTS tenants (
            id                      TEXT PRIMARY KEY,
            name                    TEXT NOT NULL,
            granicus_host           TEXT NOT NULL,
            granicus_view_id        TEXT NOT NULL DEFAULT '',
            api_key                 TEXT NOT NULL UNIQUE,
            plan                    TEXT NOT NULL DEFAULT 'starter',
            created_at              TEXT NOT NULL,
            stripe_customer_id      TEXT,
            stripe_subscription_id  TEXT
        )
    """)

    # ------------------------------------------------------------------
    # analytics tables  (from analytics.py -- analytics.db)
    # ------------------------------------------------------------------
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS query_events (
            id                  {serial},
            tenant_id           TEXT NOT NULL,
            question            TEXT NOT NULL,
            model               TEXT,
            response_time_ms    {real},
            chunks_retrieved    INTEGER DEFAULT 0,
            meeting_body        TEXT,
            timestamp           {real} NOT NULL
        )
    """)

    db.execute(f"""
        CREATE TABLE IF NOT EXISTS meeting_processed_events (
            id                      {serial},
            tenant_id               TEXT NOT NULL,
            clip_id                 TEXT NOT NULL,
            processing_time_seconds {real},
            timestamp               {real} NOT NULL
        )
    """)

    db.execute(f"""
        CREATE TABLE IF NOT EXISTS api_call_events (
            id              {serial},
            tenant_id       TEXT NOT NULL,
            endpoint        TEXT NOT NULL,
            status_code     INTEGER,
            duration_ms     {real},
            timestamp       {real} NOT NULL
        )
    """)

    db.execute("CREATE INDEX IF NOT EXISTS idx_query_tenant_ts ON query_events(tenant_id, timestamp)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_query_ts ON query_events(timestamp)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_meeting_tenant_ts ON meeting_processed_events(tenant_id, timestamp)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_api_tenant_ts ON api_call_events(tenant_id, timestamp)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_api_ts ON api_call_events(timestamp)")

    # ------------------------------------------------------------------
    # audit  (from audit.py -- audit.db)
    # ------------------------------------------------------------------
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS audit_log (
            id              {serial},
            timestamp       TEXT NOT NULL,
            timestamp_unix  {real} NOT NULL,
            tenant_id       TEXT NOT NULL,
            user_api_key    TEXT,
            action          TEXT NOT NULL,
            resource_type   TEXT NOT NULL,
            resource_id     TEXT,
            details         TEXT,
            ip_address      TEXT,
            request_id      TEXT,
            integrity_hash  TEXT NOT NULL
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS audit_retention_policies (
            tenant_id       TEXT PRIMARY KEY,
            retention_days  INTEGER NOT NULL DEFAULT 365,
            updated_at      TEXT NOT NULL
        )
    """)

    db.execute("CREATE INDEX IF NOT EXISTS idx_audit_tenant_ts ON audit_log(tenant_id, timestamp_unix)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action, timestamp_unix)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_log(resource_type, resource_id, timestamp_unix)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_audit_request_id ON audit_log(request_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(timestamp_unix)")

    # ------------------------------------------------------------------
    # webhooks  (from webhooks.py -- webhooks.db)
    # ------------------------------------------------------------------
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS webhooks (
            id          TEXT PRIMARY KEY,
            tenant_id   TEXT NOT NULL,
            url         TEXT NOT NULL,
            events      TEXT NOT NULL,
            secret      TEXT NOT NULL,
            active      {bool_type} NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL
        )
    """)

    db.execute("CREATE INDEX IF NOT EXISTS idx_webhooks_tenant ON webhooks(tenant_id)")

    db.execute(f"""
        CREATE TABLE IF NOT EXISTS webhook_deliveries (
            id              TEXT PRIMARY KEY,
            webhook_id      TEXT NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
            event_type      TEXT NOT NULL,
            payload         TEXT NOT NULL,
            status_code     INTEGER,
            response_body   TEXT,
            success         {bool_type} NOT NULL DEFAULT 0,
            attempts        INTEGER NOT NULL DEFAULT 0,
            delivered_at    TEXT NOT NULL,
            duration_ms     {real}
        )
    """)

    db.execute("CREATE INDEX IF NOT EXISTS idx_deliveries_webhook_id ON webhook_deliveries(webhook_id, delivered_at)")

    # ------------------------------------------------------------------
    # scheduler  (from scheduler.py -- scheduler.db)
    # ------------------------------------------------------------------
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS schedules (
            tenant_id       TEXT PRIMARY KEY,
            cron_expression TEXT NOT NULL DEFAULT '0 */6 * * *',
            max_clips       INTEGER NOT NULL DEFAULT 5,
            enabled         {bool_type} NOT NULL DEFAULT 1,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )
    """)

    db.execute(f"""
        CREATE TABLE IF NOT EXISTS job_history (
            id              {serial},
            tenant_id       TEXT NOT NULL REFERENCES schedules(tenant_id),
            started_at      TEXT NOT NULL,
            finished_at     TEXT,
            status          TEXT NOT NULL DEFAULT 'running',
            clips_processed INTEGER NOT NULL DEFAULT 0,
            error_message   TEXT
        )
    """)

    db.execute("CREATE INDEX IF NOT EXISTS idx_job_history_tenant ON job_history(tenant_id, started_at)")

    # ------------------------------------------------------------------
    # SSO  (from sso.py -- sso.db)
    # ------------------------------------------------------------------
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS sso_config (
            tenant_id       TEXT PRIMARY KEY,
            idp_entity_id   TEXT NOT NULL,
            idp_sso_url     TEXT NOT NULL,
            idp_x509_cert   TEXT NOT NULL,
            sp_entity_id    TEXT NOT NULL,
            enabled         {bool_type} NOT NULL DEFAULT 0,
            idp_slo_url     TEXT NOT NULL DEFAULT '',
            default_role    TEXT NOT NULL DEFAULT 'viewer',
            allowed_domains TEXT NOT NULL DEFAULT '',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS sso_users (
            id          TEXT PRIMARY KEY,
            tenant_id   TEXT NOT NULL,
            email       TEXT NOT NULL,
            name        TEXT NOT NULL,
            role        TEXT NOT NULL DEFAULT 'viewer',
            last_login  TEXT,
            created_at  TEXT NOT NULL,
            UNIQUE(tenant_id, email)
        )
    """)

    db.execute("CREATE INDEX IF NOT EXISTS idx_sso_users_tenant ON sso_users(tenant_id)")

    # ------------------------------------------------------------------
    # tracker  (from tracker.py -- tracker.db)
    # ------------------------------------------------------------------
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS alerts (
            id          TEXT PRIMARY KEY,
            tenant_id   TEXT NOT NULL,
            name        TEXT NOT NULL,
            type        TEXT NOT NULL,
            config      TEXT NOT NULL,
            enabled     {bool_type} NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS alert_matches (
            id              TEXT PRIMARY KEY,
            alert_id        TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
            clip_id         TEXT NOT NULL,
            match_type      TEXT NOT NULL,
            matched_text    TEXT NOT NULL,
            context         TEXT NOT NULL,
            created_at      TEXT NOT NULL
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS votes (
            id                      TEXT PRIMARY KEY,
            clip_id                 TEXT NOT NULL,
            identifier              TEXT,
            description             TEXT,
            motion_by               TEXT,
            second_by               TEXT,
            outcome                 TEXT,
            vote_type               TEXT,
            ayes                    INTEGER DEFAULT 0,
            nays                    INTEGER DEFAULT 0,
            abstentions             INTEGER DEFAULT 0,
            votes_for               TEXT,
            votes_against           TEXT,
            conditions              TEXT,
            transcript_approx_time  TEXT,
            meeting_date            TEXT,
            meeting_body            TEXT
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS financial_items (
            id                  TEXT PRIMARY KEY,
            clip_id             TEXT NOT NULL,
            description         TEXT,
            amount              TEXT,
            amount_cents        INTEGER,
            type                TEXT,
            identifier          TEXT,
            vendor_or_recipient TEXT,
            meeting_date        TEXT,
            meeting_body        TEXT
        )
    """)

    db.execute("CREATE INDEX IF NOT EXISTS idx_alerts_tenant ON alerts(tenant_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_matches_alert ON alert_matches(alert_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_matches_clip ON alert_matches(clip_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_votes_clip ON votes(clip_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_votes_member ON votes(motion_by)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_votes_outcome ON votes(outcome)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_votes_date ON votes(meeting_date)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_financial_clip ON financial_items(clip_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_financial_amount ON financial_items(amount_cents)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_financial_date ON financial_items(meeting_date)")

    # ------------------------------------------------------------------
    # push subscriptions  (from push.py -- push.db)
    # ------------------------------------------------------------------
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id          {serial},
            tenant_id   TEXT NOT NULL,
            user_id     TEXT NOT NULL DEFAULT '',
            endpoint    TEXT NOT NULL UNIQUE,
            keys_json   TEXT NOT NULL,
            created_at  {real} NOT NULL,
            last_used   {real}
        )
    """)

    db.execute("CREATE INDEX IF NOT EXISTS idx_push_tenant ON push_subscriptions(tenant_id)")

    # ------------------------------------------------------------------
    # branding  (from branding.py -- branding.db)
    # ------------------------------------------------------------------
    db.execute("""
        CREATE TABLE IF NOT EXISTS branding (
            tenant_id       TEXT PRIMARY KEY,
            display_name    TEXT NOT NULL DEFAULT '',
            logo_url        TEXT NOT NULL DEFAULT '',
            primary_color   TEXT NOT NULL DEFAULT '#1a56db',
            secondary_color TEXT NOT NULL DEFAULT '#1e293b',
            accent_color    TEXT NOT NULL DEFAULT '#f59e0b',
            favicon_url     TEXT NOT NULL DEFAULT '',
            custom_css      TEXT NOT NULL DEFAULT '',
            welcome_message TEXT NOT NULL DEFAULT '',
            footer_text     TEXT NOT NULL DEFAULT '',
            support_email   TEXT NOT NULL DEFAULT ''
        )
    """)

    # ------------------------------------------------------------------
    # health snapshots  (from customer_health.py -- health.db)
    # ------------------------------------------------------------------
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS health_snapshots (
            id                  {serial},
            tenant_id           TEXT NOT NULL,
            score               {real} NOT NULL,
            category            TEXT NOT NULL,
            trend               TEXT NOT NULL,
            query_frequency     {real} NOT NULL DEFAULT 0,
            feature_breadth     {real} NOT NULL DEFAULT 0,
            user_engagement     {real} NOT NULL DEFAULT 0,
            data_freshness      {real} NOT NULL DEFAULT 0,
            alert_activity      {real} NOT NULL DEFAULT 0,
            support_signals     {real} NOT NULL DEFAULT 0,
            billing_health      {real} NOT NULL DEFAULT 0,
            recommendations     TEXT NOT NULL DEFAULT '[]',
            expansion_signals   TEXT NOT NULL DEFAULT '[]',
            computed_at         TEXT NOT NULL
        )
    """)

    db.execute("CREATE INDEX IF NOT EXISTS idx_health_tenant ON health_snapshots(tenant_id, computed_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_health_date ON health_snapshots(computed_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_health_category ON health_snapshots(category)")

    # ------------------------------------------------------------------
    # export jobs and FOIA requests  (from export.py -- exports.db)
    # ------------------------------------------------------------------
    db.execute("""
        CREATE TABLE IF NOT EXISTS export_jobs (
            job_id          TEXT PRIMARY KEY,
            tenant_id       TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending',
            format          TEXT NOT NULL DEFAULT 'json',
            filters         TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL,
            completed_at    TEXT,
            file_path       TEXT,
            file_size_bytes INTEGER,
            total_meetings  INTEGER,
            error           TEXT
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS foia_requests (
            request_id      TEXT PRIMARY KEY,
            tenant_id       TEXT NOT NULL,
            query           TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending',
            created_at      TEXT NOT NULL,
            completed_at    TEXT,
            result          TEXT,
            error           TEXT
        )
    """)

    db.execute("CREATE INDEX IF NOT EXISTS idx_export_jobs_tenant ON export_jobs(tenant_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_foia_requests_tenant ON foia_requests(tenant_id)")


def down(db):
    """Reverse migration: drop all tables in dependency order."""
    tables = [
        # Drop children before parents (FK order)
        "webhook_deliveries",
        "alert_matches",
        "job_history",
        "foia_requests",
        "export_jobs",
        "health_snapshots",
        "push_subscriptions",
        "branding",
        "sso_users",
        "sso_config",
        "financial_items",
        "votes",
        "alerts",
        "schedules",
        "webhooks",
        "api_call_events",
        "meeting_processed_events",
        "query_events",
        "audit_retention_policies",
        "audit_log",
        "tenants",
    ]
    for table in tables:
        db.execute(f"DROP TABLE IF EXISTS {table}")
