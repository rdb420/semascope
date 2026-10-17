# Install

This page takes you from nothing to a running Semascope with your first
collection, and through upgrades. What to check before choosing a host is
in the [deployment guide](assess/deployment.md); day-to-day commands are in
[Operations](operations.md).

## Prerequisites

| | Needed |
|---|---|
| Host | Linux (amd64 or arm64) with Docker Engine and the Compose plugin (a recent v2: the compose files use optional `env_file` entries, which need Compose 2.24 or later); macOS and Windows through Docker Desktop work but are not supported |
| Tools | `bash`, `tar`, `git`; `uv` is optional (`./oss init` uses it when the host has it, otherwise it runs in the app image) |
| Memory | about 6 GB free for `--size small`; see [Memory and CPU](assess/deployment.md#memory-and-cpu) |
| Disk | about ten times the size of the documents you will index, plus backups ([Storage](assess/deployment.md#storage)) |
| GPU (optional) | an NVIDIA card, its driver and the NVIDIA Container Toolkit, for `--runtime gpu` |
| Network | to pull the images once; afterwards Semascope runs offline except for the map tiles and the sources that read the web ([Offline operation](assess/deployment.md#offline-and-air-gapped-operation)) |

No release of 1.0 has been tagged yet. Until one is, `./oss up` cannot pull
the app image for the checkout's version and builds it from the checkout
instead, which takes several minutes the first time (see
[Image tags](operations.md#image-tags)).

## Quick start

```bash
git clone https://github.com/rdb420/semascope && cd semascope
./oss init      # writes .env with generated secrets, the admin password among them
./oss up        # starts the stack, waits for every health check, then runs oss doctor
./oss demo      # loads the public-domain demo corpus and its pack
```

Open `https://localhost:8443/` and sign in as `admin` with
`SEMASCOPE_ADMIN_PASSWORD` from `.env`. The certificate comes from Caddy's
own authority, so the browser warns once. The admin site is at `/admin/`
and the API's interactive documentation at `/api/docs` ([API](api.md)).

`./oss init` takes options; the ones you are most likely to want:

| Option | Default | Effect |
|---|---|---|
| `--size small\|medium\|large` | `small` | Java heaps and the worker's processes and memory ([Sizes](operations.md#sizes)) |
| `--host` | `localhost` | the host name Caddy serves |
| `--https-port` | 8443 | the one published port |
| `--import-dir` | `./data/import` | the host folder `oss index` reads, mounted read-only at `/import` |
| `--runtime cpu\|gpu\|remote` | `cpu` | where entity recognition, embeddings and OCR run ([Runtimes](operations.md#runtimes)) |
| `--secrets-dir DIR` | — | one file per secret instead of `.env` ([Docker secrets](operations.md#docker-secrets)) |
| `--force` | — | overwrite an existing `.env`: new secrets, every other setting kept |

`oss init` generates every secret (nothing is asked for) and writes
`SEMASCOPE_DEFAULT_ROLE=none`, so each user reads only the collections they
are given a role on ([Roles](concepts.md#roles)). `.env.example` lists every
setting with a comment.

## Your first collection

Put files under the import folder and index them:

```bash
mkdir -p data/import/reports && cp ~/Documents/*.pdf data/import/reports/
./oss index ./data/import/reports --collection reports
./oss search "annual budget" --collection reports
```

`oss index` creates the collection and a `filesystem` source for that
folder, runs it, and waits until the run has queued every file; the
documents become searchable as the worker indexes them (`./oss stats`). Files from
anywhere else on the machine can be uploaded instead:

```bash
./oss upload report.pdf notes.docx --collection reports
```

Other kinds of source (web sites, feeds, mailboxes, archives, tables,
buckets, RDF, web archives, annotations, Mastodon) are created through the
API or the admin, with the settings in [Connector settings](connectors.md).
With the CLI's token from `.env` (`SEMASCOPE_API_TOKEN`) in your shell:

```bash
curl -sk https://localhost:8443/api/collections/reports/sources \
  -H "Authorization: Bearer $SEMASCOPE_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"name": "news", "connector": "feed", "settings": {"urls": ["https://example.org/feed.xml"]}, "schedule": "every 1h"}'
```

To load a knowledge model, import a pack (`./oss pack import <folder>
--activate`; see the [pack specification](assess/pack-spec.md)); the base
pack is imported on first start. Then give people accounts and roles in the
admin (*Users*, then *Collection roles*), or set up single sign-on
([Authentication and access](assess/deployment.md#authentication-and-access)).

## Upgrading

```bash
./oss backup               # always, first
git pull                   # a source checkout; or set SEMASCOPE_IMAGE_TAG in .env to the new release
./oss up                   # fetches the new image, migrates, reloads the Solr configsets
./oss doctor
```

On start the server runs the database migrations, uploads the Solr
configsets and reloads (or creates) the collections, re-imports the bundled
base pack when it changed and rewrites the pack synonyms; `./oss up` also
adds a generated `VALKEY_PASSWORD` to an `.env` that has none. Read the
changelog's upgrade notes for the version you move to: some changes need
`./oss reindex --collection <slug>` (optionally `--stage index` or
`--stage embed`) for each collection, which replays enrichment from the
stored text without fetching or extracting again. Upgrading from
1.0.0-rc.2 has several such steps, collected under
"Upgrade from 1.0.0-rc.2" in the
[changelog](https://github.com/rdb420/semascope/blob/main/CHANGELOG.md).

`./oss up` pulls the app image only when the tag is new: it does not
re-pull a tag that is already on the host. To change the version, change
the tag. A backup is restored with `./oss restore <dir>`
([Backup and restore](operations.md#backup-and-restore)).

## Removing it

`./oss down` stops the stack and keeps the data. `docker compose down
--volumes` also deletes the volumes (documents, index, graph, database);
`data/` holds the import folder and backups.
