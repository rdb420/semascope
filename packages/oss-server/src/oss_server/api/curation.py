"""Curation API: review queue, decisions, annotations, proposals, the outbox and webhooks."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError

from oss_server.api.access import check, read_scope, role_for
from oss_server.catalogue.models import Collection, Document
from oss_server.curation.models import Annotation, Event, Proposal, Review, Webhook
from oss_server.curation.services import propose

router = Router(tags=["curation"])


class ReviewOut(Schema):
    id: int
    collection: str
    document_id: str | None
    document_title: str
    kind: str
    target: str
    label: str
    payload: dict[str, Any]
    confidence: float | None
    status: str
    note: str
    created_at: datetime


class DecisionIn(Schema):
    decision: str  # accept | reject | defer
    note: str = ""
    link_to: str | None = None  # for link reviews: the individual's IRI to link the entity to
    label: str | None = None


class ProposalOut(Schema):
    id: int
    collection: str
    document_id: str | None
    kind: str
    target: str
    params: dict[str, Any]
    reason: str
    pack_id: str
    pack_version: str
    decided_by: str | None
    decided_at: datetime
    external_ref: str
    applied: bool


class AnnotationIn(Schema):
    text: str = ""
    tags: list[str] = []
    span: dict[str, int] | None = None


class AnnotationOut(Schema):
    id: int
    document_id: str
    author: str | None
    text: str
    tags: list[str]
    span: dict[str, int] | None
    created_at: datetime


class TagDecisionIn(Schema):
    iri: str
    decision: str  # accept | reject
    label: str = ""
    kind: str = "concept"
    reason: str = ""


class EventOut(Schema):
    id: int
    kind: str
    collection: str
    subject: str
    payload: dict[str, Any]
    created_at: datetime


class WebhookIn(Schema):
    url: str
    kinds: list[str] = []
    secret: str = ""
    enabled: bool = True


class WebhookOut(Schema):
    id: int
    url: str
    kinds: list[str]
    enabled: bool
    last_event_id: int
    last_error: str


def _user_or_none(request: HttpRequest) -> Any:
    user = getattr(request, "user", None)
    return user if user is not None and getattr(user, "is_authenticated", False) else None


def _review_out(r: Review) -> ReviewOut:
    return ReviewOut(
        id=r.id,
        collection=r.collection.slug,
        document_id=r.document_id,
        document_title=(r.document.title or r.document.uri) if r.document else "",
        kind=r.kind,
        target=r.target,
        label=r.label,
        payload=r.payload,
        confidence=r.confidence,
        status=r.status,
        note=r.note,
        created_at=r.created_at,
    )


def _proposal_out(p: Proposal) -> ProposalOut:
    return ProposalOut(
        id=p.id,
        collection=p.collection.slug,
        document_id=p.document_id,
        kind=p.kind,
        target=p.target,
        params=p.params,
        reason=p.reason,
        pack_id=p.pack_id,
        pack_version=p.pack_version,
        decided_by=getattr(p.decided_by, "username", None),
        decided_at=p.decided_at,
        external_ref=p.external_ref,
        applied=p.applied,
    )


# --- reviews ----------------------------------------------------------------------------------


@router.get("/reviews", response=list[ReviewOut])
def list_reviews(
    request: HttpRequest,
    collection: str | None = None,
    kind: str | None = None,
    status: str = "open",
    limit: int = 50,
) -> list[ReviewOut]:
    qs = Review.objects.select_related("collection", "document")
    allowed = read_scope(request, collection)
    if allowed is not None:
        qs = qs.filter(collection__slug__in=allowed)
    if collection:
        qs = qs.filter(collection__slug=collection)
    if kind:
        qs = qs.filter(kind=kind)
    if status:
        qs = qs.filter(status=status)
    return [_review_out(r) for r in qs[: min(limit, 500)]]


@router.get("/reviews/summary", response=dict[str, int])
def review_summary(request: HttpRequest, collection: str | None = None) -> dict[str, int]:
    from django.db.models import Count

    qs = Review.objects.filter(status=Review.Status.OPEN)
    allowed = read_scope(request, collection)
    if allowed is not None:
        qs = qs.filter(collection__slug__in=allowed)
    if collection:
        qs = qs.filter(collection__slug=collection)
    return {row["kind"]: row["n"] for row in qs.values("kind").annotate(n=Count("id"))}


@router.post("/reviews/{review_id}/decision", response=ReviewOut)
def decide(request: HttpRequest, review_id: int, payload: DecisionIn) -> ReviewOut:
    """Accept, reject or defer a review item. Accept and reject become proposals.

    An open or deferred item can be decided; an accepted or rejected one answers 409.
    """

    r = get_object_or_404(Review.objects.select_related("collection", "document"), pk=review_id)
    check(request, "curator", r.collection.slug)
    if r.status not in (Review.Status.OPEN, Review.Status.DEFERRED):
        raise HttpError(409, f"review {r.pk} is already {r.status}")
    if payload.decision not in ("accept", "reject", "defer"):
        raise HttpError(422, "decision must be accept, reject or defer")
    if payload.decision == "defer":
        r.status = Review.Status.DEFERRED
    else:
        r.status = (
            Review.Status.ACCEPTED if payload.decision == "accept" else Review.Status.REJECTED
        )
        if r.document is not None:
            if r.kind == Review.Kind.VIOLATION:
                if payload.decision == "accept":
                    propose(
                        r.collection,
                        Proposal.Kind.DISMISS_VIOLATION,
                        r.target,
                        reason=payload.note,
                        document=r.document,
                        user=request.user,
                    )
            elif r.kind == Review.Kind.LINK and payload.decision == "accept":
                if not payload.link_to:
                    raise HttpError(
                        422, "link_to (an individual's IRI) is required to accept a link review"
                    )
                propose(
                    r.collection,
                    Proposal.Kind.LINK_ENTITY,
                    r.target,
                    params={"iri": payload.link_to, "label": payload.label or ""},
                    reason=payload.note,
                    document=r.document,
                    user=request.user,
                )
            else:
                kind = (
                    Proposal.Kind.ACCEPT_TAG
                    if payload.decision == "accept"
                    else Proposal.Kind.REJECT_TAG
                )
                propose(
                    r.collection,
                    kind,
                    r.target,
                    params={
                        "label": r.label,
                        "kind": "concept"
                        if r.kind == Review.Kind.TAG
                        and "#" in r.target
                        and "/" not in r.target.rsplit("#", 1)[-1]
                        else "entity",
                    },
                    reason=payload.note,
                    document=r.document,
                    user=request.user,
                )
    r.note = payload.note
    r.decided_by = _user_or_none(request)
    r.decided_at = timezone.now()
    r.save()
    return _review_out(r)


# --- document tags and notes -----------------------------------------------------------------


@router.get("/documents/{doc_id}/tags", response=list[dict[str, Any]])
def document_tags(request: HttpRequest, doc_id: str) -> list[dict[str, Any]]:
    d = get_object_or_404(Document.objects.select_related("collection"), pk=doc_id)
    check(request, "reader", d.collection.slug)
    curated = d.fields.get("curated") or {}
    out = []
    for t in d.tags:
        out.append(
            {
                **{k: v for k, v in t.items() if k != "spans"},
                "span_count": len(t.get("spans") or []),
                "curated": curated.get(t.get("iri")),
            }
        )
    return out


@router.post("/documents/{doc_id}/tags", response=ProposalOut)
def decide_tag(request: HttpRequest, doc_id: str, payload: TagDecisionIn) -> ProposalOut:
    """Accept, reject or add a tag on a document directly (no review item needed)."""

    d = get_object_or_404(Document.objects.select_related("collection"), pk=doc_id)
    check(request, "curator", d.collection.slug)
    present = any(t.get("iri") == payload.iri for t in d.tags)
    if payload.decision == "accept":
        kind = Proposal.Kind.ACCEPT_TAG if present else Proposal.Kind.ADD_TAG
    elif payload.decision == "reject":
        kind = Proposal.Kind.REJECT_TAG if present else Proposal.Kind.REMOVE_TAG
    else:
        raise HttpError(422, "decision must be accept or reject")
    p = propose(
        d.collection,
        kind,
        payload.iri,
        params={"label": payload.label, "kind": payload.kind},
        reason=payload.reason,
        document=d,
        user=request.user,
    )
    Review.objects.filter(document=d, target=payload.iri, status=Review.Status.OPEN).update(
        status=Review.Status.ACCEPTED if payload.decision == "accept" else Review.Status.REJECTED,
        decided_at=timezone.now(),
    )
    return _proposal_out(p)


@router.get("/documents/{doc_id}/annotations", response=list[AnnotationOut])
def list_annotations(request: HttpRequest, doc_id: str) -> list[AnnotationOut]:
    d = get_object_or_404(Document.objects.select_related("collection"), pk=doc_id)
    check(request, "reader", d.collection.slug)
    return [
        AnnotationOut(
            id=a.id,
            document_id=a.document_id,
            author=getattr(a.author, "username", None),
            text=a.text,
            tags=a.tags,
            span=a.span,
            created_at=a.created_at,
        )
        for a in Annotation.objects.filter(document_id=doc_id).select_related("author")
    ]


@router.post("/documents/{doc_id}/annotations", response={201: AnnotationOut})
def add_annotation(
    request: HttpRequest, doc_id: str, payload: AnnotationIn
) -> tuple[int, AnnotationOut]:
    d = get_object_or_404(Document.objects.select_related("collection"), pk=doc_id)
    check(request, "curator", d.collection.slug)
    a = Annotation.objects.create(
        document=d,
        author=_user_or_none(request),
        text=payload.text,
        tags=payload.tags,
        span=payload.span,
    )
    propose(
        d.collection,
        Proposal.Kind.ADD_NOTE,
        f"annotation:{a.id}",
        params={"text": payload.text, "tags": payload.tags, "span": payload.span},
        document=d,
        user=request.user,
    )
    return 201, AnnotationOut(
        id=a.id,
        document_id=a.document_id,
        author=getattr(a.author, "username", None),
        text=a.text,
        tags=a.tags,
        span=a.span,
        created_at=a.created_at,
    )


@router.delete("/annotations/{annotation_id}", response={204: None})
def delete_annotation(request: HttpRequest, annotation_id: int) -> tuple[int, None]:
    """Curators of the document's collection may delete any note; authors their own."""

    a = get_object_or_404(
        Annotation.objects.select_related("document__collection"), pk=annotation_id
    )
    user = _user_or_none(request)
    slug = a.document.collection.slug
    own = user is not None and a.author_id is not None and a.author_id == user.pk
    if not (own and role_for(user, slug)):
        check(request, "curator", slug)
    a.delete()
    return 204, None


# --- proposals, events, webhooks ---------------------------------------------------------------


@router.get("/proposals", response=list[ProposalOut])
def list_proposals(
    request: HttpRequest,
    collection: str | None = None,
    kind: str | None = None,
    since_id: int = 0,
    limit: int = 100,
) -> list[ProposalOut]:
    qs = (
        Proposal.objects.select_related("collection", "decided_by")
        .filter(pk__gt=since_id)
        .order_by("pk")
    )
    allowed = read_scope(request, collection)
    if allowed is not None:
        qs = qs.filter(collection__slug__in=allowed)
    if collection:
        qs = qs.filter(collection__slug=collection)
    if kind:
        qs = qs.filter(kind=kind)
    return [_proposal_out(p) for p in qs[: min(limit, 1000)]]


@router.get("/events", response=list[EventOut])
def list_events(
    request: HttpRequest,
    since_id: int = 0,
    kind: str | None = None,
    collection: str | None = None,
    limit: int = 200,
) -> list[EventOut]:
    """The outbox, by cursor: pass the last id you saw as since_id."""

    qs = Event.objects.filter(pk__gt=since_id)
    allowed = read_scope(request, collection)
    if allowed is not None:
        # events without a collection (alerts over every collection) need read access to all
        qs = qs.filter(collection__in=allowed)
    if kind:
        qs = qs.filter(kind__startswith=kind)
    if collection:
        qs = qs.filter(collection=collection)
    return [
        EventOut(
            id=e.id,
            kind=e.kind,
            collection=e.collection,
            subject=e.subject,
            payload=e.payload,
            created_at=e.created_at,
        )
        for e in qs[: min(limit, 1000)]
    ]


@router.get("/webhooks", response=list[WebhookOut])
def list_webhooks(request: HttpRequest) -> list[WebhookOut]:
    if not getattr(request.user, "is_staff", False):
        raise HttpError(403, "staff only")
    return [
        WebhookOut(
            id=w.id,
            url=w.url,
            kinds=w.kinds,
            enabled=w.enabled,
            last_event_id=w.last_event_id,
            last_error=w.last_error,
        )
        for w in Webhook.objects.all()
    ]


@router.post("/webhooks", response={201: WebhookOut})
def add_webhook(request: HttpRequest, payload: WebhookIn) -> tuple[int, WebhookOut]:
    if not getattr(request.user, "is_staff", False):
        raise HttpError(403, "staff only")
    w = Webhook.objects.create(
        url=payload.url,
        kinds=payload.kinds,
        secret=payload.secret,
        enabled=payload.enabled,
        last_event_id=Event.objects.order_by("-pk").values_list("pk", flat=True).first() or 0,
    )
    return 201, WebhookOut(
        id=w.id,
        url=w.url,
        kinds=w.kinds,
        enabled=w.enabled,
        last_event_id=w.last_event_id,
        last_error=w.last_error,
    )


@router.delete("/webhooks/{webhook_id}", response={204: None})
def delete_webhook(request: HttpRequest, webhook_id: int) -> tuple[int, None]:
    if not getattr(request.user, "is_staff", False):
        raise HttpError(403, "staff only")
    get_object_or_404(Webhook, pk=webhook_id).delete()
    return 204, None


__all__ = ["router", "Collection"]
