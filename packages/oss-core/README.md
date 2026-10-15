# oss-core

Shared models, configuration and the plugin protocols every other Semascope
package builds on. It has no Django, Celery or HTTP dependency so that
connectors, stages and packs can be tested in isolation.

Contents: `config.py` (settings), `contracts.py` (Connector, Stage, Embedder,
IndexBackend protocols and the working document), `pack.py` (the
ontology-skill bundle contract: layout, validation, manifest).
