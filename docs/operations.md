# Operations

## Sizes

`oss init --size small|medium|large` writes the JVM heaps for Solr, Neo4j
and Tika (1, 2 or 4 GB each); `SEMASCOPE_SIZE` records the choice, and
nothing else reads it. Small runs the whole
stack in about 6 GB of RAM on two cores; it is the right choice for a
laptop or a first look. Medium suits a team server with tens of thousands
of documents; large is for hundreds of thousands. The size also sets the
worker's processes and memory cap (`SEMASCOPE_WORKER_CONCURRENCY` and
`SEMASCOPE_WORKER_MEMORY`: small 1 process and 4 GB, medium 2 and 6 GB,
large 4 and 12 GB); a worker process that grows past its share of the cap is
replaced, see [crashes and recovery](#crashes-and-recovery).

## Runtimes

Three heavy stages carry models: `extract` (OCR), `ner` (named entities)
and `embed` (chunk vectors). `oss init --runtime` chooses where they run and
writes one provider per stage into `.env`; each can be overridden
(`--embed`, `--ner`, `--extract`) and changed later by editing `.env` and
restarting the workers.

| runtime | embed | ner | extract | needs |
|---|---|---|---|---|
| `cpu` (default) | `fastembed`, bge-small in the image | `spacy` | `tika` with Tesseract | nothing |
| `gpu` | `fastembed` on ONNX Runtime CUDA | `gliner` (GLiNER2 zero-shot, in process) | `tika` | an NVIDIA card, driver and container toolkit; `./oss up` adds `compose.gpu.yaml`, which runs the worker from the `-gpu` image (about 15 GB, amd64) |
| `remote` | `sidecar` or `openai` service | `remote` entity service | `mineru` parse server (PDF, Word, PowerPoint and Excel in OOXML, raster images; Tika keeps the rest, legacy `.doc`, `.ppt` and `.xls` included) | the service URLs and, usually, a token |

The remote contracts are small and any service can implement them:

- embeddings, `sidecar`: `POST {url}/embed/dense {"texts": [...]}` answering
  `{"vectors": [[...]], "model": "..."}`; `openai`: `POST {url}/embeddings`
  with the OpenAI body. Health: `GET {url}/health` and `GET {url}/models`.
- entities, `remote`: `POST {url}/entities {"texts": [...], "labels": {label:
  description}, "threshold": 0.5}` answering `{"results": [[{"label", "text",
  "start", "end", "confidence"}, ...], ...]}` (GLiNER2's shape, flat or
  nested). Health: `GET {url}/health`.
- extraction, `mineru`: MinerU's v1 jobs API (`/v1/parse/jobs`, polled, with
  the Markdown fetched from `/v1/files`). Health: `GET {url}/v1/health`.
  MinerU takes PDF, OOXML Office and raster images, chosen by media type
  (from the file's first bytes when its name says nothing); every other
  format stays with Tika, with Tika's OCR. `mineru` without
  `SEMASCOPE_EXTRACT_BASE_URL` fails each document with a configuration
  error rather than quietly using Tika.

`--remote-token` (or `SEMASCOPE_REMOTE_API_KEY`) is sent as a bearer token to
every remote service, which is what a GPU router in front of them checks;
`SEMASCOPE_<STAGE>_API_KEY` overrides it per stage. `./oss doctor` lists the
configured services with their health status.

Zero-shot entity providers (`gliner`, `remote`) are asked for the four base
classes and for every class of the active packs, using the class's
definition as the description, so a mythology pack finds deities and a
property pack finds easements without any training (the base pack's own
classes are skipped; a pack can restrict the list with
`semascope.entity_classes` in its `requirements.yaml`). On the `gpu` runtime
each worker process holds GLiNER2-large and the embedding model on the
card, 5 to 11 GB at peak; size `--concurrency` on `oss-worker` to the free
memory, because a full card fails the stage instead of falling back. Found entities of pack
classes land in a `x_<class>_entities_ss` facet; the base four fill the
persons, organisations, places and events facets as spaCy does.

**Changing the embedding model.** Every chunk records the model that
embedded it, and semantic search only reads chunks of the current model,
so after switching the embed provider or model run
`./oss reindex --collection <slug> --stage embed` for each collection; until
it finishes the semantic and hybrid modes see the re-embedded documents
only. The `chunks` index has a vector field per dimension (256, 384, 512,
768, 1024, 1536, 2560, 3072 and 4096; `vector` is the 384 one, `vector_<n>`
the others), so a model of another dimension needs no schema change: after
an upgrade from 1.0.0-rc.2, `./oss up` uploads the new `chunks` configset
and reloads the collection, then the re-embed above fills the new field.
`./oss doctor` reports the model's dimension (from
`SEMASCOPE_EMBED_DIMENSIONS`, the service itself, or a table of common
models), whether the schema has its field, and the chunks still held for
other models. A stage list replays those stages, what they need (`structure` before
`embed`), the stages they feed (`link` after `tag` or `ner`) and the `index`
and `graph` stages that publish the result; tags of the other stages are
kept, and a failed replayed stage publishes nothing. `./oss stats` shows the chunk count per model, which is how you know
when it is done.

**Retries.** A stage that fails because a service is unreachable or busy
(a remote provider, Solr, Neo4j, Tika or MinerU: connection errors,
timeouts, HTTP 408, 425, 429, 500, 502, 503 or 504, except a Tika 500,
which is a parser crash) is retried in place twice, after about 2 and 4
seconds. If it still fails, and every failed stage of the document failed
that way, the document task stores the result, shows the document as
queued with the reason, and runs again after about 60, 120 and 240
seconds; after that the document stays failed and `./oss reindex` retries
it. A fetch that fails the same way is retried on the same schedule.
Permanent failures (a format Tika cannot parse, a file above the size cap,
a missing setting, a MinerU job that failed or timed out, a vector length
without a field) are never retried. A collection can change the counts
under `settings.pipeline`: `stage_retries`, `stage_retry_backoff_s`,
`task_retries`, `task_retry_backoff_s`. Retries go to the same provider;
nothing falls back to the CPU model, because a mixed index is worse than
a late one.

`settings.pipeline` holds tuning only. A collection may set `chunk_words`,
`chunk_overlap`, `ocr`, `detect_language`, `patterns`, `preview_pages`,
`preview_width`, `ner_max_chars`, `ner_min_length`, `ner_window_chars`,
`embed`, `embed_max_chunks`, `pseudonymise`, `ocr_languages`,
`embedded_max_items`, `embedded_max_chars` and the four retry settings
above. `ocr_languages` is a list of Tesseract language codes (`"deu"`,
`"eng+fra"`, or a list), sent to Tika for that collection's OCR; anything
else is refused. The Tika image in `compose.yaml` has `deu`, `eng`, `fra`,
`ita`, `jpn` and `spa`; a language it does not have gives no OCR text at
all, so a collection that needs another one needs a Tika image with that
Tesseract language installed. The default is the Tika configuration's
`eng`. `embedded_max_items` (100) and `embedded_max_chars` (1,000,000) bound
the text of embedded items (attachments, the files of a zip, the images of
a Word file) that is added to a document's own text. Service addresses, credentials, Solr collection names, paths and
secrets come from `.env` only: the API refuses a collection that sets any
other key (422), and the workers ignore other keys in collections saved
before 1.0.0-rc.4 or edited in the admin, with a warning in the worker log.

## Source runs and deletion

A source has one run at a time: a run claims the source (`running`) before
it reads anything, the API answers `409` to a run request while a run is in
progress, and the scheduler skips a running source. A run that fails (an
exception, or `recover` finding its worker gone) leaves the source `failed`
and counts as its scheduled run, so a failing source is tried again at its
next scheduled time, not every minute.

A full run deletes the documents it no longer sees only when it is sure
they are gone ([ADR-0013](adr/0013-deletion-and-retention.md)):

| The run... | Deletes |
|---|---|
| read everything it tried and saw at least half of the source's documents | what it did not see |
| could not read some items (5xx, 429, 401 or 403 answers, timeouts, names that do not resolve, folders it may not list, corrupt archives) | nothing; `last_error` names the first such item |
| stopped at a limit (`max_pages`, a SPARQL `limit`, ten times `max_pages` sitemap URLs) | nothing |
| saw nothing, or less than `min_yield` (0.5) of a source of ten or more documents | nothing; `last_error` says how many it saw |
| could read nothing at all (the site, feed or first sitemap is down) | nothing: the run fails |
| belongs to a feed source | nothing, unless the source sets `delete_missing: true` |

Set a source's `min_yield` to 0 to let a run delete everything it did not
see, and `delete_missing` to `false` to keep everything a source ever had.
A document a run deleted comes back when its source has it again.

Deleting reaches every store. A document deleted by a run leaves Solr
(document and passages), the graph (its edges, its properties, and the
entities only it mentioned; a node stays only to carry curator decisions)
and the disk (its blob and page previews, unless another document has the
same content, and its work file); the Postgres row stays as a tombstone with
its content cleared. Deleting a source or a collection through the API takes
its documents out of search at once, then removes their graph nodes
(curator decisions included), blobs, previews, the source's work area and,
for the collection's upload source or the collection, the uploaded files.

Web, feed, mail, archive, table, RDF and S3 sources write what they download
or unpack to a work area on the data volume (`/data/work/<collection>/<source
id>/`), which every worker shares; the document task removes each file once
the original is in the blob store. Once a day the `gc` task removes blobs
and previews no document uses (left for a day after their last use, so a run
in progress keeps its files), work files older than a week, and expired
leases.

Webhook deliveries and alert checks run every minute and can take longer;
each webhook, and the alert check, is held by one run at a time (a lease in
Postgres that expires on its own if its holder dies), and a webhook's cursor
only moves forward, so no event is sent twice.

## Files on this machine

The `oss` commands run inside the stack, in a one-off `cli` container that
sees the data volume and, read-only at `/import`, the folder
`SEMASCOPE_IMPORT_DIR` (`./data/import` by default). The shim bridges the
commands that name files on the host:

| Command | What happens to the host path |
|---|---|
| `./oss upload <file>... [--collection slug]` | each file is sent into the container (a tar on its standard input) and uploaded through the API |
| `./oss pack import <bundle>`, `./oss pack diff <bundle>` | the bundle folder is copied into the data volume under `/data/packs/uploaded/<time>/`, where the server may read it, and imported from there (a diff's copy is removed afterwards) |
| `./oss pack validate <bundle>`, `./oss eval <judgements>` | the folder or file is sent into the container for the command |
| `./oss export ... --out <file>` | the export is written to that file on the host |
| `./oss index <path>` | the path must be under `SEMASCOPE_IMPORT_DIR`; it is indexed as `/import/...` by the filesystem connector |

A relative path is relative to the folder you run the command from. A path
that does not exist on the host is passed through unchanged, as a path
inside the stack (`/import/...`, `/app/packs/demo/pack`). Nothing is
bind-mounted, so any file name works, and so does a Docker daemon on another
host. `pack validate --manifest` writes into the bundle, so run it on the
host (`uv run oss pack validate --manifest <bundle>`).

Every command that starts a container first creates `SEMASCOPE_IMPORT_DIR`
when it is missing, so the folder belongs to you rather than to root.

## Crashes and recovery

Tasks are delivered at least once: a worker acknowledges a task when it
ends, and every task gives the same result when it runs again. There are two
queues, `cpu` for the document tasks and `io` for everything light
(discovery, deletes, graph writes, the scheduler, recovery, webhooks), and a
document's whole profile runs in one task
([ADR-0010](adr/0010-one-task-per-document.md)).

| What happens | What Semascope does |
|---|---|
| A worker process dies mid-document (out of memory, a crash in a parser or model) | the task goes straight back to the queue and runs again on the next free process |
| The worker container is killed, or the host restarts | `recover` (every two minutes) finds documents `processing` for more than `SEMASCOPE_STALE_AFTER_S` (300 s) that no live worker is running, and queues them again; Valkey also hands the unacknowledged tasks out again after its visibility timeout, and a task for a document already indexed from the same content and settings does nothing |
| The worker is stopped (`./oss down`, an upgrade) | running tasks get 45 s to finish, then go back to the queue at once; Docker waits 90 s before killing the container |
| A document keeps killing its worker | every start is counted on the document and a finished run resets the count; after `SEMASCOPE_MAX_ATTEMPTS` (3) runs that never finished it is marked failed ("interrupted 3 times") and held: a source run does not queue it again until the file changes, and `./oss reindex` tries once more |
| A document task runs past its time limit (`SEMASCOPE_TASK_TIME_LIMIT_S`, 1800 s; 5400 s with MinerU) | its process is killed and the document is handled as above |
| Neo4j is unavailable during the graph stage | the document is indexed and searchable, with its graph projection `pending`; the graph stage alone is retried after about 60, 120, 240 and 480 seconds, then `recover` projects pending documents whenever Neo4j answers. For a minute after a failure, a worker process does not wait for Neo4j again: the next documents skip the graph stage and are `pending` too. A permanent graph error leaves it `failed` until `./oss reindex` |
| A source run is interrupted | `recover` marks the source failed, so the scheduler starts it again |
| Solr or Neo4j is unavailable when a curator decides | the decision is recorded in Postgres and the outbox, with `applied: false`; every five minutes `retry-proposals` applies the decisions in force that the stores missed |

A worker process grows as it indexes (from about 1 GB to 2.2 GB over 1,000
documents on 1.0.0-rc.2), so `oss-worker` replaces a process once it is
above its share of the container's memory cap after a task: the cap less
512 MiB for the main process, divided by the processes, less 768 MiB for one
large document. That is 2,816 MiB for `small`, 2,048 MiB for `medium` and
2,176 MiB for `large`; never less than 1,536 MiB, because a fresh process
holds about 1 GB of models. `SEMASCOPE_WORKER_MAX_MEMORY_MB` sets it
directly.

Valkey's visibility timeout (`SEMASCOPE_VISIBILITY_TIMEOUT_S`) is how long
an unacknowledged task stays with a worker that may be dead. It must be
longer than any task can legitimately take, or a running task is handed to a
second worker: the time limit, plus the longest retry wait (600 s), plus 300
s. That is 2,700 s on the CPU runtime and 6,300 s with MinerU; it is the
backstop, and `recover` is what brings a killed worker's documents back
within minutes.

`./oss stats` and `./oss doctor` show the documents whose graph projection
is pending or failed and the documents held after repeated interruptions;
the document API has `graph_status`. All five settings go in `.env`; 0 or
unset keeps the default.

## Upgrade

```bash
git pull                   # or: edit SEMASCOPE_IMAGE_TAG in .env to the new version
./oss backup               # always, before an upgrade
./oss up                   # pulls the new image, runs migrations, reloads the Solr
                           # configsets, re-imports the bundled packs, waits for health
./oss doctor
```

What `./oss up` does on start: adds a generated `VALKEY_PASSWORD` to
`.env` when it has none ([Secrets](#secrets)), then Django migrations, static files, the Solr
configsets uploaded and the collections reloaded (created when missing),
the bundled base pack imported when its content hash changed. A schema
change that needs the index rebuilt is announced in the changelog; run
`./oss reindex --collection <slug>` for each collection, which replays
enrichment and indexing from the stored text without touching the originals.

The graph is a projection: `./oss reindex` rebuilds it after a Neo4j
upgrade or a projection change.

Upgrading to 1.0.0-rc.4: `SEMASCOPE_DEFAULT_ROLE` now defaults to `none`,
so a user reads only the collections they hold a role on; put
`SEMASCOPE_DEFAULT_ROLE=reader` in `.env` to keep open reading. `./oss up`
adds a `VALKEY_PASSWORD`, and the application refuses to start while any
required secret is empty or a placeholder ([Secrets](#secrets)). The
containers are recreated on the new networks (`edge` for Caddy and the
server, an internal `extract` for Tika with the worker) with their new health
checks; no data changes. The server now stops, and compose restarts it,
until the Solr collections can be set up, instead of running without them.

## Backup and restore

`./oss backup [dir] [--with-graph]` and `./oss restore <dir>` are described
in [Concepts](concepts.md#backup-and-restore). Keep `.env` with the backup.

A backup fails (non-zero exit, a message naming the step) when any part
cannot be written or read back: the Postgres dump, a Solr backup that Solr
reports as failed or that wrote nothing, the data archive or the Neo4j
dump; the directory is then incomplete and should not be kept. A restore
checks the backup's files first (gzip and tar integrity, one Solr archive
per collection); then it stops the server, worker and beat, restores each
Solr collection under a staging name and Postgres into a staging database.
If any of that fails, the staging copies are removed, the application
starts again on the existing data, and the command exits non-zero. Only
then does it switch: the old database is renamed and dropped at the end,
each Solr collection is replaced from the verified backup, the data
volume is unpacked and, with a dump, Neo4j is loaded. A failure after the
switch names what is restored and where the staged copy is. A Solr restore
also writes the backup's copy of each configset into ZooKeeper, and a backup
keeps only a configset's top-level files, so after the Solr steps (whether
they succeeded or failed) the restore uploads the configsets from the image
again and reloads the collections before the application starts. The staging
needs free disk for a second copy of the Solr collections and the
database while the restore runs.

## Secrets

`.env` holds every secret. The server, workers and beat refuse to start
when `SEMASCOPE_SECRET_KEY`, `POSTGRES_PASSWORD`, `NEO4J_PASSWORD`,
`SOLR_PASSWORD`, `VALKEY_PASSWORD` or `SEMASCOPE_ADMIN_PASSWORD` is empty
or a placeholder such as `change-me` (the values in `.env.example`); the
error names each one. Only debug mode (`SEMASCOPE_DEBUG=true`, for
development) skips the check. `oss init` generates all of them.

Valkey requires `VALKEY_PASSWORD` (from 1.0.0-rc.4); the workers and the
server reach it through `VALKEY_URL`, which compose builds with the
password. An install whose `.env` predates this gets a generated
`VALKEY_PASSWORD` added by the next `./oss up`; Valkey reads it when it
starts, so no data changes. Starting with plain `docker compose up`
instead, Valkey and the application stop with a message naming the
missing value.

To rotate, run `./oss rotate` with the stack up: it generates a new value
for each secret, changes it where it lives first (`ALTER USER` in
PostgreSQL, `ALTER CURRENT USER` in Neo4j, Solr's `set-user`, the `admin`
account, the API token, and Grafana's admin while the observability profile
runs) and writes it to `.env` right after, one secret at a time, so a
refusal leaves `.env` matching the stores; then compose recreates the
services that read a changed value and `oss doctor` runs. `./oss rotate
POSTGRES_PASSWORD SOLR_PASSWORD` rotates only those. The previous file is
kept as `.env.before-rotate-<time>` (mode 600; delete it once the stack
runs). Rotating `SEMASCOPE_SECRET_KEY` signs everyone out;
`SEMASCOPE_PSEUDONYM_KEY`, which keys the pseudonyms of pseudonymised graph
entities, is never rotated (an `.env` from before rc.4 gets the old secret
key as its pseudonym key, so the pseudonyms stay the same).

`oss init --force` writes new secrets into `.env` and keeps every other
setting (host, size, runtime, roles, single sign-on, allowed hosts, the
pseudonym key and any line `init` does not write); an option given with it,
such as `--size`, changes only its own lines. It does not touch the stores,
so use it only before the first `./oss up` or when the volumes will be
recreated. The API token (`SEMASCOPE_API_TOKEN`) can also be replaced by
creating a token in the admin and putting its value in `.env`.

### Docker secrets

Every setting can be read from a file named by `<NAME>_FILE` instead
(`POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password`), the convention of
Docker and Kubernetes secrets; the file's content, without its trailing line
break, wins over the variable. To keep the secrets out of `.env` altogether:

```bash
./oss init --secrets-dir ./secrets   # one file per secret; .env holds the rest
./oss up                             # adds compose.secrets.yaml (SEMASCOPE_SECRETS_DIR is set)
```

`compose.secrets.yaml` mounts each file as a Docker secret into the
services that need it: the application (server, worker, beat, cli) through
`<NAME>_FILE`, PostgreSQL through `POSTGRES_PASSWORD_FILE`, Valkey and Solr
through `VALKEY_PASSWORD_FILE` and `SEMASCOPE_SOLR_PASSWORD_FILE`, and Neo4j,
whose entrypoint builds `NEO4J_AUTH` from the same file. The folder is
`0700`; the files in it are `0644`, because compose bind-mounts them into
containers that run as other users. `GRAFANA_PASSWORD` stays in `.env`, and
the observability profile (Flower and Prometheus's Solr scrape) needs the
Valkey and Solr passwords there too, so it is for the `.env` mode. `./oss
rotate` refuses this mode: change a secret in its store as above, then
replace its file and run `./oss up`.

## Logs and health

`./oss logs <service>` follows a service. Every service in `compose.yaml`
has a health check, and `./oss up` waits for all of them: the server
answers `/healthz` (with up to ten minutes to start, for migrations on a
first start or an upgrade); the worker's Celery node answers a ping through
Valkey; beat has written its schedule in the last five minutes; Caddy's admin
endpoint answers; PostgreSQL, Valkey, Solr, Neo4j and Tika answer, and
Valkey and Solr refuse anonymous clients. The server does not start until the
Solr collections are set up.

`./oss doctor` checks the server (and its version), PostgreSQL with the
application's credentials (the server version and the latest migration of
each app), Valkey with its password (version, tasks queued on `cpu` and
`io`), Solr (version, SolrCloud mode, the `documents`, `entities` and
`chunks` collections), Tika, Neo4j, the configured remote services, the fit
of the embedding model to the `chunks` schema and, with the API token, that
the base pack is active; it names each failure and exits 1. `./oss up` runs
it last.

`compose.observe.yaml` is the observability profile
(`COMPOSE_FILE=compose.yaml:compose.observe.yaml ./oss up`). The server,
workers and beat send traces and metrics over OTLP to the bundled
collector, which sends the traces to Jaeger (kept in memory until it
restarts) and serves the metrics to Prometheus; Prometheus also scrapes
Solr with its password. Grafana on `127.0.0.1:3000` (user `admin`,
`GRAFANA_PASSWORD`) has both as data sources and a provisioned *Semascope*
dashboard: documents processed by status, stage run times and outcomes,
HTTP requests by status, the Solr index size per collection and the time
of outgoing HTTP calls. Flower on `127.0.0.1:5555` (user `admin`, the same password)
shows the workers, the queues and the tasks; it can also stop workers and
revoke tasks, which is why it listens on loopback only. Point
`OTEL_EXPORTER_OTLP_ENDPOINT` at your own collector to keep traces
elsewhere. The collector and Jaeger images have no shell, so those two have
no health check.

## Where the data lives

| Volume | Holds |
|---|---|
| `blobs` (`/data`) | originals by content hash, previews, uploads, sources' work areas (`work/`), the pack store, the scheduler state |
| `postgres` | collections, sources, documents with extracted text, packs and concepts, reviews, proposals, events |
| `solr` | the `documents`, `entities` and `chunks` collections |
| `neo4j` | the graph projection |
| `valkey` | the task queue; its append-only file keeps queued tasks over a restart. If it is lost, re-run the sources: queued documents are queued again |

## Integration tests

`just integration` (or `scripts/integration.sh`) proves the stack end to end
on a throwaway copy, the same way the `integration` workflow does in CI:

1. `up`: `oss init` into `.env.ci` (small, cpu runtime) when it is missing,
   builds the image from the checkout as `ghcr.io/rdb420/semascope:<project>`
   (`sem-ci` by default; another `COMPOSE_PROJECT_NAME` gets its own image), checks
   the demo texts are in it, then `compose up --wait` and `oss doctor`.
2. `demo`: `oss demo --only …` loads four demo texts (`SEMASCOPE_DEMO_ONLY`).
3. `test`: `tests/integration/` against the HTTP API through Caddy: document
   counts by state, pack tags against `packs/demo/golden.yaml`, flat and
   hierarchical facets, keyword, semantic and hybrid search (the fusion scores),
   suggest, list search, CSV export, graph neighbours, paths and hubs, the Cypher
   console, review and tag decisions through the outbox to `CURATED` edges, and
   `oss reindex` keeping them; web (pages, sitemap, crawl), feed, S3, SPARQL and
   upload sources, OCR, schedules and deletion detection; a site going down
   and coming back, a feed rolling over, one run per source, an archive
   bomb and what deleting a collection removes; webhooks, alerts and single
   sign-on. The test-only services those need are layered over
   `compose.yaml` from `tests/integration/stack/compose.yaml`: a fixture server
   (web site, feeds, webhook receiver, mock OpenID Connect provider), MinIO and
   Oxigraph, each on a port Docker picks on 127.0.0.1.
4. `smoke`: `tests/e2e/ui_smoke.py` in Chromium; screenshots land in
   `reports/integration/screenshots/` and any console error fails it.
5. `roundtrip`: `oss backup --with-graph`, `compose down --volumes`, a fresh
   `up`, `oss restore`, then counts, search results, review decisions, curated
   tags and edges, packs and the outbox cursor must match what was there before.
6. `down`: removes the project's containers and volumes (`logs` saves the
   service logs first; `all` does both unless `SEMASCOPE_IT_KEEP=1`).

The run uses its own compose project (`COMPOSE_PROJECT_NAME`, default `sem-ci`;
`semascope` is refused), env file (`SEMASCOPE_ENV_FILE`, default `.env.ci`)
and port (`SEMASCOPE_HTTPS_PORT`, default 8473), so a stack you already run
is never touched. Steps can be run one at a time, e.g. `just integration up
demo test` and later `just integration down`. It takes about five minutes on
a workstation once the image layers are cached.

To run the README quick start word for word against a fresh clone of the
current commit (`./oss init`, `./oss up`, `./oss demo --only dhammapada`,
`./oss search`, `./oss upload`), as the `fresh-clone` job of the same
workflow does:

```bash
COMPOSE_PROJECT_NAME=sem-fresh SEMASCOPE_HTTPS_PORT=8497 SEMASCOPE_IMAGE_TAG=sem-fresh \
  just fresh-clone
```

Outside CI it refuses the `semascope` project and port 8443, so it never
touches a stack you run; it clones the committed `HEAD`, so commit first.

## Continuous integration and releases

Three workflows in `.github/workflows/`:

| Workflow | Runs on | Jobs |
|---|---|---|
| `ci` | pushes to `main`, pull requests, and inside `release` | `python` (ruff, mypy, pytest, pack validation, clean-room check), `supply chain` (versions agree, image digests, licence policy, `pip-audit` and `pnpm audit`, all failing the run), `web` (oxlint, `tsc -b`, vitest, build, bundle-size budget), `compose` (every overlay; the shim without an env file), `workflows` (actionlint, zizmor), `docs` (`mkdocs build --strict`) |
| `integration` | pushes to `main`, pull requests, weekly, by hand, and inside `release` | `stack` ([integration tests](#integration-tests); arm64 too on the schedule and for a release), `fresh-clone` (the README quick start in a clean clone) |
| `release` | a pushed tag `v*` only | the tag must match the version; then `ci` and `integration` on the tagged commit; then the images, signatures and the docs site |

Every action is pinned by commit SHA with its version in a comment; every
job declares the least `permissions:` it needs (the workflow default is
none); every `uv` command runs with `UV_LOCKED=1`, so a `uv.lock` that no
longer matches the `pyproject.toml` files fails. The same checks run locally:
`just check`, `just web`, `just supply-chain` (or `just audit`, `just licences`),
`just lint-workflows`, `just integration` and `just fresh-clone`.

### Licence policy

`.github/licence-policy.toml` is the allow-list the `supply chain` job
enforces over the runtime Python dependencies (`uv.lock` without the dev
group) and the web bundle's production dependencies. GPL and AGPL always
fail; LGPL fails unless the package has an `[[exception]]` naming the ADR
that accepts it (psycopg, ADR-0011); a licence that is neither allowed nor
refused fails too, so every new licence is a reviewed edit to that file.
Packages whose metadata does not say their licence clearly are listed under
`[override]` with the date they were checked. The GPU image's extra wheels
(`deploy/docker/Dockerfile.gpu`) are not in `uv.lock` and so are not covered
yet.

### Image tags

`compose.yaml` and `compose.gpu.yaml` default `SEMASCOPE_IMAGE_TAG` to the
version of the checkout (for example `1.0.0-rc.2`), which is the tag the
release of that version publishes. Set `SEMASCOPE_IMAGE_TAG` in `.env` to run
another release. `:main` is a development tag only: `compose.dev.yaml` builds
it from the checkout and never pulls it, and nothing publishes it any more.
Until a version has been released, `./oss up` cannot pull its tag and builds
the image from the checkout instead.

### Cutting a release

1. In one commit on `main`, set the new version in
   `packages/*/pyproject.toml` (PEP 440, `1.0.0rc3`),
   `packages/oss-core/src/oss_core/__init__.py`,
   `packages/oss-web/package.json` (`1.0.0-rc.3`) and the
   `${SEMASCOPE_IMAGE_TAG:-…}` defaults in `compose.yaml` and
   `compose.gpu.yaml`; run `uv lock`; move the *Unreleased* entries in
   `CHANGELOG.md` under the new version. `just supply-chain` checks that the
   versions agree.
2. Wait for `ci` and `integration` to pass on that commit.
3. `git tag -a v1.0.0-rc.3 -m 1.0.0-rc.3 && git push origin v1.0.0-rc.3`.
4. The `release` workflow checks the tag against the version, runs `ci` and
   `integration` again on the tagged commit (integration on amd64 and arm64),
   and only then builds, pushes, attests and signs
   `ghcr.io/rdb420/semascope:1.0.0-rc.3` (amd64 and arm64) and
   `ghcr.io/rdb420/semascope-gpu:1.0.0-rc.3` (amd64), both with a BuildKit
   SBOM and provenance, and deploys the docs site. A final release
   (`v1.0.0`) also gets `1.0` and `latest`; a pre-release does not.

To check a published image:

```bash
cosign verify ghcr.io/rdb420/semascope@sha256:<digest> \
  --certificate-identity-regexp '^https://github.com/rdb420/semascope/\.github/workflows/release\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
gh attestation verify oci://ghcr.io/rdb420/semascope@sha256:<digest> --repo rdb420/semascope
docker buildx imagetools inspect ghcr.io/rdb420/semascope:1.0.0-rc.3 --format '{{ json .SBOM }}'
```

### Repository settings the release needs

These are owner settings; a workflow cannot change them.

1. **Pages source.** Settings → Pages → *Build and deployment* → Source:
   **GitHub Actions**. Without it the `pages` job fails (the earlier
   "Resource not accessible by integration" came from the workflow trying to
   enable Pages itself, which needs an admin token; it no longer tries).
2. **Pages environment allows tags.** Settings → Environments →
   `github-pages` → *Deployment branches and tags* → *Add deployment branch or
   tag rule* → Ref type **Tag**, name pattern `v*` → *Add rule*. The
   environment is created with only the default branch allowed, and the docs
   now deploy from release tags.
3. **Package visibility.** For each of `semascope` and `semascope-gpu`:
   your profile → Packages → the package → *Package settings* → *Danger
   Zone* → *Change visibility* → **Public** (type the name to confirm). Under
   *Manage Actions access* the repository `rdb420/semascope` must have the
   **Write** role (it has it if its workflow created the package). Without
   public packages `./oss up` cannot pull without `docker login`.
4. **Workflow token default.** Settings → Actions → General → *Workflow
   permissions* → **Read repository contents and packages permissions**.
   The workflows ask for more per job where they need it. If the page offers
   *Require actions to be pinned to a full-length commit SHA*, turn it on.
5. **Protect releases** (recommended). Settings → Rules → Rulesets → *New
   ruleset* → *New tag ruleset*: target `v*`, restrict creations, updates and
   deletions, bypass for Repository admin only. And a branch ruleset for
   `main` requiring a pull request and the status checks `python`,
   `supply chain (audit, licences, versions, digests)`, `web`, `compose`,
   `workflows (actionlint, zizmor)`, `docs`, `stack (ubuntu-24.04)` and
   `fresh clone (README quick start)`.

The `:main` tags already on GHCR are no longer updated; delete those package
versions once a release is out so nobody pulls a stale build.
