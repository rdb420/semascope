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
