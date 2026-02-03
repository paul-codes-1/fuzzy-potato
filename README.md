# LFUCG Meeting Pipeline

Downloads, transcribes, and generates comprehensive summaries from Lexington-Fayette Urban County Government (LFUCG) city council meeting video clips hosted on Granicus. Includes a React SPA frontend for browsing and searching the meeting archive.

## Documentation

- **[granicus.md](granicus.md)** - Granicus platform documentation including:
  - Video player URL parameters (`entrytime`, `stoptime` for timestamp linking)
  - Legistar Web API (REST API for legislative data)
  - MediaManager SOAP API (video management)
  - RSS feeds for agendas/minutes
  - Embed options and JavaScript player API

## Quick Start

```bash
# Install dependencies
uv sync

# Set up environment
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY

# Process a single clip
uv run python main.py 6669

# Generate search index
uv run python main.py --generate-index

# Start frontend dev server
cd frontend && npm install && npm run dev
```

## Requirements

- Python 3.9+
- [uv](https://github.com/astral-sh/uv) (Python package manager)
- ffmpeg (system installation)
- tesseract-ocr (for OCR of scanned agenda PDFs)
- poppler (for pdf2image)
- Node.js 18+ (for frontend)
- OpenAI API key

### Install System Dependencies

```bash
# macOS
brew install ffmpeg tesseract poppler

# Ubuntu/Debian
sudo apt install ffmpeg tesseract-ocr poppler-utils
```

## Environment Variables

Create a `.env` file:

```bash
OPENAI_API_KEY=sk-...
FIRST_CLIP_ID=6669  # Optional: starting clip for --auto mode
```

## Pipeline Commands

```bash
# Process single clip
uv run python main.py 6669

# Process range of clips (inclusive)
uv run python main.py 6669 6675

# Auto-process from last clip + 1 (or FIRST_CLIP_ID)
uv run python main.py --auto

# Auto-process with limit
uv run python main.py --auto --max 5

# Scrape Granicus and process all new clips
uv run python main.py --scrape --max 100

# Reprocess existing clips
uv run python main.py 6669 --force

# Generate search index for frontend
uv run python main.py --generate-index

# Skip keeping audio files (saves disk space)
uv run python main.py 6669 --no-audio

# Advanced options
uv run python main.py 6669 --output-dir /path/to/output
uv run python main.py 6669 --summary-model gpt-4o-mini   # Cheaper summaries
uv run python main.py 6669 --quiet                       # Reduce output
```

### Run in Background

For long-running jobs:

```bash
# Start processing in background
nohup uv run python main.py --scrape --max 9999 > pipeline.log 2>&1 &

# Monitor progress
tail -f pipeline.log

# Check state
cat lfucg_output/state.json | python -m json.tool

# Stop processing
pkill -f "python main.py"
```

## Probe Available Clips

Discover all available clip IDs without downloading:

```bash
# Probe clips 1-7000
uv run python probe_clips.py 1 7000

# Resume from where you left off
uv run python probe_clips.py

# Run in background
nohup uv run python probe_clips.py 1 7000 > probe.log 2>&1 &

# Check progress
cat lfucg_output/available_clips.json | head -5
```

Results saved to `lfucg_output/available_clips.json`.

**Important:** Once `available_clips.json` exists, `--auto` mode will use it to only process valid clips, skipping non-existent clip IDs automatically.

## Frontend

### Development

```bash
cd frontend
npm install
npm run dev
```

The dev server proxies `/data/` to `../lfucg_output/` via symlink.

### Production Build

```bash
cd frontend
npm run build
npm run preview  # Test production build locally
```

### Deploy

Copy `frontend/dist/` and `lfucg_output/` to your hosting:

```
your-server/
  index.html
  assets/
  data/
    index.json
    clips/
      {clip_id}/
        summary.html
        metadata.json
        ...
```

## Output Structure

Files are named with date prefix for easy sorting and identification:

```
lfucg_output/
  state.json                              # Pipeline state (tracks progress)
  index.json                              # Search index for frontend
  available_clips.json                    # Probed clip IDs
  clips/
    {clip_id}/
      {date}_{title}_audio.mp3            # Downloaded audio (e.g., 2026-01-08_January_8_WQFB_audio.mp3)
      transcript_{date}_{title}_audio.txt # Whisper transcription
      summary.txt                         # AI-generated summary
      summary.html                        # HTML formatted summary
      {date}_agenda_{title}.pdf           # Meeting agenda (if available)
      {date}_agenda_{title}.txt           # Extracted agenda text
      {date}_minutes_{title}.pdf          # Meeting minutes (if available)
      {date}_minutes_{title}.txt          # Extracted minutes text
      metadata.json                       # Clip metadata
```

## Architecture

### Data Source

Videos and documents are hosted on [Granicus](https://granicus.com/), a government media platform. See [granicus.md](granicus.md) for API documentation including:
- Deep-linking to video timestamps via `entrytime` parameter
- Legistar API for legislative data (matters, votes, events)
- RSS feeds for agendas and minutes

### Pipeline Steps

1. **Title Extraction** - Gets original clip title via yt-dlp
2. **Metadata Scraping** - Extracts date and meeting body from title
3. **Download** - Downloads audio as MP3 (48kbps, 22kHz mono)
4. **Compression** - Compresses if >24MB (Whisper API limit)
5. **Transcription** - OpenAI Whisper API
6. **Agenda Download** - PDF agenda + text extraction (OCR fallback for scanned PDFs)
7. **Topic Extraction** - GPT-4o-mini extracts 3-8 topics
8. **Summary Generation** - GPT-4o creates meeting summary
9. **HTML Generation** - Converts summary to HTML
10. **Index Generation** - Creates searchable index.json for frontend

### Frontend Stack

- React 18 + Vite 5
- React Router v6
- Fuse.js 7 (full-text search)
- Components: MeetingList, MeetingDetail, SearchBar, TopicFilter

## AWS Deployment

Deploy the frontend to S3/CloudFront with Lambda for automated syncing.

### Prerequisites

- AWS CLI configured with appropriate credentials
- S3 bucket for hosting
- (Optional) CloudFront distribution for CDN

### 1. Initial Data Upload

Upload your existing processed clips to S3:

```bash
# Upload pipeline output to S3
aws s3 sync lfucg_output/ s3://your-bucket/data/ \
  --exclude ".DS_Store" \
  --exclude "*.mp3"  # Optional: skip audio files to save storage

# Upload frontend build
cd frontend && npm run build
aws s3 sync dist/ s3://your-bucket/ --exclude "data/*"
```

Your S3 bucket structure should be:

```
s3://your-bucket/
  index.html              # Frontend entry point
  assets/                 # Frontend assets (JS, CSS)
  data/
    state.json            # Pipeline state
    index.json            # Search index
    available_clips.json  # Probed clip IDs (optional)
    clips/
      6669/
        metadata.json
        summary.html
        summary.txt
        transcript_*.txt
        agenda_*.pdf
        agenda_*.txt
      6670/
        ...
```

### 2. S3 Static Website Hosting

Enable static website hosting on your bucket:

```bash
aws s3 website s3://your-bucket/ --index-document index.html --error-document index.html
```

Or use CloudFront with S3 origin for HTTPS and better performance.

### 3. Lambda Deployment

The Lambda function automatically processes new clips on a schedule.

#### Create Lambda Function

```bash
cd lambda

# Install dependencies into package
pip install -r requirements.txt -t package/
cp sync_meetings.py package/

# Copy main pipeline (Lambda imports it)
cp ../main.py package/

# Create deployment package
cd package && zip -r ../lambda.zip . && cd ..

# Create Lambda function
aws lambda create-function \
  --function-name lfucg-meeting-sync \
  --runtime python3.11 \
  --handler sync_meetings.handler \
  --zip-file fileb://lambda.zip \
  --role arn:aws:iam::YOUR_ACCOUNT:role/your-lambda-role \
  --timeout 900 \
  --memory-size 1024 \
  --environment "Variables={OPENAI_API_KEY=sk-...,S3_BUCKET=your-bucket}"
```

#### Lambda Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | OpenAI API key for transcription/summarization |
| `S3_BUCKET` | Yes | S3 bucket name |
| `S3_DATA_PREFIX` | No | Prefix for data files (default: `data/`) |
| `FIRST_CLIP_ID` | No | Starting clip ID (default: 6669) |

#### Lambda IAM Role

The Lambda role needs these permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::your-bucket",
        "arn:aws:s3:::your-bucket/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:*"
    }
  ]
}
```

#### Create EventBridge Schedule

Trigger Lambda at 12pm and 8pm on weekdays:

```bash
# Create schedule rule
aws events put-rule \
  --name lfucg-meeting-sync-schedule \
  --schedule-expression "cron(0 12,20 ? * MON-FRI *)"

# Add Lambda as target
aws events put-targets \
  --rule lfucg-meeting-sync-schedule \
  --targets "Id"="1","Arn"="arn:aws:lambda:REGION:ACCOUNT:function:lfucg-meeting-sync"

# Grant EventBridge permission to invoke Lambda
aws lambda add-permission \
  --function-name lfucg-meeting-sync \
  --statement-id eventbridge-invoke \
  --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn arn:aws:events:REGION:ACCOUNT:rule/lfucg-meeting-sync-schedule
```

#### Lambda Event Options

Invoke manually with custom options:

```bash
# Process up to 10 clips
aws lambda invoke \
  --function-name lfucg-meeting-sync \
  --payload '{"max_clips": 10}' \
  response.json

# Force reprocess
aws lambda invoke \
  --function-name lfucg-meeting-sync \
  --payload '{"max_clips": 5, "force": true}' \
  response.json

# Full sync (downloads all existing data first)
aws lambda invoke \
  --function-name lfucg-meeting-sync \
  --payload '{"full_sync": true, "max_clips": 5}' \
  response.json
```

### 4. CloudFront (Optional)

For HTTPS and better performance:

```bash
aws cloudfront create-distribution \
  --origin-domain-name your-bucket.s3.amazonaws.com \
  --default-root-object index.html
```

Configure the distribution to:
- Forward requests to S3 origin
- Handle SPA routing (return index.html for 404s)
- Set appropriate cache behaviors for `/data/` vs static assets

## API Costs

Per clip (approximate):
- Whisper: ~$0.006/min of audio
- GPT-4o: ~$0.01-0.05 per summary
- GPT-4o-mini: ~$0.001 per topic extraction

A typical 1-hour meeting costs ~$0.50-1.00 to process.

## License

MIT
