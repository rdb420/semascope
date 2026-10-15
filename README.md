# Semascope

Semascope turns a pile of documents and a knowledge model into a searchable,
navigable, reviewable knowledge base, on your own hardware.

It indexes a corpus (files, web pages, feeds, mail, archives, tables, buckets,
RDF), enriches it against one or more knowledge models (SKOS vocabularies, OWL
classes, SHACL shapes and named individuals packaged as ontology-project
bundles, called *packs*), and lets people explore and curate the result through
faceted search, concept and entity pages, a document-entity graph in Neo4j, and
review queues whose decisions are exported for whatever system owns the data.

Status: **Phase 1, vertical slice.** From a fresh clone the stack starts,
indexes a folder of mixed files (including OCR of scanned pages) and answers
searches with facets and highlighted snippets through the API and the CLI.
No browser UI yet. The development plan is in [`docs/plan.md`](docs/plan.md)
and the architecture decisions in [`docs/adr/`](docs/adr/).

## Quick start (target for 1.0)

```bash
git clone https://github.com/rdb420/semascope && cd semascope
./oss init      # writes .env with generated secrets and asks for an admin password
./oss up        # starts Solr 10, Tika 3, Neo4j, PostgreSQL, Valkey, Caddy and the app
./oss demo      # loads the public-domain demo corpus and its pack (Phase 2)
./oss index ./data/import/my-files --collection mine   # anything under SEMASCOPE_IMPORT_DIR
./oss search "what I am looking for" --collection mine
```

The admin site is at `https://localhost:8443/admin/` (user `admin`, password in
`.env`) and the OpenAPI document at `https://localhost:8443/api/docs`.

Everything runs on a laptop with no cloud account, no API key and no GPU.

## Principles

1. **Agnostic.** No assumption about domain, corpus, language, ontology or
   downstream system. Domain knowledge enters only through packs and
   configuration.
2. **Clone, set up, run.** One init, one up, every feature works.
3. **No legacy.** Every dependency is a maintained release under a licence
   compatible with Apache-2.0.
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
