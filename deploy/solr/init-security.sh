# shellcheck shell=bash
# Semascope: basic authentication for Solr. Sourced by the Solr image's entrypoint
# (/docker-entrypoint-initdb.d) before Solr starts; the work runs in the background
# because security.json lives in ZooKeeper, which only exists once Solr is up.
#
# Once Solr answers, security.json.tmpl is rendered with a salted SHA-256 hash of
# SEMASCOPE_SOLR_PASSWORD (or the file SEMASCOPE_SOLR_PASSWORD_FILE names; Solr's
# BasicAuthPlugin format) and uploaded to ZooKeeper,
# unless authentication is already configured there. Idempotent: it never replaces
# an existing security.json, so it is safe on every start and on existing installs.
(
  set +e
  log() { echo "semascope-security: $*" >&2; }
  password="${SEMASCOPE_SOLR_PASSWORD:-}"
  # Docker secrets: SEMASCOPE_SOLR_PASSWORD_FILE names a file holding the password
  if [ -n "${SEMASCOPE_SOLR_PASSWORD_FILE:-}" ]; then password="$(cat "$SEMASCOPE_SOLR_PASSWORD_FILE")"; fi
  if [ -z "$password" ]; then
    log "SEMASCOPE_SOLR_PASSWORD is empty; authentication not configured"
    exit 0
  fi
  port="${SOLR_PORT:-8983}"
  zk="localhost:$((port + 1000))"
  template=/opt/semascope/security.json.tmpl
  for _ in $(seq 1 150); do
    code="$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$port/solr/admin/info/system")"
    [ "$code" = 200 ] || [ "$code" = 401 ] && break
    sleep 2
  done
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  umask 077
  if solr zk cp zk:/security.json "$work/current.json" -z "$zk" >/dev/null 2>&1 \
    && grep -q '"authentication"' "$work/current.json"; then
    log "authentication already configured"
    exit 0
  fi
  openssl rand 32 > "$work/salt"
  hash="$({ cat "$work/salt"; printf '%s' "$password"; } \
    | openssl dgst -sha256 -binary | openssl dgst -sha256 -binary | base64 -w0)"
  salt="$(base64 -w0 "$work/salt")"
  sed "s|\${SOLR_PASSWORD_HASH}|$hash $salt|" "$template" > "$work/security.json"
  if solr zk cp "$work/security.json" zk:/security.json -z "$zk" >/dev/null 2>&1; then
    log "basic authentication enabled"
  else
    log "could not upload security.json to ZooKeeper at $zk"
  fi
) &
