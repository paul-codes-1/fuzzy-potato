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

# Add timestamps to existing transcripts (for clickable video seeking)
uv run python main.py --update-transcripts
uv run python main.py --update-transcripts --max 10  # Limit to 10 clips

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
      transcript_{date}_{title}_audio.txt # Whisper transcription (plain text)
      transcript_{date}_{title}_audio_segments.json # Timestamped segments (new transcriptions)
      summary.txt                         # AI-generated summary
      summary.html                        # HTML formatted summary
      {date}_agenda_{title}.pdf           # Meeting agenda (if available)
      {date}_agenda_{title}.txt           # Extracted agenda text
      {date}_minutes_{title}.pdf          # Meeting minutes (if available)
      {date}_minutes_{title}.txt          # Extracted minutes text
      metadata.json                       # Clip metadata
```

## Architecture

Your frontend already fetches everything from relative /data/ paths. You have two main options:

  Option 1: S3 + CloudFront (recommended)

  1. Upload lfucg_output/ to S3, mapping it to a /data/ prefix:

  ***
`  aws s3 sync lfucg_output/ s3://public-meetings/data/ --exclude "state.json" --exclude "*.mp3"
`
***

  2. Deploy the built frontend (frontend/dist/) to the same bucket root:
  cd frontend && npm run build

  ***
`  aws s3 sync dist/ s3://public-meetings/ --exclude "data/*"
`
***

  3. Create a CloudFront distribution pointing to the bucket. The structure would be:
  s3://your-bucket/
    index.html          ← from frontend/dist/
    assets/             ← from frontend/dist/
    data/
      index.json        ← from lfucg_output/index.json
      clips/
        6669/
          metadata.json
          summary.html
          ...
  4. Enable S3 static website hosting or use CloudFront with an OAC. For SPA routing, set up a custom error response that returns index.html for 403/404 errors (so React Router works).

  Option 2: Separate S3 origin (CORS)

  If you want the frontend hosted separately (e.g., Vercel/Netlify) and data on S3:

  1. Upload data to S3 and put CloudFront in front of it
  2. Add CORS headers on the S3 bucket/CloudFront
  3. Change the base URL in useMeetings.js to point to your CloudFront domain:
  const DATA_BASE = import.meta.env.VITE_DATA_URL || '/data'
  const INDEX_URL = `${DATA_BASE}/index.json`
  3. Then prefix all fetch calls with DATA_BASE instead of hardcoded /data.
  4. Set VITE_DATA_URL=https://d1234.cloudfront.net/data at build time.

  Option 1 is simpler since everything lives under one domain — no CORS, no env vars, and the code works as-is with zero changes. You just need to get the S3 directory structure to match what the fetches expect
  (/data/index.json, /data/clips/{id}/...).


## API Costs

Per clip (approximate):
- Whisper: ~$0.006/min of audio
- GPT-4o: ~$0.01-0.05 per summary
- GPT-4o-mini: ~$0.001 per topic extraction

A typical 1-hour meeting costs ~$0.50-1.00 to process.

## License

MIT
