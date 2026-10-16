#!/usr/bin/env bash
# The README quick start, run against a fresh clone of the current commit: no .env, no
# virtualenv, no node_modules, nothing but what git tracks.
#
#   .github/scripts/fresh-clone.sh [all|clone|init|up|demo|search|upload|logs|down] ...
#
# Each step is what a new user types, in a clean clone at $FRESH_CLONE_DIR:
#
#   clone   git clone of this commit (HEAD of the checkout this script lives in)
#   init    ./oss init                      (non-interactive; writes .env)
#   up      ./oss up                        (after `docker compose build`, see below)
#   demo    ./oss demo --only <slug>        then waits for `./oss search` to find it
#   search  ./oss search "<query>" --collection demo
#   upload  ./oss upload <file> --collection mine, then ./oss search finds it
#   logs    docker compose ps and the service logs (for a failed run)
#   down    docker compose down --volumes, and the clone is removed
#
# CI (the fresh-clone job in .github/workflows/integration.yml) runs it with the defaults:
# compose project `semascope`, port 8443, on a runner that has nothing else. On a
# workstation it refuses those defaults so it never touches a real stack; set
# COMPOSE_PROJECT_NAME and SEMASCOPE_HTTPS_PORT (and SEMASCOPE_IMAGE_TAG, so the image it
# builds does not replace a released tag on this machine), for example:
#
#   COMPOSE_PROJECT_NAME=sem-fresh SEMASCOPE_HTTPS_PORT=8497 SEMASCOPE_IMAGE_TAG=sem-fresh \
#     .github/scripts/fresh-clone.sh all
#
# FRESH_CLONE_BUILD=1 (the default) runs `docker compose build server` before `./oss up`,
# so the stack runs this commit's code; with 0, `./oss up` uses the released image the
# compose file names (and builds it only if that tag cannot be pulled).
set -euo pipefail
self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/$(basename "${BASH_SOURCE[0]}")"
src="$(cd "$(dirname "$self")/../.." && pwd -P)"

dest="${FRESH_CLONE_DIR:-${RUNNER_TEMP:-/tmp}/semascope-fresh-clone/semascope}"
demo_slug="${FRESH_CLONE_DEMO:-dhammapada}"
demo_query="${FRESH_CLONE_QUERY:-mind}"
wait_s="${FRESH_CLONE_WAIT:-900}"
marker="quickstartzephyr$(date +%s)"

if [ -z "${CI:-}" ]; then
  case "${COMPOSE_PROJECT_NAME:-semascope}" in
    semascope) echo "set COMPOSE_PROJECT_NAME (not 'semascope') outside CI" >&2; exit 2 ;;
  esac
  case "${SEMASCOPE_HTTPS_PORT:-8443}" in
    8443) echo "set SEMASCOPE_HTTPS_PORT (not 8443) outside CI" >&2; exit 2 ;;
  esac
fi

step() { printf '\n== fresh clone: %s (%s)\n' "$1" "$(date -u +%H:%M:%S)"; }
# Docker creates the bind-mounted ./data/import as root when it is missing (the shim
# does not create it), so plain rm cannot always remove a used clone.
remove_tree() {
  rm -rf "$1" 2>/dev/null || sudo -n rm -rf "$1" 2>/dev/null \
    || { echo "could not remove $1 (files owned by root); remove it by hand" >&2; return 1; }
}
in_clone() { cd "$dest" || { echo "no clone at $dest; run the clone step first" >&2; exit 2; }; }

# Polls `./oss search` until it reports at least one hit.
wait_for_hits() {
  local query="$1" collection="$2" deadline=$((SECONDS + wait_s)) out
  while :; do
    out="$(./oss search "$query" --collection "$collection" --rows 1 --no-facets 2>&1 || true)"
    if printf '%s\n' "$out" | grep -Eq '^[1-9][0-9]* hits in'; then
      printf '%s\n' "$out"
      return 0
    fi
    if [ $SECONDS -ge $deadline ]; then
      printf '%s\n' "$out" >&2
      echo "no hit for '$query' in $collection after ${wait_s}s" >&2
      ./oss stats >&2 || true
      return 1
    fi
    sleep 10
  done
}

clone() {
  step clone
  remove_tree "$dest"
  mkdir -p "$(dirname "$dest")"
  git init --quiet "$dest"
  git -C "$dest" fetch --quiet --depth 1 "file://$src" HEAD
  git -C "$dest" checkout --quiet --detach FETCH_HEAD
  in_clone
  echo "cloned $(git rev-parse --short HEAD) into $dest"
  # nothing a user would not have
  for leftover in .env .venv packages/oss-web/node_modules data; do
    [ ! -e "$leftover" ] || { echo "the clone already has $leftover" >&2; exit 1; }
  done
}

init() {
  step "./oss init"
  in_clone
  ./oss init
  [ -s .env ] || { echo "./oss init wrote no .env" >&2; exit 1; }
}

up() {
  in_clone
  if [ "${FRESH_CLONE_BUILD:-1}" = 1 ]; then
    step "docker compose build (this commit's code)"
    docker compose build server
  fi
  step "./oss up"
  ./oss up
}

demo() {
  step "./oss demo --only $demo_slug"
  in_clone
  ./oss demo --only "$demo_slug"
  step "waiting for the demo text to be searchable"
  wait_for_hits "$demo_query" demo >/dev/null
}

search() {
  step "./oss search \"$demo_query\" --collection demo"
  in_clone
  wait_for_hits "$demo_query" demo
}

upload() {
  step "./oss upload quickstart-note.txt --collection mine"
  in_clone
  printf 'A note uploaded by the quick start check. Marker: %s.\n' "$marker" > quickstart-note.txt
  ./oss upload quickstart-note.txt --collection mine
  step "./oss search finds the upload"
  wait_for_hits "$marker" mine
}

logs() {
  step logs
  [ -d "$dest" ] || return 0
  (cd "$dest" && docker compose ps --all && docker compose logs --no-color --tail 200) || true
}

down() {
  step down
  if [ -d "$dest" ]; then
    (cd "$dest" && docker compose down --volumes --remove-orphans) || true
    cd /
    remove_tree "$dest" || true
  fi
}

all() {
  local status=0
  trap down EXIT
  clone
  init
  up
  demo
  search
  upload || status=$?
  return $status
}

if [ $# -eq 0 ]; then set -- all; fi
for cmd in "$@"; do
  case "$cmd" in
    all|clone|init|up|demo|search|upload|logs|down) "$cmd" ;;
    *) echo "unknown step $cmd (all|clone|init|up|demo|search|upload|logs|down)" >&2; exit 2 ;;
  esac
done
