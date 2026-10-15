# One Python image, started as server, worker, beat or cli by command.
# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS web
WORKDIR /web
RUN npm install -g pnpm@12 >/dev/null 2>&1
COPY packages/oss-web/package.json packages/oss-web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY packages/oss-web/ ./
# vite.config.ts writes into ../oss-server/...; inside the image that is /web-out
RUN pnpm exec tsc -b && pnpm exec vite build --outDir /web-out --emptyOutDir

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
COPY --from=web /web-out packages/oss-server/src/oss_server/static/web
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --all-packages

# The default embedding model is downloaded at build time so semantic search works offline.
ENV SEMASCOPE_MODEL_CACHE=/app/models FASTEMBED_CACHE_PATH=/app/models
RUN --mount=type=cache,target=/root/.cache/uv \
    uv run --no-sync python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/models')" \
    || echo "embedding model not baked (no network?)"

# The demo corpus is fetched from Project Gutenberg at build time (header, footer
# and trademark stripped by the script) so `oss demo` works offline afterwards.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv run --no-sync packs/demo/build.py || echo "demo corpus not built (no network?)"

RUN useradd --create-home --uid 1000 semascope && mkdir -p /data && chown semascope /data
USER semascope
ENV PATH="/app/.venv/bin:$PATH" SEMASCOPE_DATA_DIR=/data
EXPOSE 8000
CMD ["oss", "--help"]
