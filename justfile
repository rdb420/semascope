# Developer recipes. The `./oss` shim covers operators; this covers contributors.

default:
    @just --list

# Install every Python package and the web dependencies
sync:
    uv sync --all-packages
    pnpm --dir packages/oss-web install

# Lint, type-check and test everything
check: lint typecheck test

lint:
    uv run ruff check .
    uv run ruff format --check .

typecheck:
    uv run mypy packages

test:
    uv run pytest

# Lint, type-check, test and build the web UI, then check the bundle-size budget
web:
    pnpm --dir packages/oss-web lint
    pnpm --dir packages/oss-web typecheck
    pnpm --dir packages/oss-web test
    pnpm --dir packages/oss-web exec vite build
    pnpm --dir packages/oss-web size

# CI's supply-chain job: versions agree, image digests, licence policy, audits
supply-chain *checks="all":
    .github/scripts/supply-chain.sh {{checks}}

# Known vulnerabilities in the Python and web dependencies (pip-audit, pnpm audit)
audit:
    .github/scripts/supply-chain.sh audit

# Licence policy (.github/licence-policy.toml) over the shipped dependencies
licences:
    .github/scripts/supply-chain.sh licences

# Lint the GitHub workflows as CI does (actionlint image pinned by digest; zizmor)
lint-workflows:
    docker run --rm -v "$PWD:/repo:ro" -w /repo rhysd/actionlint@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667 -no-color
    uvx zizmor@1.31.1 --offline --min-severity medium .github/workflows

# README quick start in a fresh clone of HEAD (outside CI set COMPOSE_PROJECT_NAME, SEMASCOPE_HTTPS_PORT, SEMASCOPE_IMAGE_TAG)
fresh-clone *steps="all":
    .github/scripts/fresh-clone.sh {{steps}}

# Real-stack tests on a throwaway stack (sem-ci, :8473); steps: all | up demo test smoke roundtrip logs down
integration *steps="all":
    scripts/integration.sh {{steps}}

# Validate the base pack and the demo pack with the ontology-skill contract
packs:
    uv run oss pack validate packs/base
    uv run oss pack validate packs/demo/pack

# Build the demo corpus from Project Gutenberg (cached under packs/demo/cache); fails if a
# text no longer matches the hash pinned in packs/demo/sources.yaml
demo-corpus:
    uv run packs/demo/build.py

# Derive the mixed-1k benchmark corpus (about 1,000 chapters in twelve formats) from the
# demo corpus into packs/demo/derived/; fails if it differs from packs/demo/mixed-1k.sha256
mixed-1k: demo-corpus
    uv run packs/demo/derive.py

# Start the development stack with exposed ports and hot reload
dev:
    docker compose -f compose.yaml -f compose.dev.yaml up --wait

# Build the documentation site
docs:
    uv run mkdocs build --strict

# Record new image digests after a version bump
bump-images:
    uv run python deploy/pin_images.py
