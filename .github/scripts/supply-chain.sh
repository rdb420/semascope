#!/usr/bin/env bash
# Supply-chain checks, the same in CI (the supply-chain job of ci.yml) and on a
# workstation (`just audit`, `just licences`, `just supply-chain`):
#
#   .github/scripts/supply-chain.sh [all|audit|licences|versions|images] ...
#
#   audit     pip-audit over uv.lock's runtime set and pnpm audit over the web
#             dependencies; any known vulnerability fails
#   licences  .github/scripts/licence_gate.py with .github/licence-policy.toml
#   versions  the package versions, the compose image-tag defaults (and the git tag,
#             when RELEASE_TAG is set) agree
#   images    every third-party image in compose*.yaml is pinned by digest
#
# Needs `uv sync --locked --all-packages` and `pnpm --dir packages/oss-web install
# --frozen-lockfile` first. Network access is needed for the audits only.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

PIP_AUDIT_VERSION=2.10.1
tmp="${RUNNER_TEMP:-$(mktemp -d)}"
web=packages/oss-web

audit() {
  echo "== pip-audit (uv.lock, runtime dependencies)"
  # Workspace members are not on PyPI, and the spaCy models are wheels from GitHub
  # releases with no advisory database, so both are left out; everything else is pinned.
  uv export --locked --all-packages --no-dev --no-emit-workspace --no-hashes --no-annotate \
    --format requirements-txt --quiet | grep -v ' @ ' > "$tmp/requirements-audit.txt"
  uv run --with "pip-audit==$PIP_AUDIT_VERSION" pip-audit --strict --no-deps --disable-pip \
    --progress-spinner off -r "$tmp/requirements-audit.txt"
  echo "== pnpm audit (web: production dependencies, any severity)"
  pnpm --dir "$web" audit --prod
  echo "== pnpm audit (web: build tooling, high and critical)"
  pnpm --dir "$web" audit --audit-level high
}

licences() {
  echo "== licence gate"
  pnpm --dir "$web" licenses list --prod --json > "$tmp/web-licences.json"
  uv run python .github/scripts/licence_gate.py --python --pnpm-json "$tmp/web-licences.json"
}

versions() {
  echo "== versions"
  uv run python .github/scripts/check_version.py ${RELEASE_TAG:+--tag "$RELEASE_TAG"}
}

images() {
  echo "== image digests"
  uv run python deploy/pin_images.py --check
}

all() {
  local status=0
  for check in versions images licences audit; do "$check" || status=1; done
  return $status
}

if [ $# -eq 0 ]; then set -- all; fi
for cmd in "$@"; do
  case "$cmd" in
    all|audit|licences|versions|images) "$cmd" ;;
    *) echo "unknown check $cmd (all|audit|licences|versions|images)" >&2; exit 2 ;;
  esac
done
