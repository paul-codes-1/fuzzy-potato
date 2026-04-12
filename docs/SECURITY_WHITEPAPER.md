# CivicLens Security Whitepaper

| Field | Value |
|---|---|
| Document version | 1.0 |
| Last updated | 2026-04-10 |
| Classification | Public |
| Owner | CivicLens Security (security@civiclens.io) |
| Intended audience | CISOs, security architects, compliance officers, procurement |

> This whitepaper expands on the internal reference document `docs/SECURITY.md` and the audit system reference `docs/AUDIT_LOGGING.md`. Where those documents describe verifiable controls in the current codebase, this document positions them for an enterprise security reviewer and explicitly marks items that are in progress or planned.

---

## 1. Executive Summary

CivicLens is a multi-tenant SaaS platform that turns Granicus-hosted government meeting archives into searchable, queryable knowledge bases. It performs AI-powered transcription, structured data extraction, and retrieval-augmented question answering for city and county governments.

Because customers are public-sector entities subject to records laws, open-meeting laws, and state and federal security frameworks, CivicLens is engineered with the following security principles:

1. **Tenant isolation by default.** Every tenant has its own identifier, API key, data directory, and metadata-scoped vector embeddings. Application code enforces `tenant_id` scoping on every read path.
2. **Tamper-evident auditability.** Every API request is recorded in an append-only SQLite audit log with a SHA-256 integrity chain. Any retroactive modification is detectable.
3. **Least privilege and defense in depth.** Non-root container runtime, a hardened HTTP middleware stack, per-tenant rate limits, separate administrative credentials, and role-based permissions for SSO users.
4. **Transparent posture.** We publish what is implemented today, what is planned, and what is explicitly delegated to the operator in a shared responsibility model. We do not claim certifications that are not yet attested.

The remainder of this document maps CivicLens controls to the Trust Services Criteria (SOC 2), NIST 800-53 (FISMA moderate baseline), and the CJIS Security Policy for customers handling law enforcement records. It also documents subprocessors, dependency management, incident response, and the customer side of the shared responsibility model.

CivicLens is currently positioned as **enterprise-oriented** rather than fully enterprise-hardened. SOC 2 Type I observation is **planned**, with Type II readiness activity **in progress**. Customers with regulated workloads should read Section 11 carefully before procurement.

---

## 2. Architecture Overview

### 2.1 Logical architecture

CivicLens is a FastAPI application fronted by CloudFront (or an equivalent TLS-terminating edge) and deployed as a containerized service. State lives under a single configurable directory (`MEETINGS_OUTPUT_DIR`) that aggregates per-tenant artifacts, several SQLite databases, and the ChromaDB vector store. PostgreSQL is supported as an optional backend for production multi-instance deployments.

```
             +-------------------------------+
             |          End users            |
             |  (browser, widget, API, SDKs) |
             +---------------+---------------+
                             | HTTPS (TLS 1.2+)
                             v
             +-------------------------------+
             |   CloudFront / Edge (ACM)     |
             |     HSTS, cert management     |
             +---------------+---------------+
                             |
                             v
             +-------------------------------+
             |  CivicLens API (FastAPI)      |
             |  - AuditMiddleware            |
             |  - RequestIDMiddleware        |
             |  - SecurityHeadersMiddleware  |
             |  - CORSMiddleware             |
             +---+-------------+-----+-------+
                 |             |     |
      tenant-    |             |     |  admin-
      scoped     |             |     |  scoped
                 v             v     v
          +-----------+  +--------+  +------------+
          | tenants.db|  | audit  |  | analytics  |
          | (SQLite)  |  |  .db   |  |    .db     |
          +-----------+  +--------+  +------------+
                 |
                 v
          +-------------------------------------+
          | meetings_output/{tenant_id}/clips/  |
          |   transcripts, summaries, facts     |
          +-------------------------------------+
                 |
                 v
          +-------------------------------+
          | ChromaDB (tenant_id metadata) |
          +-------------------------------+
                 |
                 v (outbound only)
          +-------------------------------+
          | OpenAI, Anthropic, Stripe,    |
          | Granicus, SMTP, S3 (backups)  |
          +-------------------------------+
```

### 2.2 Tenant isolation model

Each tenant represents a city or jurisdiction. Isolation is implemented through a combination of identity, path, and metadata scoping:

| Layer | Control |
|---|---|
| Identity | Every request resolves to exactly one `Tenant` via `api/auth.py::_resolve_tenant`. |
| Storage | Meeting artifacts are written under `meetings_output/{tenant_id}/clips/` (the pipeline accepts `--tenant-id`). |
| Relational | Tenant-scoped rows include `tenant_id` in `tenants.db`, `analytics.db`, `audit.db`, `scheduler.db`, `sso.db`, `exports.db`. |
| Vector store | ChromaDB chunks carry `tenant_id` metadata and queries apply a `where={"tenant_id": ...}` filter. |
| Cross-tenant access | Prevented at the application layer. Admin queries use a separate `ADMIN_API_KEY` checked by `require_admin`. |

Isolation is currently **enforced in application code** rather than by database-native row-level security (RLS). Adding native RLS on the PostgreSQL backend is on the roadmap.

### 2.3 Network topology (AWS reference deployment)

The reference Terraform in `infra/` provisions CloudFront fronting App Runner. There is **no ALB or WAF in the provided Terraform today**. Operators who require a WAF layer should place AWS WAF in front of CloudFront or terminate behind an ALB/WAF of their own. All application traffic egresses to managed AI providers (OpenAI, Anthropic) and to Stripe/Granicus over HTTPS.

---

## 3. Authentication and Authorization

CivicLens supports three authentication paths, resolved in order by `api/auth.py::_resolve_tenant`:

1. **Tenant API key** via the `X-API-Key` header. Used for programmatic access, SDKs, webhooks, and integrations.
2. **JWT bearer token** in the `Authorization: Bearer ...` header. Issued after a successful SAML assertion.
3. **`civiclens_session` cookie.** Same JWT as method 2, used by the browser SPA.

If none are supplied and the installation has no tenants provisioned (and `AUTH_REQUIRED` is unset), a development fallback tenant is returned. Production operators **must** set `AUTH_REQUIRED=true` to eliminate this fallback.

### 3.1 Tenant API keys

- Format: `mra_` prefix + 32 bytes from `secrets.token_urlsafe(32)` (approximately 256 bits of entropy).
- Generated at tenant creation via `TenantStore.create`; rotatable via `TenantStore.rotate_key` or `POST /api/v1/admin/tenants/{id}/rotate-key`.
- Uniqueness is enforced at the database level (`UNIQUE` constraint on `tenants.api_key`).
- Keys are masked in audit logs as `mra_***last4` by `api/audit.py::mask_api_key`.
- Administrative access uses a separate `ADMIN_API_KEY` environment variable, checked by `require_admin`, and must differ from any tenant-facing key.

> **Known gap (tracked).** Tenant API keys are currently stored as plaintext in `tenants.db`, not as one-way hashes. Moving to hashed-at-rest tenant keys is a planned hardening item. Operators should restrict direct filesystem access to `MEETINGS_OUTPUT_DIR` and rely on platform-level encryption at rest for the underlying volume.

### 3.2 JWT sessions and SSO

SAML 2.0 SSO is implemented in `api/sso.py` using `python3-saml`. On a successful assertion, CivicLens issues an HS256-signed JWT session token with the following characteristics:

| Parameter | Value |
|---|---|
| Algorithm | HS256 (symmetric, signed with `JWT_SECRET`) |
| Expiry | 8 hours (`JWT_EXPIRY_SECONDS = 8 * 3600`) |
| Refresh window | 30 minutes before expiry |
| Claims | `tenant_id`, `user_id`, `email`, `role`, `exp`, `iat` |
| Transport | `Authorization: Bearer ...` or `civiclens_session` HttpOnly cookie |

`JWT_SECRET` must be set to a strong, stable secret in production. If it is absent, the SSO module refuses to issue tokens.

### 3.3 Role-based access control

`api/sso.py` defines three roles with explicit permission sets:

| Role | Permissions |
|---|---|
| `admin` | `query`, `export`, `alerts`, `settings`, `manage_users`, `sso_config` |
| `analyst` | `query`, `export`, `alerts` |
| `viewer` | `query` |

SSO user-management and SSO-config routes require the `admin` role when the caller is authenticated via JWT. API-key callers are coarse-grained tenant credentials and bypass the per-user role check; treat tenant API keys as full-tenant secrets. Fine-grained RBAC for API-key callers is a planned enhancement.

### 3.4 Session management

- Tokens are stateless and verified on every request.
- Sign-out is handled client-side by discarding the token or clearing the cookie; SLO (Single Logout) is supported when the IdP provides `idp_slo_url`.
- Replay protection relies on `exp` and `iat` claims. Shortening the expiry below 8 hours is supported via code change.

---

## 4. Data Protection

### 4.1 Data at rest

CivicLens does **not** implement its own file-level encryption for tenant API keys, SQLite databases, ChromaDB data, or meeting artifacts. At-rest encryption depends on the underlying platform:

| Data store | Encryption source |
|---|---|
| `tenants.db`, `audit.db`, etc. (SQLite) | Host disk / volume encryption (EBS, LUKS, FileVault) |
| `chroma_db/` (vector store) | Host disk / volume encryption |
| Meeting artifacts (`meetings_output/{tenant_id}/clips/`) | Host disk / volume encryption |
| Optional S3 backup targets (`api/backup.py`) | S3 SSE (AES-256 or KMS), operator-configured |
| Optional PostgreSQL (`api/database.py`) | Native database encryption or volume/TDE |

Operators deploying on AWS should enable EBS encryption on the App Runner / EC2 volumes, KMS-managed encryption on any S3 backup bucket, and RDS encryption if they adopt the PostgreSQL backend. Secrets referenced through `.env` or AWS Secrets Manager are never written to disk by the application.

### 4.2 Data in transit

- TLS is terminated at the edge. In the reference deployment, certificates are issued and rotated through AWS Certificate Manager.
- The API emits `Strict-Transport-Security: max-age=63072000; includeSubDomains` on every response.
- Outbound calls to OpenAI, Anthropic, Stripe, Granicus, and SMTP providers use HTTPS/TLS 1.2 or newer as provided by the respective client libraries.
- No plaintext HTTP listener is exposed; CORS origins are configurable via `CORS_ORIGINS` and should be set explicitly (not `*`) in production.

### 4.3 Tenant isolation in queries

Every tenant-scoped route dereferences the authenticated `Tenant` and uses it as a filter. For example:

- RAG retrieval filters ChromaDB on `tenant_id` metadata.
- Audit search (`/api/v1/audit`) returns only the caller's own events; cross-tenant search requires `ADMIN_API_KEY`.
- Exports, analytics, branding, scheduler, and search all scope to `tenant_id`.
- File I/O uses `os.path.join(output_dir, tenant_id, ...)` and the pipeline writes under tenant-scoped directories.

---

## 5. Audit and Compliance

### 5.1 Immutable audit log

`api/audit.py` implements an append-only audit logger backed by SQLite (WAL mode) with a SHA-256 integrity chain. Each row's `integrity_hash` is computed as:

```
SHA-256(previous_hash | timestamp | tenant_id | action | resource_type | resource_id | details)
```

Any retroactive modification to an earlier row invalidates every subsequent hash. The chain can be verified on demand via `GET /api/v1/audit/verify` (returns `{"valid": true, "checked": N, "first_broken_id": null}`).

### 5.2 What gets logged

Every API request is automatically captured by `AuditMiddleware`, which records the tenant, masked API key, method, path, status code, duration, IP address, and `X-Request-ID`. Domain actions such as `meeting.processed`, `query.asked`, `export.started`, `api_key.rotated`, `tenant.created`, `billing.subscription_changed`, and more are logged from the application layer. The full action catalog is in `docs/AUDIT_LOGGING.md`.

| Field | Description |
|---|---|
| `timestamp` | ISO 8601 UTC |
| `tenant_id` | Tenant that triggered the event |
| `user_api_key` | Masked (`mra_***last4`) |
| `action` | Event type |
| `resource_type`, `resource_id` | Affected resource |
| `details` | JSON blob with event-specific context |
| `ip_address`, `request_id` | Client IP and correlation ID |
| `integrity_hash` | SHA-256 chained hash |

### 5.3 Retention

Retention is per-tenant and configurable:

| Plan | Default retention |
|---|---|
| Starter | 365 days |
| Pro | 365 days |
| Enterprise | 2,555 days (~7 years) |

Admins can override per tenant via `PUT /api/v1/admin/audit/retention/{tenant_id}`. A separate purge endpoint (`POST /api/v1/admin/audit/purge`) deletes entries past their retention policy; purge is the **only** sanctioned path by which audit rows are ever removed. All purge events are themselves audit-logged.

### 5.4 SOC 2 alignment

CivicLens controls map to the SOC 2 Trust Services Criteria as follows. SOC 2 Type I observation is **planned**; this mapping is for readiness.

| TSC | Criterion | CivicLens control |
|---|---|---|
| CC6.1 | Logical access | API key + JWT + SAML; `require_tenant`, `require_admin` |
| CC6.2 | Registration & authorization | Tenant provisioning via CLI/admin API; onboarding flow |
| CC6.3 | Access removal | `TenantStore.delete`, `rotate_key`; SAML user lifecycle |
| CC6.6 | Boundary protections | TLS at edge, security headers, CORS, tenant scoping |
| CC6.7 | Transmission integrity | TLS, HSTS, HMAC-signed webhooks |
| CC6.8 | Malicious software prevention | Non-root Docker, read-only code image, dependency pinning |
| CC7.1 | Anomaly detection | Request IDs, audit log, rate-limit 429s surfaced |
| CC7.2 | Monitoring | Structured logging (`api/logging_config.py`), health checks |
| CC7.3 | Incident response | See Section 7.4 |
| CC7.4 | Incident recovery | Backups via `api/backup.py`, exports |
| A1.1 | Availability commitments | Health endpoints, status page (`api/status.py`) |
| PI1.1 | Input processing | Parameterized queries, Pydantic schemas |
| C1.1 | Confidentiality | Tenant isolation, masked keys in logs |
| P4.1 | Privacy collection | Minimal PII (transcripts + speaker names only) |

### 5.5 FISMA / NIST 800-53 readiness

For customers subject to FISMA (moderate baseline), the following control mappings apply. Full ATO support is **not claimed**; this table is intended to accelerate an agency's own control inheritance review.

| Control family | Example controls | CivicLens support |
|---|---|---|
| AC (Access Control) | AC-2, AC-3, AC-6, AC-7 | Tenant store, RBAC, admin separation |
| AU (Audit & Accountability) | AU-2, AU-3, AU-9, AU-10, AU-11 | Append-only audit log with hash chain, retention policy, integrity verification |
| IA (Identification & Authentication) | IA-2, IA-5, IA-8 | API keys, SAML, JWT, key rotation |
| SC (System & Comms Protection) | SC-8, SC-13, SC-23 | TLS, platform-level at-rest encryption, HSTS |
| SI (System & Info Integrity) | SI-2, SI-4, SI-10 | Dependency updates, audit monitoring, input validation |
| CM (Configuration Management) | CM-2, CM-6 | Immutable container images, pinned dependencies |

### 5.6 CJIS considerations

Customers handling criminal justice information (CJI) are subject to the CJIS Security Policy. CivicLens does **not** currently hold a CJIS attestation. Agencies that ingest meetings containing CJI should:

- Enable SAML SSO with an agency-approved IdP and enforce MFA at the IdP layer.
- Restrict `allowed_domains` on the tenant's SSO config.
- Store `MEETINGS_OUTPUT_DIR` on encrypted volumes in a CJIS-compliant region.
- Consider self-hosting under `docs/SELF_HOSTING.md` to keep all processing inside their boundary.

### 5.7 FOIA and public records

`api/export.py` provides a dedicated FOIA request handler. It uses the RAG pipeline to locate relevant meeting segments across the archive, assembles source documents and transcripts, and packages the result as a downloadable ZIP. FOIA export jobs are audit-logged (`export.started`, `export.downloaded`) and scoped to the requesting tenant.

---

## 6. Operational Security

### 6.1 Middleware stack

`api/server.py` composes the following middleware (outermost first). Every response traverses the full stack.

1. `AuditMiddleware` — writes an `api.request` audit row with tenant, endpoint, status, and duration.
2. `RequestIDMiddleware` — attaches and returns `X-Request-ID`.
3. `SecurityHeadersMiddleware` — applies:
   - `X-Content-Type-Options: nosniff`
   - `X-Frame-Options: DENY`
   - `Strict-Transport-Security: max-age=63072000; includeSubDomains`
   - `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'`
   - `X-XSS-Protection: 1; mode=block`
   - `Referrer-Policy: strict-origin-when-cross-origin`
   - `Permissions-Policy: camera=(), microphone=(), geolocation=()`
4. `CORSMiddleware` — configurable via `CORS_ORIGINS`.

### 6.2 Rate limiting

Rate limiting is implemented in `api/auth.py::RateLimiter` as an in-process sliding 30-day window keyed by tenant. Limits come from the plan:

| Plan | Monthly query limit |
|---|---|
| starter | 100 |
| pro | 1,000 |
| enterprise | Unlimited |

When exceeded, the API returns HTTP 429 with a `Retry-After` header. The current limiter resets on process restart and is **not distributed**. Multi-instance deployments should front the API with an edge rate limiter (CloudFront, AWS WAF, or an API gateway) until the roadmap Redis-backed limiter lands.

### 6.3 Input validation and injection protection

- All API routes use Pydantic request/response models for schema validation.
- All SQLite and PostgreSQL queries use parameterized placeholders (`?` / `%s`). There is no string concatenation of user input into SQL. `api/database.py::_sqlite_to_pg` preserves this property across both backends.
- RAG query synthesis passes user input to the LLM as a user-role message, not as a template literal, which limits the surface for prompt injection into the system prompt. Retrieved documents are tagged with their source so that a downstream application can surface provenance.
- File paths derived from user input (tenant IDs, clip IDs) are validated and joined via `os.path.join`; tenant IDs are drawn from the authenticated session, never from request bodies.

### 6.4 Secrets handling

- Secrets are passed exclusively via environment variables (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ADMIN_API_KEY`, `JWT_SECRET`, `STRIPE_SECRET_KEY`, `SLACK_SIGNING_SECRET`, SMTP credentials, etc.).
- No secrets are hard-coded in the repository. The `.env.example` file documents the full set.
- The AWS reference Terraform provisions an AWS Secrets Manager secret for app secrets.
- API keys written to the audit log are always masked; structured logging uses `logging_config.py` which does not log request bodies by default.

### 6.5 Dependency management and SBOM

- Python dependencies are managed with `uv` against a pinned `uv.lock`.
- Frontend dependencies are pinned via `package-lock.json`.
- A Software Bill of Materials (SBOM) can be generated on demand for any release from the lockfiles.
- Automated dependency vulnerability scanning in CI is **in progress**. Operators running the container today should layer their own image scanning (Trivy, Grype, AWS Inspector).

---

## 7. Infrastructure Security

### 7.1 Docker hardening

`Dockerfile` follows several hardening patterns:

| Control | Implementation |
|---|---|
| Multi-stage build | Separate `builder` and `runtime` stages; build toolchain is never shipped in the runtime image. |
| Non-root user | `civiclens` user (UID 1000, nologin shell). `USER civiclens` is set before `CMD`. |
| Proper PID 1 | `tini` is the entrypoint, ensuring clean signal handling and zombie reaping. |
| Minimal runtime packages | Only `ffmpeg`, `tesseract-ocr`, `poppler-utils`, `libxmlsec1`, `curl`, and `tini` in the runtime layer. |
| Healthcheck | `HEALTHCHECK` against `/health` every 30s. |
| No write to source | Source and venv are copied with `--chown=civiclens:civiclens`; only `MEETINGS_OUTPUT_DIR` needs to be writable at runtime. |

Operators can further constrain the runtime with `--read-only`, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, and a writable tmpfs or volume mounted at `/app/meetings_output`.

### 7.2 AWS deployment considerations

- IAM: App Runner service role should be least-privileged (read Secrets Manager, write S3 backup bucket, CloudWatch logs).
- Networking: For customers who require it, App Runner supports VPC egress connectors so that outbound traffic (including to OpenAI/Anthropic) can be routed through private subnets and NAT gateways.
- WAF: Not provisioned by the reference Terraform. Customers with WAF requirements should add AWS WAF in front of CloudFront.
- Logging: App Runner logs to CloudWatch; operators should set a retention policy and enable log group KMS encryption.

### 7.3 Backup and disaster recovery

`api/backup.py` implements full and incremental tenant backups, local-directory or S3 upload targets, restore with dry-run, and rotation (keep last N per tenant). Backup archives are ZIP files covering meeting artifacts, relevant SQLite databases, and configuration metadata.

Recommended operator practice:

- Schedule daily full backups and push to a versioned, KMS-encrypted S3 bucket in a separate account.
- Test restore against a staging environment at least quarterly.
- Retain backups in line with retention policy for audit data (365 days default, up to 7 years for enterprise).

Target RPO is 24 hours and target RTO is 4 hours for the managed deployment; self-hosted installations are responsible for their own RPO/RTO.

### 7.4 Incident response

CivicLens maintains an incident response process that covers:

1. **Triage** — reporter contacts security@civiclens.io or files a ticket; initial acknowledgment within 1 business day.
2. **Containment** — rotate compromised credentials, revoke tenant API keys, suspend SSO configs if necessary.
3. **Eradication** — deploy patches, invalidate sessions, update dependencies.
4. **Recovery** — restore from backups if necessary, verify audit chain integrity.
5. **Post-incident** — internal postmortem; customer notification within contractual SLA for confirmed breaches affecting their data.

The audit log provides the forensic basis for any post-incident reconstruction.

---

## 8. Privacy and Data Handling

### 8.1 PII minimization

CivicLens processes publicly broadcast government meeting recordings. The data it retains is:

| Data type | Collected? |
|---|---|
| Meeting audio | Yes (can be discarded with `--no-audio`) |
| Transcripts | Yes |
| Speaker names (as spoken) | Yes |
| Agenda and minutes PDFs | Yes (publicly released by the city) |
| Structured extractions (votes, financials) | Yes |
| SSNs, drivers' licenses, payment card data | **No** — not collected; the platform has no fields for these and the RAG prompts do not solicit them. |
| End-user biometrics | **No** |

If a tenant inadvertently ingests sensitive personal data, the redaction workflow is: re-run the pipeline with corrected source data, delete the affected clip via the tenant API, and rely on audit log events to demonstrate removal.

### 8.2 GDPR and international considerations

CivicLens is primarily targeted at U.S. municipal governments. For international deployments or EU data subjects:

- A Data Processing Agreement (DPA) can be signed on request.
- Right-to-erasure requests are satisfied by tenant offboarding (see 8.3) or per-clip deletion.
- Right-to-access requests are satisfied by the existing export feature (`api/export.py`).
- Cross-border transfer: the managed service runs in U.S. regions; self-hosted deployments can be pinned to any region the operator chooses.

### 8.3 Tenant offboarding

On tenant deletion (`DELETE /api/v1/admin/tenants/{id}`):

1. Tenant row is removed from `tenants.db` (which invalidates the API key).
2. The operator is expected to run a cleanup job that deletes `meetings_output/{tenant_id}/` and purges ChromaDB entries with that `tenant_id` metadata.
3. The deletion itself is audit-logged (`tenant.deleted`).

A fully automated end-to-end offboarding workflow that also purges backups and analytics is on the roadmap.

### 8.4 Subprocessors

| Subprocessor | Purpose | Data sent | DPA status |
|---|---|---|---|
| OpenAI | Whisper transcription, GPT-4o extraction, embeddings, translation | Meeting audio, transcripts, prompts | Standard DPA available from OpenAI |
| Anthropic | Claude Sonnet narrative generation, chat | Extracted facts, user questions | Standard DPA available from Anthropic |
| Stripe | Billing | Tenant metadata (no meeting content) | Stripe DPA |
| Granicus | Source meeting video/audio | None (read-only pull) | N/A (public source) |
| AWS | Hosting, S3, Secrets Manager | Everything (at rest) | AWS DPA |
| SMTP provider (operator-chosen) | Email notifications, digests | Subscriber email addresses | Operator-managed |

Operators who cannot use OpenAI or Anthropic (for example, because of data-residency requirements) can self-host the pipeline with alternative models. This is not a configuration flag today and requires source-level changes; it is tracked as a roadmap item.

---

## 9. Penetration Testing and Vulnerability Management

### 9.1 Responsible disclosure

Security researchers and customers can report vulnerabilities to **security@civiclens.io**. We commit to:

- Acknowledge reports within 2 business days.
- Provide an initial triage and CVSS estimate within 5 business days.
- Coordinate disclosure with the reporter.
- Credit reporters (with consent) in release notes.

A PGP key for encrypted reports is published at `/.well-known/security.txt` (placeholder key; see Section 12).

### 9.2 Patch cadence

| Severity | Target SLA |
|---|---|
| Critical (CVSS 9.0+) | Patch within 72 hours |
| High (CVSS 7.0–8.9) | Patch within 14 days |
| Medium (CVSS 4.0–6.9) | Patch in the next scheduled release |
| Low (CVSS < 4.0) | Batched into regular maintenance |

### 9.3 Penetration testing

Third-party penetration testing is **planned** ahead of SOC 2 Type II observation. Results and remediation will be summarized in future revisions of this document. In the meantime, customers may conduct their own penetration tests against their tenant with prior written notice to security@civiclens.io.

---

## 10. Shared Responsibility Model

| Domain | CivicLens (managed SaaS) | Customer / Operator |
|---|---|---|
| Application code and dependencies | Own, patch, SBOM | Review release notes |
| Container image hardening | Own | Configure read-only FS, cap-drop if self-hosting |
| TLS certificates (managed) | Own (ACM) | Own if self-hosted |
| Tenant API key issuance | Own (rotation endpoint) | Store and rotate the key, restrict distribution |
| Admin API key (`ADMIN_API_KEY`) | N/A | Own, rotate |
| `JWT_SECRET` | N/A | Generate, rotate, store securely |
| SAML IdP | N/A | Configure and enforce MFA at IdP |
| At-rest encryption | Own (platform-level EBS/S3 SSE) | Own if self-hosted |
| Backups (managed) | Own (daily S3) | Request restore |
| Backups (self-hosted) | N/A | Own full backup/restore |
| Audit log retention | Own (default per plan) | Request override |
| Data classification in transcripts | N/A | Decide whether to ingest sensitive meetings |
| Tenant user lifecycle | N/A (SSO-driven) | Provision and deprovision in IdP |
| Incident reporting from customer side | Receive and triage | Report at security@civiclens.io |
| FOIA and public-records responses | Provide export tooling | Legally respond to requests |

---

## 11. Certifications and Attestations

CivicLens publishes its certification posture honestly. Current status as of the document date:

| Program | Status | Notes |
|---|---|---|
| SOC 2 Type I | **Planned** | Readiness activity underway; target observation window TBD. |
| SOC 2 Type II | **Planned** | Follows Type I. |
| FISMA ATO (moderate) | Not pursued | Control mapping provided; agencies may inherit where appropriate. |
| FedRAMP | Not pursued | |
| StateRAMP | Not pursued | |
| CJIS | Not attested | Customers handling CJI should consider self-hosting. |
| ISO 27001 | Not pursued | |
| HIPAA | Not applicable | CivicLens does not process PHI. |
| PCI DSS | Not applicable | Payment data is handled exclusively by Stripe. |
| GDPR | Supported via DPA | See Section 8.2. |

No certification is claimed that has not been attested. Customers requiring a specific compliance framework should contact security@civiclens.io to discuss timing, scope, and whether a self-hosted deployment is a better fit.

---

## 12. Contact and security.txt

### 12.1 Contact

- **Security reports:** security@civiclens.io
- **Procurement / trust center questions:** trust@civiclens.io
- **PGP key:** [Placeholder — the current public key fingerprint is published at `https://civiclens.ai/.well-known/security.txt`.]

### 12.2 security.txt

CivicLens publishes a `/.well-known/security.txt` in line with RFC 9116. The published fields include:

```
Contact: mailto:security@civiclens.io
Expires: 2027-04-10T00:00:00Z
Preferred-Languages: en
Canonical: https://civiclens.ai/.well-known/security.txt
Policy: https://civiclens.ai/security/disclosure
```

---

## Appendix A. Document Change Log

| Version | Date | Changes |
|---|---|---|
| 1.0 | 2026-04-10 | Initial public release. |

## Appendix B. References

- `docs/SECURITY.md` — Verifiable controls in the current codebase.
- `docs/AUDIT_LOGGING.md` — Audit event schema, endpoints, retention.
- `docs/SSO_SETUP.md` — SAML IdP configuration walkthrough.
- `docs/SELF_HOSTING.md` — Self-hosted deployment guide.
- `docs/ARCHITECTURE_REVIEW.md` — Current and planned architecture work.
- `docs/DATA_EXPORT.md` — Export and FOIA response workflow.
- `CLAUDE.md` — Authoritative architecture reference.
