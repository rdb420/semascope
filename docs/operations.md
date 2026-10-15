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
| `remote` | `sidecar` or `openai` service | `remote` entity service | `mineru` parse server (PDF, Office, images; Tika keeps the rest) | the service URLs and, usually, a token |

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
only. A stage list replays those stages, what they need (`structure` before
`embed`), the stages they feed (`link` after `tag` or `ner`) and the `index`
and `graph` stages that publish the result; tags of the other stages are
kept, and a failed replayed stage publishes nothing. `./oss stats` shows the chunk count per model, which is how you know
when it is done. A remote service that is down fails the stage and the task
retries; nothing falls back to the CPU model, because a mixed index is worse
than a late one.

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
changed to match (or recreate their volumes on a fresh install). The API
token (`SEMASCOPE_API_TOKEN`) can also be replaced by creating a token in the
admin and putting its value in `.env`.

## Logs and health

`./oss logs <service>` follows a service. `/healthz` answers on the server;
every container has a compose health check. `./oss doctor` checks the
server, Solr, Tika and Neo4j and reports versions. With
`compose.observe.yaml`, traces and metrics go to the bundled collector and
Grafana on `127.0.0.1:3000`.

## Where the data lives

| Volume | Holds |
|---|---|
| `blobs` (`/data`) | originals by content hash, previews, uploads, the pack store, the scheduler state |
| `postgres` | collections, sources, documents with extracted text, packs and concepts, reviews, proposals, events |
| `solr` | the `documents`, `entities` and `chunks` collections |
| `neo4j` | the graph projection |
| `valkey` | the task queue (safe to lose: in-flight tasks are redelivered) |
