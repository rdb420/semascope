"""The explanation record shared by stages and the server: pages of spans, and the curated view.

Pages: the structure stage joins the pages with a blank line and records where
each one starts (`fields["page_starts"]`); a span's page is the last page that
starts at or before it.

Curated view: `Document.tags` keeps what the machine found (plus tags curators
added); `fields["curated"]` holds the decision in force per IRI. `curated_tags`
combines the two the same way for every store: a rejected tag is left out of
Solr and Neo4j (and flagged, not dropped, in the API), an accepted addition is
put in, and an accepted link points the NER tag at the individual it was
linked to, keeping the NER entity in `linked_from`.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping
from typing import Any

from oss_core.contracts import LinkedFrom, Tag, WorkingDocument

PAGE_SEPARATOR = "\n\n"  # what the structure stage joins pages with


def page_starts(doc: WorkingDocument) -> list[int]:
    """Character offset at which each page starts in `doc.text`; empty when there are no pages."""

    stored = doc.fields.get("page_starts")
    if isinstance(stored, list) and len(stored) == len(doc.pages) and stored:
        return [int(x) for x in stored]
    if not doc.pages or PAGE_SEPARATOR.join(doc.pages) != (doc.text or ""):
        return []
    starts: list[int] = []
    pos = 0
    for page in doc.pages:
        starts.append(pos)
        pos += len(page) + len(PAGE_SEPARATOR)
    return starts


def assign_pages(tags: Iterable[Tag], doc: WorkingDocument) -> None:
    """Fill `page` (1-based) on every span of these tags that has none."""

    starts = page_starts(doc)
    if not starts:
        return
    for tag in tags:
        for span in tag.spans:
            if span.page is None:
                span.page = max(1, bisect_right(starts, span.start))


def _as_tag(value: Tag | Mapping[str, Any]) -> Tag:
    return value if isinstance(value, Tag) else Tag.model_validate(dict(value))


def curated_tags(
    tags: Iterable[Tag | Mapping[str, Any]],
    curated: Mapping[str, Any] | None,
    *,
    keep_rejected: bool = False,
) -> list[Tag]:
    """The tags after the decisions in force.

    `keep_rejected=True` keeps rejected tags (for the API, which flags them);
    the index and the graph leave them out.
    """

    decisions: dict[str, dict[str, Any]] = {
        str(k): dict(v) for k, v in (curated or {}).items() if isinstance(v, Mapping)
    }
    # links a curator made: the NER IRI -> the individual it was linked to (a later
    # reject of the individual keeps `linked_from`, so the mention stays rejected)
    links = {
        str(v["linked_from"]): iri
        for iri, v in decisions.items()
        if isinstance(v.get("linked_from"), str)
    }
    out: list[Tag] = []
    by_iri: dict[str, Tag] = {}
    for raw in tags:
        tag = _as_tag(raw).model_copy(deep=True)
        target = links.get(tag.iri)
        if target is not None and decisions.get(tag.iri, {}).get("decision") is None:
            decision = decisions[target]
            existing = by_iri.get(target)
            if existing is not None:
                existing.spans.extend(tag.spans)
                continue
            tag.linked_from = tag.linked_from or LinkedFrom(
                iri=tag.iri, label=tag.label, confidence=tag.confidence, model=tag.model
            )
            tag.iri = target
            tag.label = str(decision.get("label") or tag.label)
            tag.pack_id = decision.get("pack_id") or tag.pack_id
            tag.pack_version = decision.get("pack_version") or tag.pack_version
            tag.stage, tag.stage_version, tag.confidence = "curated", None, 1.0
        elif tag.iri in by_iri:
            by_iri[tag.iri].spans.extend(tag.spans)
            continue
        if not keep_rejected and decisions.get(tag.iri, {}).get("decision") == "reject":
            continue
        out.append(tag)
        by_iri[tag.iri] = tag
    for iri, decision in decisions.items():
        if decision.get("decision") != "accept" or iri in by_iri or not decision.get("label"):
            continue
        tag = Tag(
            iri=iri,
            label=str(decision["label"]),
            kind="concept" if decision.get("kind", "concept") == "concept" else "entity",
            stage="curated",
            confidence=1.0,
            pack_id=decision.get("pack_id"),
            pack_version=decision.get("pack_version"),
        )
        out.append(tag)
        by_iri[iri] = tag
    return out
