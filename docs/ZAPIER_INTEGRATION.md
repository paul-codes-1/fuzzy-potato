# Zapier & Make (Integromat) Integration

Connect CivicLens meeting intelligence to your existing workflows. Pipe meeting summaries, vote alerts, and financial data into Google Sheets, Airtable, Slack, email campaigns, CRMs, and hundreds of other apps.

## Quick Start

### 1. Get Your API Key

You need a CivicLens API key. If you have an account, find it in your dashboard settings. If you're an admin, create a tenant:

```bash
curl -X POST https://your-civiclens-instance/api/v1/admin/tenants \
  -H "X-API-Key: YOUR_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"id": "my-org", "name": "My Organization", "granicus_host": "lexington.granicus.com", "plan": "pro"}'
```

The response includes your `api_key`.

### 2. Connect in Zapier

1. In Zapier, search for **CivicLens** (or create a custom integration using the Zapier Developer Platform)
2. When prompted for authentication, enter your CivicLens API key
3. Zapier will call `GET /api/v1/zapier/auth/test` to verify the key

### 3. Set Up Your First Zap

Choose a **trigger** (what starts the Zap) and an **action** (what happens).

---

## Authentication

All Zapier endpoints use the same `X-API-Key` header authentication as the rest of the CivicLens API:

```
X-API-Key: mra_your_api_key_here
```

Test endpoint for Zapier connection setup:

```
GET /api/v1/zapier/auth/test
```

Returns:
```json
{
  "id": "my-org",
  "name": "My Organization",
  "plan": "pro",
  "authenticated": true
}
```

---

## Triggers (What Starts a Zap)

Triggers fire when something new happens in your meeting archive. Zapier supports two modes:

### Polling Triggers

Zapier calls these endpoints every few minutes to check for new items. Each item has a unique `id` field that Zapier uses for deduplication.

#### New Meeting Processed

Fires when a new meeting is transcribed and summarized.

```
GET /api/v1/zapier/triggers/new_meeting
```

**Fields available in Zapier:**

| Field | Description |
|-------|-------------|
| `clip_id` | Unique meeting clip identifier |
| `title` | Meeting title |
| `date` | Meeting date (YYYY-MM-DD) |
| `meeting_body` | Council body (e.g., "Urban County Council") |
| `topics` | Comma-separated topic list |
| `vote_count` | Number of votes in this meeting |
| `financial_item_count` | Number of financial items |
| `public_comment_count` | Number of public comments |
| `transcript_words` | Word count of transcript |
| `presiding_officer` | Who presided over the meeting |
| `members_present` | Count of members present |
| `attendance_list` | Comma-separated list of attendees |

#### New Vote Detected

Fires for each vote detected in a meeting.

```
GET /api/v1/zapier/triggers/new_vote
```

**Fields:**

| Field | Description |
|-------|-------------|
| `clip_id` | Meeting clip identifier |
| `meeting_date` | Date of the meeting |
| `meeting_body` | Council body name |
| `identifier` | Vote identifier (e.g., "Ordinance 0016-26") |
| `description` | What was voted on |
| `motion_by` | Who made the motion |
| `second_by` | Who seconded |
| `outcome` | "passed" or "failed" |
| `ayes` / `nays` / `abstentions` | Vote counts |
| `votes_for` | Comma-separated names who voted yes |
| `votes_against` | Comma-separated names who voted no |

#### Financial Item Alert

Fires when a financial item is detected. Supports a `threshold` parameter to only fire above a dollar amount.

```
GET /api/v1/zapier/triggers/financial_alert?threshold=100000
```

**Fields:**

| Field | Description |
|-------|-------------|
| `clip_id` | Meeting clip identifier |
| `meeting_date` | Date of the meeting |
| `description` | What the money is for |
| `amount` | Display amount (e.g., "$18,040,000") |
| `amount_cents` | Amount in cents for comparison |
| `type` | Item type (appropriation, contract, etc.) |
| `vendor_or_recipient` | Who receives the funds |

#### Keyword Match

Fires when a keyword appears in meeting agenda items, votes, or public comments.

```
GET /api/v1/zapier/triggers/keyword_match?keyword=zoning
```

**Fields:**

| Field | Description |
|-------|-------------|
| `keyword` | The matched keyword |
| `matched_section` | Where it was found (agenda_items, motions_and_votes, public_comments) |
| `matched_text` | The text that matched |
| `meeting_date` | Date of the meeting |
| `meeting_body` | Council body name |
| `meeting_title` | Full meeting title |

### REST Hook / Instant Triggers

For real-time notifications (no polling delay), use REST Hooks. When you enable an instant trigger in Zapier, it calls:

```
POST /api/v1/zapier/hooks/subscribe
```

```json
{
  "trigger_type": "new_meeting",
  "target_url": "https://hooks.zapier.com/hooks/standard/...",
  "config": {}
}
```

When the Zap is turned off, Zapier calls:

```
DELETE /api/v1/zapier/hooks/{hook_id}
```

Supported trigger types for REST Hooks: `new_meeting`, `new_vote`, `financial_alert`, `keyword_match`.

---

## Actions (What a Zap Can Do)

Actions let Zapier send data to CivicLens.

### Ask a Question (RAG Query)

Submit a natural-language question about meetings.

```
POST /api/v1/zapier/actions/ask
```

```json
{
  "question": "What has the city done about short-term rentals?",
  "meeting_body": "",
  "date_after": "2025-01-01",
  "date_before": ""
}
```

**Response fields:** `question`, `answer`, `source_count`, `sources` (semicolon-separated), `filters_applied`

### Search Votes

Search vote records with filters.

```
POST /api/v1/zapier/actions/search_votes
```

```json
{
  "member": "Brown",
  "outcome": "passed",
  "keyword": "zoning",
  "date_after": "2025-06-01",
  "limit": 20
}
```

Returns an array of flat vote objects.

### Search Financial Items

Search financial items with dollar amount filters.

```
POST /api/v1/zapier/actions/search_financial
```

```json
{
  "min_amount": 100000,
  "keyword": "bonds",
  "date_after": "2025-01-01",
  "limit": 20
}
```

Returns an array of flat financial item objects.

### Export Meeting

Export a specific meeting's full data.

```
POST /api/v1/zapier/actions/export_meeting
```

```json
{
  "clip_id": "6669"
}
```

Returns a flat object with metadata, summary text, transcript preview, attendance, and serialized JSON fields for votes, financial items, and public comments.

---

## Example Zaps

### New Meeting -> Google Sheet Row

**Trigger:** New Meeting Processed
**Action:** Google Sheets - Create Spreadsheet Row

Map fields:
- Column A: `date`
- Column B: `title`
- Column C: `meeting_body`
- Column D: `topics`
- Column E: `vote_count`
- Column F: `public_comment_count`

### Vote Alert -> Slack Message

**Trigger:** New Vote Detected
**Action:** Slack - Send Channel Message

Message template:
```
Vote Result: {{identifier}} - {{outcome}}
Meeting: {{meeting_title}} ({{meeting_date}})
Motion by: {{motion_by}}, Second by: {{second_by}}
Ayes: {{ayes}} / Nays: {{nays}}
For: {{votes_for}}
Against: {{votes_against}}
```

### Financial Item -> Airtable Record

**Trigger:** Financial Item Alert (threshold: $50,000)
**Action:** Airtable - Create Record

Map fields:
- Description: `description`
- Amount: `amount`
- Type: `type`
- Vendor: `vendor_or_recipient`
- Meeting Date: `meeting_date`
- Meeting: `meeting_title`

### Keyword Alert -> Email

**Trigger:** Keyword Match (keyword: "affordable housing")
**Action:** Gmail - Send Email

Subject: `CivicLens Alert: "{{keyword}}" mentioned in {{meeting_title}}`
Body: `Matched in {{matched_section}}: {{matched_text}}`

### Daily Digest -> Notion Database

**Trigger:** Schedule (daily at 8am)
**Action 1:** CivicLens - Ask Question: "Summarize yesterday's council meetings"
**Action 2:** Notion - Create Page with the answer

---

## REST Hook vs Polling

| Feature | Polling | REST Hooks |
|---------|---------|------------|
| Latency | 1-15 minutes (Zapier polls periodically) | Near real-time |
| Setup | Automatic | Requires subscribe/unsubscribe |
| Reliability | Very high (Zapier manages retries) | Depends on webhook delivery |
| Zapier plan | All plans | All plans |

**Recommendation:** Start with polling triggers. They work out of the box and are reliable. Switch to REST Hooks only if you need sub-minute latency.

---

## Make (Integromat) Compatibility

CivicLens works with Make (formerly Integromat) using the same API endpoints. The key differences:

### Authentication in Make

1. Create an HTTP module connection
2. Set the header: `X-API-Key: your_api_key`
3. Base URL: `https://your-civiclens-instance`

### Polling Triggers in Make

Use the **HTTP - Make a request** module:
- URL: `https://your-instance/api/v1/zapier/triggers/new_meeting`
- Method: GET
- Headers: `X-API-Key: your_key`
- Parse response: Yes (JSON)

Set this as a **Watch** trigger with a schedule interval.

### Actions in Make

Use the **HTTP - Make a request** module:
- Method: POST
- Headers: `X-API-Key: your_key`, `Content-Type: application/json`
- Body: JSON with action parameters

### Webhooks in Make

Use the **Webhooks** module to create a custom webhook URL, then subscribe it:

```
POST /api/v1/zapier/hooks/subscribe
{
  "trigger_type": "new_vote",
  "target_url": "https://hook.make.com/your-webhook-url",
  "config": {}
}
```

### Make-Specific Notes

- Make handles nested JSON natively, but CivicLens returns flat JSON for maximum compatibility
- Use the **JSON - Parse JSON** module if you need to parse `votes_json` or `financial_items_json` fields from the export action
- Make's scheduling is more flexible than Zapier's -- you can poll as frequently as every minute on paid plans

---

## API Reference Summary

| Method | Endpoint | Type | Description |
|--------|----------|------|-------------|
| GET | `/api/v1/zapier/auth/test` | Auth | Verify API key |
| GET | `/api/v1/zapier/triggers/new_meeting` | Trigger | Poll new meetings |
| GET | `/api/v1/zapier/triggers/new_vote` | Trigger | Poll new votes |
| GET | `/api/v1/zapier/triggers/financial_alert` | Trigger | Poll financial items |
| GET | `/api/v1/zapier/triggers/keyword_match` | Trigger | Poll keyword matches |
| POST | `/api/v1/zapier/actions/ask` | Action | RAG Q&A query |
| POST | `/api/v1/zapier/actions/search_votes` | Action | Search votes |
| POST | `/api/v1/zapier/actions/search_financial` | Action | Search financial items |
| POST | `/api/v1/zapier/actions/export_meeting` | Action | Export meeting data |
| POST | `/api/v1/zapier/hooks/subscribe` | Hook | Subscribe instant trigger |
| DELETE | `/api/v1/zapier/hooks/{id}` | Hook | Unsubscribe instant trigger |
| GET | `/api/v1/zapier/hooks` | Hook | List active subscriptions |

---

## Rate Limits

Zapier integration endpoints share the same rate limits as the rest of the CivicLens API:

| Plan | Monthly Queries |
|------|----------------|
| Starter | 100 |
| Pro | 1,000 |
| Enterprise | Unlimited |

Polling triggers count against your query limit. If you poll frequently, consider upgrading your plan or using REST Hooks to reduce API calls.
