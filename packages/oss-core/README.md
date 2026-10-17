# oss-core

Shared models, configuration and the plugin protocols every other Semascope
package builds on. It has no Django, Celery or HTTP dependency so that
connectors, stages and packs can be tested in isolation.

Contents: `config.py` (settings), `contracts.py` (Connector, Stage, Embedder
and IndexBackend protocols and the working document; `IndexBackend` is
defined but not yet implemented or used), `pack.py` (the ontology-skill bundle
contract: layout, validation, manifest). Writing a plugin:
`docs/plugins.md`.
