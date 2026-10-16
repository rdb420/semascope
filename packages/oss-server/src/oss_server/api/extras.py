"""Capabilities Open Semantic Search promised and never delivered (ADR-0012), in one router
that `root.py` mounts at the API root:

- `GET  /documents/{id}/similar`          more like this (`similar.py`)
- `GET  /export/documents/{id}`           one document as Turtle or JSON-LD (`exports.py`)
- `POST /export/search`                   a search's hits as Turtle or JSON-LD
- `GET  /annotations/w3c`                 notes and decisions as W3C Web Annotations
- `POST /annotations/w3c/import`          W3C or Hypothesis annotations in (`annotations_w3c.py`)
- `GET  /opensearch.xml`, `/opensearch/suggest`   browser search (`opensearch.py`)
- `GET  /analytics/overview`              counts, top entities, documents over time

Each module adds its routes to this router with `mount`, so the authorisation
matrix test sees every one of them in the API's route table.
"""

from __future__ import annotations

from ninja import Router

from oss_server.api import analytics, annotations_w3c, exports, opensearch, similar

router = Router()
for _module in (similar, exports, annotations_w3c, opensearch, analytics):
    _module.mount(router)
