# Semascope

Semascope turns a pile of documents and a knowledge model into a searchable,
navigable, reviewable knowledge base, on your own hardware.

It indexes a corpus (files, web pages, feeds, mail, archives, tables, buckets,
RDF), enriches it against one or more knowledge models (SKOS vocabularies, OWL
classes, SHACL shapes and named individuals packaged as ontology-project
bundles, called *packs*), and lets people explore and curate the result through
faceted search, concept and entity pages, a document-entity graph in Neo4j, and
review queues whose decisions are exported for whatever system owns the data.

Status: **release candidate.** The last release candidate was 1.0.0-rc.2
(not tagged in git, and published only as a `:main` image); the main line,
to become 1.0.0-rc.3, adds the hardening and features in the
[changelog](CHANGELOG.md)'s *Unreleased* section. Nothing has been released
through the tag-driven release workflow yet.

From a fresh clone the stack starts, indexes folders, uploads, web pages
(with scrape rules), feeds, mail, archives, tables, S3 buckets, RDF and web
archives (including OCR of scanned pages, now or later), tags every document
against the knowledge models you import (hierarchical facets, named entities
linked to your individuals, SHACL checks), projects it into Neo4j, and
answers keyword, semantic and hybrid searches in 28 languages through the
browser UI, the API and the CLI. Curators review what the machine proposed;
every decision is logged and leaves through the outbox and webhooks.
`./oss demo` loads sixteen public-domain texts and a demo model.

What is **experimental** (code ships, tested only against mocks or stand-ins,
or not yet run end to end): the `gpu` and `remote` runtimes (GLiNER2,
MinerU, remote embedding and entity services), the `pst`, `hypothesis` and
`mastodon` connectors, indexing the text of embedded items, the
observability profile, and the release workflow (multi-arch images with
SBOM, provenance and cosign signatures, gated on CI), which has not yet run
from a tag. What is **planned** and not built: optional LLM answers (the
settings exist; nothing reads them), merging entities, entity pages for
unlinked named entities, managing sources and collections in the UI, Helm
and multi-host scaling. The [capability matrix](docs/assess/capabilities.md)
gives the state and the tests of every capability; the
[development plan](docs/plan.md) says what is left before and after 1.0, and
the architecture decisions are in [`docs/adr/`](docs/adr/).

## Quick start

```bash
git clone https://github.com/rdb420/semascope && cd semascope
./oss init      # writes .env with generated secrets, the admin password among them
./oss up        # starts Solr 10, Tika 3, Neo4j, PostgreSQL, Valkey, Caddy and the app, then checks them
./oss demo      # loads the public-domain demo corpus and its pack
./oss index ./data/import/my-files --collection mine   # anything under SEMASCOPE_IMPORT_DIR
./oss upload report.pdf notes.docx --collection mine    # files from anywhere on this machine
./oss search "what I am looking for" --collection mine
./oss backup                                            # Postgres, Solr and the data volume
```

Then open `https://localhost:8443/` and sign in as `admin` with the
`SEMASCOPE_ADMIN_PASSWORD` from `.env`. You need Docker with the Compose
plugin and bash; `./oss init` uses `uv` when the host has it and otherwise
runs in the app image. Until a release is published, `./oss up` builds the
app image from the checkout the first time. The `fresh-clone` job of the
`integration` workflow runs these steps (`init`, `up`, a one-text `demo`,
`search`, `upload`) in a clean clone for pushes to `main` and pull requests;
`just fresh-clone` runs it locally. The [install guide](docs/install.md)
covers prerequisites, a first collection and upgrades. `./oss rotate` replaces the secrets later, in the
stores and in `.env` together; see [operations](docs/operations.md#secrets),
which also covers Docker secrets.

The admin site is at `https://localhost:8443/admin/` (user `admin`, password in
`.env`) and the OpenAPI document at `https://localhost:8443/api/docs`.

Everything runs on a laptop with no cloud account, no API key and no GPU
(the map loads OpenStreetMap tiles in the browser unless
`SEMASCOPE_MAP_TILE_URL` points elsewhere or is empty).
With a GPU, `./oss init --runtime gpu` moves entity recognition and
embeddings onto the card; `--runtime remote` points those stages at your own
inference services instead (see [operations](docs/operations.md#runtimes)).

## Principles

1. **Agnostic.** No assumption about domain, corpus, ontology or downstream
   system; domain knowledge enters only through packs and configuration.
   Search analyses 28 languages; the bundled OCR, entity and embedding
   models default to English or multilingual small models and can be
   changed ([Languages and OCR](docs/languages.md)).
2. **Clone, set up, run.** One init and one up give a working stack with
   every bundled CPU feature; the GPU and remote runtimes and the PST extra
   need their own setup.
3. **No legacy.** Every dependency is a maintained release under a licence
   compatible with Apache-2.0 (psycopg's LGPL and the `-gpu` image's CUDA
   libraries are the recorded exceptions: ADR-0011, ADR-0008).
4. **Standards at the edges.** RDF, SKOS, OWL, SHACL, OpenAPI, OpenTelemetry,
   the W3C Reconciliation API, Compose.
5. **Review before write.** Curation produces proposals; proposals are logged
   and exportable; nothing rewrites a knowledge model silently.
6. **Ontology-skill native.** Packs follow the `ontology` skill's project
   bundle contract, and the product's own base vocabulary is such a bundle.

## Layout

| Path | Contents |
|---|---|
| `packages/oss-core` | Shared models, configuration, plugin protocols, the pack contract. No Django. |
| `packages/oss-server` | Django 5.2 project: API (Django Ninja), auth, admin, collections, knowledge, search, curation. |
| `packages/oss-pipeline` | Connectors, stages, Celery app, Solr, Neo4j and Tika clients. |
| `packages/oss-cli` | The `oss` command. |
| `packages/oss-web` | React and TypeScript single-page app. |
| `packs/base` | The product's own knowledge model, an ontology-skill bundle. |
| `packs/demo` | Builder for the public-domain demo corpus and demo pack. |
| `deploy/` | Dockerfile, Solr configsets, Tika config, Caddy, Neo4j bootstrap. |
| `docs/` | MkDocs site and architecture decision records. |

## Licence

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Contributions
must follow the clean-room rules in [CONTRIBUTING.md](CONTRIBUTING.md).
