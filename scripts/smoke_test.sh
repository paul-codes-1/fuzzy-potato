#!/usr/bin/env bash
# smoke_test.sh — Smoke test for CivicLens API endpoints
#
# Usage:
#   ./scripts/smoke_test.sh                          # defaults: localhost:8000, no API key
#   BASE_URL=https://api.example.com ./scripts/smoke_test.sh
#   API_KEY=mra_abc123 ./scripts/smoke_test.sh
#   BASE_URL=http://localhost:8000 API_KEY=mra_abc123 ADMIN_KEY=secret ./scripts/smoke_test.sh
#
# Environment variables:
#   BASE_URL   — API base URL (default: http://localhost:8000)
#   API_KEY    — Tenant API key for authenticated endpoints (default: empty, relies on dev fallback)
#   ADMIN_KEY  — Admin API key for admin endpoints (default: empty)

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
API_KEY="${API_KEY:-}"
ADMIN_KEY="${ADMIN_KEY:-}"

# Colors (disabled if not a terminal)
if [[ -t 1 ]]; then
  GREEN='\033[0;32m'
  RED='\033[0;31m'
  BOLD='\033[1m'
  RESET='\033[0m'
else
  GREEN='' RED='' BOLD='' RESET=''
fi

PASS=0
FAIL=0
RESULTS=()

# test_endpoint NAME METHOD PATH [CURL_EXTRA_ARGS...]
test_endpoint() {
  local name="$1" method="$2" path="$3"
  shift 3
  local url="${BASE_URL}${path}"

  local http_code
  http_code=$(curl -s -o /dev/null -w '%{http_code}' -X "$method" "$@" "$url") || http_code="000"

  if [[ "$http_code" =~ ^2[0-9]{2}$ ]]; then
    RESULTS+=("${GREEN}PASS${RESET}  ${name} (${method} ${path}) — HTTP ${http_code}")
    ((PASS++))
  else
    RESULTS+=("${RED}FAIL${RESET}  ${name} (${method} ${path}) — HTTP ${http_code}")
    ((FAIL++))
  fi
}

# Build common auth header args
auth_args=()
if [[ -n "$API_KEY" ]]; then
  auth_args=(-H "X-API-Key: ${API_KEY}")
fi

admin_args=()
if [[ -n "$ADMIN_KEY" ]]; then
  admin_args=(-H "X-API-Key: ${ADMIN_KEY}")
elif [[ -n "$API_KEY" ]]; then
  admin_args=(-H "X-API-Key: ${API_KEY}")
fi

echo -e "${BOLD}CivicLens API Smoke Test${RESET}"
echo "Target: ${BASE_URL}"
echo "---"

# 1. Public health check
test_endpoint "Public health" GET "/health"

# 2. Authenticated health check
test_endpoint "Auth health" GET "/api/v1/health" "${auth_args[@]+"${auth_args[@]}"}"

# 3. Ask endpoint (single-turn RAG)
test_endpoint "Ask question" POST "/api/v1/ask" \
  "${auth_args[@]+"${auth_args[@]}"}" \
  -H "Content-Type: application/json" \
  -d '{"question":"What is the most recent meeting about?"}'

# 4. Chat endpoint (multi-turn)
test_endpoint "Chat" POST "/api/v1/chat" \
  "${auth_args[@]+"${auth_args[@]}"}" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Hello"}]}'

# 5. Search endpoint
test_endpoint "Search" GET "/api/v1/search?q=test" "${auth_args[@]+"${auth_args[@]}"}"

# 6. Admin tenants list
test_endpoint "Admin tenants" GET "/api/v1/admin/tenants" "${admin_args[@]+"${admin_args[@]}"}"

# 7. OpenAPI docs
test_endpoint "OpenAPI docs" GET "/docs"

# Print summary
echo ""
echo -e "${BOLD}Results${RESET}"
echo "---"
for line in "${RESULTS[@]}"; do
  echo -e "  $line"
done

echo "---"
echo -e "  ${GREEN}Passed: ${PASS}${RESET}  ${RED}Failed: ${FAIL}${RESET}  Total: $((PASS + FAIL))"

if [[ "$FAIL" -gt 0 ]]; then
  echo -e "\n${RED}Some tests failed.${RESET}"
  exit 1
else
  echo -e "\n${GREEN}All tests passed.${RESET}"
  exit 0
fi
