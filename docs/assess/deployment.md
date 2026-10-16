# Deployment guide

Status: **draft**, 2026-10-16. Describes 1.0.0-rc.2 (commit `8e4cb2b`).
Day-to-day commands are in [Operations](../operations.md); this page is what
to check before you choose where to run Semascope.

## Supported platforms

| | Supported | Tested |
|---|---|---|
| Host OS | Linux with Docker Engine and the Compose plugin | Ubuntu 24.04 (CI), Ubuntu VM (fresh-clone check) |
| CPU architecture | amd64 and arm64 (multi-arch image) | amd64; arm64 is built, not yet exercised end to end |
| macOS and Windows | through Docker Desktop, unsupported | not tested |
| GPU runtime | amd64 Linux with an NVIDIA card, driver and NVIDIA Container Toolkit | RTX 3090 (24 GB) |
| Host tools | `bash`, Docker Compose; `uv` only for `./oss init` | |

Kubernetes (Helm) and multi-host scaling come after 1.0. One Compose project
runs on one host.

## Services and versions

| Service | Image | Role |
|---|---|---|
| caddy | `caddy:2-alpine` | TLS and reverse proxy; the only published port (`SEMASCOPE_HTTPS_PORT`, default 8443) |
| server, worker, beat | `ghcr.io/rdb420/semascope:<tag>` (about 1.4 GB) | API and UI, pipeline workers, scheduler |
| postgres | `postgres:17-alpine` | collections, documents and extracted text, packs, reviews, proposals, events |
| valkey | `valkey/valkey:8-alpine` | task queue (append-only file on) |
| solr | `solr:10.0.0` | `documents`, `entities` (tagging dictionary) and `chunks` (vectors) |
| neo4j | `neo4j:5.26-community` | the document-entity graph; no plugins needed |
| tika | `apache/tika:3.3.0.0-full` | text extraction and OCR (Tesseract, English) |

The `gpu` runtime replaces the worker image with
`ghcr.io/rdb420/semascope-gpu:<tag>` (about 15 GB, amd64).
`compose.observe.yaml` adds an OpenTelemetry collector, Prometheus and
Grafana.

## Memory and CPU

`oss init --size` sets the Java heaps of Solr, Neo4j and Tika, and the
worker's processes and memory cap:

| Size | Heaps (each) | Worker | Suits | Host |
|---|---|---|---|---|
| small | 1 GB | 1 process, 4 GB cap | a laptop, a first look | about 6 GB RAM, 2 cores (estimate) |
| medium | 2 GB | 2 processes, 6 GB cap | a team server, tens of thousands of documents | 16 GB RAM (measured, below) |
| large | 4 GB | 4 processes, 12 GB cap | hundreds of thousands of documents | 32 GB RAM (estimate) |

Measured for medium on the CPU runtime (2026-10-16, [Quality
numbers](../quality.md#benchmark-run-2026-10-16-rc2-cpu-medium)), indexing the
demo corpus and then the 988 documents of `mixed-1k`: the containers peaked
together at 11.2 GiB (worker 3.8, Neo4j 3.0, Solr 2.8, Tika 1.1, server 0.7,
PostgreSQL 0.1 GiB). The worker then ran two processes under the old 4 GB
cap, and each grew to about 2.2 GB resident, so the cap was reached; that is
why medium now has 6 GB. The small and large host figures are still
estimates from the heaps and worker caps. `SEMASCOPE_WORKER_CONCURRENCY` and
`SEMASCOPE_WORKER_MEMORY` in `.env` override the size's choice. On the `gpu`
runtime the worker is capped at 8 GB and each worker process holds 5 to 11 GB
of GPU memory; two processes peaked at 22 GB on an RTX 3090.

## Storage

| Volume | Grows with | Notes |
|---|---|---|
| `blobs` (`/data`) | the originals (stored once per content hash), page previews, uploads, packs | usually the largest; plan for the corpus size plus previews |
| `postgres` | extracted text and tags per document | roughly the extracted text size plus indexes |
| `solr` | indexed text, the dictionary, and one 384-dimension vector per chunk | |
| `neo4j` | documents, concepts, entities and edges | a projection; `oss reindex` rebuilds it |
| `valkey` | queued tasks | small; safe to lose |
| backups | `data/backups/<stamp>` | each backup is a full copy |

Measured on the CPU runtime (2026-10-16, [Quality
numbers](../quality.md#benchmark-run-2026-10-16-rc2-cpu-medium)) for 1,000
documents of `mixed-1k` (chapters of about 16,000 characters, 35 MB of
originals in twelve formats, a fifth of them PDFs): about 310 MB in all, 8.7
times the originals. The blob store holds most of it (235 MB: the originals
plus 200 MB of page previews of the PDFs and images); Solr 42 MB (documents
15, chunks 27), PostgreSQL 23 MB, Neo4j 7 MB. For whole books (the sixteen
demo texts, 16.7 MB) the stores took 7.7 times the originals, mostly Solr
(the `documents` core at 2.8 times, because the full text is stored and
indexed). Plan for ten times the corpus, plus room for backups, and check
with `scripts/bench_size.py` on a sample of your own documents.

## Authentication and access

- **Local accounts.** `oss init` generates the `admin` password. Passwords
  are at least 12 characters. There is no self-service sign-up: an
  administrator creates local accounts (in the admin or with `oss-manage`).
- **API tokens.** Bearer tokens (`oss_…`), stored hashed, created in the
  admin. `SEMASCOPE_API_TOKEN` in `.env` is the CLI's token.
- **Single sign-on.** OpenID Connect when `SEMASCOPE_OIDC_SERVER_URL`,
  `SEMASCOPE_OIDC_CLIENT_ID` and `SEMASCOPE_OIDC_CLIENT_SECRET` are set.
  A first sign-in creates the account when `SEMASCOPE_OIDC_AUTO_SIGNUP` is
  true (the default; set it to `false` to admit only accounts an
  administrator created, matched by e-mail address). Accounts created that
  way get `SEMASCOPE_OIDC_DEFAULT_ROLE` as a global role; the default `none`
  writes no role, so they get `SEMASCOPE_DEFAULT_ROLE` like everyone else.
- **Roles.** `reader`, `curator`, `editor`, `admin`, global or per
  collection, for reading and writing. With the default
  `SEMASCOPE_DEFAULT_ROLE=reader` every signed-in user can read every
  collection; set it to `none` to keep collections apart, so that each user
  reads only the collections they hold a role on (see
  [Roles](../concepts.md#roles)). Releases up to 1.0.0-rc.2 did not restrict
  reading per collection.

There is no anonymous access to the UI or the search API. The
Reconciliation API (`/api/reconcile`) is public by design, so that
OpenRefine can call it.

## TLS and host name

Caddy serves `https://SEMASCOPE_HOST:SEMASCOPE_HTTPS_PORT/` with a
certificate from its own internal authority (`tls internal`), which
browsers will not trust. For a server:

1. Set `SEMASCOPE_HOST` to the real name and keep `SEMASCOPE_HTTPS_PORT`
   at 443 or 8443. In rc.2 the server trusts form posts only from those
   two ports, so another port breaks sign-in from the browser.
2. Either let Caddy obtain a certificate (replace `tls internal` in
   `deploy/caddy/Caddyfile` with your e-mail address, port 443 reachable
   from the internet), or put your own certificate in the Caddyfile, or
   terminate TLS on your existing proxy and forward to Caddy.
3. Keep every other service unpublished. `compose.dev.yaml` publishes them
   on loopback for development only.

## Backups

`./oss backup [dir] [--with-graph]` writes a PostgreSQL dump, Solr backups
of the three collections and an archive of the data volume to
`data/backups/<stamp>`; `--with-graph` also dumps Neo4j (stopping it
briefly). `./oss restore <dir>` stops the application, restores all of
them and starts it again. Keep `.env` with every backup: it holds the
secrets the data was written with. Copy backups off the host; Semascope does
not.

## Updates

```bash
./oss backup
# edit SEMASCOPE_IMAGE_TAG in .env, or git pull for a source checkout
./oss up       # pulls, migrates, reloads the Solr configsets, re-imports changed bundled packs
./oss doctor
```

A release whose index layout changes says so in the changelog; then run
`./oss reindex --collection <slug>` for each collection. Reindexing replays
enrichment from the stored text without fetching or extracting again.
Image tags are pinned by version; use a release tag, not `main`, in
production.

## Offline and air-gapped operation

Everything runs without a network connection once the images are on the
host:

- the embedding model (bge-small), the spaCy models and, in the `-gpu`
  image, GLiNER2 are baked into the images;
- Tesseract's English data is in the Tika image;
- no telemetry is sent anywhere unless you point
  `OTEL_EXPORTER_OTLP_ENDPOINT` at a collector.

To install without a network: on a connected machine, `docker pull` the
images listed above and `docker save` them; on the target, `docker load`
them, copy the repository (or just `compose*.yaml`, `oss`, `deploy/` and
`.env`), and run `./oss up`. The pull step fails quietly and the local
images are used.

What still needs a network:

- the **map** loads its background tiles from OpenStreetMap by default. Set
  `SEMASCOPE_MAP_TILE_URL` in `.env` to your own tile server (an XYZ raster
  template such as `https://tiles.example.org/{z}/{x}/{y}.png`, with
  `SEMASCOPE_MAP_TILE_ATTRIBUTION`), or leave it empty
  (`SEMASCOPE_MAP_TILE_URL=`) for no base map: the places are then drawn on
  a plain background and the browser makes no tile requests;
- the web, feed and S3 connectors, and SPARQL-endpoint sources;
- the `remote` runtime's services and any OpenAI-compatible endpoint;
- an embedding model other than the bundled one, which FastEmbed downloads
  on first use unless it is already in the model cache.

## Before you go to production

- [ ] A release tag in `SEMASCOPE_IMAGE_TAG`
- [ ] A real host name and a trusted certificate
- [ ] `.env` backed up somewhere safe, outside the host
- [ ] A scheduled `./oss backup` copied off the host, and one restore tried
- [ ] Roles assigned; `SEMASCOPE_DEFAULT_ROLE` chosen deliberately (`none`
      when collections must be kept apart)
- [ ] The `remote` runtime pointed only at services you control, because
      document text is sent to them
