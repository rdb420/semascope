"""Curation logic: emit events, record proposals, apply decisions to the stores."""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.utils import timezone

from oss_server.catalogue.models import Collection, Document
from oss_server.curation.models import Event, Proposal, Review

log = logging.getLogger(__name__)

REVIEW_THRESHOLD = 0.8  # tags below this confidence go to the review queue


def emit(
    kind: str, *, collection: str = "", subject: str = "", payload: dict[str, Any] | None = None
) -> Event:
    return Event.objects.create(
        kind=kind, collection=collection, subject=subject, payload=payload or {}
    )


def propose(
    collection: Collection,
    kind: str,
    target: str,
    *,
    params: dict[str, Any] | None = None,
    reason: str = "",
    document: Document | None = None,
    user: Any = None,
    pack_id: str = "",
    pack_version: str = "",
) -> Proposal:
    """Record a decision, apply it to the stores, and put it in the outbox."""

    params = params or {}
    ref = Proposal.make_ref(
        collection.slug, kind, target, params, document.id if document else None
    )
    existing = Proposal.objects.filter(external_ref=ref).first()
    if existing:
        return existing
    with transaction.atomic():
        proposal = Proposal.objects.create(
            collection=collection,
            document=document,
            kind=kind,
            target=target,
            params=params,
            reason=reason,
            pack_id=pack_id,
            pack_version=pack_version,
            decided_by=user if getattr(user, "is_authenticated", False) else None,
        )
        try:
            apply_proposal(proposal)
        except Exception:  # noqa: BLE001 - the decision is recorded even if a store is down
            log.exception("could not apply proposal %s", proposal.pk)
            proposal.applied = False
            proposal.save(update_fields=["applied"])
        emit(
            f"proposal.{kind}",
            collection=collection.slug,
            subject=target,
            payload={
                "proposal_id": proposal.pk,
                "external_ref": proposal.external_ref,
                "document_id": document.id if document else None,
                "params": params,
                "reason": reason,
                "pack_id": pack_id,
                "pack_version": pack_version,
                "decided_by": getattr(proposal.decided_by, "username", None),
                "decided_at": proposal.decided_at.isoformat(),
            },
        )
    return proposal


def apply_proposal(p: Proposal) -> None:
    """Make the decision real in Postgres, Solr and the graph. Idempotent."""

    from oss_server.services import graph_client, solr_client, solr_documents_collection

    if p.document is None or p.kind in (Proposal.Kind.ADD_LABEL, Proposal.Kind.NEW_CONCEPT):
        return  # pack-level kinds are handled by the knowledge app's deltas
    doc = p.document
    tags: list[dict[str, Any]] = list(doc.tags)
    curated: dict[str, Any] = dict(doc.fields.get("curated") or {})
    if p.kind in (Proposal.Kind.ACCEPT_TAG, Proposal.Kind.ADD_TAG):
        curated[p.target] = {
            "decision": "accept",
            "label": p.params.get("label", ""),
            "kind": p.params.get("kind", "concept"),
        }
        if p.kind == Proposal.Kind.ADD_TAG and not any(t.get("iri") == p.target for t in tags):
            tags.append(
                {
                    "iri": p.target,
                    "label": p.params.get("label", p.target),
                    "kind": p.params.get("kind", "concept"),
                    "stage": "curated",
                    "confidence": 1.0,
                    "spans": [],
                    "pack_id": p.pack_id or None,
                    "pack_version": p.pack_version or None,
                }
            )
    elif p.kind in (Proposal.Kind.REJECT_TAG, Proposal.Kind.REMOVE_TAG):
        curated[p.target] = {"decision": "reject"}
        tags = [t for t in tags if t.get("iri") != p.target]
    elif p.kind == Proposal.Kind.LINK_ENTITY:
        new_iri = str(p.params.get("iri") or "")
        for t in tags:
            if t.get("iri") == p.target:
                t["iri"], t["pack_id"], t["pack_version"], t["stage"], t["confidence"] = (
                    new_iri,
                    p.pack_id or t.get("pack_id"),
                    p.pack_version or t.get("pack_version"),
                    "curated",
                    1.0,
                )
                if p.params.get("label"):
                    t["label"] = p.params["label"]
        curated[new_iri] = {"decision": "accept", "linked_from": p.target}
    elif p.kind == Proposal.Kind.DISMISS_VIOLATION:
        dismissed = set(doc.fields.get("dismissed_violations") or [])
        dismissed.add(p.target)
        doc.fields["dismissed_violations"] = sorted(dismissed)
    else:
        return
    doc.tags = tags
    doc.fields["curated"] = curated
    doc.save(update_fields=["tags", "fields", "updated_at"])

    # Solr: atomic update of the tag facets from the curated tag list
    concept = [t for t in tags if t.get("kind") == "concept"]
    linked = [t for t in tags if t.get("kind") == "entity" and t.get("pack_id")]
    update = {
        "id": doc.id,
        "concept_iris_ss": {"set": sorted({t["iri"] for t in concept})},
        "concept_labels_ss": {"set": sorted({t["label"] for t in concept})},
        "entity_iris_ss": {"set": sorted({t["iri"] for t in linked})},
        "entity_labels_ss": {"set": sorted({t["label"] for t in linked})},
        "curated_ss": {"set": [f"{k}:{v['decision']}" for k, v in curated.items()]},
    }
    solr_client().add(solr_documents_collection(), [update])

    # graph: a CURATED edge records the human decision beside the machine edge
    try:
        node_kind = (
            "Concept"
            if (
                p.params.get("kind", "concept") == "concept" and p.kind != Proposal.Kind.LINK_ENTITY
            )
            else "Entity"
        )
        key_prop = "iri" if node_kind == "Concept" else "key"
        target = (
            str(p.params.get("iri") or p.target)
            if p.kind == Proposal.Kind.LINK_ENTITY
            else p.target
        )
        graph_client().execute(
            f"MATCH (d:Document {{id: $doc}}) MERGE (x:{node_kind} {{{key_prop}: $target}}) "
            "MERGE (d)-[c:CURATED]->(x) "
            "SET c.decision = $decision, c.by = $by, c.at = $at, c.note = $note",
            doc=doc.id,
            target=target,
            decision="reject"
            if p.kind in (Proposal.Kind.REJECT_TAG, Proposal.Kind.REMOVE_TAG)
            else "accept",
            by=getattr(p.decided_by, "username", None),
            at=timezone.now().isoformat(),
            note=p.reason,
        )
    except Exception:  # noqa: BLE001
        log.exception("graph CURATED edge failed for %s", doc.id)


def queue_reviews(doc: Document) -> int:
    """Create review items for a freshly indexed document; returns how many are open."""

    created = 0
    curated = doc.fields.get("curated") or {}
    for tag in doc.tags:
        iri = str(tag.get("iri") or "")
        if not iri or iri in curated:
            continue
        confidence = float(tag.get("confidence") or 0)
        is_ner = tag.get("stage") == "ner" and tag.get("pack_id") is None
        candidates = _candidates(tag) if is_ner else []
        wanted = candidates if is_ner else confidence < REVIEW_THRESHOLD
        if wanted:
            kind = Review.Kind.LINK if is_ner else Review.Kind.TAG
            _, was_created = Review.objects.get_or_create(
                document=doc,
                kind=kind,
                target=iri,
                defaults={
                    "collection": doc.collection,
                    "label": str(tag.get("label") or ""),
                    "payload": {
                        "stage": tag.get("stage"),
                        "spans": len(tag.get("spans") or []),
                        "candidates": candidates,
                    },
                    "confidence": confidence,
                },
            )
            created += was_created
    dismissed = set(doc.fields.get("dismissed_violations") or [])
    for v in doc.fields.get("shacl_violations") or []:
        target = f"{v.get('path')}|{v.get('message')}"[:500]
        if target in dismissed:
            continue
        _, was_created = Review.objects.get_or_create(
            document=doc,
            kind=Review.Kind.VIOLATION,
            target=target,
            defaults={
                "collection": doc.collection,
                "label": str(v.get("message") or "")[:500],
                "payload": v,
            },
        )
        created += was_created
    return created


def _resembles_individual(tag: dict[str, Any]) -> bool:
    return bool(_candidates(tag))


def _candidates(tag: dict[str, Any]) -> list[dict[str, str]]:
    """Pack individuals sharing a word with an unlinked NER entity (cheap, for the queue)."""

    from oss_server.knowledge.models import Concept, Pack

    label = str(tag.get("label") or "")
    words = [w for w in label.split() if len(w) > 3]
    if not words or tag.get("pack_id"):
        return []
    qs = Concept.objects.filter(
        pack__status=Pack.Status.ACTIVE, kind=Concept.Kind.INDIVIDUAL, taggable=True
    )
    out: list[dict[str, str]] = []
    for word in words[:2]:
        for c in qs.filter(pref_label__icontains=word)[:3]:
            out.append({"iri": c.iri, "label": c.pref_label, "type": c.type_label})
    return out[:5]
