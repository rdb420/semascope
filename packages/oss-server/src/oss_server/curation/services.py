"""Curation logic: emit events, record proposals, apply decisions to the stores."""

from __future__ import annotations

import logging
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from oss_pipeline.provenance import curated_tags
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


ACCEPTS = ("accept_tag", "add_tag")
REJECTS = ("reject_tag", "remove_tag")


def _decision(kind: str) -> str:
    """What a proposal decides about its target: accept, reject, or its own kind."""

    return "accept" if kind in ACCEPTS else "reject" if kind in REJECTS else kind


TAG_KINDS = ("accept_tag", "reject_tag", "add_tag", "remove_tag", "link_entity")
SNAPSHOT_SPANS = 200  # spans kept in a decision's snapshot of the tag; span_count has them all


def _decision_params(params: dict[str, Any]) -> dict[str, Any]:
    """The params that say what was decided (not the snapshot of the evidence)."""

    return {k: v for k, v in params.items() if k != "tag"}


def tag_snapshot(document: Document | None, iri: str) -> dict[str, Any] | None:
    """The document's tag for an IRI as it is now, for the decision log; None if absent."""

    if document is None:
        return None
    for t in document.tags:
        if t.get("iri") == iri:
            spans = list(t.get("spans") or [])
            return {
                **{k: v for k, v in t.items() if k != "spans"},
                "spans": spans[:SNAPSHOT_SPANS],
                "span_count": len(spans),
            }
    return None


def pack_of(collection: Collection, iri: str) -> tuple[str, str]:
    """The active pack version that defines an IRI, by the collection's precedence."""

    from oss_server.knowledge.models import Concept, Pack
    from oss_server.knowledge.precedence import pack_order, preferred

    rows = Concept.objects.filter(iri=iri, pack__status=Pack.Status.ACTIVE).select_related("pack")
    best = preferred(rows, pack_order(collection))
    return (best.pack.pack_id, best.pack.version) if best is not None else ("", "")


def latest_proposal(
    collection: Collection, target: str, document: Document | None = None
) -> Proposal | None:
    """The decision currently in force for a target (on a document): the newest one."""

    return (
        Proposal.objects.filter(collection=collection, document=document, target=target)
        .order_by("-pk")
        .first()
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
    """Record a decision, apply it to the stores, and put it in the outbox.

    The proposal log is append-only and the newest decision on a target wins, so
    accept, reject, accept leaves three proposals and the target accepted. Each
    decision has its own `external_ref`, chained to the one it supersedes, so the
    outbox carries one stable reference per decision. Repeating the decision that
    is already in force (a retried request, a double click) records nothing and
    returns that proposal, applying it again if a store was down the first time.

    A decision on a tag keeps the tag as it was (`params["tag"]`: stage, confidence,
    provenance and up to 200 spans), and `pack_id`/`pack_version` default to the
    tag's pack version (for a link, the individual's).
    """

    params = dict(params or {})
    doc_id = document.id if document else None
    if kind in TAG_KINDS and "tag" not in params:
        snapshot = tag_snapshot(document, target)
        if snapshot is not None:
            params["tag"] = snapshot
    if kind in TAG_KINDS and not pack_id:
        # the pack version of what is decided: the linked individual, else the tag itself
        iri = str(params.get("iri") or "") if kind == Proposal.Kind.LINK_ENTITY else target
        tag = params.get("tag") if kind != Proposal.Kind.LINK_ENTITY else None
        if tag and tag.get("pack_id"):
            pack_id, pack_version = str(tag["pack_id"]), str(tag.get("pack_version") or "")
        elif iri:
            pack_id, pack_version = pack_of(collection, iri)
    previous = latest_proposal(collection, target, document)
    if (
        previous is not None
        and _decision(previous.kind) == _decision(kind)
        and _decision_params(previous.params) == _decision_params(params)
    ):
        if not previous.applied:
            _apply(previous)
        return previous
    ref = Proposal.make_ref(
        collection.slug,
        kind,
        target,
        params,
        doc_id,
        previous=previous.external_ref if previous else "",
    )
    try:
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
                external_ref=ref,
            )
            _apply(proposal)
            emit(
                f"proposal.{kind}",
                collection=collection.slug,
                subject=target,
                payload={
                    "proposal_id": proposal.pk,
                    "external_ref": proposal.external_ref,
                    "supersedes": previous.external_ref if previous else None,
                    "document_id": doc_id,
                    "params": params,
                    "reason": reason,
                    "pack_id": pack_id,
                    "pack_version": pack_version,
                    "decided_by": getattr(proposal.decided_by, "username", None),
                    "decided_at": proposal.decided_at.isoformat(),
                },
            )
    except IntegrityError:
        # the same decision arrived twice at once: the other request recorded it
        return Proposal.objects.get(external_ref=ref)
    return proposal


def _apply(proposal: Proposal) -> None:
    """Apply a proposal, recording whether it reached the stores."""

    try:
        apply_proposal(proposal)
        applied = True
    except Exception:  # noqa: BLE001 - the decision is recorded even if a store is down
        log.exception("could not apply proposal %s", proposal.pk)
        applied = False
    if proposal.applied != applied:
        proposal.applied = applied
        proposal.save(update_fields=["applied"])


def apply_proposal(p: Proposal) -> None:
    """Make the decision real in Postgres, Solr and the graph. Idempotent."""

    from oss_server.services import graph_client, solr_client, solr_documents_collection

    if p.document is None:
        return
    doc = p.document
    tags: list[dict[str, Any]] = list(doc.tags)
    before = {str(t.get("iri")): t for t in tags}
    curated: dict[str, Any] = dict(doc.fields.get("curated") or {})
    # a curator's link survives later decisions on the individual
    kept = {k: v for k, v in (curated.get(p.target) or {}).items() if k == "linked_from"}
    if p.kind in (Proposal.Kind.ACCEPT_TAG, Proposal.Kind.ADD_TAG):
        curated[p.target] = {
            "decision": "accept",
            "label": p.params.get("label", ""),
            "kind": p.params.get("kind", "concept"),
            **kept,
        }
        if p.pack_id:
            curated[p.target].update(pack_id=p.pack_id, pack_version=p.pack_version or None)
        # accepting a tag an earlier decision removed puts it back
        if not any(t.get("iri") == p.target for t in tags):
            tags.append(
                {
                    "iri": p.target,
                    "label": p.params.get("label") or p.target,
                    "kind": p.params.get("kind", "concept"),
                    "stage": "curated",
                    "confidence": 1.0,
                    "spans": [],
                    "pack_id": p.pack_id or None,
                    "pack_version": p.pack_version or None,
                }
            )
    elif p.kind in (Proposal.Kind.REJECT_TAG, Proposal.Kind.REMOVE_TAG):
        # the machine's tag stays in the record, flagged by the decision; Solr and Neo4j
        # leave it out (`curated_tags`). A tag only a curator had added has no evidence
        # to keep: it goes (its proposal keeps it).
        curated[p.target] = {"decision": "reject", **kept}
        tags = [t for t in tags if t.get("iri") != p.target or t.get("stage") != "curated"]
    elif p.kind == Proposal.Kind.LINK_ENTITY:
        # the NER tag stays as the machine found it; the curated view points it at the
        # individual (`curated_tags`), here and after every reindex
        new_iri = str(p.params.get("iri") or "")
        old = before.get(p.target) or {}
        curated[new_iri] = {
            "decision": "accept",
            "linked_from": p.target,
            "label": str(p.params.get("label") or old.get("label") or new_iri),
            "kind": "entity",
            "pack_id": p.pack_id or None,
            "pack_version": p.pack_version or None,
        }
    elif p.kind == Proposal.Kind.DISMISS_VIOLATION:
        dismissed = set(doc.fields.get("dismissed_violations") or [])
        dismissed.add(p.target)
        doc.fields["dismissed_violations"] = sorted(dismissed)
    else:
        return
    doc.tags = tags
    doc.fields["curated"] = curated
    doc.save(update_fields=["tags", "fields", "updated_at"])

    # Solr: atomic update of the tag facets from the curated view, as the index stage does
    effective = curated_tags(tags, curated)
    concept = [t for t in effective if t.kind == "concept"]
    linked = [t for t in effective if t.kind == "entity" and t.pack_id]
    update = {
        "id": doc.id,
        "concept_iris_ss": {"set": sorted({t.iri for t in concept})},
        "concept_labels_ss": {"set": sorted({t.label for t in concept})},
        "entity_iris_ss": {"set": sorted({t.iri for t in linked})},
        "entity_labels_ss": {"set": sorted({t.label for t in linked})},
        "pack_versions_ss": {
            "set": sorted({f"{t.pack_id}@{t.pack_version}" for t in effective if t.pack_id})
        },
        "curated_ss": {"set": sorted(f"{k}:{v['decision']}" for k, v in curated.items())},
    }
    solr_client().add(solr_documents_collection(), [update])

    # graph: a CURATED edge records the human decision beside the machine edge
    node = curated_node(p, doc, before)
    if node is None:
        return
    try:
        node_kind, key_prop, key = node
        graph_client().execute(
            f"MATCH (d:Document {{id: $doc}}) MERGE (x:{node_kind} {{{key_prop}: $target}}) "
            "MERGE (d)-[c:CURATED]->(x) "
            "SET c.decision = $decision, c.by = $by, c.at = $at, c.note = $note",
            doc=doc.id,
            target=key,
            decision="reject"
            if p.kind in (Proposal.Kind.REJECT_TAG, Proposal.Kind.REMOVE_TAG)
            else "accept",
            by=getattr(p.decided_by, "username", None),
            at=timezone.now().isoformat(),
            note=p.reason,
        )
    except Exception:  # noqa: BLE001
        log.exception("graph CURATED edge failed for %s", doc.id)


def curated_node(
    p: Proposal, doc: Document, before: dict[str, dict[str, Any]]
) -> tuple[str, str, str] | None:
    """The graph node a decision is about: (label, key property, key), or None for none.

    Concepts are keyed by IRI. Entities are keyed exactly as the graph stage keys
    them (`entity_key`): the IRI for pack individuals, `<Class>/<label>` for NER
    entities, or its HMAC when the collection pseudonymises, so the CURATED edge
    lands on the node the MENTIONS edge points at.
    """

    from oss_core.contracts import Tag
    from oss_pipeline.stages.graph import entity_key
    from oss_server.pipeline.tasks import pseudonymise_secret

    if p.kind == Proposal.Kind.LINK_ENTITY:
        return "Entity", "key", str(p.params.get("iri") or p.target)
    if p.kind == Proposal.Kind.DISMISS_VIOLATION:
        return None  # a dismissed SHACL violation is not a node in the projection
    if p.params.get("kind", "concept") == "concept":
        return "Concept", "iri", p.target
    old = before.get(p.target) or {}
    tag = Tag(
        iri=p.target,
        label=str(old.get("label") or p.params.get("label") or p.target),
        kind="entity",
        pack_id=old.get("pack_id") or p.pack_id or None,
        stage=str(old.get("stage") or "curated"),
    )
    return "Entity", "key", entity_key(tag, pseudonymise_secret(doc.collection))


def queue_reviews(doc: Document) -> int:
    """Create review items for a freshly indexed document; returns how many are open."""

    created = 0
    curated = doc.fields.get("curated") or {}
    linked = {v.get("linked_from") for v in curated.values() if isinstance(v, dict)}
    for tag in doc.tags:
        iri = str(tag.get("iri") or "")
        if not iri or iri in curated or iri in linked:
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
    """Pack individuals whose label contains, or is contained in, an unlinked NER entity."""

    from oss_server.knowledge.models import Concept, Pack

    label = " ".join(str(tag.get("label") or "").lower().split())
    if len(label) < 4 or tag.get("pack_id"):
        return []
    words = [w for w in label.split() if len(w) > 3]
    if not words:
        return []
    qs = Concept.objects.filter(
        pack__status=Pack.Status.ACTIVE, kind=Concept.Kind.INDIVIDUAL, taggable=True
    )
    out: list[dict[str, str]] = []
    for word in words[:2]:
        for c in qs.filter(pref_label__icontains=word)[:5]:
            names = [" ".join(x.lower().split()) for x in c.labels]
            if any(n in label or label in n for n in names if len(n) >= 4):
                if all(o["iri"] != c.iri for o in out):
                    out.append({"iri": c.iri, "label": c.pref_label, "type": c.type_label})
    return out[:5]
