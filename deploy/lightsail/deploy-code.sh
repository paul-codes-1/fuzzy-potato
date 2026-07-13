#!/usr/bin/env bash
#
# Code-only deploy to a fuzzy-potato box. Dogfoods git-checkout deploys: on the
# box, refuse a dirty tree, fetch + hard-reset to origin/main, `uv sync
# --frozen`, restart the systemd unit, then health-check. NEVER touches
# lfucg_output/ (chroma_db / search.db / clips) — that's data, not code.
#
# Usage: ./deploy-code.sh <ssh-host> <systemd-unit>
#   ./deploy-code.sh lfucg-meetings lfucg-rag
#   ./deploy-code.sh ubuntu@100.51.123.66 paris-rag   # bare user@ip works too
#
set -euo pipefail

HOST="${1:?usage: deploy-code.sh <ssh-host> <systemd-unit>}"
UNIT="${2:?usage: deploy-code.sh <ssh-host> <systemd-unit>}"
REPO="${FUZZY_POTATO_DIR:-/opt/fuzzy-potato}"

# Extra ssh args (e.g. -i ~/.ssh/lfucg-meetings.pem) via SSH_OPTS.
read -r -a _SSH_OPTS <<< "${SSH_OPTS:-}"

echo "==> Deploying origin/main to $HOST ($UNIT)"
ssh "${_SSH_OPTS[@]}" "$HOST" bash -s "$REPO" "$UNIT" <<'REMOTE'
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
REPO="$1"; UNIT="$2"
cd "$REPO"
git diff --quiet || { echo "dirty tree, aborting"; exit 1; }
git fetch origin
git reset --hard origin/main
uv sync --frozen
sudo systemctl restart "$UNIT"
REMOTE

echo "==> Waiting for service to come up"
sleep 3
echo "==> Health check"
ssh "${_SSH_OPTS[@]}" "$HOST" 'curl -fsS -m 10 http://127.0.0.1:8000/api/health' \
  || { echo "HEALTH CHECK FAILED on $HOST"; exit 1; }
echo
echo "==> Deployed $HOST ($UNIT)"
