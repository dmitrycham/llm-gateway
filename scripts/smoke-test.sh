#!/usr/bin/env bash
# Smoke test of the local Compose stack: migrations applied, every service healthy,
# health endpoints answer. Run after `docker compose up --build -d`.
#
# Env: SMOKE_TIMEOUT_S (180). The go-api address comes from `docker compose port`,
# so a GO_API_HOST_PORT override in .env is picked up.
set -euo pipefail

cd "$(dirname "$0")/.."

TIMEOUT_S="${SMOKE_TIMEOUT_S:-180}"
SERVICES=(postgres redis gateway ai-service go-api)
deadline=$((SECONDS + TIMEOUT_S))

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

ok() {
  echo "ok    $*"
}

# Prints "<state>|<health>|<exit code>" of a service's container, empty if it has none.
status() {
  docker compose ps --all --format '{{.State}}|{{.Health}}|{{.ExitCode}}' "$1" 2>/dev/null || true
}

expect() { # name, body, expected substring
  [[ "$2" == *"$3"* ]] || fail "$1: response lacks $3: $2"
  ok "$1"
}

# 1. migrate is a one-shot service: wait for it to exit, then check the exit code.
while :; do
  IFS='|' read -r state _ code <<<"$(status migrate)"
  if [[ "$state" == exited ]]; then
    [[ "$code" == 0 ]] || fail "migrate exited with code $code (docker compose logs migrate)"
    ok "migrate exited 0"
    break
  fi
  ((SECONDS < deadline)) || fail "migrate is '${state:-missing}' after ${TIMEOUT_S}s"
  sleep 2
done

# 2. Every long-running service reports healthy. An exited container fails at once.
for svc in "${SERVICES[@]}"; do
  while :; do
    IFS='|' read -r state health _ <<<"$(status "$svc")"
    if [[ "$health" == healthy ]]; then
      ok "$svc healthy"
      break
    fi
    [[ "$state" != exited && "$state" != dead ]] ||
      fail "$svc is $state (docker compose logs $svc)"
    ((SECONDS < deadline)) ||
      fail "$svc is '${state:-missing}/${health:-no health}' after ${TIMEOUT_S}s"
    sleep 2
  done
done

# 3. Health endpoints. go-api is published; gateway and ai-service are internal, so the
#    requests run inside their containers.
go_api_addr="$(docker compose port go-api 8090)" ||
  fail "go-api: could not resolve the published port"
GO_API_URL="http://$go_api_addr"
headers="$(mktemp)"
trap 'rm -f "$headers"' EXIT
body="$(curl -fsS --max-time 5 -D "$headers" "$GO_API_URL/health/live")" ||
  fail "go-api: GET $GO_API_URL/health/live failed"
expect "go-api /health/live" "$body" '"status":"ok"'
grep -qi '^x-request-id: ' "$headers" || fail "go-api: no X-Request-ID header"
ok "go-api X-Request-ID"

body="$(docker compose exec -T gateway \
  wget -q -O - http://127.0.0.1:8081/internal/health/ready)" ||
  fail "gateway: /internal/health/ready failed"
expect "gateway /internal/health/ready" "$body" '"status":"ready"'

body="$(docker compose exec -T ai-service python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/internal/health/ready', timeout=2).read().decode())")" ||
  fail "ai-service: /internal/health/ready failed"
expect "ai-service /internal/health/ready" "$body" '"status":"ready"'
# Redis is optional for readiness (spec §9.3) but part of this stack, so it must be up.
expect "ai-service redis check" "$body" '"degraded":false'

# 4. Schemas exist and no migration is left dirty.
# Single quotes on purpose: $POSTGRES_* expand inside the container, not here.
# shellcheck disable=SC2016
schemas="$(docker compose exec -T postgres \
  sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At' <<'SQL'
SELECT string_agg(nspname, ',' ORDER BY nspname) FROM pg_namespace
WHERE nspname IN ('app', 'ai', 'gateway');
SELECT count(*) FROM (
  SELECT dirty FROM public.schema_migrations_app
  UNION ALL SELECT dirty FROM public.schema_migrations_ai
  UNION ALL SELECT dirty FROM public.schema_migrations_gateway
) v WHERE dirty;
SQL
)" || fail "postgres: schema query failed"
[[ "$schemas" == $'ai,app,gateway\n0' ]] ||
  fail "postgres: want schemas ai,app,gateway and 0 dirty migrations, got: $schemas"
ok "postgres schemas ai, app, gateway; no dirty migrations"

echo "smoke test passed"
