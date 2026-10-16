# Operations

## Sizes

`oss init --size small|medium|large` writes the JVM heaps for Solr, Neo4j
and Tika (1, 2 or 4 GB each) and the data folders. Small runs the whole
stack in about 6 GB of RAM on two cores; it is the right choice for a
laptop or a first look. Medium suits a team server with tens of thousands
of documents; large is for hundreds of thousands. Worker concurrency is
`--concurrency` on `oss-worker` in `compose.yaml` (default 2).

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

## Upgrade

```bash
git pull                   # or: edit SEMASCOPE_IMAGE_TAG in .env to the new version
./oss backup               # always, before an upgrade
./oss up                   # pulls the new image, runs migrations, reloads the Solr
                           # configsets, re-imports the bundled packs, waits for health
./oss doctor
```

What `./oss up` does on start: Django migrations, static files, the Solr
configsets uploaded and the collections reloaded (created when missing),
the bundled base pack imported when its content hash changed. A schema
change that needs the index rebuilt is announced in the changelog; run
`./oss reindex --collection <slug>` for each collection, which replays
enrichment and indexing from the stored text without touching the originals.

The graph is a projection: `./oss reindex` rebuilds it after a Neo4j
upgrade or a projection change.

## Backup and restore

`./oss backup [dir] [--with-graph]` and `./oss restore <dir>` are described
in [Concepts](concepts.md#backup-and-restore). Keep `.env` with the backup.

## Secrets

`.env` holds every secret. To rotate: `oss init --force --import-dir <same>`
writes new ones; PostgreSQL, Neo4j and Solr then need their passwords
changed to match (or recreate their volumes on a fresh install). Solr's
basic authentication is set up from `SOLR_PASSWORD` on first start; the
[Solr notes](https://github.com/rdb420/semascope/blob/main/deploy/solr/README.md)
show how to change it. The API
token (`SEMASCOPE_API_TOKEN`) can also be replaced by creating a token in the
admin and putting its value in `.env`.

## Logs and health

`./oss logs <service>` follows a service. `/healthz` answers on the server;
every container has a compose health check. `./oss doctor` checks the
server, Solr, Tika and Neo4j, the configured remote services and the fit
of the embedding model to the `chunks` schema. With `compose.observe.yaml`,
the server, workers and beat send traces and metrics over OTLP to the
bundled collector: it logs the traces (no trace store is bundled) and
passes the metrics to Prometheus, which Grafana on `127.0.0.1:3000`
reads. Point `OTEL_EXPORTER_OTLP_ENDPOINT` at your own collector to keep
traces.

## Where the data lives

| Volume | Holds |
|---|---|
| `blobs` (`/data`) | originals by content hash, previews, uploads, the pack store, the scheduler state |
| `postgres` | collections, sources, documents with extracted text, packs and concepts, reviews, proposals, events |
| `solr` | the `documents`, `entities` and `chunks` collections |
| `neo4j` | the graph projection |
| `valkey` | the task queue (safe to lose: in-flight tasks are redelivered) |

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
   hierarchical facets, keyword, semantic and hybrid search, list search, graph
   neighbours and a shortest path, a review decision through the outbox to a
   `CURATED` edge, and `oss reindex` keeping it.
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
