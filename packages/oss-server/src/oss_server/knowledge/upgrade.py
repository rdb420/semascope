"""Pack upgrades as a diff and a reindex plan (ADR-0007), and removing a pack version.

A diff compares the active version of a pack with a candidate (a bundle on
disk, or another imported version) term by term. The reindex plan says which
collections a switch would reindex and how many of their documents carry the
active version's tags, or tags whose concept is removed or changed. Nothing
here changes the index; the API asks for confirmation before it activates.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from oss_server.knowledge.models import Concept, Pack

log = logging.getLogger(__name__)

# Fields that change what a term tags or how its tags are faceted.
TAG_FIELDS = (
    "kind",
    "taggable",
    "pref_label",
    "pref_labels",
    "alt_labels",
    "hidden_labels",
    "scheme_slug",
    "path",
    "type_iri",
    "broader",
)
# Fields that change only what the pack page and concept cards show.
INFO_FIELDS = ("lang", "definition", "notation", "support", "term_status", "coordinates")
LIST_CAP = 200


def concept_row(c: Concept) -> dict[str, Any]:
    """A stored Concept in the shape `importer.derive` produces."""

    return {
        "iri": c.iri,
        "kind": c.kind,
        "pref_label": c.pref_label,
        "lang": c.lang,
        "alt_labels": list(c.alt_labels),
        "hidden_labels": list(c.hidden_labels),
        "definition": c.definition,
        "scheme_slug": c.scheme_slug,
        "type_iri": c.type_iri,
        "broader": list(c.broader),
        "path": c.path,
        "taggable": c.taggable,
        "support": c.support,
        "term_status": c.term_status,
        "extra": dict(c.extra or {}),
    }


def _norm(row: dict[str, Any]) -> dict[str, Any]:
    extra = row.get("extra") or {}
    coords = (
        [extra["lat"], extra["lon"]]
        if extra.get("lat") is not None and extra.get("lon") is not None
        else None
    )
    return {
        "kind": row["kind"],
        "taggable": bool(row["taggable"]),
        "pref_label": row["pref_label"],
        "pref_labels": sorted(
            f"{p.get('label')}@{p.get('lang') or ''}" for p in extra.get("pref_labels") or []
        ),
        "alt_labels": sorted(row.get("alt_labels") or []),
        "hidden_labels": sorted(row.get("hidden_labels") or []),
        "scheme_slug": row.get("scheme_slug") or "",
        "path": row.get("path") or "",
        "type_iri": row.get("type_iri") or "",
        "broader": sorted(row.get("broader") or []),
        "lang": row.get("lang") or "",
        "definition": row.get("definition") or "",
        "notation": sorted(extra.get("notation") or []),
        "support": row.get("support") or "",
        "term_status": row.get("term_status") or "",
        "coordinates": coords,
    }


def _dictionary_labels(iri: str, n: dict[str, Any]) -> set[tuple[str, str]]:
    if not n["taggable"]:
        return set()
    labels = {n["pref_label"], *n["alt_labels"], *n["hidden_labels"]}
    labels |= {p.rsplit("@", 1)[0] for p in n["pref_labels"]}
    return {(iri, label) for label in labels if len(label.strip()) >= 2}


def diff_rows(old_rows: list[dict[str, Any]], new_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Term-level differences between two derived vocabularies (pure; no database)."""

    old = {r["iri"]: _norm(r) for r in old_rows}
    new = {r["iri"]: _norm(r) for r in new_rows}
    added = sorted(new.keys() - old.keys())
    removed = sorted(old.keys() - new.keys())
    changed: list[dict[str, Any]] = []
    affecting: set[str] = {iri for iri in removed if old[iri]["taggable"]}
    for iri in sorted(old.keys() & new.keys()):
        fields = {
            f: [old[iri][f], new[iri][f]]
            for f in (*TAG_FIELDS, *INFO_FIELDS)
            if old[iri][f] != new[iri][f]
        }
        if not fields:
            continue
        changed.append(
            {
                "iri": iri,
                "kind": new[iri]["kind"],
                "label": new[iri]["pref_label"],
                "fields": fields,
            }
        )
        if old[iri]["taggable"] and any(f in TAG_FIELDS for f in fields):
            affecting.add(iri)

    old_labels = set().union(*(_dictionary_labels(i, n) for i, n in old.items()))
    new_labels = set().union(*(_dictionary_labels(i, n) for i, n in new.items()))
    common = old.keys() & new.keys()
    summary = {
        "added": dict(Counter(new[i]["kind"] for i in added)),
        "removed": dict(Counter(old[i]["kind"] for i in removed)),
        "changed": dict(Counter(c["kind"] for c in changed)),
        "labels_added": len(new_labels - old_labels),
        "labels_removed": len(old_labels - new_labels),
        "became_taggable": sum(1 for i in common if new[i]["taggable"] and not old[i]["taggable"]),
        "no_longer_taggable": sum(
            1 for i in common if old[i]["taggable"] and not new[i]["taggable"]
        ),
        "terms_before": len(old),
        "terms_after": len(new),
    }

    def brief(iris: list[str], rows: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {"iri": i, "kind": rows[i]["kind"], "label": rows[i]["pref_label"]}
            for i in iris[:LIST_CAP]
        ]

    return {
        "summary": summary,
        "added": brief(added, new),
        "removed": brief(removed, old),
        "changed": changed[:LIST_CAP],
        "truncated": max(len(added), len(removed), len(changed)) > LIST_CAP,
        "tag_affecting_iris": sorted(affecting),
    }


def collections_using(pack_id: str) -> list[str]:
    """Slugs of the collections that tag with a pack: bound to it, or bound to nothing."""

    from oss_server.catalogue.models import Collection
    from oss_server.knowledge.models import PackBinding

    bound = set(
        PackBinding.objects.filter(pack_id=pack_id, enabled=True).values_list(
            "collection__slug", flat=True
        )
    )
    unbound = set(
        Collection.objects.exclude(pack_bindings__isnull=False).values_list("slug", flat=True)
    )
    return sorted(bound | unbound)


def _quote(value: str) -> str:
    from oss_pipeline.clients.solr import quote_term

    return quote_term(value)


def _facet_collections(fq: list[str], **extra: Any) -> dict[str, int]:
    from oss_server.services import solr_client, solr_documents_collection

    body = solr_client().select(
        solr_documents_collection(),
        q="*:*",
        fq=fq,
        rows=0,
        hl=False,
        facet=True,
        **{"facet.field": "collection_s", "facet.limit": -1, "facet.mincount": 1},
        **extra,
    )
    flat = (body.get("facet_counts") or {}).get("facet_fields", {}).get("collection_s", [])
    return {str(flat[i]): int(flat[i + 1]) for i in range(0, len(flat) - 1, 2)}


def document_counts(pack_id: str, version: str | None, iris: list[str]) -> dict[str, Any]:
    """Per collection: all documents, those tagged by `pack_id@version`, and those carrying
    one of `iris` as a concept tag or a linked entity. Solr errors leave the counts out."""

    try:
        documents = _facet_collections([])
        tagged = (
            _facet_collections([f"pack_versions_ss:{_quote(f'{pack_id}@{version}')}"])
            if version
            else {}
        )
        affected: dict[str, int] = {}
        if iris and version:
            # terms queries have no clause limit; IRIs cannot contain spaces
            affected = _facet_collections(
                ["{!bool should=$sem_concepts should=$sem_entities}"],
                sem_concepts="{!terms f=concept_iris_ss separator=' ' v=$sem_iris}",
                sem_entities="{!terms f=entity_iris_ss separator=' ' v=$sem_iris}",
                sem_iris=" ".join(iris),
            )
    except Exception as exc:  # noqa: BLE001 - a plan without counts is still a plan
        log.warning("pack plan: document counts unavailable: %s", exc)
        return {"error": str(exc)[:300]}
    return {"documents": documents, "tagged": tagged, "affected": affected}


def reindex_plan(pack_id: str, active: Pack | None, iris: list[str]) -> dict[str, Any]:
    slugs = collections_using(pack_id)
    counts = document_counts(pack_id, active.version if active else None, iris)
    rows: list[dict[str, Any]] = []
    if "error" not in counts:
        for slug in sorted(set(slugs) | set(counts["tagged"])):
            rows.append(
                {
                    "collection": slug,
                    "reindex": slug in slugs,
                    "documents": counts["documents"].get(slug, 0),
                    "tagged_documents": counts["tagged"].get(slug, 0),
                    "affected_documents": counts["affected"].get(slug, 0),
                }
            )
    plan: dict[str, Any] = {
        "reindex": slugs,
        "collections": rows,
        "documents": sum(r["documents"] for r in rows if r["reindex"]),
        "tagged_documents": sum(r["tagged_documents"] for r in rows),
        "affected_documents": sum(r["affected_documents"] for r in rows),
        "note": (
            "Activation re-syncs the dictionary and queues a reindex of every collection "
            "marked reindex. Added labels can match any document, so whole collections "
            "are reindexed, not only the affected documents."
        ),
    }
    if "error" in counts:
        plan["counts_error"] = counts["error"]
    return plan


def upgrade_plan(
    pack_id: str,
    candidate_version: str,
    candidate_rows: list[dict[str, Any]],
    candidate_hash: str | None = None,
) -> dict[str, Any]:
    """The diff between the active version of `pack_id` and a candidate, with its plan."""

    active = Pack.objects.filter(pack_id=pack_id, status=Pack.Status.ACTIVE).first()
    old_rows = [concept_row(c) for c in active.concepts.all()] if active else []
    diff = diff_rows(old_rows, candidate_rows)
    iris = diff.pop("tag_affecting_iris")
    same_version = active is not None and active.version == candidate_version
    same_content = (
        same_version
        and candidate_hash is not None
        and active is not None
        and (candidate_hash == active.manifest_hash)
    )
    changes = bool(diff["added"] or diff["removed"] or diff["changed"])
    return {
        "pack_id": pack_id,
        "active_version": active.version if active else None,
        "candidate_version": candidate_version,
        "same_content": same_content,
        "changes": changes,
        # a first import changes nothing that is tagged; switching or replacing does
        "requires_confirmation": active is not None and not same_content,
        "diff": diff,
        "tag_affecting_terms": len(iris),
        "plan": reindex_plan(pack_id, active, iris),
    }
