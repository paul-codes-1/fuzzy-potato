# Architecture Review

Review date: April 9, 2026

## Executive Summary

CivicLens has strong product breadth and a real working core: ingestion, retrieval, search, exports, analytics, tenancy, branding, billing hooks, and integrations are all present in the codebase.

The main gap is not product scope. It is deployment maturity.

Today the app is best described as:

- a feature-rich single-node SaaS application
- with partial foundations for enterprise deployment
- but not yet a fully hardened multi-instance enterprise platform

## What Exists Today

### Strengths

- Clear separation between ingestion (`main.py`), application API (`api/`), and frontend (`frontend/`)
- Good automated test coverage across the platform surface
- Meaningful tenant model, analytics, audit logging, and export/compliance features
- Extensive integration surface: Slack, Teams, Zapier, widget, calendar, digests
- Terraform, Docker, SDKs, and marketing collateral are already in-repo

### Verified State

During this review I ran the Python suite and verified:

- `518` tests passing

## Main Findings

### 1. Critical: the deployed data plane is still effectively single-node

The current deployment story centers on App Runner plus local state:

- the Docker image bakes `meetings_output/` into the container
- App Runner points `MEETINGS_OUTPUT_DIR` at `/app/meetings_output`
- Chroma uses local persistent files
- many operational modules still use local SQLite stores

That means horizontal scale, failover, and data durability are limited by local container state.

For a true enterprise app, the durable system of record needs to move out of the web container.

### 2. High: the database strategy is mid-migration

`api/database.py` and `docs/DATABASE.md` describe a unified SQLite/PostgreSQL path, but many runtime modules still manage their own SQLite files directly.

Examples include:

- auth
- audit
- analytics
- search
- branding
- exports
- webhooks
- scheduler
- status

That split creates operational complexity:

- backup and restore are fragmented
- failover behavior is inconsistent
- multi-instance deployments are unsafe unless every stateful module is moved to shared storage

### 3. High: background work still lives inside the API process

The app currently performs operational work in-process:

- scheduler startup from the API server
- export jobs via thread pools
- other module-level singleton services started in app lifespan

That is acceptable for a small deployment, but enterprise systems usually separate:

- stateless API nodes
- durable job queues
- dedicated workers
- repeatable job orchestration

Without that split, retries, observability, throughput isolation, and recovery behavior remain weak.

### 4. High: auth exists, but enterprise identity is incomplete

The platform has:

- tenant API keys
- an admin API key
- optional SSO support
- audit logging

But it does not yet have a full enterprise identity model:

- robust role-based access control
- user/org/member lifecycle management
- strong separation between admin users and tenant users
- browser-safe admin auth that does not rely on API keys in session storage

This is enough for controlled deployments, not for a mature enterprise posture.

### 5. Medium: docs had drifted from both product scope and implementation reality

The root README still framed the repo mainly as an LFUCG pipeline, while other docs overstated controls that are not actually implemented in code or Terraform today.

That creates real operational risk because new contributors cannot tell:

- what the product actually includes
- which components are core
- which features are optional
- what is ready for production and what is not
- which security controls are real versus planned

## Changes Made In This Review Pass

I made a targeted hardening pass while reviewing the architecture:

- added `.env.example`
- added a docs index
- added a product guide
- added an operator guide
- added this architecture review
- updated the root README so it describes the full product, not just the original pipeline
- corrected security, SSO, API, and changelog docs to reflect the verified implementation
- made auth, audit, branding, search, and export modules lazy-initialize more safely
- restored backward-compatible legacy auth behavior for dev-mode routes
- cleaned up Stripe loading so it is easier to test and safer to mock
- fixed audit route compatibility
- preserved clip URLs in RAG source metadata so deep links resolve correctly
- tightened SSO admin routes so browser sessions must have the `admin` role to manage SSO config and users

## Recommended Enterprise Roadmap

### Phase 1: stabilize the foundation

- Make PostgreSQL the required system of record for all operational state
- Move meeting artifacts and exports to object storage
- Treat Chroma and search indexes as rebuildable derived data, not primary state
- Keep one documented deployment topology and make it the default

### Phase 2: separate web from jobs

- Move exports, digests, scheduled ingestion, and long-running workflows to worker processes
- Introduce a durable queue
- Add retry policies, dead-letter handling, and job-level telemetry

### Phase 3: harden identity and controls

- Replace admin API key browser workflows with user auth
- Add real RBAC
- Expand SSO into a first-class admin/operator model
- Add explicit access policies around tenant administration and sensitive exports

### Phase 4: enterprise operations

- Define backup and restore runbooks
- Add SLOs and alerting
- Add disaster recovery expectations
- Add cost controls and usage attribution by tenant and subsystem

## Bottom Line

This repo is already much closer to a product than to a prototype.

What it needs next is not more random feature work. It needs a consolidation phase:

- one durable persistence model
- one production topology
- one background job strategy
- one enterprise auth story

Once those are in place, the existing feature set is strong enough to support a credible enterprise offering.
