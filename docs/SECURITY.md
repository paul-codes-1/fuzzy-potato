# CivicLens Security Practices

Last reviewed: April 9, 2026

This document describes the security controls that are verifiably present in this repository today. It intentionally separates current implementation from roadmap items.

## What Exists Today

### Deployment Shapes

The codebase currently supports two practical operating modes:

- a local or self-hosted single-node deployment where application state lives under `MEETINGS_OUTPUT_DIR`
- an AWS reference deployment using CloudFront, S3 for frontend assets, and App Runner for the API

The AWS Terraform in `infra/` does **not** currently provision an ALB or WAF. The API reference deployment is CloudFront -> App Runner, not CloudFront -> WAF -> ALB.

### Transport Security

- TLS is expected at the edge when deployed behind HTTPS infrastructure.
- In the AWS reference deployment, certificates are managed through ACM and traffic terminates at CloudFront.
- `api/server.py` adds these response headers on API responses:
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `Strict-Transport-Security: max-age=63072000; includeSubDomains`
  - `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'`
  - `X-XSS-Protection: 1; mode=block`
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Permissions-Policy: camera=(), microphone=(), geolocation=()`

### Authentication and Authorization

- Tenant API keys are sent via `X-API-Key`.
- Platform-admin routes use a separate `ADMIN_API_KEY` environment variable.
- Tenant API keys are currently stored in `tenants.db` as plaintext values, not as one-way hashes.
- Optional SAML SSO issues JWT session tokens for browser sessions.
- SSO user-management and SSO-config routes now require the `admin` role when the caller is authenticated via JWT.
- Tenant API keys are still coarse-grained tenant credentials and should be treated as full-tenant secrets.

### Rate Limiting

- Rate limiting is implemented in-process in `api/auth.py`.
- The limiter uses a rolling 30-day window keyed by tenant.
- Limits reset on process restart.
- The current limiter is not distributed and is not safe to treat as a shared global control across multiple web instances.

### Tenant Isolation

- Tenant scoping is primarily enforced in application code.
- RAG queries filter on `tenant_id` metadata where available.
- Audit, export, branding, search, and other tenant-facing routes scope results to the authenticated tenant.
- Isolation is not currently backed by database-native row-level security.

### Audit Logging

- The API assigns `X-Request-ID` values and returns them in responses.
- `api/audit.py` stores audit events in SQLite with a SHA-256 integrity chain.
- API keys written to the audit log are masked before storage.
- Audit data can be searched, exported, and verified through `/api/v1/audit/*` routes.

### Secrets Handling

- Local development typically relies on `.env`.
- The AWS reference Terraform provisions an AWS Secrets Manager secret for app secrets.
- Secrets are read by the application at runtime through environment variables.

## Storage and Encryption

### Current Data Locations

By default, CivicLens stores most state on the local filesystem under `MEETINGS_OUTPUT_DIR`, including:

- meeting artifacts and transcripts
- Chroma vector data
- multiple SQLite databases such as `tenants.db`, `audit.db`, `analytics.db`, `exports.db`, `scheduler.db`, and `sso.db`

### At-Rest Encryption

The application does **not** currently perform its own file-level encryption for:

- tenant API keys
- SQLite databases
- Chroma data
- meeting artifacts on disk

At-rest encryption therefore depends on the underlying platform:

- encrypted host disks or volumes for self-hosted installs
- managed cloud storage and volume encryption when configured by the operator

`api/backup.py` supports optional S3 upload targets for backup archives, but that should not be read as a blanket guarantee that all production data is stored in S3.

## Controls That Are Not Present Yet

These are common enterprise controls that the repo does not fully implement today:

- hashed tenant API keys at rest
- fine-grained RBAC across all tenant-scoped routes
- distributed rate limiting
- queue-backed background workers
- shared durable persistence for all operational state
- database row-level security
- WAF configuration in the provided Terraform
- verified request body size limits in the API layer
- verified automated dependency vulnerability scanning in CI

## Production Guidance

If you deploy this today, treat these as minimum operator requirements:

- set `AUTH_REQUIRED=true`
- set a stable `JWT_SECRET`
- set explicit `CORS_ORIGINS`
- put the API behind HTTPS
- use persistent storage for `MEETINGS_OUTPUT_DIR`
- rotate tenant API keys on loss or suspected exposure
- back up both meeting artifacts and SQLite databases
- assume this is a single-node or carefully controlled deployment unless you complete the database and worker migration work described in `ARCHITECTURE_REVIEW.md`

## Compliance Position

The repo includes useful compliance-oriented features such as audit logging, exports, and SSO support, but the current implementation should be described as **enterprise-oriented** rather than fully enterprise-hardened.

Use `ARCHITECTURE_REVIEW.md` as the authoritative description of the remaining work.
