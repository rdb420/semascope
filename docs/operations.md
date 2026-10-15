# Operations

## Sizes

`oss init --size small|medium|large` writes the JVM heaps for Solr, Neo4j
and Tika (1, 2 or 4 GB each) and the data folders. Small runs the whole
stack in about 6 GB of RAM on two cores; it is the right choice for a
laptop or a first look. Medium suits a team server with tens of thousands
of documents; large is for hundreds of thousands. Worker concurrency is
`--concurrency` on `oss-worker` in `compose.yaml` (default 2).

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
