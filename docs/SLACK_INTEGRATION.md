# Slack Integration

Connect CivicLens to your Slack workspace to query meeting intelligence and receive notifications directly in your channels.

## Features

- **Slash commands** - Ask questions and search meetings from any channel
- **@mentions** - Mention @CivicLens in a channel to ask a question
- **Notifications** - Receive alerts when new meetings are processed, votes are detected, or financial items appear
- **Rich formatting** - Meeting summaries, vote results, and Q&A responses are displayed as structured Slack cards

## Setup

### 1. Create a Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App** > **From scratch**
2. Name it **CivicLens** and select your workspace
3. Under **OAuth & Permissions**, add the following **Bot Token Scopes**:
   - `chat:write` - Post messages
   - `commands` - Handle slash commands
   - `app_mentions:read` - Respond to @CivicLens mentions
   - `channels:read` - List channels for configuration

### 2. Configure Slash Commands

Under **Slash Commands**, create a new command:

| Field | Value |
|-------|-------|
| Command | `/civiclens` |
| Request URL | `https://your-domain.com/api/v1/integrations/slack/commands` |
| Short Description | Query city meeting intelligence |
| Usage Hint | `ask <question>` or `search <term>` |

### 3. Configure Event Subscriptions

Under **Event Subscriptions**:

1. Toggle **Enable Events** to On
2. Set **Request URL** to `https://your-domain.com/api/v1/integrations/slack/events`
   - Slack will send a challenge request; the endpoint handles this automatically
3. Under **Subscribe to bot events**, add:
   - `app_mention`

### 4. Set Environment Variables

Add these to your server's `.env` file:

```bash
SLACK_SIGNING_SECRET=your_signing_secret     # From Basic Information > App Credentials
SLACK_CLIENT_ID=your_client_id               # From Basic Information > App Credentials
SLACK_CLIENT_SECRET=your_client_secret       # From Basic Information > App Credentials
```

### 5. Install to Workspace

**Option A: Direct install from Slack UI**

Click **Install to Workspace** on the Slack App settings page. Then manually configure the bot token via the API config endpoint.

**Option B: OAuth flow via CivicLens API**

```bash
# Start the OAuth flow (returns an authorization URL)
curl -X POST https://your-domain.com/api/v1/integrations/slack/install \
  -H "X-API-Key: your_tenant_api_key"

# Visit the returned authorization_url in your browser to complete installation
```

## Slash Command Usage

### Ask a question

```
/civiclens ask What has the city council done about short-term rental regulations?
```

The bot will acknowledge immediately, then post the full answer with source citations once the RAG query completes.

### Search meetings

```
/civiclens search zoning
```

Returns a list of meetings matching the search term, sorted by date.

### Check status

```
/civiclens status
```

Shows connection status and notification settings.

### Help

```
/civiclens help
```

Lists all available commands.

## @Mention Usage

Mention the bot in any channel where it's been added:

```
@CivicLens What were the recent votes on the city budget?
```

The bot will reply in a thread with the answer and sources.

## Notification Configuration

Configure which notifications your workspace receives and which channel they go to.

### Get current config

```bash
curl https://your-domain.com/api/v1/integrations/slack/config \
  -H "X-API-Key: your_tenant_api_key"
```

### Update config

```bash
curl -X PUT https://your-domain.com/api/v1/integrations/slack/config \
  -H "X-API-Key: your_tenant_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "channel_id": "C0123456789",
    "notify_new_meetings": true,
    "notify_votes": true,
    "notify_financial": false
  }'
```

### Notification types

| Setting | Description |
|---------|-------------|
| `notify_new_meetings` | Post a summary card when a new meeting is processed |
| `notify_votes` | Post vote result cards for detected votes |
| `notify_financial` | Post alerts for financial items above configured thresholds |

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/v1/integrations/slack/commands` | Slack signing secret | Slash command handler |
| POST | `/api/v1/integrations/slack/events` | Slack signing secret | Events API handler |
| POST | `/api/v1/integrations/slack/install` | Tenant API key | Start OAuth install flow |
| GET | `/api/v1/integrations/slack/callback` | None (OAuth) | OAuth callback |
| PUT | `/api/v1/integrations/slack/config` | Tenant API key | Update notification config |
| GET | `/api/v1/integrations/slack/config` | Tenant API key | Get current config |

## Message Formats

### Q&A Response

[Screenshot placeholder: Shows a Slack message with CivicLens Q&A header, the question in bold, a divider, the answer text, and a sources section with clickable meeting links and timestamps]

### Meeting Summary Card

[Screenshot placeholder: Shows a Slack card with meeting title header, metadata fields (date, body, clip ID, vote count), topic tags, summary excerpt, and a "Watch Meeting" button linking to Granicus]

### Vote Result Card

[Screenshot placeholder: Shows a Slack message with pass/fail indicator, vote identifier and description, meeting metadata, vote tally (ayes/nays/abstentions), roll call names, and a link to the meeting video timestamp]

### Search Results

[Screenshot placeholder: Shows a Slack message with search header, result count, and a list of matching meetings with titles, dates, bodies, topics, and "View" buttons]

## Troubleshooting

### Bot not responding to slash commands

1. Verify `SLACK_SIGNING_SECRET` is correct in your environment
2. Check that the Request URL in Slack app settings points to your server
3. Ensure the workspace is connected: run `/civiclens status`

### Bot not responding to @mentions

1. Verify `app_mention` is listed under Event Subscriptions
2. Ensure the bot has been invited to the channel (`/invite @CivicLens`)
3. Check server logs for event processing errors

### OAuth install fails

1. Verify `SLACK_CLIENT_ID` and `SLACK_CLIENT_SECRET` are set
2. Ensure the callback URL `https://your-domain.com/api/v1/integrations/slack/callback` is listed in OAuth Redirect URLs in the Slack app settings
