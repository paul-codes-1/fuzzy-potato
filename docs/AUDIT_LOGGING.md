# Audit Logging

CivicLens includes an append-only, tamper-evident audit logging system designed for government compliance requirements (SOC 2, FISMA). Every API request is automatically logged with full context.

## Overview

- **Append-only** -- Audit rows are never updated or deleted during normal operation.
- **Integrity chain** -- Each entry includes a SHA-256 hash chained to the previous entry, creating a tamper-evident log. Any modification to historical entries breaks the chain and is detectable.
- **Automatic capture** -- The `AuditMiddleware` logs every API request (method, path, status code, duration, tenant, IP address) without any code changes in endpoint handlers.
- **Per-tenant isolation** -- Tenants can only view their own audit events. Admins can query across all tenants.
- **Configurable retention** -- Per-tenant retention policies (default 365 days, up to 7 years for enterprise).

## Audit Events

Every audit entry includes:

| Field | Description |
|---|---|
| `timestamp` | ISO 8601 timestamp (UTC) |
| `tenant_id` | Tenant that triggered the event |
| `user_api_key` | Masked API key (`mra_***last4`) |
| `action` | Event type (see below) |
| `resource_type` | Category of affected resource |
| `resource_id` | Specific resource identifier |
| `details` | JSON object with event-specific context |
| `ip_address` | Client IP address |
| `request_id` | Correlation ID for the HTTP request |
| `integrity_hash` | SHA-256 hash chained to previous entry |

### Action Types

| Action | Description |
|---|---|
| `api.request` | Any API request (auto-logged by middleware) |
| `query.asked` | Q&A question submitted |
| `query.answered` | Q&A answer returned |
| `meeting.processed` | New meeting clip processed |
| `meeting.deleted` | Meeting data deleted |
| `tenant.created` | New tenant provisioned |
| `tenant.updated` | Tenant settings changed |
| `tenant.deleted` | Tenant removed |
| `api_key.rotated` | API key rotated |
| `export.started` | Data export initiated |
| `export.downloaded` | Export file downloaded |
| `webhook.registered` | Webhook endpoint registered |
| `webhook.deleted` | Webhook endpoint removed |
| `schedule.updated` | Ingestion schedule changed |
| `branding.updated` | Branding configuration changed |
| `billing.subscription_changed` | Billing plan changed |

## API Endpoints

### Tenant Endpoints (scoped to own data)

**Search audit log:**
```bash
curl "https://api.civiclens.ai/api/v1/audit?action=query.asked&limit=20" \
  -H "X-API-Key: mra_your_key"
```

Query parameters: `action`, `resource_type`, `resource_id`, `date_after`, `date_before`, `request_id`, `limit` (1-500), `offset`.

**Export audit log:**
```bash
# CSV export
curl "https://api.civiclens.ai/api/v1/audit/export?format=csv" \
  -H "X-API-Key: mra_your_key"

# JSON export
curl "https://api.civiclens.ai/api/v1/audit/export?format=json&date_after=2026-01-01" \
  -H "X-API-Key: mra_your_key"
```

**Audit statistics:**
```bash
curl "https://api.civiclens.ai/api/v1/audit/stats?days=30" \
  -H "X-API-Key: mra_your_key"
```

Returns event counts by action type and by day.

**Verify integrity:**
```bash
curl "https://api.civiclens.ai/api/v1/audit/verify" \
  -H "X-API-Key: mra_your_key"
```

Returns `{"valid": true, "checked": 1234, "first_broken_id": null}` if the hash chain is intact.

### Admin Endpoints (cross-tenant)

All admin endpoints require the `ADMIN_API_KEY` header.

- `GET /api/v1/admin/audit` -- Cross-tenant search (add `tenant_id` query param to filter)
- `GET /api/v1/admin/audit/export` -- Cross-tenant export
- `GET /api/v1/admin/audit/stats` -- Cross-tenant statistics
- `GET /api/v1/admin/audit/verify` -- Verify integrity chain
- `POST /api/v1/admin/audit/purge` -- Delete entries past their retention policy
- `PUT /api/v1/admin/audit/retention/{tenant_id}?days=730` -- Set per-tenant retention

## Retention Policies

| Plan | Default Retention |
|---|---|
| Starter | 365 days |
| Pro | 365 days |
| Enterprise | 2,555 days (~7 years) |

Admins can override retention per tenant. The `purge` endpoint deletes entries older than the configured retention period.

## Integrity Verification

The audit log uses a hash chain for tamper detection. Each entry's `integrity_hash` is computed as:

```
SHA-256(previous_hash | timestamp | tenant_id | action | resource_type | resource_id | details)
```

If any row is modified after the fact, all subsequent hashes will fail verification. Run the verify endpoint periodically or as part of compliance audits.

## Storage

Audit data is stored in SQLite with WAL (Write-Ahead Logging) mode for concurrent read/write performance. The database file is located at `{MEETINGS_OUTPUT_DIR}/audit.db`.

For high-volume deployments, the SQLite backend can be replaced with PostgreSQL by implementing the same interface in a custom `AuditLogger` subclass.
