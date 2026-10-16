"""Analytics overview: what a collection holds and how it grew (one Solr query, read-scoped).

Counts by collection, type and language; the most frequent people, places,
organisations, concepts and linked entities; documents per period of their
modification date; documents indexed per day over the last 90 days; and, per
collection, its top concepts and entities.
"""

from __future__ import annotations

import re
from typing import Any

from django.http import HttpRequest
from ninja import Router, Schema

from oss_server import services
from oss_server.api import search as search_mod
from oss_server.api.access import read_scope
from oss_server.api.schemas import FacetValue

TOP_FIELDS = {
    "people": "persons_ss",
    "places": "places_ss",
    "organisations": "organisations_ss",
    "concepts": "concept_labels_ss",
    "entities": "entity_labels_ss",
}
BREAKDOWN_FIELDS = {
    "collections": "collection_s",
    "types": "content_type_group_s",
    "languages": "language_s",
}
MAX_TOP = 50
MAX_COLLECTIONS = 100
INDEXED_DAYS = 90


class CollectionOverview(Schema):
    collection: str
    documents: int
    concepts: list[FacetValue]
    entities: list[FacetValue]


class OverviewOut(Schema):
    documents: int
    breakdown: dict[str, list[FacetValue]]
    top: dict[str, list[FacetValue]]
    modified: list[FacetValue]
    modified_gap: str
    indexed: list[FacetValue]
    per_collection: list[CollectionOverview]


def _gap(value: str) -> str:
    value = value.strip()
    if value and value[0].isdigit():  # an unencoded "+" in a query string arrives as a space
        value = "+" + value
    return value if re.fullmatch(r"\+\d{1,3}(DAY|MONTH|YEAR)S?", value) else "+1YEAR"


def overview_params(
    allowed: list[str] | None, collection: str | None, top: int, gap: str
) -> dict[str, Any]:
    fq = search_mod.collection_filter(allowed)
    if collection:
        fq.append(f"collection_s:{search_mod._escape(collection)}")
    params: dict[str, Any] = {
        "q": "*:*",
        "fq": fq,
        "rows": 0,
        "hl": False,
        "facet": True,
        "facet.mincount": 1,
        "facet.limit": top,
        "facet.field": [*TOP_FIELDS.values(), *BREAKDOWN_FIELDS.values()],
        "f.collection_s.facet.limit": MAX_COLLECTIONS,
        "facet.range": ["{!key=modified}modified_dt", "{!key=indexed}indexed_dt"],
        "f.modified_dt.facet.range.start": (
            "NOW/YEAR-200YEARS" if "YEAR" in gap else "NOW/DAY-5YEARS"
        ),
        "f.modified_dt.facet.range.end": "NOW/DAY+1DAY",
        "f.modified_dt.facet.range.gap": gap,
        "f.indexed_dt.facet.range.start": f"NOW/DAY-{INDEXED_DAYS}DAYS",
        "f.indexed_dt.facet.range.end": "NOW/DAY+1DAY",
        "f.indexed_dt.facet.range.gap": "+1DAY",
        "facet.pivot": [
            "{!key=collection_concepts}collection_s,concept_labels_ss",
            "{!key=collection_entities}collection_s,entity_labels_ss",
        ],
    }
    return params


def _values(flat: list[Any]) -> list[FacetValue]:
    return [FacetValue(value=str(flat[i]), count=int(flat[i + 1])) for i in range(0, len(flat), 2)]


def _range(ranges: dict[str, Any], key: str, cut: int) -> list[FacetValue]:
    flat = (ranges.get(key) or {}).get("counts", [])
    return [
        FacetValue(value=str(flat[i])[:cut], count=int(flat[i + 1]))
        for i in range(0, len(flat), 2)
        if int(flat[i + 1])
    ]


def parse_overview(body: dict[str, Any], gap: str, top: int) -> OverviewOut:
    counts = body.get("facet_counts", {})
    fields = counts.get("facet_fields", {})
    ranges = counts.get("facet_ranges", {})
    pivots = counts.get("facet_pivot", {})
    per: dict[str, CollectionOverview] = {}
    for key, attr in (("collection_concepts", "concepts"), ("collection_entities", "entities")):
        for row in pivots.get(key, []):
            slug = str(row.get("value"))
            item = per.setdefault(
                slug,
                CollectionOverview(
                    collection=slug, documents=int(row.get("count", 0)), concepts=[], entities=[]
                ),
            )
            values = [
                FacetValue(value=str(p["value"]), count=int(p["count"]))
                for p in (row.get("pivot") or [])
            ]
            setattr(item, attr, values[:top])
    cut = 4 if "YEAR" in gap else 7 if "MONTH" in gap else 10
    return OverviewOut(
        documents=int(body.get("response", {}).get("numFound", 0)),
        breakdown={name: _values(fields.get(f, [])) for name, f in BREAKDOWN_FIELDS.items()},
        top={name: _values(fields.get(f, []))[:top] for name, f in TOP_FIELDS.items()},
        modified=_range(ranges, "modified", cut),
        modified_gap=gap,
        indexed=_range(ranges, "indexed", 10),
        per_collection=sorted(per.values(), key=lambda c: (-c.documents, c.collection)),
    )


def overview(
    request: HttpRequest, collection: str | None = None, top: int = 10, gap: str = "+1YEAR"
) -> OverviewOut:
    """Counts, top entities and concepts, and documents over time for the readable
    collections (or one). `gap` buckets modification dates: +1YEAR, +1MONTH, +10YEARS..."""

    allowed = read_scope(request, collection)
    top = max(1, min(top, MAX_TOP))
    gap = _gap(gap)
    body = services.solr_client().select(
        services.solr_documents_collection(), **overview_params(allowed, collection, top, gap)
    )
    return parse_overview(body, gap, top)


def mount(router: Router) -> None:
    router.get("/analytics/overview", response=OverviewOut, tags=["analytics"])(overview)
