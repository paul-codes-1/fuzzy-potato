# Granicus API & Integration Documentation

This document covers known Granicus APIs, URL parameters, embed options, and integration points for government meeting video/document management systems.

## Table of Contents

- [Video Player URL Parameters](#video-player-url-parameters)
- [Page Endpoints](#page-endpoints)
- [RSS Feeds](#rss-feeds)
- [Legistar Web API](#legistar-web-api)
- [MediaManager SOAP API](#mediamanager-soap-api)
- [govDelivery APIs](#govdelivery-apis)
- [JavaScript Player API](#javascript-player-api)
- [Resources](#resources)

---

## Video Player URL Parameters

The Granicus video player supports various URL parameters for embedding and controlling playback.

### Player URL Patterns

```
# Standard player page
https://{subdomain}.granicus.com/player/clip/{clip_id}?view_id={view_id}

# Legacy MediaPlayer
https://{subdomain}.granicus.com/MediaPlayer.php?clip_id={clip_id}&view_id={view_id}
```

### Available Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `clip_id` | int | Required. The video clip identifier |
| `view_id` | int | View/channel identifier |
| `entrytime` | int | **Start time in seconds** (e.g., `entrytime=120` starts at 2:00) |
| `stoptime` | int | **End time in seconds** (e.g., `stoptime=300` stops at 5:00) |
| `embed` | 1/0 | Enable embed mode (removes navigation chrome) |
| `player_width` | int | Width in pixels (e.g., `720`) |
| `player_height` | int | Height in pixels (e.g., `480`) |
| `auto_start` | 1/0 | Autoplay on load |
| `autostart` | 1/0 | Alternative autoplay parameter |
| `redirect` | true | Follow redirects |
| `meta_id` | int | Metadata/agenda item identifier |

### Example URLs

```bash
# Basic player
https://lfucg.granicus.com/player/clip/6669?view_id=14

# Start at 2 minutes, stop at 5 minutes
https://lfucg.granicus.com/player/clip/6669?view_id=14&entrytime=120&stoptime=300

# Embedded player with autoplay
https://lfucg.granicus.com/player/clip/6669?view_id=14&embed=1&player_width=720&player_height=480&auto_start=1

# Jump to specific agenda item timestamp
https://lfucg.granicus.com/player/clip/6669?view_id=14&entrytime=77
```

### Embed iframe Example

```html
<iframe
  src="https://lfucg.granicus.com/player/clip/6669?view_id=14&embed=1&entrytime=120&stoptime=300"
  width="720"
  height="480"
  frameborder="0"
  allowfullscreen>
</iframe>
```

---

## Page Endpoints

### ViewPublisher (Meeting Archive)

Lists all meetings/clips for a view.

```
https://{subdomain}.granicus.com/ViewPublisher.php?view_id={view_id}
```

### AgendaViewer

Displays meeting agenda PDF.

```
https://{subdomain}.granicus.com/AgendaViewer.php?view_id={view_id}&clip_id={clip_id}
```

### MinutesViewer

Displays meeting minutes (PDF or HTML).

```
https://{subdomain}.granicus.com/MinutesViewer.php?view_id={view_id}&clip_id={clip_id}&doc_id={uuid}
```

| Parameter | Description |
|-----------|-------------|
| `view_id` | View/channel identifier |
| `clip_id` | Meeting clip identifier |
| `doc_id` | Document UUID (for specific minutes version) |

### ASX (Direct Video Stream)

Windows Media Player compatible stream URL.

```
https://{subdomain}.granicus.com/ASX.php?view_id={view_id}&clip_id={clip_id}
```

---

## RSS Feeds

Granicus provides RSS feeds for meeting agendas and minutes.

### Feed URLs

```bash
# Agendas RSS feed
https://{subdomain}.granicus.com/ViewPublisherRSS.php?view_id={view_id}&mode=agendas

# Minutes RSS feed
https://{subdomain}.granicus.com/ViewPublisherRSS.php?view_id={view_id}&mode=minutes
```

### Example (LFUCG)

```bash
# Lexington agendas
https://lfucg.granicus.com/ViewPublisherRSS.php?view_id=14&mode=agendas

# Lexington minutes
https://lfucg.granicus.com/ViewPublisherRSS.php?view_id=14&mode=minutes
```

---

## Legistar Web API

RESTful API for accessing legislative data (matters, events, votes, persons).

**Base URL:** `https://webapi.legistar.com/v1/{Client}/`

**Documentation:** https://webapi.legistar.com/

### Authentication

Some clients require an API token appended as a query parameter:
```
?token={your_api_token}
```

### Key Endpoints

| Endpoint | Description |
|----------|-------------|
| `/matters` | Legislative matters/items |
| `/matters/{id}` | Single matter by ID |
| `/matters/{id}/histories` | Matter history/actions |
| `/events` | Calendar events/meetings |
| `/events/{id}` | Single event by ID |
| `/events/{id}/eventitems` | Agenda items for event |
| `/eventitems/{id}/votes` | Votes on agenda item |
| `/bodies` | Legislative bodies |
| `/persons` | Officials/members |

### ODATA Query Parameters

Results limited to 1000 items per request. Use ODATA for pagination and filtering:

| Parameter | Example | Description |
|-----------|---------|-------------|
| `$top` | `$top=10` | Limit results |
| `$skip` | `$skip=10` | Offset for pagination |
| `$filter` | See below | Filter criteria |
| `$orderby` | `$orderby=EventDate desc` | Sort results |

### Filter Examples

```bash
# Events in September 2024
$filter=EventDate ge datetime'2024-09-01' and EventDate lt datetime'2024-10-01'

# Matters passed by specific body
$filter=MatterHistoryPassedFlag ne null and MatterHistoryActionBodyName eq 'Council'
```

### Full Example Requests

```bash
# Get first 10 matters
curl "https://webapi.legistar.com/v1/lexingtonky/matters?\$top=10"

# Get events for date range
curl "https://webapi.legistar.com/v1/lexingtonky/events?\$filter=EventDate%20ge%20datetime'2024-01-01'"

# Get votes for agenda item
curl "https://webapi.legistar.com/v1/lexingtonky/eventitems/12345/votes"
```

### Third-Party Libraries

- **Python:** [LegisPy](https://github.com/mjumbewu/LegisPy) - SOAP API wrapper
- **R:** [legistarapi](https://elipousson.github.io/legistarapi/) - REST API wrapper

---

## MediaManager SOAP API

SOAP-based API for backend automation of MediaManager tasks.

**Note:** This API may require Granicus credentials/contract for access.

### Available SDKs

| Language | Repository |
|----------|------------|
| .NET | https://github.com/Granicus/platform-api-net |
| Java | https://github.com/Granicus/platform-api-java |

### .NET SDK Usage

```csharp
using Granicus.MediaManager.SDK;

// Connect and authenticate
var client = new MediaManager(host, username, password);

// Get folders
var folders = client.GetFolders();

// Get cameras
var cameras = client.GetCameras();
```

### Java SDK Usage

```java
import com.granicus.soap.PlatformClient;
import com.granicus.xsd.FolderData;

// Connect with automatic authentication
PlatformClient client = new PlatformClient(site, username, password);

// Get folders
FolderData[] folders = client.getFolders();
for (FolderData folder : folders) {
    System.out.println(folder.getName());
}
```

### Known Operations

- `GetFolders()` / `getFolders()` - List folders
- `GetCameras()` - List cameras
- Upload/manage videos
- Upload/manage agendas
- Live indexing operations

---

## govDelivery APIs

Communications platform APIs for subscriber management and messaging.

**Developer Portal:** https://developers.govdelivery.com/

### Available APIs

| API | Description |
|-----|-------------|
| Communications Cloud | Manage subscribers, topics, bulletins |
| Targeted Messaging (TMS) | Email, SMS, voice delivery |
| Interactive Text | 2-way SMS engagement |

### Communications Cloud Features

- Subscriber management
- Bulletin creation/distribution
- Batch subscriber sync
- Signup management
- Reports Pro

### TMS Features

- Email sending
- SMS delivery
- Voice messages
- Delivery tracking

### Support

- Portal: https://support.granicus.com
- Email: support@granicus.com
- Phone (US): (800) 314-0147
- Phone (Europe): (0800) 032 7764

---

## JavaScript Player API

The Granicus player uses Flowplayer internally and exposes some JavaScript methods.

### Available Methods (Observed)

```javascript
// Seek to position (time in seconds)
SetPlayerPosition(timeInSeconds);

// Internal flowplayer seek
flowplayer().seek(seconds);

// Time conversion helpers
convertHHMMSSToSeconds("01:23:45");  // Returns 5025
convertSecondsToHHMMSS(5025);        // Returns "01:23:45"
```

### Cuepoints

The player supports cuepoints for agenda items:
```javascript
// Internal cuepoint structure
{"time": 77, "type": "Agenda"}
```

### Embed Controls

Embedded players may include start/stop time controls that internally use:
- `entrytime` parameter (seconds)
- `stoptime` parameter (seconds)

---

## URL Pattern Reference

Quick reference for common Granicus URLs:

```bash
# Meeting archive listing
https://{org}.granicus.com/ViewPublisher.php?view_id={id}

# Video player (modern)
https://{org}.granicus.com/player/clip/{clip_id}?view_id={id}

# Video player (legacy)
https://{org}.granicus.com/MediaPlayer.php?clip_id={id}&view_id={id}

# Agenda PDF
https://{org}.granicus.com/AgendaViewer.php?view_id={id}&clip_id={id}

# Minutes viewer
https://{org}.granicus.com/MinutesViewer.php?view_id={id}&clip_id={id}

# RSS - Agendas
https://{org}.granicus.com/ViewPublisherRSS.php?view_id={id}&mode=agendas

# RSS - Minutes
https://{org}.granicus.com/ViewPublisherRSS.php?view_id={id}&mode=minutes

# Direct video stream (ASX)
https://{org}.granicus.com/ASX.php?view_id={id}&clip_id={id}
```

---

## Resources

### Official Documentation

- [Granicus Support Portal](https://support.granicus.com)
- [Legistar Web API](https://webapi.legistar.com/)
- [Legistar API Examples](https://webapi.legistar.com/Home/Examples)
- [govDelivery Developer Portal](https://developers.govdelivery.com/)

### GitHub Repositories

- [Platform API .NET SDK](https://github.com/Granicus/platform-api-net)
- [Platform API Java SDK](https://github.com/Granicus/platform-api-java)
- [Granicus GitHub Org](https://github.com/Granicus)

### Third-Party Tools

- [LegisPy (Python)](https://github.com/mjumbewu/LegisPy)
- [legistarapi (R)](https://elipousson.github.io/legistarapi/)

### Contact

- General: info@granicus.com
- Support: support@granicus.com
- Support Phone (US): (800) 314-0147

---

## Notes

- Time parameters (`entrytime`, `stoptime`) use **seconds** from video start
- API token may be required for Legistar API depending on client configuration
- RSS feeds require valid URL structure per W3C Feed Validation
- MediaManager SOAP API requires Granicus account credentials
- Some organizations have custom parameter prefixes (e.g., `coa_clip_id` for Alexandria)
