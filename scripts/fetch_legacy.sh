#!/usr/bin/env bash
# Fetch the legacy Open Semantic Search sources (GPL-3.0) at pinned commits into
# a scratch directory, for scripts/cleanroom_check.py only (ADR-0001).
#
#   scripts/fetch_legacy.sh <scratch-dir>
#
# The directory must be outside this repository: the legacy code is compared
# against, never copied, vendored or committed. Every fetch failure is fatal so
# that the clean-room check can never pass by comparing against nothing.
set -euo pipefail

dest="${1:?usage: scripts/fetch_legacy.sh <scratch-dir outside the repository>}"
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
mkdir -p "$dest"
dest="$(cd "$dest" && pwd -P)"
case "$dest/" in
  "$repo"/*) echo "refusing: $dest is inside the repository" >&2; exit 2 ;;
esac

base=https://github.com/opensemanticsearch
# open-semantic-search at its last commit (2025-04-19) and the submodule commits
# that commit pins. The third-party submodules it also pins (cytoscape.js and
# cytoscape.js-panzoom, MIT; tika-python, Apache-2.0) are not Open Semantic
# Search code and are left out.
pins=(
  "open-semantic-search cd1412754c3f11c8db22b65bea797ba0420f54fc"
  "open-semantic-etl f51efea6c18f2862328e68db4e90653fea13cb22"
  "open-semantic-search-apps a9817d165b63db27dbe085971b37a30702621610"
  "open-semantic-entity-search-api 8fceefc5f24980202f683f6f647c14f57a44bb81"
  "open-semantic-visual-graph-explorer 7fcd0635feaae67f80463ac66655c17355ad534f"
  "solr-ontology-tagger 1a06af977f4d8503d1d965aca779d1bd57489a9b"
  "solr-php-ui 4e9ce94f47e48d3882b84f4e8955fe74dae8b0fc"
  "solr-relevance-ranking-analysis b3fb3e72070dc1e30130a476346361498e917c28"
  "solr-synonames 8e1de52519990a5deff086fcfbde7ecbdb47fd61"
  "solr.deb 8b4a5a6862bd92a02f7c5fd33d941bb9b7847c10"
  "spacy-services.deb d20e81a3656d7d6033d5e4edd95713dda54f3fde"
  "tika-server.deb f2f01e81fdad224aecfdf13bb5bebdc7334e9b7b"
)

for pin in "${pins[@]}"; do
  read -r name sha <<<"$pin"
  dir="$dest/$name"
  if [ "$(git -C "$dir" rev-parse HEAD 2>/dev/null || true)" = "$sha" ]; then
    echo "have  $name@${sha:0:12}"
    continue
  fi
  rm -rf "$dir"
  git init -q "$dir"
  for attempt in 1 2 3; do
    if git -C "$dir" fetch -q --depth 1 "$base/$name.git" "$sha"; then break; fi
    [ "$attempt" = 3 ] && { echo "could not fetch $name@$sha" >&2; exit 1; }
    sleep $((attempt * 5))
  done
  git -C "$dir" -c advice.detachedHead=false checkout -q FETCH_HEAD
  echo "fetched $name@${sha:0:12}"
done
