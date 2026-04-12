---
title: CivicLens Customer Success Playbook
version: 1.0
last_updated: 2026-04-10
owner: Customer Success Team
audience: CSMs, AEs, CS leadership
---

# CivicLens Customer Success Playbook

This playbook defines how the Customer Success (CS) team operates across the full customer lifecycle for CivicLens, the multi-tenant meeting intelligence platform. It is grounded in the product's actual capabilities: per-tenant Granicus ingestion, RAG-based Q&A, structured fact extraction, vote tracking, alerts, analytics, audit logging, exports, and integrations with Slack, Teams, and Zapier. Where this document references a feature, that feature exists in the codebase today.

CS exists to move tenants from a signed contract to durable, quantified value, and to grow that value through expansion and renewal. The tactical sections below define stages, checklists, templates, thresholds, and metrics. The goal is that any CSM can pick up a new account and execute a predictable motion without inventing it from scratch.

---

## Part 1: Customer Lifecycle Stages

A tenant in CivicLens progresses through a defined set of stages. Each stage has exit criteria, an owner, and a handoff. Stage transitions are tracked in the CRM and reflected in the CS team's weekly book review.

### Stage definitions

| Stage | Definition | Owner | Exit criteria |
|-------|------------|-------|---------------|
| Prospect | Qualified lead in active sales conversation. No contract. | Account Executive (AE) | Signed order form or executed contract |
| Trial | Short-lived evaluation tenant, usually 14-30 days, created via `python -m api.tenants create --plan starter`. | AE (with CSM shadowing) | Contract executed and converted to a paid plan |
| Onboarding | First 30 days after contract execution. Pipeline ingesting meetings, users getting trained. | Customer Success Manager (CSM) | All Day 30 success criteria met (see Part 2) |
| Active | Tenant is in steady-state use. Regular query volume, fresh data, stable health score. | CSM | Crossed into At-Risk, Expansion, or Renewal |
| Expansion | Tenant shows upsell signals (usage near cap, new user requests, integration requests). | CSM + AE | Expansion contract signed, returning to Active |
| Renewal | T-90 to T-0 before contract end date. | CSM (lead) + AE (commercial) | Renewal signed, moves back to Active |
| At-Risk | Health score below 60, exec turnover, usage decline, support churn signals. | CSM + CS leadership | Score recovers above 70 for two consecutive weeks, or move to Churned |
| Churned | Contract ended without renewal, or early termination. | CSM (offboarding), then Marketing (alumni nurture) | Data exported, tenant deleted, 90-day grace period ended |

### Ownership and handoff rules

- **AE to CSM handoff** happens within 48 hours of contract execution. AE files a handoff packet in the CRM containing: signed contract, stakeholder map, original pain points, promised outcomes, custom commitments, billing plan, and contract end date.
- **CSM to AE handoff** triggers at any Expansion signal or at T-90 days before renewal. The CSM retains the relationship; the AE joins for commercial negotiations.
- **Escalation** from CSM to CS leadership is mandatory when health drops below 50 (the `churning` category returned by `api/customer_health.py`) or when an executive sponsor leaves the customer organization.

No account should sit without a clearly-named owner. The CRM field `cs_owner` is the single source of truth.

---

## Part 2: Onboarding Playbook (First 30 Days)

Onboarding is the highest-leverage window the CS team owns. Get this right and the rest of the lifecycle gets easier. Get it wrong and no later effort will fully recover the account.

Every new tenant gets a 30-day structured plan. The CSM's job is to drive through it, not wait for the customer to ask.

### Day 0: Provisioning and welcome

The AE or CSM provisions the tenant using the admin CLI described in `CLAUDE.md` and `docs/OPERATOR_GUIDE.md`:

```bash
python -m api.tenants create \
  --id <tenant-slug> \
  --name "<City Name>" \
  --granicus-host <host>.granicus.com \
  --granicus-view-id <id> \
  --plan <starter|pro|enterprise>
```

This prints a one-time `mra_`-prefixed API key. It is never stored in cleartext after creation. The CSM delivers the key through the customer's secure channel (1Password shared vault, encrypted email, or the customer's preferred SSO flow if SAML is configured per `docs/SSO_SETUP.md`).

At this point the automated onboarding email sequence (`api/onboarding_emails.py`) begins. The sequence sends five emails over 14 days: welcome and quickstart (Day 0), first meeting tutorial (Day 1), Slack/Teams integration guide (Day 3), advanced features tour (Day 7), and a check-in (Day 14). The CSM should review the sequence template in the repo so their manual touches complement rather than duplicate it.

### Day 1: Kickoff call (45 minutes)

Calendar the kickoff within 72 hours of provisioning. Invite the customer's executive sponsor, primary admin, and any power users. Agenda:

1. Introductions and roles (5 min)
2. Recap of stated goals from the sales cycle (5 min)
3. How CivicLens works at a high level: Granicus ingestion, summaries, extracted facts, RAG Q&A (10 min)
4. Live walkthrough of the admin UI and the Ask/Chat endpoints (10 min)
5. Confirm which meeting bodies will be ingested and what history to backfill (5 min)
6. Set success criteria for Day 30 and Day 90 in writing (5 min)
7. Schedule the Day 7 training and the Day 30 QBR-lite (5 min)

Leave the call with a written success plan the customer has seen and agreed to. File it in the CRM.

### Day 3: First meeting ingested verification

By Day 3, the pipeline should have run at least one scheduled or manual ingestion for the tenant. Verify:

- At least one clip exists under `MEETINGS_OUTPUT_DIR/clips/<clip_id>` with a `metadata.json`, `summary.txt`, and `extracted_facts.json`.
- The search index for this tenant has been regenerated.
- The meeting is visible in the tenant's frontend meeting browser at `/`.
- A test Q&A against `/api/v1/ask` returns a cited answer.

If any of this fails, open an internal support ticket and escalate to Engineering. The CSM owns unblocking the customer, not writing the fix.

### Day 7: Training session (45 minutes)

This is a working session for the customer's actual users, not executives. Agenda:

1. Tour the meeting browser: search, filter by body, tabbed meeting detail (5 min)
2. Tour the Ask and Chat interfaces: single-turn vs multi-turn, model selector, citations and the Granicus deep-link timestamps (10 min)
3. Walk through the Vote Tracker and how to create a policy alert (10 min)
4. Walk through the Analytics dashboard: usage summary, queries over time, top questions (5 min)
5. Discuss exports and FOIA use cases (5 min)
6. Open Q&A (10 min)

Record the session and send the recording plus written notes.

### Day 14: Check-in email

A written, non-pushy email that reviews what they have accomplished and what remains before Day 30. This should reference real numbers from the analytics store (query count, meetings processed, distinct active days) so the customer sees you are paying attention.

### Day 21: First value milestone

By Day 21 the customer should have at least one concrete, named win. Examples:

- A policy alert that has actually fired and surfaced a relevant meeting.
- A saved search that a staff member uses weekly.
- An export used in a real FOIA response.
- A question answered via chat that would have previously required someone to watch an hour of video.

The CSM documents the win in the CRM. If there is no win by Day 21, that is itself a yellow flag and the CSM should intervene with additional training or a use-case workshop.

### Day 30: QBR-lite and NPS

Half-hour call with the primary admin and sponsor. Review the Day 30 success criteria, confirm status, and plan the next 30-60 days. Send an NPS survey immediately after the call. Survey template is in Part 8.

### Day 30 success criteria

These criteria are tracked in the CRM for every onboarding account.

| Metric | Target | Where to measure |
|--------|--------|------------------|
| % of assigned users logged in | 80% | SSO user store and analytics `_score_user_engagement` signal |
| Meetings ingested | 10 or more | `meeting_processed_events` table or `/api/v1/admin/tenants` stats |
| Saved alerts configured | 1 or more | Tracker store `list_alerts(tenant_id)` |
| Queries per active user | 5 or more | `query_events` grouped by day-active proxy |
| Day 30 NPS response submitted | Yes | Survey tool |

An onboarding is considered successful only when all five criteria are green. A partial pass is treated as an ongoing onboarding for up to another 30 days, after which the account moves to At-Risk if still deficient.

---

## Part 3: Health Scoring (Operational)

CivicLens computes a per-tenant health score daily via `api/customer_health.py`. The score is a weighted blend of seven signals, each normalized to 0-100.

### Signal weights

| Signal | Weight | What it measures |
|--------|--------|------------------|
| Query frequency | 25% | 7-day query volume vs the 30-day daily average |
| Feature breadth | 20% | How many of chat, votes, alerts, export, webhooks, Slack, Teams are in use |
| User engagement | 15% | Unique active days in the last 30 days plus SSO user count |
| Data freshness | 15% | Days since the most recent `meeting_processed_event` |
| Alert activity | 10% | Enabled alerts and whether they have recent matches |
| Support signals | 10% | Inverse of API error rate on `api_call_events` |
| Billing health | 5% | Plan tier and presence of an active Stripe subscription |

The resulting score is bucketed by `api/customer_health.py` into three categories: `healthy` (80 or above), `at_risk` (50-79), and `churning` (below 50). This playbook uses a slightly finer-grained operational scheme on top of those categories.

### Operational thresholds

| Band | Score range | Category | CS action |
|------|-------------|----------|-----------|
| Green | 80-100 | healthy | Standard cadence. Look for expansion signals. |
| Yellow | 60-79 | at_risk (upper) | Investigate. Identify which signal is dragging the score. |
| Light Red | 50-59 | at_risk (lower) | Intervene. See the save playbook in Part 6. |
| Red | below 50 | churning | Escalate to CS leadership within 24 hours. |

### What to do at each level

**Green.** Do not over-manage healthy accounts. Keep the scheduled QBRs, send monthly check-ins, and watch for expansion signals in `expansion_signals` from the scorer output (examples the code actually emits: "Very high query volume -- may be hitting or approaching rate limits", "Using most available features -- good candidate for enterprise upsell", "Starter plan tenant with high query volume -- upgrade candidate for Pro").

**Yellow.** Pull up the signal breakdown from `/api/v1/health-scores` and identify the lowest signal. Use the recommendations the scorer already generates; they are pragmatic and mapped to the underlying signal. Common yellow interventions:

- Query frequency drop: schedule a 30-minute usage review call and identify what changed.
- Feature breadth gap: run a targeted mini-demo of the unused feature (most often alerts or webhooks).
- Data freshness drop: verify the ingestion pipeline and scheduler config with Operations.
- Elevated error rate: pull the tenant's recent `api_call_events` with status codes 400 or greater and reach out with a specific diagnosis.

**Light Red.** This is the save window. Weekly check-ins, executive sponsor reach-out, and a written recovery plan filed in the CRM. See Part 6.

**Red.** The account is actively churning. CS leadership is looped in. The CSM works the save playbook with support from leadership and the AE. If the save fails, the team moves cleanly into the offboarding flow in Part 7.

### Reading the score responsibly

The score is directional, not deterministic. A tenant with a low score who has explicitly told you they are on summer recess is not in trouble. A tenant with a high score who just lost their executive sponsor is. CSMs weight the automated score against the qualitative context in the CRM.

---

## Part 4: Expansion Playbook

Expansion is the single biggest lever on net revenue retention. The goal for every Active account is to leave it larger than you found it, without pressure tactics that damage trust.

### Upsell signals

The health scorer already surfaces most of these in its `expansion_signals` field. CSMs should treat a new expansion signal as a task to investigate within one business day.

| Signal | Where it surfaces | Pitch |
|--------|------------------|-------|
| Query frequency score above 85 on a Pro plan | `expansion_signals` | Enterprise upgrade for unlimited queries |
| Query frequency score above 70 on a starter plan | `expansion_signals` | Pro upgrade |
| Feature breadth score above 80 | `expansion_signals` | Enterprise for unlimited, priority support, custom SLA |
| User engagement score above 85 | `expansion_signals` | Seat expansion or move to Enterprise |
| Customer requests a new meeting body | Support ticket / email | Adds ingestion scope, may affect plan |
| Customer requests Slack, Teams, or Zapier | Support ticket | Often tied to Pro or Enterprise |
| Customer asks about SSO | Support ticket | SAML 2.0 is available; position as Enterprise feature |
| Customer asks about custom branding | Support ticket | Per-tenant branding is available via `api/branding.py` |

### Cross-sell opportunities

CivicLens supports Slack, Teams, Zapier, webhooks, SSO, custom branding, embeddable widget, and email digests. Each of these is a legitimate cross-sell on top of the base subscription.

- **Slack and Teams.** Both are end-user facing. Strongest with comms teams and city managers who want alerts in their daily feeds.
- **SSO.** Enterprise-only. Position as the answer to "we need to add twenty more users without managing passwords."
- **Custom branding.** White-label for public-facing deployments. Often the deciding factor for jurisdictions that want a city-branded constituent portal.
- **Webhooks and Zapier.** Strongest with IT teams that want to wire CivicLens events into existing workflows.
- **Embeddable widget.** A drop-in script tag for the city's public website. High perceived value, low effort, usually included in Pro and above.

### Pitching upgrades without pressure

The playbook for an upgrade conversation:

1. Lead with data from the health and analytics dashboards. Show the customer their own usage.
2. Frame the upgrade as a response to the customer's own growth, not a sales ask.
3. Offer a 30-day trial on the new plan with no contractual commitment. Downgrade back to the original plan is a single CLI call (`python -m api.tenants update-plan`).
4. Only discuss pricing after they have agreed the upgrade makes sense.

Never dunk on the current plan. The customer chose it for a reason, and that reason may still apply.

### MDF and co-marketing

For accounts that are willing to be public references, CS can offer co-marketing dollars and/or case study production. The current offer structure:

- Case study: CS pays for a writer and a designer. Customer gets final approval and a free quarter of their current plan.
- Conference co-presentation: travel and registration covered for one customer speaker.
- Joint press release on a notable deployment milestone.

MDF requests are approved by CS leadership and tracked in the co-marketing spreadsheet.

---

## Part 5: Renewal Playbook

Renewal is not an event. It is a 90-day motion that begins at T-90 and ends at T-0 on the contract anniversary.

### Renewal timeline

| Milestone | Activity |
|-----------|----------|
| T-120 | CS leadership reviews the book. Every account with an upcoming renewal gets a named CSM and a renewal status (likely, at risk, in play). |
| T-90 | Renewal prep starts. CSM pulls usage data, health trend, open tickets, and stakeholder map. CSM schedules the T-60 QBR. |
| T-60 | Executive QBR held. CSM presents usage, ROI, and the renewal proposal. AE shadows for commercial handoff. |
| T-45 | Written renewal proposal delivered. |
| T-30 | Decision meeting. Verbal commitment or structured objection handling. |
| T-14 | Redlines closed. Legal review. |
| T-0 | Contract signed. New term begins. Account returns to Active. |

Any slippage against this timeline is itself a warning sign and should trigger a conversation with CS leadership.

### Renewal prep checklist

Before the T-60 QBR, the CSM assembles:

- Health score trend (30, 60, 90 day)
- Usage summary from `analytics.usage_summary` for the full contract period
- Meetings processed count
- Top questions and popular meeting bodies
- Win log from the CRM (alerts that fired, exports used in FOIA, etc.)
- Open support tickets and their resolution status
- Stakeholder map with any changes since the last QBR
- Known roadmap items relevant to the customer
- Pricing position (on current pricing, below current pricing, grandfathered)

### QBR deck section outline

A good QBR is honest, specific, and customer-centered. The deck has eight sections:

1. Agenda (1 slide)
2. Recap of last QBR's commitments and their status (1 slide)
3. Usage in period, with numbers (2 slides)
4. Wins and quantified value (2 slides)
5. Gaps and what we plan to do about them (1 slide)
6. Roadmap preview, under NDA if needed (1 slide)
7. Renewal and proposed changes (1 slide)
8. Asks and next steps (1 slide)

Keep it under 12 slides total. The customer did not come to watch slides.

### Handling price increases

If the renewal involves a price increase:

- Communicate the increase in writing at T-60, not T-30.
- Explain the basis: new features added, infrastructure cost, inflation adjustment.
- For customers who were on CivicLens before a major pricing change, honor the grandfather offer for at least one additional renewal term. Legacy customers are a retention asset.
- Offer a multi-year lock-in at the current rate as an alternative to accepting the increase at one year.
- Be willing to walk away from the increase if the relationship is otherwise strong. Losing a customer over an increase is usually worse than absorbing it.

---

## Part 6: At-Risk and Churn Prevention

At-risk is a signal, not a verdict. The job of the save playbook is to turn the account around, not to manage its decline.

### Early warning signals

- Health score decline of 10 points or more week over week.
- Executive sponsor departure, announced or detected via LinkedIn.
- Usage decline: query frequency score falling below 50.
- Data freshness below 50 (meetings not being ingested).
- Rising error rate in `api_call_events`.
- Slow or silent response to CSM outreach for two consecutive weeks.
- Budget cut rumors, especially in municipal customers during fiscal year planning.
- Support ticket sentiment turning negative.

### Save playbook

When an account crosses into At-Risk:

1. **Within 24 hours.** CSM files a save plan in the CRM. The plan names the risk, the root cause hypothesis, the intervention, and the owner.
2. **Within 48 hours.** CSM makes direct contact with the primary admin. If the primary admin is unresponsive, escalate to the executive sponsor.
3. **Within one week.** Executive sponsor on CS side reaches out to executive sponsor on customer side. This is the "I noticed you were having trouble, what can we do" email, not a sales motion.
4. **Week two.** Tactical interventions: additional training, temporary plan credits (CS leadership approval), roadmap commitments for specific feature requests, a feature bounty where CS pays to prioritize a customer-requested feature.
5. **Week three.** Review. If the health score has not moved, CS leadership decides whether to continue the save or move to graceful offboarding.

### Escalation matrix

| Severity | Triggers | Who is looped in |
|----------|----------|------------------|
| Level 1 | Health yellow, single signal declining | CSM only |
| Level 2 | Health light red, or exec sponsor change | CSM and CS manager |
| Level 3 | Health red, or explicit churn signal from customer | CSM, CS manager, CS leadership, AE |
| Level 4 | Contract termination notice received | Full escalation, including CEO if the logo warrants it |

### When to accept churn gracefully

Sometimes the right answer is to let the customer go. Indicators:

- The use case has genuinely changed and CivicLens no longer fits.
- A regulatory change made the tool redundant.
- A budget cut is structural, not a negotiation.
- The customer has a new vendor decision that they are not willing to reconsider.

In these cases the CSM shifts immediately to a clean offboarding. A graceful churn is a future win: customers who leave well come back more often than customers who leave badly.

---

## Part 7: Offboarding

Every offboarding, whether due to churn or a scheduled end of engagement, follows the same process.

### Data export

All customer data belongs to the customer. Before tenant deletion, the CSM triggers a full export using `api/export.py`. This produces a ZIP containing meeting metadata, summaries, extracted facts, transcripts, analytics, and any custom configuration. The CSM delivers the export through the customer's secure channel and confirms receipt in writing.

If the customer needs a FOIA-specific export, the export module also supports FOIA request handling with RAG-assisted search across the archive. This is a strong last impression for municipal customers.

### Tenant deletion

Tenant deletion is a CLI call:

```bash
python -m api.tenants delete --id <tenant-slug>
```

Deletion removes the tenant record from `tenants.db`. Per the current product state, associated per-tenant data under `meetings_output/clips/<tenant_id>/` is retained for 90 days before final purge, which gives the customer a recovery window if they reconsider. The exact retention policy for each ancillary database (audit, analytics, search) is documented in `docs/DATA_EXPORT.md`.

### Exit interview

A 30-minute conversation with the primary admin and, if possible, the executive sponsor. The script is in Part 8. The point of the exit interview is not to save the account; the save window has passed. The point is to learn and to leave the door open.

### Alumni nurture

After offboarding, the customer is moved into the alumni nurture track managed by Marketing. This is a low-frequency, high-quality newsletter that covers product changes and public case studies. Alumni who re-engage are routed back to the original AE.

---

## Part 8: Templates

### Welcome email (Day 0)

> Subject: Welcome to CivicLens
>
> Hi [First Name],
>
> Welcome to CivicLens. I am [CSM Name] and I will be your main point of contact as you get set up.
>
> Your tenant is provisioned and your API key is ready. I am sending it through [secure channel] immediately after this email.
>
> Over the next 30 days we have a structured onboarding plan with three touchpoints: a kickoff call on [date], a training session on [date], and a check-in on [date]. You will also receive a short automated email series covering quickstart, integrations, and advanced features.
>
> In the meantime, the fastest way to see the product in action is to open the meeting browser at [URL] and try a question in the Ask interface.
>
> Please reply to this email if anything is unclear. I am here to make this work.
>
> [CSM Name]

### Day 7 training invite

> Subject: CivicLens training session, [day and time]
>
> Hi [First Name],
>
> I would like to run a 45-minute training session for your team next [day] at [time]. The agenda covers the meeting browser, Ask and Chat, the Vote Tracker, analytics, and exports. This is a working session, so please invite the users who will actually be using CivicLens day to day.
>
> I will record the session and send the recording afterwards.
>
> Calendar invite is attached. Let me know if the time does not work.
>
> [CSM Name]

### QBR deck section outline

1. Agenda
2. Last QBR recap and commitments
3. Usage this period (queries, meetings ingested, active users)
4. Wins and quantified value
5. Gaps and action plan
6. Roadmap preview
7. Renewal and proposed changes
8. Asks and next steps

### Save-the-account executive email

> Subject: Checking in on CivicLens at [Customer]
>
> Hi [Sponsor First Name],
>
> I wanted to reach out directly. Our team noticed that usage of CivicLens at [Customer] has slowed over the last few weeks, and I wanted to check in rather than wait for a scheduled call.
>
> I am not writing to sell you anything. I am writing because we invested in your success and I want to make sure we are still the right fit. If something has changed on your side, or if the product is falling short of what we promised, I want to know.
>
> Can we spend 20 minutes together this week or next? I will bring a concrete plan and your CSM will join.
>
> [CS Leader Name]

### NPS survey (three questions)

1. On a scale of 0 to 10, how likely are you to recommend CivicLens to a colleague at another jurisdiction?
2. What is the single most valuable thing CivicLens has helped you do?
3. What is the single most frustrating thing about using CivicLens today?

### Exit interview script (five questions)

1. Looking back on your time with CivicLens, what did you hope it would do for you, and how much of that did we deliver?
2. What ultimately drove the decision to not continue?
3. Was there anything we could have done differently that would have changed the outcome?
4. What are you planning to use instead, if anything?
5. If we solved the core issue you just described, is there a version of the future where you come back?

---

## Part 9: Metrics and Reporting

The CS team is measured on a small set of metrics, reviewed monthly with leadership and quarterly with the board.

### Team targets

| Metric | Target | Notes |
|--------|--------|-------|
| NPS | 50 or higher | Measured via the post-Day-30 survey and the post-QBR survey |
| Gross Revenue Retention (GRR) | 95% or higher | Annualized |
| Net Revenue Retention (NRR) | 110% or higher | Includes expansion minus churn and contraction |
| Time to first value | Under 7 days | Measured as days from contract execution to first value milestone |
| Expansion rate | 20% of accounts per year | Any upgrade or added module counts |
| Onboarding completion | 90% of accounts green by Day 30 | All five Day 30 success criteria met |
| Health score distribution | 70% green, under 10% red | Across the full active book |

### CSM capacity planning

CSM capacity depends on plan tier, because higher-tier accounts demand more relationship time and lower-tier accounts need more scalable playbooks.

| Plan tier | Accounts per CSM |
|-----------|-----------------|
| Starter | 40 to 60 (tech-touch model) |
| Pro | 15 to 20 (standard touch) |
| Enterprise | 5 to 8 (high touch) |

Starter accounts are managed through automation, the email drip sequence, and scheduled office hours. Pro accounts get individual quarterly QBRs. Enterprise accounts get monthly business reviews plus ad-hoc strategic support.

### Reporting cadence

| Report | Cadence | Audience |
|--------|---------|----------|
| Account book review | Weekly | CS team |
| Health distribution and at-risk list | Weekly | CS leadership |
| NRR and GRR by segment | Monthly | CS leadership, Finance |
| Board metrics | Quarterly | Executive team |
| NPS and CSAT trend | Quarterly | Executive team |

### Tooling

The CS team's tooling stack is deliberately minimal: the CivicLens admin dashboard for real-time health and usage, the CRM for stakeholder data and notes, a shared calendar for customer meetings, and a small set of dashboards built on top of the analytics and health databases. The goal is that a CSM can answer any question about any account in under two minutes.

---

This playbook is a living document. It is reviewed every quarter and updated when product capabilities change, when customer feedback reveals gaps, or when the CS team finds a better way to run a motion. Proposed changes are submitted as pull requests against this file and reviewed by the CS team lead.
