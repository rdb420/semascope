# ADR-0006: The rest of the stack

Status: proposed.

| Concern | Choice | Reason |
|---|---|---|
| Backend | Django 5.2 LTS, Django Ninja | Auth, sessions, permissions, admin, ORM and migrations on day one; typed OpenAPI 3.1 |
| Browser UI | React 19, TypeScript, Vite, TanStack, Tailwind, shadcn/ui | Rich client state (graph, map, timeline, curation); contributor pool |
| Queue | Celery 5.6 on Valkey | Priority queues, routing, Flower, community; Valkey is BSD-3 and Redis-compatible |
| Relational store | PostgreSQL 17 | Application state, curation, audit, pack registry |
| Pack store | pyoxigraph 0.5 (embedded), rdflib 7, pyshacl | SPARQL over packs without a triple-store service |
| Graph | Neo4j 5.26 Community, official driver 6 | Document-entity graph with an ontology-driven projection |
| Graph rendering | Cytoscape.js (MIT) | Neo4j's NVL is not open source |
| Extraction | Apache Tika 3.3 full image | Format breadth, Tesseract OCR; Tika 4 adopted once stable |
| NLP | spaCy 3.8 in-process, Python 3.13 images | spaCy 3.8 declares Python < 3.14 |
| Embeddings | FastEmbed, bge-small default, ONNX on CPU | No PyTorch in the image; GPU through ONNX Runtime providers |
| Previews | pypdfium2, Pillow | No shell-outs to ImageMagick |
| Edge | Caddy with internal TLS | Zero-configuration HTTPS |
| Configuration | TOML and environment through Pydantic Settings; runtime settings in Postgres | Never generated code |
| Observability | Structured logs, OpenTelemetry | Matches Solr 10 |
