# One Python image, started as server, worker, beat or cli by command.
# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS web
WORKDIR /web
COPY packages/oss-web/package.json packages/oss-web/pnpm-lock.yaml* ./
RUN corepack enable && if [ -f pnpm-lock.yaml ]; then pnpm install --frozen-lockfile; fi
COPY packages/oss-web/ ./
RUN if [ -f pnpm-lock.yaml ]; then pnpm build; else mkdir -p dist; fi

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS python
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependency layer: lockfile and package manifests only.
COPY pyproject.toml uv.lock ./
COPY packages/oss-core/pyproject.toml packages/oss-core/
COPY packages/oss-server/pyproject.toml packages/oss-server/
COPY packages/oss-pipeline/pyproject.toml packages/oss-pipeline/
COPY packages/oss-cli/pyproject.toml packages/oss-cli/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --all-packages --no-install-workspace

# Source layer.
COPY packages/ packages/
COPY packs/ packs/
COPY deploy/solr/ deploy/solr/
COPY --from=web /web/dist packages/oss-server/src/oss_server/static/web
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --all-packages

# Models (spaCy, embeddings) are baked here in Phase 2 and Phase 4 with
# pinned URLs and hashes so the image works offline.

RUN useradd --create-home --uid 1000 semascope && mkdir -p /data && chown semascope /data
USER semascope
ENV PATH="/app/.venv/bin:$PATH" SEMASCOPE_DATA_DIR=/data
EXPOSE 8000
CMD ["oss", "--help"]
