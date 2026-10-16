#!/usr/bin/env bash
# Integration run against a throwaway Semascope stack: the same steps in CI and
# on a workstation (`just integration`). See docs/operations.md#integration-tests.
#
#   scripts/integration.sh [all|up|demo|test|smoke|roundtrip|logs|down] ...
#
# `all` runs up, demo, test, smoke and roundtrip, then tears the stack down
# (SEMASCOPE_IT_KEEP=1 keeps it). Everything is scoped to its own compose
# project, env file and port, so a running `semascope` stack is never touched:
#
#   COMPOSE_PROJECT_NAME  sem-ci       never `semascope`
#   SEMASCOPE_ENV_FILE    .env.ci      written by `oss init` when missing
#   SEMASCOPE_HTTPS_PORT  8473
#   SEMASCOPE_IMAGE_TAG   ci           built from the checkout (SEMASCOPE_IT_BUILD=0 skips)
#   SEMASCOPE_DEMO_ONLY   odyssey iliad dhammapada song-celestial
#   SEMASCOPE_IT_ARTIFACTS  reports/integration (screenshots, logs)
set -euo pipefail
self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/$(basename "${BASH_SOURCE[0]}")"
cd "$(dirname "$self")/.."

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-sem-ci}"
export SEMASCOPE_ENV_FILE="${SEMASCOPE_ENV_FILE:-.env.ci}"
export COMPOSE_ENV_FILES="$SEMASCOPE_ENV_FILE"
export SEMASCOPE_HTTPS_PORT="${SEMASCOPE_HTTPS_PORT:-8473}"
export SEMASCOPE_IMAGE_TAG="${SEMASCOPE_IMAGE_TAG:-ci}"
export SEMASCOPE_DEMO_ONLY="${SEMASCOPE_DEMO_ONLY:-odyssey iliad dhammapada song-celestial}"
export SEMASCOPE_IT_URL="${SEMASCOPE_IT_URL:-https://localhost:$SEMASCOPE_HTTPS_PORT}"
export COMPOSE_FILE=compose.yaml  # the cpu runtime, whatever the env file says
artifacts="${SEMASCOPE_IT_ARTIFACTS:-reports/integration}"

case "$COMPOSE_PROJECT_NAME" in
  ""|semascope) echo "refusing to run against the compose project '$COMPOSE_PROJECT_NAME'" >&2; exit 2 ;;
esac

step() { printf '\n== %s (%s, project %s)\n' "$1" "$(date -u +%H:%M:%S)" "$COMPOSE_PROJECT_NAME"; }
# Each step runs as its own process so that `set -e` holds inside it even when
# the caller tests its status.
timed() {
  local name="$1" start=$SECONDS rc=0; shift
  step "$name"
  "$BASH" "$self" "_$1" || rc=$?
  printf '== %s %s in %ss\n' "$name" "$([ $rc = 0 ] && echo ok || echo "FAILED ($rc)")" \
    "$((SECONDS - start))"
  return $rc
}
env_value() { grep -E "^$1=" "$SEMASCOPE_ENV_FILE" | cut -d= -f2-; }

up() {
  if [ ! -f "$SEMASCOPE_ENV_FILE" ]; then
    uv run --package oss-cli oss init --env-file "$SEMASCOPE_ENV_FILE" \
      --size small --runtime cpu --https-port "$SEMASCOPE_HTTPS_PORT"
  fi
  if [ "${SEMASCOPE_IT_BUILD:-1}" = 1 ]; then
    docker compose build server
  fi
  # the image must carry the demo corpus (fetched at build time); fail here, not later
  for slug in $SEMASCOPE_DEMO_ONLY; do
    docker compose run --rm --no-deps -T cli test -s "/app/packs/demo/corpus/$slug.txt" \
      || { echo "the image has no demo text $slug; the corpus fetch failed at build time" >&2; exit 1; }
  done
  mkdir -p "$(env_value SEMASCOPE_IMPORT_DIR || echo ./data/import)"
  docker compose up --detach --wait --wait-timeout 600
  ./oss doctor
}

demo() {
  local only=()
  for slug in $SEMASCOPE_DEMO_ONLY; do only+=(--only "$slug"); done
  ./oss demo "${only[@]}"
}

integration_tests() {
  uv run pytest tests/integration -p no:cacheprovider -o addopts= -v --durations=10 \
    --ignore=tests/integration/test_roundtrip.py
}

smoke() {
  uv run --with playwright python -m playwright install chromium >/dev/null
  mkdir -p "$artifacts/screenshots"
  SEMASCOPE_UI_URL="$SEMASCOPE_IT_URL" \
  SEMASCOPE_UI_PASSWORD="$(env_value SEMASCOPE_ADMIN_PASSWORD)" \
  SEMASCOPE_UI_SHOTS="$artifacts/screenshots" \
    uv run tests/e2e/ui_smoke.py
}

roundtrip() {
  SEMASCOPE_IT_ROUNDTRIP=1 uv run pytest tests/integration/test_roundtrip.py \
    -p no:cacheprovider -o addopts= -v -s
}

logs() {
  mkdir -p "$artifacts"
  docker compose ps --all > "$artifacts/ps.txt" 2>&1 || true
  docker compose logs --no-color --timestamps > "$artifacts/compose.log" 2>&1 || true
  echo "logs in $artifacts/"
}

down() {
  docker compose down --volumes --remove-orphans
  # backups made by the round trip hold this stack's data only
  rm -rf "data/backups/it-$COMPOSE_PROJECT_NAME-"*
}

all() {
  local start=$SECONDS status=0
  if [ "${SEMASCOPE_IT_KEEP:-0}" != 1 ]; then trap 'logs; down' EXIT; fi
  timed up up
  timed demo demo
  timed "integration tests" test || status=1
  timed "ui smoke test" smoke || status=1
  timed "backup and restore" roundtrip || status=1
  printf '\n== integration %s in %ss\n' "$([ $status = 0 ] && echo passed || echo FAILED)" \
    "$((SECONDS - start))"
  return $status
}

if [ $# -eq 0 ]; then set -- all; fi
for cmd in "$@"; do
  case "$cmd" in
    all) all ;;
    up|demo|test|smoke|roundtrip|down) timed "$cmd" "$cmd" ;;
    logs) logs ;;
    _up) up ;;
    _demo) demo ;;
    _test) integration_tests ;;
    _smoke) smoke ;;
    _roundtrip) roundtrip ;;
    _down) down ;;
    *) echo "unknown step $cmd (all|up|demo|test|smoke|roundtrip|logs|down)" >&2; exit 2 ;;
  esac
done
