# Contributing

## Clean-room rule

Semascope is an independent implementation under Apache-2.0. Open Semantic
Search (GPL-3.0) inspired it, and that is where the relationship ends.

- Do not copy, paste, port or translate line by line any Open Semantic Search
  code, Solr configset, template, stylesheet, script, image or documentation
  text. This covers every repository under github.com/opensemanticsearch.
- Do not add that project, or any GPL-licensed project, as a submodule,
  vendored file or build-time download.
- Behaviour may be described in words (what a feature does, which inputs it
  accepts) and reimplemented from that description.
- Every pull request confirms the rule in its template. CI runs a similarity
  check of Python and XML files against the legacy tree.

## Licences we accept for dependencies

Apache-2.0, MIT, BSD-2/3, ISC, MPL-2.0 (file-level), PSF, Unlicense, CC0. Ask
in an issue before adding anything else. LGPL is acceptable only as an
optional extra that is not installed by default (for example `pypff`).
Non-OSI licences (including the Neo4j Visualization Library licence) are not
accepted in the product.

## Workflow

- One monorepo, uv workspace for Python, pnpm for the web package.
- Conventional commits. Semantic versioning.
- Every change to a contract in `packages/oss-core` needs an ADR in
  `docs/adr/`.
- Tests run against real services in containers; a change without tests is
  not merged.
- Australian English in prose and UI strings (`colour`, `licence`,
  `organisation`).

## Checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy packages
uv run pytest                         # unit tests, no services
docker compose config --quiet         # compose files
uv run mkdocs build --strict          # docs
```

Against a running stack with the demo loaded (`./oss up && ./oss demo`):

```bash
# tagging precision and recall on the demo corpus (docs/quality.md)
docker compose run --rm -v "$PWD/scripts:/scripts:ro" -v "$PWD/packs/demo/golden.yaml:/golden.yaml:ro" \
  cli python /scripts/tag_quality.py --collection demo --golden /golden.yaml

# browser smoke test (needs Chromium once: uv run --with playwright python -m playwright install chromium)
SEMASCOPE_UI_PASSWORD='the admin password from .env' uv run tests/e2e/ui_smoke.py
```
