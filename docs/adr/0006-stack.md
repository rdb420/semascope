# ADR-0006: The rest of the stack

Status: proposed. Amended 2026-10-17 (see the end) to match what was built.

| Concern | Choice | Reason |
|---|---|---|
| Backend | Django 5.2 LTS, Django Ninja | Auth, sessions, permissions, admin, ORM and migrations on day one; typed OpenAPI 3.1 |
| Browser UI | React 19, TypeScript, Vite, TanStack Query and Router, Tailwind 4; Cytoscape.js, ECharts and MapLibre for the graph, charts and map | Rich client state (graph, map, timeline, curation); contributor pool |
| Queue | Celery 5.6 on Valkey, two queues (`cpu`, `io`; [ADR-0010](0010-one-task-per-document.md)) | Priorities, retries, Flower, community; Valkey is BSD-3 and Redis-compatible |
| Relational store | PostgreSQL 17 (psycopg 3, [ADR-0011](0011-psycopg-lgpl.md)) | Application state, curation, audit log, pack registry |
| Pack store | pyoxigraph 0.5 (embedded), rdflib 7, pyshacl | SPARQL over packs without a triple-store service |
| Graph | Neo4j 5.26 Community, official driver 6 | Document-entity graph with an ontology-driven projection |
| Graph rendering | Cytoscape.js (MIT) | Neo4j's NVL is not open source |
| Extraction | Apache Tika 3.3 full image | Format breadth, Tesseract OCR; Tika 4 adopted once stable |
| NLP | spaCy 3.8 in-process, Python 3.13 images | spaCy 3.8 declares Python < 3.14 |
| Embeddings | FastEmbed, bge-small default, ONNX on CPU | No PyTorch in the default image; the optional `-gpu` image carries PyTorch for GLiNER2 ([ADR-0008](0008-runtimes-and-providers.md)) |
| Previews | pypdfium2, Pillow | No shell-outs to ImageMagick |
| Edge | Caddy, internal TLS by default (`SEMASCOPE_TLS` for ACME or your own certificate) | Zero-configuration HTTPS |
| Single sign-on | django-allauth (OpenID Connect) | Maintained, covers the admin and the API session |
| Configuration | Environment variables (and `.env`) through Pydantic Settings, each readable from a file named by `<NAME>_FILE` (Docker secrets); per-collection settings in Postgres | Never generated code; one mechanism for compose and secrets |
| Observability | Plain-text logs on standard output (credentials masked), OpenTelemetry; an optional profile with a collector, Jaeger, Prometheus, Grafana and Flower | Matches Solr 10 |

## Amended 2026-10-17

The table above is the stack as built for 1.0.0-rc.3 (unreleased). It differs from the
first version of this record in six places, all corrections of what was
proposed rather than new decisions:

- **Configuration.** The first version said "TOML and environment". No TOML
  file was ever read: settings come from the environment and `.env`, and
  from `<NAME>_FILE` files for secrets (`oss_core.config`). A TOML file is
  not planned; a second configuration format would mean two places to look
  for every setting.
- **Flower** was given as a reason for Celery but was not deployed. It now
  runs in the observability profile (`compose.observe.yaml`), on loopback
  behind a password.
- **PyTorch.** "No PyTorch in the image" holds for the default image; the
  `gpu` runtime's image carries it (ADR-0008).
- **shadcn/ui** was never used; the UI is plain Tailwind components.
  ECharts, MapLibre and django-allauth were added without being recorded;
  they are listed now.
- **Queues.** "Priority queues, routing" became two queues and task
  priorities (ADR-0010, ADR-0014), not routing per stage.
- **Logs.** "Structured logs" were never structured: they are plain text
  lines, with URL passwords masked.

This record has no context or alternatives sections; it should be accepted
as amended or superseded by one that has them.
