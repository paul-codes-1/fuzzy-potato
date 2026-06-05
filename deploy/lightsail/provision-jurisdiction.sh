#!/usr/bin/env bash
#
# provision-jurisdiction.sh <slug> — stand up a fresh jurisdiction's Lightsail
# box end-to-end, idempotently. This is the automated form of SETUP.md (the
# manual LFUCG runbook) parameterized for any jurisdiction, per
# COUNTY2_ENGINEERING_SCOPE.md §WS5 / MULTI_COUNTY_EXPANSION_SPEC.md §2.5.
#
# WHAT IT DOES (phase numbers track SETUP.md + spec §2.5):
#   0. Load + validate jurisdictions/<slug>.toml; derive all names from <slug>.
#   1. [once] Lightsail instance  <slug>-meetings   (Ubuntu 22.04, 4GB/2vCPU/80GB)
#   2. [once] Static IP  <slug>-meetings-ip  + attach
#   3. [once] Lightsail firewall: open 22, 80, 443
#   4. [once] S3 bucket (from [storage].s3_bucket) + public-read on data/ + assets/
#   5. [MANUAL] CloudFront distribution (default -> S3, /api/* -> origin host).
#              See the CloudFront decision note below; the script PRINTS the
#              exact settings and writes the dist id back to the TOML.
#   6. [once] Scoped IAM user  <slug>-box  + bucket/dist/ecr policy; key -> .env
#   7. [once] Route 53 DNS: public (CNAME -> CloudFront) + origin (A -> static IP)
#   8. [idem] On-box: apt deps, uv, AWS CLI, Caddy, 4GB swap, TZ, Python 3.11
#   9. [once] Deploy key + git clone to /opt/fuzzy-potato; [idem] uv sync --extra rag
#  10. [idem] Render + install .env  (JURISDICTION, secrets, S3/CF, RELOAD_TOKEN)
#  11. [idem] Render + install systemd unit  <slug>-rag.service  (enable --now)
#  12. [idem] Render + install Caddyfile for [site].origin_hostname (reload caddy)
#  13. [idem] sudoers NOPASSWD: systemctl restart <slug>-rag
#  14. [idem] Install crontab (lean 6h ingest + weekly backfill), flock-guarded
#  15. STOP before cold-start. Print the §2.6 cold-start commands + §2.7 checklist.
#
# IT DOES NOT: run the ingest pipeline / cold-start (that's billable LLM spend
# and the operator's call), enable the cron before cold-start, or touch any
# other jurisdiction's resources (every resource is <slug>-scoped).
#
# ───────────────────────────────────────────────────────────────────────────
# CloudFront DECISION (option b — manual, documented): creating the
# distribution programmatically (`aws cloudfront create-distribution`) is
# fragile to do unattended — it needs an Origin Access Control for the S3
# origin, a custom-origin block for the HTTPS-only Caddy origin, two cache
# behaviors (default -> S3 SPA, /api/* -> origin), a default root object, and
# the SPA custom-error-response 403/404 -> /index.html rewrite. A wrong field
# silently breaks the public surface for a whole city and is painful to detect.
# So this script PRINTS the exact distribution settings as a manual step and
# captures the resulting dist id (via --cloudfront-dist-id or by re-reading the
# TOML after you fill it in), then continues. A skeleton CloudFront config
# template is intentionally NOT auto-applied. See SETUP.md "CloudFront".
# ───────────────────────────────────────────────────────────────────────────
#
# SECRETS: OPENAI/ANTHROPIC/FEEDS keys come from a --secrets-file (an env-style
# file) or, if absent, the operator's local ./.env. RELOAD_TOKEN is generated
# with `openssl rand -hex 32`. The IAM access key is written straight to the
# box's ~/.aws/credentials and the rendered .env. NOTHING SECRET IS EVER
# ECHOED to stdout or the logs — only key NAMES and a redacted confirmation.
#
# USAGE:
#   ./provision-jurisdiction.sh <slug> [--dry-run] [--secrets-file PATH]
#       [--ssh-key PATH] [--zone DOMAIN] [--cloudfront-dist-id ID]
#       [--repo-url URL] [--region REGION] [--bundle BUNDLE] [--blueprint BP]
#
#   --dry-run             Print every action without creating anything. Never
#                         calls AWS / SSH. Use this for review.
#   --secrets-file PATH   env-style file with OPENAI_API_KEY / ANTHROPIC_API_KEY
#                         / FEEDS_API_TOKEN (default: ./.env if present).
#   --ssh-key PATH        SSH private key for the box (default: the Lightsail
#                         default key downloaded for the region).
#   --zone DOMAIN         Route 53 hosted zone (default: derived from site_url's
#                         registrable domain, e.g. lexingtonky.news).
#   --cloudfront-dist-id  Pre-existing / just-created CloudFront dist id to use
#                         (skips the manual prompt; written back to the TOML).
#   --repo-url URL        git remote to clone (default: this repo's origin).
#   --region REGION       AWS region (default: us-east-1 — same as S3/ECR).
#
# Re-running is SAFE: AWS creation steps are describe-or-create guarded; on-box
# steps are naturally idempotent. A re-run repairs a half-finished box.

set -euo pipefail
export AWS_PAGER=""

# ── Defaults ────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

DRY_RUN=0
SECRETS_FILE=""
SSH_KEY=""
ZONE=""
CF_DIST_ID_ARG=""
REPO_URL=""
REGION="us-east-1"
BUNDLE="medium_3_0"        # 4GB RAM / 2 vCPU / 80GB SSD (~$24/mo) — SETUP.md §1; matches the live LFUCG box
BLUEPRINT="ubuntu_22_04"   # Ubuntu 22.04 LTS — SETUP.md §1
BOX_USER="ubuntu"
REMOTE_REPO_DIR="/opt/fuzzy-potato"

# ── Logging helpers ──────────────────────────────────────────────────────────
# Portable ISO-8601 timestamp: `date -Is` is GNU-only (the box is Ubuntu, but
# the operator may dry-run on macOS/BSD where -Is is rejected). This format
# works on both.
now()  { date '+%Y-%m-%dT%H:%M:%S%z'; }
log()  { echo "==> [$(now)] $*"; }
warn() { echo "!!  [$(now)] $*" >&2; }
die()  { echo "XX  [$(now)] $*" >&2; exit 1; }
phase(){ echo; echo "────────────────────────────────────────────────────────"; echo "PHASE $*"; echo "────────────────────────────────────────────────────────"; }

# run a command, or just print it under --dry-run. Use ONLY for commands that
# create/mutate cloud or remote state — never for read-only describe guards
# (those must run even in dry-run so the guard logic is exercised, except they
# would hit AWS, so under dry-run we short-circuit the guards explicitly).
run() {
  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY-RUN would run: $*"
  else
    "$@"
  fi
}

# ── Arg parsing ──────────────────────────────────────────────────────────────
SLUG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)            DRY_RUN=1; shift ;;
    --secrets-file)       SECRETS_FILE="${2:?--secrets-file needs a path}"; shift 2 ;;
    --ssh-key)            SSH_KEY="${2:?--ssh-key needs a path}"; shift 2 ;;
    --zone)               ZONE="${2:?--zone needs a domain}"; shift 2 ;;
    --cloudfront-dist-id) CF_DIST_ID_ARG="${2:?--cloudfront-dist-id needs an id}"; shift 2 ;;
    --repo-url)           REPO_URL="${2:?--repo-url needs a URL}"; shift 2 ;;
    --region)             REGION="${2:?--region needs a value}"; shift 2 ;;
    --bundle)             BUNDLE="${2:?--bundle needs a value}"; shift 2 ;;
    --blueprint)          BLUEPRINT="${2:?--blueprint needs a value}"; shift 2 ;;
    -h|--help)
      sed -n '2,80p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    --*)                  die "Unknown option: $1" ;;
    *)
      if [ -z "$SLUG" ]; then SLUG="$1"; shift
      else die "Unexpected positional arg: $1"; fi ;;
  esac
done

[ -n "$SLUG" ] || die "slug is required.  usage: $0 <slug> [--dry-run] ..."
# DNS-safe slug: lowercase alnum + hyphen, used for instance / unit / collection
# / DNS record names. Reject anything that would break those downstream.
echo "$SLUG" | grep -Eq '^[a-z0-9][a-z0-9-]{0,30}[a-z0-9]$' \
  || die "slug '$SLUG' is not DNS-safe (lowercase alnum + hyphen, 2-32 chars)"

# HARD-GUARD: never provision the live LFUCG box. A real (non-dry-run) run for
# slug=lfucg would clobber production — reset firewall ports on lfucg-meetings,
# overwrite /opt/fuzzy-potato/.env with a fresh RELOAD_TOKEN (breaking the live
# /admin/reload hook), and overwrite the systemd unit + Caddyfile. The live box
# was provisioned MANUALLY (SETUP.md §1–§7); re-run individual SETUP.md steps to
# repair it. --dry-run lfucg is permitted (it touches nothing — useful for
# inspecting what the script would do).
if [ "$SLUG" = "lfucg" ] && [ "$DRY_RUN" != "1" ]; then
  die "Refusing to provision slug 'lfucg' — the live LFUCG box was provisioned manually (see SETUP.md §1–§7); re-run individual SETUP.md steps instead. (--dry-run lfucg is allowed for inspection.)"
fi

TOML="$REPO_ROOT/jurisdictions/$SLUG.toml"
[ -f "$TOML" ] || die "config not found: $TOML  (copy jurisdictions/lfucg.toml and edit it)"

# ── TOML reader (Python 3.11 tomllib — never hand-parse TOML in bash) ─────────
# tget <dotted.key> [default] — prints the value or the default (or "" / fails
# if required and missing). Reads jurisdictions/<slug>.toml directly; this is
# independent of config.py (which the app uses) so it can also read the infra
# fields (storage.s3_bucket, storage.cloudfront_dist_id, site.origin_hostname,
# integrations.feeds_webhook_url) that config.py does not currently expose.
tget() {
  local key="$1" default="${2-__REQUIRED__}"
  TOML_PATH="$TOML" TOML_KEY="$key" TOML_DEFAULT="$default" python3 - <<'PY'
import os, sys, tomllib
path = os.environ["TOML_PATH"]
key = os.environ["TOML_KEY"]
default = os.environ["TOML_DEFAULT"]
with open(path, "rb") as fh:
    data = tomllib.load(fh)
cur = data
for part in key.split("."):
    if isinstance(cur, dict) and part in cur:
        cur = cur[part]
    else:
        if default == "__REQUIRED__":
            sys.stderr.write(f"missing required TOML key: {key}\n")
            sys.exit(3)
        print(default)
        sys.exit(0)
print(cur if not isinstance(cur, (list, dict)) else cur)
PY
}

# strip the scheme from a value like s3://bucket or https://host -> bucket / host
strip_scheme() { echo "$1" | sed -E 's#^[a-z0-9+.-]+://##'; }
# registrable zone from a host: meetings.lexingtonky.news -> lexingtonky.news
zone_of() { echo "$1" | awk -F. '{ if (NF>=2) print $(NF-1)"."$NF; else print $0 }'; }

# ── Phase 0: load config, derive names ───────────────────────────────────────
phase "0 — load jurisdictions/$SLUG.toml + derive resource names"

CFG_SLUG="$(tget jurisdiction.slug)"
[ "$CFG_SLUG" = "$SLUG" ] || die "slug mismatch: arg='$SLUG' but TOML jurisdiction.slug='$CFG_SLUG'"
NAME="$(tget jurisdiction.name)"

SITE_URL="$(tget site.site_url)"
ORIGIN_HOST="$(tget site.origin_hostname)"
S3_BUCKET_URL="$(tget storage.s3_bucket)"          # e.g. s3://public-meetings-<slug>
CHROMA_COLLECTION="$(tget storage.chroma_collection)"
# cloudfront_dist_id may be empty on a first run (created during provisioning).
CF_DIST_ID_TOML="$(tget storage.cloudfront_dist_id '')"
FEEDS_WEBHOOK_URL="$(tget integrations.feeds_webhook_url '')"

BUCKET_NAME="$(strip_scheme "$S3_BUCKET_URL")"     # bare bucket name (no s3://)
PUBLIC_HOST="$(strip_scheme "$SITE_URL")"          # e.g. meetings.lexingtonky.news
[ -n "$ZONE" ] || ZONE="$(zone_of "$PUBLIC_HOST")"

# Derived, slug-keyed resource names (spec §2.5).
INSTANCE_NAME="${SLUG}-meetings"
STATIC_IP_NAME="${SLUG}-meetings-ip"
IAM_USER="${SLUG}-box"
IAM_POLICY_NAME="${SLUG}-box-policy"
RAG_SERVICE="${SLUG}-rag"

# CloudFront dist id precedence: --cloudfront-dist-id > TOML > (prompt later).
CF_DIST_ID="${CF_DIST_ID_ARG:-$CF_DIST_ID_TOML}"

# Repo URL: explicit flag, else this checkout's origin.
if [ -z "$REPO_URL" ]; then
  REPO_URL="$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || true)"
fi

cat <<EOF
Resolved configuration for '$SLUG':
  name              : $NAME
  site_url (public) : $SITE_URL   (host: $PUBLIC_HOST)
  origin_hostname   : $ORIGIN_HOST
  route53 zone      : $ZONE
  s3 bucket         : $S3_BUCKET_URL   (name: $BUCKET_NAME)
  chroma collection : $CHROMA_COLLECTION
  cloudfront dist   : ${CF_DIST_ID:-<none yet — manual step 5>}
  feeds webhook     : ${FEEDS_WEBHOOK_URL:-<none>}
  region            : $REGION
  instance          : $INSTANCE_NAME ($BLUEPRINT, $BUNDLE)
  static ip         : $STATIC_IP_NAME
  iam user          : $IAM_USER  (policy: $IAM_POLICY_NAME)
  systemd unit      : $RAG_SERVICE
  repo url          : ${REPO_URL:-<unknown — pass --repo-url>}
EOF

# Sanity: a misconfigured TOML degrades to LFUCG defaults (config.py). Refuse to
# provision a NEW slug that still points at LFUCG's bucket/host (spec §2.7).
if [ "$SLUG" != "lfucg" ]; then
  [ "$BUCKET_NAME" != "public-meetings" ] || die "TOML storage.s3_bucket still points at LFUCG's bucket (public-meetings) — edit jurisdictions/$SLUG.toml"
  [ "$PUBLIC_HOST" != "meetings.lexingtonky.news" ] || die "TOML site.site_url still points at LFUCG's domain — edit jurisdictions/$SLUG.toml"
  [ "$CHROMA_COLLECTION" != "lfucg_meetings" ] || die "TOML storage.chroma_collection still 'lfucg_meetings' (cross-tenant bleed risk) — edit jurisdictions/$SLUG.toml"
fi

if [ "$DRY_RUN" = "1" ]; then
  log "DRY-RUN: config parsed and validated. No AWS/SSH calls will be made."
fi

# ── Preflight: tooling + env the operator must have ──────────────────────────
phase "preflight — required tooling + operator env"
need() { command -v "$1" >/dev/null 2>&1 || die "missing required tool: $1"; }
# Note: scp is intentionally NOT required — every on-box transfer goes through
# SSH stdin (`ssh ... cat > file`), and `need scp` would fail on hosts where scp
# is only the sftp subsystem.
need aws; need python3; need openssl; need ssh; need curl
if [ "$DRY_RUN" != "1" ]; then
  aws sts get-caller-identity >/dev/null 2>&1 || die "AWS credentials not working (aws sts get-caller-identity failed)"
fi
log "preflight ok${DRY_RUN:+ (dry-run: skipped AWS identity check)}"

# AWS account id (for ARNs); placeholder under dry-run.
if [ "$DRY_RUN" = "1" ]; then
  ACCOUNT_ID="<ACCOUNT_ID>"
else
  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
fi

# ── Secrets sourcing (NEVER echoed) ──────────────────────────────────────────
# Load OPENAI/ANTHROPIC/FEEDS keys from --secrets-file or ./.env into THIS
# process's env only. We confirm presence by NAME, never print the values.
phase "secrets — source OPENAI/ANTHROPIC/FEEDS keys (values never printed)"
if [ -z "$SECRETS_FILE" ] && [ -f "$REPO_ROOT/.env" ]; then
  SECRETS_FILE="$REPO_ROOT/.env"
  log "no --secrets-file given; defaulting to $REPO_ROOT/.env"
fi
if [ -n "$SECRETS_FILE" ]; then
  [ -f "$SECRETS_FILE" ] || die "secrets file not found: $SECRETS_FILE"
  set -a
  # shellcheck disable=SC1090  # path is operator-supplied at runtime
  . "$SECRETS_FILE"
  set +a
fi
# RELOAD_TOKEN: generate fresh per box (do not reuse another box's token).
RELOAD_TOKEN="$(openssl rand -hex 32)"

secret_status() {  # name -> "set" / "MISSING" without revealing the value
  local n="$1"
  if [ -n "${!n:-}" ]; then echo "set"; else echo "MISSING"; fi
}
log "secret presence (names only): OPENAI_API_KEY=$(secret_status OPENAI_API_KEY) ANTHROPIC_API_KEY=$(secret_status ANTHROPIC_API_KEY) FEEDS_API_TOKEN=$(secret_status FEEDS_API_TOKEN) RELOAD_TOKEN=set(generated)"
if [ "$DRY_RUN" != "1" ]; then
  [ "$(secret_status OPENAI_API_KEY)" = "set" ]    || die "OPENAI_API_KEY missing (needed for transcription/embeddings) — supply via --secrets-file"
  [ "$(secret_status ANTHROPIC_API_KEY)" = "set" ] || die "ANTHROPIC_API_KEY missing (needed for summaries) — supply via --secrets-file"
  # FEEDS_API_TOKEN is optional (only if the feeds webhook is wired).
fi

# ═════════════════════════════════════════════════════════════════════════════
#  AWS / CLOUD PROVISIONING (steps 1-7) — all [once], describe-or-create guarded
# ═════════════════════════════════════════════════════════════════════════════

# helper: does an AWS resource exist? Returns 0/1. Under dry-run we cannot ask
# AWS, so we treat everything as ABSENT and print the create command.
exists() {  # exists <describe-cmd...>
  if [ "$DRY_RUN" = "1" ]; then return 1; fi
  "$@" >/dev/null 2>&1
}

# ── Phase 1: Lightsail instance [once] ───────────────────────────────────────
phase "1 — Lightsail instance $INSTANCE_NAME [once]"
# Pick the first AZ in the region for the instance (e.g. us-east-1a).
AZ="${REGION}a"
if exists aws lightsail get-instance --instance-name "$INSTANCE_NAME" --region "$REGION"; then
  log "instance $INSTANCE_NAME already exists — skipping create"
else
  log "creating Lightsail instance $INSTANCE_NAME ($BLUEPRINT, $BUNDLE) in $AZ"
  run aws lightsail create-instances \
    --instance-names "$INSTANCE_NAME" \
    --availability-zone "$AZ" \
    --blueprint-id "$BLUEPRINT" \
    --bundle-id "$BUNDLE" \
    --region "$REGION"
fi

# Always wait for 'running' BEFORE Phase 2's attach-static-ip — not just in the
# freshly-created branch. On a re-run the instance may already exist but be
# stopped/pending (e.g. a half-finished prior run), in which case attach would
# fail. (Skipped under --dry-run.)
log "waiting for instance to reach 'running' (poll get-instance-state)…"
if [ "$DRY_RUN" != "1" ]; then
  for _ in $(seq 1 60); do
    state="$(aws lightsail get-instance-state --instance-name "$INSTANCE_NAME" --region "$REGION" --query 'state.name' --output text 2>/dev/null || echo pending)"
    [ "$state" = "running" ] && break
    sleep 5
  done
fi

# ── Phase 2: static IP + attach [once] ───────────────────────────────────────
phase "2 — static IP $STATIC_IP_NAME + attach [once]"
if exists aws lightsail get-static-ip --static-ip-name "$STATIC_IP_NAME" --region "$REGION"; then
  log "static IP $STATIC_IP_NAME already exists — skipping allocate"
else
  log "allocating static IP $STATIC_IP_NAME"
  run aws lightsail allocate-static-ip --static-ip-name "$STATIC_IP_NAME" --region "$REGION"
fi
log "attaching $STATIC_IP_NAME to $INSTANCE_NAME (idempotent — no-op if already attached)"
run aws lightsail attach-static-ip --static-ip-name "$STATIC_IP_NAME" --instance-name "$INSTANCE_NAME" --region "$REGION"

if [ "$DRY_RUN" = "1" ]; then
  STATIC_IP="<STATIC_IP>"
else
  STATIC_IP="$(aws lightsail get-static-ip --static-ip-name "$STATIC_IP_NAME" --region "$REGION" --query 'staticIp.ipAddress' --output text)"
fi
log "static IP address: $STATIC_IP"

# ── Phase 3: firewall ports 22/80/443 [idem] ─────────────────────────────────
phase "3 — Lightsail firewall: open 22, 80, 443 [idem]"
# put-instance-public-ports is declarative (replaces the whole port set), so
# it's naturally idempotent — re-applying the same set is a no-op.
log "opening tcp 22, 80, 443 on $INSTANCE_NAME"
run aws lightsail put-instance-public-ports \
  --instance-name "$INSTANCE_NAME" \
  --region "$REGION" \
  --port-infos \
    fromPort=22,toPort=22,protocol=TCP \
    fromPort=80,toPort=80,protocol=TCP \
    fromPort=443,toPort=443,protocol=TCP

# ── Phase 4: S3 bucket + public-read on data/ + assets/ [once] ───────────────
phase "4 — S3 bucket $BUCKET_NAME + public-read policy [once]"
if exists aws s3api head-bucket --bucket "$BUCKET_NAME"; then
  log "bucket $BUCKET_NAME already exists — skipping create"
else
  log "creating bucket $BUCKET_NAME in $REGION"
  if [ "$REGION" = "us-east-1" ]; then
    # us-east-1 must NOT pass a LocationConstraint (API quirk).
    run aws s3api create-bucket --bucket "$BUCKET_NAME" --region "$REGION"
  else
    run aws s3api create-bucket --bucket "$BUCKET_NAME" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=$REGION"
  fi
fi
# The SPA + per-clip data are served publicly via CloudFront -> S3, so the
# bucket needs a public-read policy scoped to data/* and assets/* only.
# Disable the account/bucket BlockPublicAccess that would otherwise override
# the bucket policy (mirrors the live LFUCG bucket posture).
log "relaxing BlockPublicAccess so the public-read policy can apply"
run aws s3api put-public-access-block --bucket "$BUCKET_NAME" \
  --public-access-block-configuration "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false"
log "applying public-read bucket policy (data/* + assets/* + top-level SPA files)"
BUCKET_POLICY="$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PublicReadDataAndAssets",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": [
        "arn:aws:s3:::$BUCKET_NAME/data/*",
        "arn:aws:s3:::$BUCKET_NAME/assets/*",
        "arn:aws:s3:::$BUCKET_NAME/index.html",
        "arn:aws:s3:::$BUCKET_NAME/skill.md",
        "arn:aws:s3:::$BUCKET_NAME/llms.txt",
        "arn:aws:s3:::$BUCKET_NAME/favicon.ico"
      ]
    }
  ]
}
EOF
)"
if [ "$DRY_RUN" = "1" ]; then
  echo "DRY-RUN would apply bucket policy:"; echo "$BUCKET_POLICY"
else
  tmp_policy="$(mktemp)"; printf '%s' "$BUCKET_POLICY" > "$tmp_policy"
  aws s3api put-bucket-policy --bucket "$BUCKET_NAME" --policy "file://$tmp_policy"
  rm -f "$tmp_policy"
fi

# ── Phase 5: CloudFront distribution [MANUAL — see decision note] ─────────────
phase "5 — CloudFront distribution [MANUAL]"
if [ -n "$CF_DIST_ID" ]; then
  log "CloudFront dist id already provided/known: $CF_DIST_ID — skipping manual prompt"
else
  cat <<EOF
  ┌──────────────────────────────────────────────────────────────────────┐
  │ MANUAL STEP: create the CloudFront distribution (see decision note at  │
  │ the top of this script — programmatic create is intentionally skipped).│
  └──────────────────────────────────────────────────────────────────────┘
  Create a CloudFront distribution with:
    • Origin A (default behavior, the SPA):
        - Origin domain: $BUCKET_NAME.s3.$REGION.amazonaws.com  (REST endpoint)
        - Origin access:  Origin Access Control (OAC), signing for s3
          (then add the generated OAC statement to the bucket policy)
        - Default root object: index.html
        - SPA rewrite: Custom error responses 403 AND 404 -> /index.html (200)
    • Origin B + behavior for path pattern  /api/*  (the RAG/MCP API):
        - Origin domain: $ORIGIN_HOST   (the Caddy box origin)
        - Protocol: HTTPS only
        - Cache policy: CachingDisabled (or forward all on /api/*; the app
          sets its own cache headers)  — Origin request: forward all headers
          EXCEPT Host, plus query strings + the request body (POST).
        - Allowed methods: GET,HEAD,OPTIONS,PUT,POST,PATCH,DELETE
    • Alternate domain name (CNAME): $PUBLIC_HOST   + an ACM cert (us-east-1)
      covering it (or *.${ZONE}).
  This mirrors the live LFUCG distribution (E8OIXOXDRETLZ).

  Then re-run this script with:  --cloudfront-dist-id <NEW_DIST_ID>
  (or paste the dist id below to continue now and write it back to the TOML).
EOF
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: would prompt for the new CloudFront dist id here."
    CF_DIST_ID="<CLOUDFRONT_DIST_ID>"
  else
    read -r -p "  CloudFront distribution id (blank to stop here and re-run later): " CF_DIST_ID
    if [ -z "$CF_DIST_ID" ]; then
      die "no CloudFront dist id — create it per the manual step above, then re-run with --cloudfront-dist-id <id>"
    fi
  fi
fi

# Write the dist id back into the TOML (self-documenting; spec §2.5 step 5).
if [ -n "$CF_DIST_ID" ] && [ "$CF_DIST_ID" != "$CF_DIST_ID_TOML" ]; then
  log "writing cloudfront_dist_id=$CF_DIST_ID back to jurisdictions/$SLUG.toml"
  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY-RUN would set storage.cloudfront_dist_id=$CF_DIST_ID in $TOML"
  else
    TOML_PATH="$TOML" NEW_ID="$CF_DIST_ID" python3 - <<'PY'
import os, re
path = os.environ["TOML_PATH"]; new_id = os.environ["NEW_ID"]
text = open(path).read()
if re.search(r'(?m)^\s*cloudfront_dist_id\s*=', text):
    text = re.sub(r'(?m)^(\s*cloudfront_dist_id\s*=).*$', rf'\1 "{new_id}"', text)
elif re.search(r'(?m)^\[storage\]', text):
    # Match the WHOLE [storage] header line (including any inline comment, e.g.
    # `[storage]  # infra`) so a commented header doesn't fall through and
    # append a duplicate [storage] section.
    text = re.sub(r'(?m)^(\[storage\][^\n]*\n)', rf'\1cloudfront_dist_id = "{new_id}"\n', text, count=1)
else:
    text = text.rstrip("\n") + f'\n\n[storage]\ncloudfront_dist_id = "{new_id}"\n'
open(path, "w").write(text)
print("ok")
PY
  fi
fi
CF_DIST_ARN="arn:aws:cloudfront::${ACCOUNT_ID}:distribution/${CF_DIST_ID}"

# ── Phase 6: scoped IAM user + policy; access key -> box .env / credentials ───
phase "6 — scoped IAM user $IAM_USER + policy [once]"
# ECR repo ARN for the seed image (read-only). LFUCG's seed lives in
# lfucg-rag-api; a new county has NO seed, so ECR read is harmless/unused but
# kept in the policy to mirror the live fuzzy-potato-box shape and to allow a
# future shared base image. Repo name is generic here.
ECR_REPO_ARN="arn:aws:ecr:${REGION}:${ACCOUNT_ID}:repository/${SLUG}-rag-api"

if exists aws iam get-user --user-name "$IAM_USER"; then
  log "IAM user $IAM_USER already exists — skipping create"
else
  log "creating IAM user $IAM_USER"
  run aws iam create-user --user-name "$IAM_USER"
fi

# Render the scoped policy document from the template.
POLICY_TEMPLATE="$SCRIPT_DIR/box-iam-policy.json.template"
[ -f "$POLICY_TEMPLATE" ] || die "missing $POLICY_TEMPLATE"
POLICY_DOC="$(sed \
  -e "s|__BUCKET_NAME__|$BUCKET_NAME|g" \
  -e "s|__CLOUDFRONT_DIST_ARN__|$CF_DIST_ARN|g" \
  -e "s|__ECR_REPO_ARN__|$ECR_REPO_ARN|g" \
  "$POLICY_TEMPLATE")"
log "attaching inline policy $IAM_POLICY_NAME (S3 rw on $BUCKET_NAME, CF invalidation on $CF_DIST_ID, ECR read)"
if [ "$DRY_RUN" = "1" ]; then
  echo "DRY-RUN would put-user-policy $IAM_POLICY_NAME:"; echo "$POLICY_DOC"
else
  tmp_iam="$(mktemp)"; printf '%s' "$POLICY_DOC" > "$tmp_iam"
  aws iam put-user-policy --user-name "$IAM_USER" --policy-name "$IAM_POLICY_NAME" --policy-document "file://$tmp_iam"
  rm -f "$tmp_iam"
fi

# Access key: create only if the user has none (avoid the 2-key limit on
# re-runs). The secret is captured into shell vars and written to the box —
# NEVER echoed.
AWS_ACCESS_KEY_ID_NEW=""
AWS_SECRET_ACCESS_KEY_NEW=""
if [ "$DRY_RUN" = "1" ]; then
  log "DRY-RUN: would create an access key for $IAM_USER (secret captured, never printed)"
  AWS_ACCESS_KEY_ID_NEW="<ACCESS_KEY_ID>"
  AWS_SECRET_ACCESS_KEY_NEW="<SECRET>"
else
  existing_keys="$(aws iam list-access-keys --user-name "$IAM_USER" --query 'length(AccessKeyMetadata)' --output text 2>/dev/null || echo 0)"
  if [ "$existing_keys" != "0" ]; then
    warn "$IAM_USER already has $existing_keys access key(s); NOT creating a new one (the secret of an existing key cannot be re-fetched)."
    warn "If the box's ~/.aws/credentials is missing/stale, rotate manually: aws iam create-access-key --user-name $IAM_USER (delete an old one first)."
  else
    log "creating an access key for $IAM_USER"
    key_json="$(aws iam create-access-key --user-name "$IAM_USER" --output json)"
    AWS_ACCESS_KEY_ID_NEW="$(echo "$key_json" | python3 -c 'import sys,json; print(json.load(sys.stdin)["AccessKey"]["AccessKeyId"])')"
    AWS_SECRET_ACCESS_KEY_NEW="$(echo "$key_json" | python3 -c 'import sys,json; print(json.load(sys.stdin)["AccessKey"]["SecretAccessKey"])')"
    unset key_json
    log "access key created: ${AWS_ACCESS_KEY_ID_NEW:0:4}…(redacted)  (secret NOT printed)"
  fi
fi

# ── Phase 7: Route 53 DNS (public -> CloudFront, origin -> static IP) [once] ──
phase "7 — Route 53 DNS: public (CNAME->CloudFront) + origin (A->static IP) [once]"
# The civicmemory.news zone lives in Route 53 (registered there). Route 53 is
# authoritative-only — no proxy/grey-cloud concept — so both records resolve
# directly: CloudFront fronts the public host, and the origin host points
# straight at the box so Caddy HTTP-01 + CloudFront-to-origin both work.
# UPSERT is natively idempotent (create-or-replace), so re-runs are safe.
upsert_dns() {  # upsert_dns <name> <type> <content>
  local rec_name="$1" rec_type="$2" rec_content="$3" batch
  batch="$(python3 - "$rec_name" "$rec_type" "$rec_content" <<'PY'
import json, sys
name, rtype, content = sys.argv[1:4]
print(json.dumps({"Changes": [{"Action": "UPSERT", "ResourceRecordSet": {
    "Name": name, "Type": rtype, "TTL": 300,
    "ResourceRecords": [{"Value": content}]}}]}))
PY
)"
  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY-RUN would UPSERT Route53 ($ZONE): $rec_type $rec_name -> $rec_content"
    return 0
  fi
  aws route53 change-resource-record-sets --hosted-zone-id "$ZONE_ID" \
    --change-batch "$batch" --query 'ChangeInfo.Status' --output text >/dev/null
  log "upserted $rec_type $rec_name -> $rec_content"
}

if [ "$DRY_RUN" = "1" ]; then
  ZONE_ID="<ZONE_ID>"
  log "DRY-RUN: would look up Route53 hosted zone for $ZONE"
else
  ZONE_ID="$(aws route53 list-hosted-zones-by-name --dns-name "$ZONE" \
    --query "HostedZones[?Name=='${ZONE}.'].Id | [0]" --output text 2>/dev/null | sed 's#/hostedzone/##')"
  [ -n "$ZONE_ID" ] && [ "$ZONE_ID" != "None" ] || die "Route53 hosted zone '$ZONE' not found — register the domain / add the zone first"
fi
# Public record -> CloudFront (CNAME to the dist's *.cloudfront.net domain),
# resolved from the dist id (skipped under dry-run).
if [ "$DRY_RUN" = "1" ]; then
  CF_DOMAIN="<dist>.cloudfront.net"
else
  CF_DOMAIN="$(aws cloudfront get-distribution --id "$CF_DIST_ID" --query 'Distribution.DomainName' --output text 2>/dev/null || echo '')"
  [ -n "$CF_DOMAIN" ] || die "could not resolve CloudFront domain for dist $CF_DIST_ID"
fi
upsert_dns "$PUBLIC_HOST" CNAME "$CF_DOMAIN"
upsert_dns "$ORIGIN_HOST" A "$STATIC_IP"

# ═════════════════════════════════════════════════════════════════════════════
#  ON-BOX PROVISIONING (steps 8-14) — all [idem], run over SSH
# ═════════════════════════════════════════════════════════════════════════════
SSH_TARGET="${BOX_USER}@${STATIC_IP}"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)
[ -n "$SSH_KEY" ] && SSH_OPTS+=(-i "$SSH_KEY")

ssh_run() {  # ssh_run <remote bash script via stdin marker>
  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY-RUN would SSH to $SSH_TARGET and run:"; sed 's/^/    | /'
    return 0
  fi
  ssh "${SSH_OPTS[@]}" "$SSH_TARGET" 'bash -seuo pipefail'
}

# ── Phase 8: on-box system deps (verbatim from SETUP.md §2) [idem] ───────────
phase "8 — on-box system deps: apt, uv, AWS CLI, Caddy, swap, TZ [idem]"
ssh_run <<'REMOTE'
set -euo pipefail
echo "==> apt deps"
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ffmpeg tesseract-ocr poppler-utils git curl unzip python3.11 python3.11-venv

echo "==> uv (idempotent installer)"
if ! command -v "$HOME/.local/bin/uv" >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

echo "==> AWS CLI v2"
if ! command -v aws >/dev/null 2>&1; then
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
  ( cd /tmp && unzip -oq awscliv2.zip && sudo ./aws/install )
fi

echo "==> Caddy"
if ! command -v caddy >/dev/null 2>&1; then
  sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update && sudo apt-get install -y caddy
fi

echo "==> timezone America/New_York"
sudo timedatectl set-timezone America/New_York

echo "==> 4GB swap (idempotent)"
if ! sudo swapon --show | grep -q '/swapfile'; then
  if [ ! -f /swapfile ]; then
    sudo fallocate -l 4G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
  fi
  sudo swapon /swapfile
fi
grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null

echo "==> on-box system deps done"
REMOTE

# ── Phase 9: deploy key + git clone + uv sync [once/idem] ─────────────────────
phase "9 — deploy key + git clone $REPO_URL -> $REMOTE_REPO_DIR; uv sync [idem]"
[ -n "$REPO_URL" ] || die "repo URL unknown — pass --repo-url <git remote>"
# Generate a read-only deploy key ON the box, print the PUBLIC half, and have
# the operator add it as a GitHub deploy key (read-only). We never handle the
# private key off-box. If the clone already exists, just `git pull` + uv sync.
ssh_run <<REMOTE
set -euo pipefail
export PATH="\$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
if [ ! -f ~/.ssh/${SLUG}_deploy_key ]; then
  echo "==> generating read-only deploy key ~/.ssh/${SLUG}_deploy_key"
  ssh-keygen -t ed25519 -N '' -C "${SLUG}-box deploy key" -f ~/.ssh/${SLUG}_deploy_key
  cat >> ~/.ssh/config <<SSHCFG
Host github-${SLUG}
  HostName github.com
  User git
  IdentityFile ~/.ssh/${SLUG}_deploy_key
  IdentitiesOnly yes
SSHCFG
  chmod 600 ~/.ssh/config
fi
echo "──────────────────────────────────────────────────────────────"
echo "ADD THIS as a READ-ONLY deploy key on the repo, then press Enter:"
echo "  GitHub -> repo -> Settings -> Deploy keys -> Add deploy key"
echo "──────────────────────────────────────────────────────────────"
cat ~/.ssh/${SLUG}_deploy_key.pub
REMOTE

if [ "$DRY_RUN" != "1" ]; then
  read -r -p "  Press Enter once the deploy key above is added to the repo… " _
fi

# Rewrite the clone URL to use the per-box Host alias so the deploy key is used.
CLONE_URL="$REPO_URL"
case "$REPO_URL" in
  git@github.com:*) CLONE_URL="git@github-${SLUG}:${REPO_URL#git@github.com:}" ;;
  https://github.com/*) CLONE_URL="git@github-${SLUG}:${REPO_URL#https://github.com/}" ;;
esac
ssh_run <<REMOTE
set -euo pipefail
export PATH="\$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
if [ ! -d ${REMOTE_REPO_DIR}/.git ]; then
  echo "==> cloning ${CLONE_URL} -> ${REMOTE_REPO_DIR}"
  sudo mkdir -p ${REMOTE_REPO_DIR}
  sudo chown ${BOX_USER}:${BOX_USER} ${REMOTE_REPO_DIR}
  git clone ${CLONE_URL} ${REMOTE_REPO_DIR}
else
  echo "==> repo already cloned; git pull"
  git -C ${REMOTE_REPO_DIR} pull --ff-only
fi
cd ${REMOTE_REPO_DIR}
echo "==> uv sync --extra rag"
uv sync --extra rag
mkdir -p lfucg_output
REMOTE

# ── Phase 10: render + install .env [idem] ───────────────────────────────────
phase "10 — render + install $REMOTE_REPO_DIR/.env [idem]"
# Build the .env body locally with the resolved values + secrets, then write it
# to the box over SSH WITHOUT it ever touching disk locally or stdout. Secrets
# flow through the SSH stdin pipe only.
render_env() {
  cat <<EOF
# Rendered by provision-jurisdiction.sh for $SLUG on $(now). DO NOT COMMIT.
JURISDICTION=$SLUG
OPENAI_API_KEY=${OPENAI_API_KEY:-}
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
FIRST_CLIP_ID=$(tget granicus.first_clip_id 1)
LFUCG_OUTPUT_DIR=$REMOTE_REPO_DIR/lfucg_output
S3_BUCKET=$S3_BUCKET_URL
CLOUDFRONT_DISTRIBUTION_ID=$CF_DIST_ID
RAG_SERVICE=$RAG_SERVICE
RELOAD_TOKEN=$RELOAD_TOKEN
FEEDS_API_TOKEN=${FEEDS_API_TOKEN:-}
FEEDS_WEBHOOK_URL=$FEEDS_WEBHOOK_URL
EOF
}
if [ "$DRY_RUN" = "1" ]; then
  echo "DRY-RUN would write $REMOTE_REPO_DIR/.env with keys (values redacted):"
  render_env | sed -E 's/(OPENAI_API_KEY|ANTHROPIC_API_KEY|FEEDS_API_TOKEN|RELOAD_TOKEN)=.*/\1=<redacted>/'
else
  # shellcheck disable=SC2029  # $REMOTE_REPO_DIR is a local, validated config
  # value we WANT expanded client-side so the remote `cat` targets the right path.
  render_env | ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "umask 077 && cat > $REMOTE_REPO_DIR/.env"
  log ".env written to box (0600, secrets piped over SSH — not logged)"
fi

# Write the box's ~/.aws/credentials from the IAM access key (if we minted one
# this run). Skip if we didn't create a key (existing-key case).
phase "10b — box ~/.aws/credentials from IAM key [idem]"
if [ -n "$AWS_SECRET_ACCESS_KEY_NEW" ] && [ "$AWS_SECRET_ACCESS_KEY_NEW" != "<SECRET>" ]; then
  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY-RUN would write ~/.aws/credentials + ~/.aws/config (region=$REGION) on the box (secret redacted)"
  else
    printf '[default]\naws_access_key_id = %s\naws_secret_access_key = %s\n' \
      "$AWS_ACCESS_KEY_ID_NEW" "$AWS_SECRET_ACCESS_KEY_NEW" \
      | ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "umask 077 && mkdir -p ~/.aws && cat > ~/.aws/credentials"
    printf '[default]\nregion = %s\noutput = json\n' "$REGION" \
      | ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "umask 077 && cat > ~/.aws/config"
    log 'box ~/.aws/credentials written (0600, secret piped over SSH — not logged)'
  fi
elif [ "$DRY_RUN" != "1" ]; then
  warn "no new IAM key minted this run; assuming ~/.aws/credentials is already present on the box (re-run case)."
fi

# ── Phase 11: render + install systemd unit [idem] ───────────────────────────
phase "11 — render + install $RAG_SERVICE.service [idem]"
SERVICE_TEMPLATE="$SCRIPT_DIR/rag.service.template"
[ -f "$SERVICE_TEMPLATE" ] || die "missing $SERVICE_TEMPLATE"
SERVICE_RENDERED="$(sed -e "s|__SLUG__|$SLUG|g" "$SERVICE_TEMPLATE")"
if [ "$DRY_RUN" = "1" ]; then
  echo "DRY-RUN would install /etc/systemd/system/$RAG_SERVICE.service:"; echo "$SERVICE_RENDERED"
  echo "DRY-RUN would: systemctl daemon-reload && systemctl enable --now $RAG_SERVICE"
else
  # shellcheck disable=SC2029  # $RAG_SERVICE is a local, validated config value
  # we WANT expanded client-side so the remote command targets the right unit.
  printf '%s\n' "$SERVICE_RENDERED" | ssh "${SSH_OPTS[@]}" "$SSH_TARGET" \
    "sudo tee /etc/systemd/system/$RAG_SERVICE.service >/dev/null && sudo systemctl daemon-reload && sudo systemctl enable --now $RAG_SERVICE"
  log "$RAG_SERVICE installed + enabled"
fi

# ── Phase 12: render + install Caddyfile [idem] ──────────────────────────────
phase "12 — render + install Caddyfile for $ORIGIN_HOST [idem]"
CADDY_TEMPLATE="$SCRIPT_DIR/Caddyfile.template"
[ -f "$CADDY_TEMPLATE" ] || die "missing $CADDY_TEMPLATE"
CADDY_RENDERED="$(sed -e "s|__ORIGIN_HOST__|$ORIGIN_HOST|g" -e "s|__SLUG__|$SLUG|g" "$CADDY_TEMPLATE")"
if [ "$DRY_RUN" = "1" ]; then
  echo "DRY-RUN would install /etc/caddy/Caddyfile:"; echo "$CADDY_RENDERED"
  echo "DRY-RUN would: systemctl reload caddy"
else
  printf '%s\n' "$CADDY_RENDERED" | ssh "${SSH_OPTS[@]}" "$SSH_TARGET" \
    "sudo tee /etc/caddy/Caddyfile >/dev/null && sudo systemctl reload caddy"
  log "Caddyfile installed + caddy reloaded"
fi

# ── Phase 13: sudoers NOPASSWD for the cron's systemctl restart [idem] ───────
phase "13 — sudoers NOPASSWD: systemctl restart $RAG_SERVICE [idem]"
SUDOERS_LINE="$BOX_USER ALL=(root) NOPASSWD: /bin/systemctl restart $RAG_SERVICE"
if [ "$DRY_RUN" = "1" ]; then
  echo "DRY-RUN would install /etc/sudoers.d/$RAG_SERVICE: $SUDOERS_LINE"
else
  # shellcheck disable=SC2029  # $RAG_SERVICE is a local, validated config value
  # we WANT expanded client-side so the remote sudoers file is named correctly.
  printf '%s\n' "$SUDOERS_LINE" | ssh "${SSH_OPTS[@]}" "$SSH_TARGET" \
    "sudo tee /etc/sudoers.d/$RAG_SERVICE >/dev/null && sudo chmod 0440 /etc/sudoers.d/$RAG_SERVICE && sudo visudo -cf /etc/sudoers.d/$RAG_SERVICE"
  log "sudoers line installed + validated (visudo -c)"
fi

# ── Phase 14: render + install crontab (NOT enabled until cold-start) [idem] ──
phase "14 — render crontab (flock-guarded) [idem]"
CRON_TEMPLATE="$SCRIPT_DIR/crontab.txt.template"
[ -f "$CRON_TEMPLATE" ] || die "missing $CRON_TEMPLATE"
CRON_RENDERED="$(sed -e "s|__SLUG__|$SLUG|g" "$CRON_TEMPLATE")"
# Deliberately DO NOT `crontab` it yet — spec §2.6 says keep the cron disabled
# until cold-start finishes. Stage it on the box for the operator to install.
if [ "$DRY_RUN" = "1" ]; then
  echo "DRY-RUN would stage /home/$BOX_USER/${SLUG}-crontab.txt (NOT installed — cold-start gate):"; echo "$CRON_RENDERED"
else
  # shellcheck disable=SC2029  # $BOX_USER/$SLUG are local, validated config values
  # we WANT expanded client-side so the staged crontab path is correct.
  printf '%s\n' "$CRON_RENDERED" | ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "cat > /home/$BOX_USER/${SLUG}-crontab.txt"
  log "crontab staged at /home/$BOX_USER/${SLUG}-crontab.txt (install it AFTER cold-start with: crontab /home/$BOX_USER/${SLUG}-crontab.txt)"
fi

# ═════════════════════════════════════════════════════════════════════════════
#  Phase 15: STOP before cold-start. Print cold-start (§2.6) + checklist (§2.7).
# ═════════════════════════════════════════════════════════════════════════════
phase "15 — provisioning complete. NEXT STEPS (cold-start + validation)"
cat <<EOF

The box is provisioned but EMPTY — a new jurisdiction has no seed. Run the
one-time cold-start to backfill the archive, THEN validate, THEN flip public
DNS / enable the cron. (This script intentionally does NOT spend LLM \$.)

  Box:        $SSH_TARGET   (static IP $STATIC_IP)
  Origin:     https://$ORIGIN_HOST
  Public:     $SITE_URL   (CloudFront $CF_DIST_ID)
  Unit:       $RAG_SERVICE
  Bucket:     $S3_BUCKET_URL
  Collection: $CHROMA_COLLECTION

── COLD-START (spec §2.6) — run on the box, in tmux/nohup ────────────────────
  ssh $SSH_TARGET
  cd $REMOTE_REPO_DIR && export JURISDICTION=$SLUG
  # 1. Probe the full ID range (Granicus) / enumerate the channel (YouTube)
  uv run python probe_clips.py 1 8000
  # 2. Batched scrape+process; resumable via state.json; audio OFF to save disk
  nohup uv run python main.py --scrape --rag --no-audio --max 9999 \\
    > /var/log/$SLUG-coldstart.log 2>&1 &
  tail -f /var/log/$SLUG-coldstart.log
  # 3. One-time sweeps
  uv run python main.py --backfill-tables-of-motions
  uv run python main.py --upgrade-summaries --max 9999
  # 4. Build search.db once, sync to S3, deploy SPA, invalidate CF
  uv run python main.py --build-search-db
  bash deploy/lightsail/sync_data_s3.sh
  CLOUDFRONT_DISTRIBUTION_ID=$CF_DIST_ID ./deploy.sh

── AFTER cold-start: enable the cron (was staged, NOT installed) ─────────────
  crontab /home/$BOX_USER/$SLUG-crontab.txt && crontab -l

── VALIDATION CHECKLIST (spec §2.7) — before AND after flipping public DNS ───
  curl -s localhost:8000/api/health
  curl -s https://$ORIGIN_HOST/api/health
  curl -s -X POST https://$ORIGIN_HOST/api/search -H 'Content-Type: application/json' -d '{"q":"budget","limit":3}' | head
  curl -s -X POST https://$ORIGIN_HOST/api/ask    -H 'Content-Type: application/json' -d '{"question":"What did the council vote on most recently?"}' | head
  curl -s -X POST $SITE_URL/api/mcp/ -H 'Accept: application/json, text/event-stream' -H 'Content-Type: application/json' -H 'MCP-Protocol-Version: 2025-06-18' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"1.0"}}}'
  curl -sI $SITE_URL/ | grep -i '200\\|content-type'
  curl -s $SITE_URL/llms.txt | head -c 80          # must say '$NAME', NOT 'LFUCG'

  Checklist:
   [ ] /api/health 200 local + origin
   [ ] search / ask / facets / related return data
   [ ] MCP initialize returns a session
   [ ] SPA loads; clip.md served as text/markdown
   [ ] llms.txt / skill.md / disclosures show '$NAME' (no LFUCG leak)
   [ ] ChromaDB collection == $CHROMA_COLLECTION (no cross-tenant bleed)
   [ ] cron enabled ONLY after cold-start
   [ ] feeds webhook points at the new ingest path

── DELIBERATELY NOT scripted (one-time human integration; spec §2.5) ─────────
   [ ] (optional) AWS WAF / CloudFront crawler rules — civicmemory.news is Route 53 + CloudFront, no Cloudflare
   [ ] paulBot !ask + feeds civic-memory widget wiring (cross-repo)
   [ ] master ~/lt/CLAUDE.md update

EOF
log "done."
