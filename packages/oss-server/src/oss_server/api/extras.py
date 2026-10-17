"""Capabilities Open Semantic Search promised and never delivered (ADR-0012), in one router
that `root.py` mounts at the API root:

- `GET  /documents/{id}/similar`          more like this (`similar.py`)
- `GET  /export/documents/{id}`           one document as Turtle or JSON-LD (`exports.py`)
- `POST /export/search`                   a search's hits as Turtle or JSON-LD
- `GET  /annotations/w3c`                 notes and decisions as W3C Web Annotations
- `POST /annotations/w3c/import`          W3C or Hypothesis annotations in (`annotations_w3c.py`)
- `GET  /opensearch.xml`, `/opensearch/suggest`   browser search (`opensearch.py`)
- `GET  /analytics/overview`              counts, top entities, documents over time
- `POST /analytics/clusters|compare|wordtree`, `GET /analytics/diff`   analysis views
                                          of a search's results (`analysis.py`)
- `POST /export/documents|entities`, `GET /export/graph`   tables (CSV, JSON Lines,
                                          Parquet) and the graph as RDF (`exports_table.py`)
- `GET  /synonyms/expand|suggest`         what a query expands to, candidate labels
                                          (`synonym_tools.py`)
- `POST /explain`                         why a hit ranked where it did (`explain.py`)
- `POST /annotations/w3c`, `GET|PUT|DELETE /annotations/w3c/{id}`   the Web Annotation
                                          Protocol's per-annotation operations
                                          (`annotations_ldp.py`)

Each module adds its routes to this router with `mount`, so the authorisation
matrix test sees every one of them in the API's route table.
"""

from __future__ import annotations

from ninja import Router

from oss_server.api import (
    analysis,
    analytics,
    annotations_ldp,
    annotations_w3c,
    explain,
    exports,
    exports_table,
    opensearch,
    similar,
    synonym_tools,
)

router = Router()
for _module in (similar, exports, annotations_w3c, opensearch, analytics):
    _module.mount(router)
# WS-12 rest (extras2): analysis views, table and graph exports, synonym tooling, ranking
# explain, the Web Annotation Protocol's per-annotation operations
for _module in (analysis, exports_table, synonym_tools, explain, annotations_ldp):
    _module.mount(router)
