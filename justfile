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

# Real-stack tests on a throwaway stack (sem-ci, :8473); steps: all | up demo test smoke roundtrip logs down
integration *steps="all":
    scripts/integration.sh {{steps}}

# Validate the base pack and the demo pack with the ontology-skill contract
packs:
    uv run oss pack validate packs/base
    uv run oss pack validate packs/demo/pack

# Build the demo corpus from Project Gutenberg (cached under packs/demo/cache)
demo-corpus:
    uv run python packs/demo/build.py

# Start the development stack with exposed ports and hot reload
dev:
    docker compose -f compose.yaml -f compose.dev.yaml up --wait

# Build the documentation site
docs:
    uv run mkdocs build --strict

# Record new image digests after a version bump
bump-images:
    uv run python deploy/pin_images.py
