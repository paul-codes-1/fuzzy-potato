---
title: CivicLens Onboarding Checklist
version: 1.0
last_updated: 2026-04-10
owner: Customer Success Team
purpose: Printable checklist for CSMs running a new tenant through the first 30 days
---

# CivicLens Onboarding Checklist

Customer: ______________________________________
Tenant ID: ______________________________________
CSM: ____________________________________________
Contract start date: ____________________________
Target Day 30 review date: ______________________

Use this checklist alongside the full Customer Success Playbook in `docs/CUSTOMER_SUCCESS_PLAYBOOK.md`. Check each item as it is completed and file the finished sheet in the CRM.

---

## Day 0: Provisioning and Welcome

- [ ] Tenant created via `python -m api.tenants create` with correct plan, Granicus host, and view ID
- [ ] `mra_` API key captured and delivered through secure channel
- [ ] Automated onboarding email sequence confirmed to be running
- [ ] Welcome email sent from CSM (in addition to the automated sequence)
- [ ] Primary admin and executive sponsor identified in the CRM
- [ ] Stakeholder map filed in the CRM
- [ ] Handoff packet received from AE and reviewed
- [ ] Contract end date and renewal owner recorded

## Day 1: Kickoff Call

- [ ] Kickoff call scheduled within 72 hours of provisioning
- [ ] Exec sponsor, primary admin, and power users invited
- [ ] Agenda sent in advance
- [ ] Call held; recording and notes filed in the CRM
- [ ] Meeting bodies to ingest confirmed in writing
- [ ] Backfill scope agreed
- [ ] Day 30 and Day 90 success criteria agreed in writing
- [ ] Day 7 training session scheduled
- [ ] Day 30 review scheduled

## Day 3: First Meeting Verification

- [ ] At least one clip exists under `MEETINGS_OUTPUT_DIR/clips/<clip_id>`
- [ ] `metadata.json`, `summary.txt`, and `extracted_facts.json` present
- [ ] Search index regenerated
- [ ] Meeting visible in the tenant's frontend at `/`
- [ ] Test `/api/v1/ask` request returns a cited answer
- [ ] Any pipeline or ingestion issues escalated to Engineering

## Day 7: Training Session

- [ ] 45-minute training held with actual end users
- [ ] Meeting browser walkthrough completed
- [ ] Ask and Chat walkthrough completed (including the model selector and Granicus deep links)
- [ ] Vote Tracker and policy alert creation walkthrough completed
- [ ] Analytics dashboard walkthrough completed
- [ ] Export and FOIA use case discussed
- [ ] Recording sent to the customer
- [ ] Written notes filed in the CRM

## Day 14: Check-in Email

- [ ] Usage numbers pulled from analytics (queries, meetings processed, active days)
- [ ] Written check-in email sent referencing specific customer activity
- [ ] Response tracked; any red flags escalated

## Day 21: First Value Milestone

- [ ] At least one concrete win recorded (alert fired, saved search in use, FOIA export, high-value question answered)
- [ ] Win documented in the CRM
- [ ] If no win yet: intervention scheduled (additional training or use-case workshop)

## Day 30: QBR-lite and NPS

- [ ] Day 30 review call held with primary admin and executive sponsor
- [ ] Success criteria reviewed against actual metrics (see scoreboard below)
- [ ] 30-60 day plan agreed
- [ ] NPS survey sent within 24 hours of the review
- [ ] NPS response captured in the CRM

---

## Day 30 Success Scoreboard

All five criteria must be green for onboarding to be marked complete.

| Criterion | Target | Actual | Status |
|-----------|--------|--------|--------|
| % of assigned users logged in | 80% or more | ______ | [ ] Green [ ] Yellow [ ] Red |
| Meetings ingested | 10 or more | ______ | [ ] Green [ ] Yellow [ ] Red |
| Saved alerts configured | 1 or more | ______ | [ ] Green [ ] Yellow [ ] Red |
| Queries per active user | 5 or more | ______ | [ ] Green [ ] Yellow [ ] Red |
| Day 30 NPS submitted | Yes | ______ | [ ] Green [ ] Yellow [ ] Red |

## Final Sign-off

- [ ] All Day 30 criteria green: onboarding marked complete in the CRM
- [ ] OR: partial pass, extended onboarding opened with a new 30-day plan
- [ ] OR: failed onboarding, account moved to At-Risk and escalated to CS leadership

CSM signature: ______________________________  Date: ____________
CS lead signature: ___________________________  Date: ____________
