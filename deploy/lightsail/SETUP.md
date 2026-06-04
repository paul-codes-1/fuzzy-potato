# Co-located Lightsail deploy — pipeline + RAG API on one box

This replaces the old split where the ingest pipeline ran on Paul's Mac and
the RAG/MCP API ran on **App Runner** (with chroma_db + search.db **baked into
a ~5.4 GB Docker image**, requiring a full ECR push + App Runner redeploy on
every ingest).

**New model:** one AWS Lightsail box runs **both** the ingest pipeline (on
cron) and the FastAPI RAG/MCP server, sharing `lfucg_output/` on local disk.
New data is surfaced by a sub-second `systemctl restart` of the API — no
Docker, no ECR, no App Runner. Same pattern as the `maps-api` box.

```
                 meetings.lexingtonky.news  (CloudFront)
                  /                                   \
        /api/*  (HTTPS)                              /*  (SPA)
            |                                          |
  meetings-origin.lexingtonky.news                    S3  s3://public-meetings
   (Lightsail static IP, Caddy TLS)              (browse/detail + clip.md + index.json)
            |
      127.0.0.1:8000  uvicorn rag.server:app  (systemd: lfucg-rag)
            |  reads
      /opt/fuzzy-potato/lfucg_output/  <-- written by ingest_cron.sh (cron)
        chroma_db/ (5.1G)  search.db (300M)  clips/ (5.9G)  index.json
```

Files in this directory:

| File | Role |
|------|------|
| `ingest_cron.sh`      | Lean incremental ingest (every 6h, weekdays) |
| `backfill_weekly.sh`  | Heavy archive-wide sweeps (weekly) |
| `sync_data_s3.sh`     | Lean S3 sync of per-clip data + index (no npm build) |
| `lfucg-rag.service`   | systemd unit for the uvicorn API |
| `Caddyfile`           | TLS reverse proxy → 127.0.0.1:8000 |
| `crontab.txt`         | The two cron entries |

> **Note on jurisdiction-specific values.** Bucket `public-meetings`,
> CloudFront `E8OIXOXDRETLZ`, domain `meetings.lexingtonky.news`, and
> `FIRST_CLIP_ID` are all LFUCG-specific. A multi-jurisdiction refactor
> (pending the architecture review) will lift these into a per-jurisdiction
> config; for now they live in `.env` / script defaults.

---

## 1. Provision the box

- Lightsail, **us-east-1** (same region as S3/ECR — keeps seeding + S3 sync free/fast).
- Blueprint: **Ubuntu 22.04 LTS**.
- Plan: **4 GB RAM / 2 vCPU / 80 GB SSD (~$20/mo)** — matches App Runner's 4 GB
  for ChromaDB serving, leaves headroom for a concurrent pipeline run + the
  11 GB data set + growth.
- Attach a **static IP**.
- Firewall: allow 22 (SSH), 80 + 443 (Caddy/HTTP-01 + serving).

## 2. System dependencies

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg tesseract-ocr poppler-utils git curl
# uv (installs to ~/.local/bin/uv)
curl -LsSf https://astral.sh/uv/install.sh | sh
# AWS CLI v2
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
cd /tmp && unzip -q awscliv2.zip && sudo ./aws/install
# Caddy
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy
sudo timedatectl set-timezone America/New_York

# 4GB swap — cheap backstop so the pipeline's bursty allocations (ffmpeg,
# Whisper uploads, pdf2image/OCR) running alongside the ChromaDB-backed API
# don't OOM-kill the server on this 4GB box.
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Configure AWS creds (IAM user/role with S3 `public-meetings` + CloudFront
invalidation + — for seeding only — ECR pull): `aws configure`.

## 3. Clone the repo + secrets

```bash
sudo mkdir -p /opt/fuzzy-potato && sudo chown ubuntu:ubuntu /opt/fuzzy-potato
git clone <repo-url> /opt/fuzzy-potato
cd /opt/fuzzy-potato
uv sync --extra rag                 # installs RAG deps (chromadb, anthropic, etc.)
```

Create `/opt/fuzzy-potato/.env` (copy values from the Mac's `.env`):

```bash
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
FIRST_CLIP_ID=6669
LFUCG_OUTPUT_DIR=/opt/fuzzy-potato/lfucg_output
S3_BUCKET=s3://public-meetings
CLOUDFRONT_DISTRIBUTION_ID=E8OIXOXDRETLZ
RAG_SERVICE=lfucg-rag
# Shared secret for the graceful POST /admin/reload hook (the cron refreshes
# the API's caches without a full restart). Generate once: `openssl rand -hex 32`.
# Must be present in BOTH the cron's env (this .env) and the service's env
# (the systemd EnvironmentFile points at this same .env). If unset, the cron
# falls back to `systemctl restart`.
RELOAD_TOKEN=...
# JURISDICTION=lfucg   # default; set per-box when onboarding another county
FEEDS_API_TOKEN=...
FEEDS_WEBHOOK_URL=https://feeds.lexingtonky.news/api/ingest/lfucg-meeting-archive
```

## 4. Seed the 11 GB data set (no re-embedding)

The freshest `chroma_db` + `search.db` + per-clip `metadata.json` already live
inside the current ECR image; the full per-clip text/pdf already live in S3.
Pull both — **zero OpenAI re-embedding cost, all in-AWS (fast)**:

```bash
cd /opt/fuzzy-potato && mkdir -p lfucg_output

# 4a. chroma_db + search.db + metadata.json out of the live ECR image
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin 861476138515.dkr.ecr.us-east-1.amazonaws.com
# (install docker if not present: sudo apt-get install -y docker.io && sudo usermod -aG docker ubuntu)
docker pull 861476138515.dkr.ecr.us-east-1.amazonaws.com/lfucg-rag-api:latest
cid=$(docker create 861476138515.dkr.ecr.us-east-1.amazonaws.com/lfucg-rag-api:latest)
docker cp "$cid:/app/lfucg_output/chroma_db" lfucg_output/
docker cp "$cid:/app/lfucg_output/search.db" lfucg_output/search.db
docker rm "$cid"

# 4b. full per-clip artifacts (txt/json/pdf/md/vtt/html) + top-level state from S3
aws s3 sync s3://public-meetings/data/clips/ lfucg_output/clips/
for f in index.json available_clips.json rag_state.json state.json; do
  aws s3 cp "s3://public-meetings/data/$f" "lfucg_output/$f" || true
done
```

> **Alternative (perfect mirror):** `rsync -avz` `lfucg_output/` straight from
> the Mac. Most accurate (captures any local-only state ahead of the last
> deploy) but pushes 11 GB over home upload. The ECR+S3 path above is
> consistent as long as the image and the S3 `rag_state.json`/`state.json`
> come from the **same** last deploy — which they do under the current
> `ingest_all.sh` flow. After seeding, the first cron run just catches up on
> anything newer (idempotent).

Docker is only needed for this one-time seed; it can be removed afterward.

## 5. Install the API service + Caddy

```bash
sudo cp deploy/lightsail/lfucg-rag.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now lfucg-rag
# let the cron restart it without a password prompt:
echo 'ubuntu ALL=(root) NOPASSWD: /bin/systemctl restart lfucg-rag' | sudo tee /etc/sudoers.d/lfucg-rag

# DNS first: A record meetings-origin.lexingtonky.news -> <static IP>
sudo cp deploy/lightsail/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy

# verify locally + through Caddy
curl -s localhost:8000/api/health
curl -s https://meetings-origin.lexingtonky.news/api/health
```

## 6. Repoint CloudFront, then verify end-to-end

In the `meetings.lexingtonky.news` CloudFront distribution, change the
**`/api/*` cache behavior's origin** from the App Runner domain
(`mphdvbnhwm.us-east-1.awsapprunner.com`) to a new custom origin
`meetings-origin.lexingtonky.news` (HTTPS-only, origin protocol policy
"HTTPS only"). Then:

```bash
aws cloudfront create-invalidation --distribution-id E8OIXOXDRETLZ --paths '/api/*'
# verify the public surface now hits the box:
curl -s https://meetings.lexingtonky.news/api/health
# smoke the MCP handshake:
curl -s -X POST https://meetings.lexingtonky.news/api/mcp/ \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' -H 'MCP-Protocol-Version: 2025-06-18' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"1.0"}}}'
```

Also confirm the **downstream consumers** still work: paulBot `!ask`
(YouTube/Facebook chat) and the feeds civic-memory widget both hit
`/api/ask`.

## 7. Install cron

```bash
crontab deploy/lightsail/crontab.txt
crontab -l
# dry-run the lean path once by hand and watch the log:
bash deploy/lightsail/ingest_cron.sh
```

## 8. Decommission the old serving path

Only after the box has served live traffic cleanly for a few cycles:

- App Runner: `aws apprunner delete-service --service-arn <lfucg-rag-api ARN>`
- ECR: optionally keep `lfucg-rag-api:latest` as a cold backup of chroma_db +
  search.db, or `aws ecr delete-repository --repository-name lfucg-rag-api --force`.
- Repo: `ingest_all.sh`'s Docker/ECR/App Runner steps (7) and the dead
  `lambda/` dir can be retired in a follow-up cleanup PR.

---

## Operations

- **Logs:** `/var/log/lfucg-ingest.log`, `/var/log/lfucg-backfill.log`;
  `journalctl -u lfucg-rag -f` for the API.
- **Manual refresh:** `bash deploy/lightsail/ingest_cron.sh`.
- **Restart API only:** `sudo systemctl restart lfucg-rag`.
- **Backups:** `clips/` is the only irreplaceable artifact (the source of
  truth — already mirrored to S3). `search.db` rebuilds from `clips/` in ~30s
  (`uv run python main.py --build-search-db`); `chroma_db` re-derives via
  `uv run python main.py --rebuild-rag` (re-embeds — costs OpenAI $, takes a
  while). So a full rebuild-from-S3 is always possible.
- **Concurrency note:** `build_search_db.py` rebuilds `search.db` in place at
  the end of a batch while the API may hold a cached sqlite fd; the cron's
  `systemctl restart` immediately after re-opens it. The only risk window is
  reads during the ~30s rebuild. If this ever bites, switch the builder to
  write a temp file + atomic `os.replace()` (flagged for the architecture
  review).
