# Database Configuration Guide

CivicLens supports two database backends: **SQLite** for development and single-instance deployments, and **PostgreSQL** for production multi-instance deployments.

## Backend Selection

The database backend is selected via the `DATABASE_URL` environment variable:

| Value                                | Backend    | Use Case              |
|--------------------------------------|------------|-----------------------|
| _(unset or empty)_                   | SQLite     | Local dev (auto-creates `civiclens.db` in output dir) |
| `sqlite:///path/to/file.db`          | SQLite     | Explicit SQLite path  |
| `postgresql://user:pass@host/dbname` | PostgreSQL | Production            |

### Connection String Format (PostgreSQL)

```
postgresql://USERNAME:PASSWORD@HOSTNAME:PORT/DATABASE_NAME
```

Examples:
```bash
# Local PostgreSQL
DATABASE_URL=postgresql://civiclens:secret@localhost:5432/civiclens

# AWS RDS
DATABASE_URL=postgresql://civiclens:secret@mydb.abc123.us-east-1.rds.amazonaws.com:5432/civiclens

# With SSL (RDS requires this)
DATABASE_URL=postgresql://civiclens:secret@mydb.abc123.us-east-1.rds.amazonaws.com:5432/civiclens?sslmode=require
```

## SQLite vs PostgreSQL Comparison

| Feature                    | SQLite                         | PostgreSQL                         |
|----------------------------|--------------------------------|------------------------------------|
| Setup                      | Zero config, file-based        | Requires server                    |
| Concurrency                | Single-writer, WAL for readers | Full MVCC, many concurrent writers |
| Horizontal scaling         | Single instance only           | Read replicas, connection poolers  |
| Backup                     | Copy the file                  | pg_dump, WAL archiving, snapshots  |
| Max database size          | ~281 TB (practical: single GB) | Unlimited (practical: many TB)     |
| Connection pooling         | N/A (single connection)        | Built-in pool (min 2, max 10)     |
| Crash recovery             | WAL journal                    | WAL + point-in-time recovery       |
| Cost                       | Free                           | Free (self-hosted) or managed      |
| Recommended for            | Dev, demos, small deployments  | Production, enterprise, multi-node |

## Schema Management

All tables are managed through versioned migrations in `api/migrations/`.

### Migration Files

Each migration is a numbered Python file:
```
api/migrations/
  __init__.py
  001_initial.py      # Creates all tables and indexes
  002_add_feature.py  # Future migrations
```

Every migration module exports:
- `up(db)` -- apply the migration (required)
- `down(db)` -- reverse the migration (optional, for development rollback)

### How Migrations Run

Migrations run automatically when `init_database()` is called at application startup. The `schema_migrations` table tracks which versions have been applied:

```sql
SELECT * FROM schema_migrations;
-- version | name         | applied_at
-- 1       | 001_initial  | 2026-04-08T...
```

Migrations are idempotent: re-running the application will skip already-applied versions.

### Manual Rollback (Development Only)

```python
from api.database import get_database
db = get_database()
db.rollback_migration(1)  # Rolls back migration 001
```

## Migration Guide: SQLite to PostgreSQL

### Prerequisites

1. A running PostgreSQL 14+ instance
2. `psycopg2-binary` installed (`pip install psycopg2-binary` or `uv sync --extra postgres`)

### Step-by-Step Migration

**1. Create the PostgreSQL database**

```bash
createdb civiclens
# Or via psql:
psql -c "CREATE DATABASE civiclens;"
psql -c "CREATE USER civiclens WITH PASSWORD 'your-secure-password';"
psql -c "GRANT ALL PRIVILEGES ON DATABASE civiclens TO civiclens;"
```

**2. Set the DATABASE_URL**

```bash
# In your .env file
DATABASE_URL=postgresql://civiclens:your-secure-password@localhost:5432/civiclens
```

**3. Start the application**

The migration system will automatically create all tables in PostgreSQL:

```bash
uv run uvicorn api.server:app --port 8000
```

**4. Migrate existing data (optional)**

If you have data in existing SQLite databases, use this approach to export and import:

```bash
# Export from SQLite
sqlite3 lfucg_output/tenants.db ".mode csv" ".headers on" "SELECT * FROM tenants;" > tenants.csv
sqlite3 lfucg_output/analytics.db ".mode csv" ".headers on" "SELECT * FROM query_events;" > query_events.csv
# ... repeat for each table

# Import into PostgreSQL
psql civiclens -c "\COPY tenants FROM 'tenants.csv' CSV HEADER"
psql civiclens -c "\COPY query_events FROM 'query_events.csv' CSV HEADER"
# ... repeat for each table
```

For large datasets, consider using `pgloader` which can read SQLite files directly:

```bash
pgloader sqlite:///path/to/analytics.db postgresql://civiclens:pass@localhost/civiclens
```

## Production PostgreSQL Recommendations

### AWS RDS Configuration

| Setting                 | Recommended Value        | Notes                               |
|-------------------------|--------------------------|-------------------------------------|
| Instance class          | db.t3.medium (start)     | Scale up as needed                  |
| Storage                 | 20 GB gp3 (start)       | Auto-scaling enabled                |
| Multi-AZ               | Yes (production)          | Automatic failover                  |
| Encryption              | Yes                      | At-rest encryption via KMS          |
| Backup retention        | 7 days minimum           | 35 days for compliance workloads    |
| Performance Insights    | Enabled                  | Free tier for 7-day retention       |
| Parameter group         | Custom (see below)       |                                     |

### PostgreSQL Parameter Tuning

For a `db.t3.medium` (2 vCPU, 4 GB RAM):

```
shared_buffers = 1GB
effective_cache_size = 3GB
work_mem = 16MB
maintenance_work_mem = 256MB
max_connections = 100
```

### Connection Pooling

CivicLens includes built-in connection pooling (psycopg2 SimpleConnectionPool, min 2 / max 10). For larger deployments, consider an external pooler:

- **PgBouncer** -- lightweight, battle-tested. Run as a sidecar or separate service.
- **RDS Proxy** -- managed by AWS, transparent to the application.

Configure the pool size via environment variable:

```bash
DB_POOL_MAX=20  # Default: 10
```

### SSL / TLS

Always use SSL in production:

```bash
DATABASE_URL=postgresql://user:pass@host/db?sslmode=require
```

RDS provides SSL certificates automatically. For self-hosted PostgreSQL, configure `sslmode=verify-full` with the CA certificate.

## Backup Strategy

### SQLite Backups

SQLite databases are regular files. Back them up with any file-level backup tool:

```bash
# Safe backup (uses SQLite's backup API)
sqlite3 civiclens.db ".backup backup.db"

# Or simply copy (safe if using WAL mode and no writers)
cp civiclens.db civiclens-backup-$(date +%Y%m%d).db
```

### PostgreSQL Backups

**Logical backups (pg_dump)**

```bash
# Full backup
pg_dump -Fc civiclens > civiclens-$(date +%Y%m%d).dump

# Restore
pg_restore -d civiclens civiclens-20260408.dump
```

**Continuous archiving (WAL)**

For point-in-time recovery, enable WAL archiving:

```
archive_mode = on
archive_command = 'aws s3 cp %p s3://my-bucket/wal/%f'
```

**AWS RDS automated backups**

RDS handles this automatically:
- Daily snapshots retained for the configured retention period
- Transaction logs archived every 5 minutes
- Point-in-time restore to any second within the retention window

### Recommended Backup Schedule

| Environment | Method               | Frequency     | Retention |
|-------------|----------------------|---------------|-----------|
| Development | SQLite file copy     | Manual        | N/A       |
| Staging     | pg_dump              | Daily         | 7 days    |
| Production  | RDS automated + WAL  | Continuous    | 35 days   |
| Compliance  | RDS + cross-region   | Continuous    | 7 years   |

## Monitoring

### Health Check

The database manager exposes a health check method:

```python
from api.database import get_database
db = get_database()
print(db.health_check())
# {"status": "ok", "backend": "postgresql"}
```

### Table Statistics

```python
print(db.table_stats())
# {"tenants": 12, "query_events": 45230, "votes": 892, ...}
```

### PostgreSQL Monitoring Queries

```sql
-- Active connections
SELECT count(*) FROM pg_stat_activity WHERE datname = 'civiclens';

-- Table sizes
SELECT relname, pg_size_pretty(pg_total_relation_size(relid))
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC;

-- Slow queries (requires pg_stat_statements extension)
SELECT query, calls, mean_exec_time, total_exec_time
FROM pg_stat_statements
ORDER BY mean_exec_time DESC LIMIT 10;

-- Index usage
SELECT indexrelname, idx_scan, idx_tup_read
FROM pg_stat_user_indexes
ORDER BY idx_scan DESC;
```
