# Microsoft Teams Integration

Connect CivicLens to Microsoft Teams to receive meeting notifications and query the meeting archive directly from your channels.

## Overview

The Teams integration supports two directions:

- **Incoming webhooks** - CivicLens posts Adaptive Cards to a Teams channel (new meeting summaries, vote results, financial items)
- **Outgoing webhooks** - Team members @mention CivicLens in a channel to ask questions or search the meeting archive

## Prerequisites

- A Microsoft Teams workspace with admin access (or permission to create webhooks)
- A running CivicLens API server
- An API key for your CivicLens tenant

## Setup Guide

### Step 1: Create an Incoming Webhook in Teams

Incoming webhooks let CivicLens post notifications to a Teams channel.

1. Open Microsoft Teams and navigate to the channel where you want notifications
2. Click the **...** (more options) next to the channel name
3. Select **Connectors** (or **Manage channel** > **Connectors** in newer versions)
4. Search for **Incoming Webhook** and click **Configure**
5. Give it a name (e.g., "CivicLens") and optionally upload the CivicLens logo
6. Click **Create**
7. Copy the webhook URL that appears -- you will need this for configuration

### Step 2: Configure CivicLens with the Webhook URL

Use the CivicLens API to save your Teams configuration:

```bash
curl -X PUT https://your-civiclens-server/api/v1/integrations/teams/config \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "webhook_url": "https://your-org.webhook.office.com/webhookb2/...",
    "channel_name": "City Council Updates",
    "teams_tenant_id": "your-m365-tenant-id",
    "notify_new_meetings": true,
    "notify_votes": true,
    "notify_financial": false
  }'
```

### Step 3: Test the Connection

Send a test Adaptive Card to verify the webhook works:

```bash
curl -X POST https://your-civiclens-server/api/v1/integrations/teams/test \
  -H "X-API-Key: your-api-key"
```

You should see a test card appear in your Teams channel.

### Step 4: Create an Outgoing Webhook (Optional)

Outgoing webhooks let team members @mention CivicLens to ask questions.

1. In Teams, go to **Apps** > **Manage your apps** > **Create an outgoing webhook** (or navigate via Team settings > Apps > Outgoing Webhook)
2. Fill in:
   - **Name**: CivicLens
   - **Callback URL**: `https://your-civiclens-server/api/v1/integrations/teams/webhook`
   - **Description**: Query the city meeting archive
3. Click **Create**
4. Teams will display a **security token** (HMAC secret) -- copy this
5. Set the `TEAMS_WEBHOOK_SECRET` environment variable on your CivicLens server:

```bash
# In your .env file or environment
TEAMS_WEBHOOK_SECRET=your-base64-encoded-secret-from-teams
```

6. Restart the CivicLens API server

## Usage

### Notifications (Incoming)

When configured, CivicLens automatically posts Adaptive Cards for:

- **New meetings processed** - Summary card with date, body, topics, vote count, and a "Watch Meeting" button
- **Vote results** - Detailed card showing motion, outcome, tally, and roll call
- **Financial items** - Cards for significant financial actions (when enabled)

### Querying (Outgoing Webhook)

Mention @CivicLens in any channel where the outgoing webhook is active:

```
@CivicLens ask What has the city done about short-term rentals?
@CivicLens search zoning
@CivicLens status
@CivicLens help
```

**Commands:**

| Command | Description |
|---------|-------------|
| `ask <question>` | Ask a natural-language question about city meetings |
| `search <term>` | Search the meeting archive by keyword |
| `status` | Check integration connection status |
| `help` | Show available commands |

If you omit a command keyword, the message is treated as an `ask` query:

```
@CivicLens What was the budget for parks this year?
```

## API Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/integrations/teams/webhook` | HMAC | Outgoing webhook handler (called by Teams) |
| PUT | `/api/v1/integrations/teams/config` | API Key | Save/update Teams configuration |
| GET | `/api/v1/integrations/teams/config` | API Key | Get current Teams configuration |
| POST | `/api/v1/integrations/teams/test` | API Key | Send test message to configured channel |

## Notification Settings

Control which notifications are sent via the config endpoint:

```bash
# Enable only new meeting alerts
curl -X PUT https://your-civiclens-server/api/v1/integrations/teams/config \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "notify_new_meetings": true,
    "notify_votes": false,
    "notify_financial": false
  }'
```

| Setting | Default | Description |
|---------|---------|-------------|
| `notify_new_meetings` | `true` | Post a summary card when a new meeting is processed |
| `notify_votes` | `true` | Post individual vote result cards |
| `notify_financial` | `false` | Post cards for financial agenda items |

## Adaptive Card Examples

### Meeting Summary Card

```json
{
  "type": "AdaptiveCard",
  "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
  "version": "1.4",
  "body": [
    {
      "type": "TextBlock",
      "text": "New Meeting Processed: January 8 2026 Council Meeting",
      "size": "Large",
      "weight": "Bolder",
      "color": "Accent",
      "wrap": true
    },
    {
      "type": "FactSet",
      "facts": [
        { "title": "Date", "value": "2026-01-08" },
        { "title": "Body", "value": "Urban County Council" },
        { "title": "Clip ID", "value": "6669" },
        { "title": "Votes", "value": "5" }
      ]
    },
    {
      "type": "TextBlock",
      "text": "**Topics:** Budget, Zoning, Public Safety",
      "wrap": true,
      "size": "Small",
      "isSubtle": true
    },
    { "type": "TextBlock", "text": "---", "spacing": "Small" },
    {
      "type": "TextBlock",
      "text": "The council approved the 2026 operating budget...",
      "wrap": true
    }
  ],
  "actions": [
    {
      "type": "Action.OpenUrl",
      "title": "Watch Meeting",
      "url": "https://lexington.granicus.com/player/clip/6669",
      "style": "positive"
    }
  ]
}
```

### Q&A Response Card

```json
{
  "type": "AdaptiveCard",
  "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
  "version": "1.4",
  "body": [
    {
      "type": "TextBlock",
      "text": "CivicLens Q&A",
      "size": "Large",
      "weight": "Bolder",
      "color": "Accent"
    },
    {
      "type": "TextBlock",
      "text": "**Question:** What has the city done about short-term rentals?",
      "wrap": true
    },
    { "type": "TextBlock", "text": "---", "spacing": "Small" },
    {
      "type": "TextBlock",
      "text": "The council has discussed short-term rental regulations in several meetings...",
      "wrap": true
    },
    { "type": "TextBlock", "text": "---", "spacing": "Small" },
    {
      "type": "TextBlock",
      "text": "**Sources:**\n- [March 2026 Council Meeting](https://...) (2026-03-12) [15:30]\n- [January 2026 Planning](https://...) (2026-01-20) [42:15]",
      "wrap": true,
      "size": "Small"
    }
  ]
}
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TEAMS_WEBHOOK_SECRET` | For outgoing webhooks | Base64-encoded HMAC secret from Teams outgoing webhook setup |

The incoming webhook URL is stored per-tenant in the database (via PUT /config), not as an environment variable.

## Troubleshooting

**Test message not appearing:**
- Verify the webhook URL is correct and has not expired
- Check that the webhook connector is still active in the Teams channel
- Look at the CivicLens API server logs for HTTP errors

**Outgoing webhook not responding:**
- Confirm your CivicLens server is publicly accessible from the internet
- Verify `TEAMS_WEBHOOK_SECRET` matches the token displayed when you created the outgoing webhook
- Check that the callback URL in Teams matches your server's endpoint exactly

**"Teams webhook secret not configured" error:**
- Set the `TEAMS_WEBHOOK_SECRET` environment variable and restart the server

**Cards not rendering:**
- Teams requires Adaptive Card schema version 1.4 or lower
- Verify JSON is well-formed in the API server logs
