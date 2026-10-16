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
- Every pull request confirms the rule: the first box of the pull request
  template (`.github/pull_request_template.md`) is that confirmation, and a
  pull request without it ticked is not reviewed. CI runs a similarity check
  of Python, XML, PHP, JavaScript, TypeScript and CSS files against the
  legacy tree, fetched at pinned commits into the runner's scratch space
  (`scripts/cleanroom_check.py`; it fails when it has nothing to compare).

## Licences we accept for dependencies

Apache-2.0, MIT, BSD-2/3, ISC, MPL-2.0 (file-level), PSF, Unlicense, CC0. Ask
in an issue before adding anything else. LGPL is acceptable only as an
optional extra that is not installed by default (for example `pypff`), or
where an ADR accepts a specific unmodified, dynamically linked library
(psycopg, ADR-0011). GPL and AGPL are never accepted.

CI enforces this with `.github/licence-policy.toml` (`just licences`): the
allowed SPDX identifiers, the ADR-backed LGPL exceptions and hand-checked
overrides for packages whose metadata is unclear. A dependency under a
licence the file does not list fails the build; changing the file is a
policy change and says why in the pull request.
The GPU image (`deploy/docker/Dockerfile.gpu`) additionally installs
`fastembed-gpu` and `gliner2` (Apache-2.0), PyTorch (BSD-3) and NVIDIA's
CUDA runtime libraries from pip, which are redistributable under the CUDA
EULA; keep that list in the Dockerfile and in ADR-0008.

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
uv sync --locked --all-packages       # CI fails when uv.lock is out of date
uv run ruff check . && uv run ruff format --check .
uv run mypy packages
uv run pytest                         # unit tests, no services
just web                              # oxlint, tsc, vitest, build, bundle-size budget
just supply-chain                     # versions, image digests, licences, pip-audit, pnpm audit
just lint-workflows                   # actionlint and zizmor, when .github/ changes
docker compose config --quiet         # compose files
uv run mkdocs build --strict          # docs
```

CI (`.github/workflows/ci.yml`) runs all of these on every pull request, and
`integration.yml` adds the real stack and the README quick start in a fresh
clone (`just fresh-clone`). Releases are cut from tags only, after both pass
on the tagged commit; see [Operations](docs/operations.md#continuous-integration-and-releases).

Against a running stack with the demo loaded (`./oss up && ./oss demo`):

```bash
# tagging precision and recall on the demo corpus (docs/quality.md)
docker compose run --rm -v "$PWD/scripts:/scripts:ro" -v "$PWD/packs/demo/golden.yaml:/golden.yaml:ro" \
  cli python /scripts/tag_quality.py --collection demo --golden /golden.yaml

# browser smoke test (needs Chromium once: uv run --with playwright python -m playwright install chromium)
SEMASCOPE_UI_PASSWORD='the admin password from .env' uv run tests/e2e/ui_smoke.py
```

`just integration` does all of this on a throwaway stack (its own compose
project, env file and port), adds the HTTP API tests in `tests/integration/`
and a backup and restore round trip, and tears the stack down; CI runs the
same script. See [Operations](docs/operations.md#integration-tests).
