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
> `FIRST_CLIP_ID` are all LFUCG-specific. The multi-jurisdiction refactor
> lifted these into `jurisdictions/<slug>.toml` (`[storage] s3_bucket` /
> `cloudfront_dist_id`, `[site] site_url` / `origin_hostname`); for the live
> LFUCG box they also still live in `.env` / script defaults.

---

## 0. Automated provisioning (jurisdictions 2..N)

The manual steps in §1–§7 below are the **canonical runbook** — they document
exactly what provisioned the live LFUCG box and remain the reference. For a
**new** jurisdiction (county #2 onward) those steps are automated by:

```bash
deploy/lightsail/provision-jurisdiction.sh <slug> [--dry-run] [--secrets-file PATH]
```

It reads `jurisdictions/<slug>.toml` for every per-jurisdiction value, derives
the resource names from the slug (instance `<slug>-meetings`, static IP
`<slug>-meetings-ip`, IAM user `<slug>-box`, systemd unit `<slug>-rag`, S3
bucket + CloudFront from `[storage]`, public domain from `[site].site_url`,
origin host from `[site].origin_hostname`), and runs §1–§7 (cloud) + §2/§5
(on-box) idempotently — every AWS resource is **describe-or-create guarded**,
so a re-run repairs a half-finished box instead of duplicating. It then renders
the per-slug `*-rag.service`, `Caddyfile`, sudoers line and crontab from the
`*.template` files in this directory.

What it does **not** do (deliberate):

- **Run the cold-start / ingest pipeline.** A new county has no seed (unlike
  LFUCG, which was seeded from ECR+S3 — §4). The script STOPS after the box is
  serving and prints the exact §2.6 cold-start commands + §2.7 validation
  checklist as next steps. It also **stages** the crontab on the box but does
  **not** install it (the cron must stay off until cold-start finishes).
- **Create the CloudFront distribution.** Creating it programmatically is
  fragile (OAC, two behaviors, SPA error-response rewrite, ACM cert); the
  script PRINTS the exact distribution settings as a manual step, captures the
  resulting dist id (`--cloudfront-dist-id <id>` or an interactive prompt), and
  writes it back into the TOML's `storage.cloudfront_dist_id`.
- **One-time human integration** (spec §2.5): the Cloudflare WAF crawler
  allow-list and the cross-repo wiring (paulBot `!ask`, feeds civic-memory
  widget, master `~/lt/CLAUDE.md`).

Secrets (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `FEEDS_API_TOKEN`) come from
a `--secrets-file` (defaults to the local `./.env`); `RELOAD_TOKEN` is
generated with `openssl rand -hex 32`. Nothing secret is echoed — values are
piped to the box over SSH and the IAM access key is written straight to the
box's `~/.aws/credentials`. Use `--dry-run` first to review every action
without creating anything (it never calls AWS / Cloudflare / SSH).

Onboarding flow: write `jurisdictions/<slug>.toml` (discovery sub-procedure in
`MULTI_COUNTY_EXPANSION_SPEC.md` §2.3) → `provision-jurisdiction.sh <slug>` →
cold-start (§2.6) → validate (§2.7) → flip public DNS → enable the cron.

> **Cold-start + validation are NOT in this file.** The per-jurisdiction
> cold-start commands and the post-provision checklist (health/search/ask/MCP
> smoke tests, the identity-leak check on `llms.txt`/`skill.md`, rollback +
> teardown) live in `MULTI_COUNTY_EXPANSION_SPEC.md` **§2.6** (cold-start vs
> seeded) and **§2.7** (validation checklist + rollback). Follow that checklist
> verbatim after provisioning — `/api/health` now reports the active
> `jurisdiction` slug, and `get_config()` fails loud (RuntimeError) when a
> non-lfucg `JURISDICTION` has a missing/unreadable TOML or a slug mismatch,
> so a misconfigured box can't silently serve LFUCG identity.

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
> come from the **same** last deploy. After seeding, the first cron run just
> catches up on anything newer (idempotent).

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

The cron jobs log to `/var/log/fuzzy-potato/` — a dir **owned by `ubuntu`**.
Do NOT point them at `/var/log/*.log` directly: `/var/log` is `root:syslog`, so
the `ubuntu` cron user cannot create a new file there and the `>>` redirect
fails to open BEFORE `flock` runs — the job then silently never executes.

```bash
# 7a. Create the ubuntu-owned log dir + install log rotation (do this FIRST,
#     before installing the crontab).
sudo mkdir -p /var/log/fuzzy-potato && sudo chown ubuntu:ubuntu /var/log/fuzzy-potato
sudo cp deploy/lightsail/logrotate-fuzzy-potato /etc/logrotate.d/fuzzy-potato
sudo chown root:root /etc/logrotate.d/fuzzy-potato && sudo chmod 0644 /etc/logrotate.d/fuzzy-potato
sudo logrotate --debug /etc/logrotate.d/fuzzy-potato   # dry-run; must NOT print "bad file mode"

# 7b. Install the crontab (as ubuntu).
crontab deploy/lightsail/crontab.txt
crontab -l
# dry-run the lean path once by hand and watch the log:
bash deploy/lightsail/ingest_cron.sh
```

The crontab runs five jobs. The four pipeline jobs use a **tri-state dead-man
heartbeat** (CloudWatch `LT/Heartbeat`): each captures `flock`'s exit code and
pings the `<slug>-<job>` **success** metric only on exit 0, pings a distinct
`<slug>-<job>-fail` metric on a **real failure** (main.py now exits non-zero when
the search.db build / RAG ingest / index generation fail — so a broken run can't
masquerade as healthy), and pings **nothing** on a `flock -n -E 99` lock-contended
skip. The jobs: lean ingest (6h **every day** — was weekdays-only, which delayed
weekend clips up to ~54h), daily v2 summaries (00:00), weekly backfill (Sun
03:00), and a **weekly off-box index backup** (`backup_indexes.sh`, Sun 04:30 →
`s3://lt-backups-861476138515/fuzzy-potato/<slug>/`, self-heartbeats
`<slug>-index-backup`). The fifth job is a **weekly telemetry prune** (Sun 05:00
— deletes `rag_events` older than 90d from `telemetry.db`). `ingest_cron.sh` also
sweeps stranded video intermediates and aborts before doing work if the
`lfucg_output` filesystem is >85% full. The box's aws identity needs
`cloudwatch:PutMetricData` (for `LT/Heartbeat`) and `s3:PutObject` on the
backups bucket; both scripts fail loud into their log otherwise. Consider alarms
on `lt-heartbeat-<slug>-<job>-fail` (>0) for immediate failure paging.

For a **document-driven (CivicClerk) box like Paris**, install ingest + daily
summaries + weekly index backup + weekly telemetry prune and OMIT the weekly
backfill line (render the template, drop the `backfill_weekly.sh` line).

## 8. Decommission the old serving path

Only after the box has served live traffic cleanly for a few cycles:

- App Runner: `aws apprunner delete-service --service-arn <lfucg-rag-api ARN>`
- ECR: optionally keep `lfucg-rag-api:latest` as a cold backup of chroma_db +
  search.db, or `aws ecr delete-repository --repository-name lfucg-rag-api --force`.
- Repo: **done** — the App-Runner-era dead code (`ingest_all.sh`, `Dockerfile`,
  `entrypoint.sh`, `.github/workflows/ingest.yml`, the `lambda/` dir, and
  `tests/test_docker.py`) was deleted in the ops-hygiene cleanup.

---

## Operations

- **Code deploy:** `deploy/lightsail/deploy-code.sh <ssh-host> <unit>` (from a
  workstation) — refuses a dirty tree on the box, `git reset --hard origin/main`
  + `uv sync --frozen` + `systemctl restart`, then health-checks. NEVER touches
  `lfucg_output/`. e.g. `./deploy-code.sh lfucg-meetings lfucg-rag`. Confirm the
  landed SHA via `curl -s https://meetings.lexingtonky.news/api/health` — it now
  reports `sha` alongside `status`/`jurisdiction`.
- **Query analytics:** `curl -s -H "X-Reload-Token: $RELOAD_TOKEN"
  http://127.0.0.1:8000/admin/analytics` (origin-only, same guard as
  `/admin/reload`) — top queries / empty-result rate / volume by transport +
  endpoint / p50-p95 latency / rate-limited count, from `lfucg_output/telemetry.db`.
- **Logs:** `/var/log/fuzzy-potato/lfucg-ingest.log`,
  `/var/log/fuzzy-potato/lfucg-summaries.log`,
  `/var/log/fuzzy-potato/lfucg-backfill.log`,
  `/var/log/fuzzy-potato/lfucg-backup.log`,
  `/var/log/fuzzy-potato/lfucg-telemetry-prune.log` (rotated by
  `/etc/logrotate.d/fuzzy-potato`); `journalctl -u lfucg-rag -f` for the API.
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
