"""W3C Web Annotations (parity row ANN-07): export notes and decisions, import annotations.

GET  /api/annotations/w3c          an AnnotationPage of a document, a collection or every
                                   readable collection (reader)
POST /api/annotations/w3c/import   W3C annotations or a Hypothesis export into one
                                   collection (curator), as notes on the matching documents

Imported annotations are notes like any other: each one is recorded in the
decision log (an `add_note` proposal) with what the import knew about the
original (its id, creator and creation time), which also makes a re-import
skip what is already there.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import unquote, urlencode

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError

from oss_server.api.access import check, read_scope
from oss_server.catalogue.models import Collection, Document
from oss_server.curation.models import Annotation, Proposal
from oss_server.curation.services import propose
from oss_server.exports import ids, w3c
from oss_server.exports.load import secret_lookup, site_of

PAGE_SIZE = 100
MAX_IMPORT = 5000  # annotations per request
MAX_IMPORT_BYTES = 20 * 1024 * 1024  # an uploaded file; a JSON body is capped by Django (2.5 MB)
MAX_REPORTED = 50


class ImportProblem(Schema):
    id: str
    reason: str


class ImportOut(Schema):
    received: int
    imported: int
    already_imported: int
    unmatched: int
    anchored: int  # imported with a passage found in the document's text
    unanchored: int  # imported as a note on the whole document
    dry_run: bool
    problems: list[ImportProblem]


def _scope_documents(
    request: HttpRequest, collection: str | None, document: str | None
) -> tuple[QuerySet[Document], str]:
    """The documents an export covers, after the read check, and a label for it."""

    if document:
        doc = get_object_or_404(Document.objects.select_related("collection"), pk=document)
        check(request, "reader", doc.collection.slug)
        return Document.objects.filter(pk=doc.pk), f"annotations on {doc.title or doc.uri}"
    allowed = read_scope(request, collection)
    qs = Document.objects.all()
    if collection:
        get_object_or_404(Collection, slug=collection)
        qs = qs.filter(collection__slug=collection)
        return qs, f"annotations in {collection}"
    if allowed is not None:
        qs = qs.filter(collection__slug__in=allowed)
    return qs, "annotations"


def export_annotations(
    request: HttpRequest,
    collection: str | None = None,
    document: str | None = None,
    page: int = 0,
    history: bool = False,
    decisions: bool = True,
) -> HttpResponse:
    """Notes, then curator decisions (the one in force per target unless `history`), as
    one page of a W3C AnnotationCollection (100 per page; follow `next`)."""

    docs, label = _scope_documents(request, collection, document)
    page = max(0, page)
    notes = w3c.notes_query(docs)
    decided = w3c.decisions_query(docs, history=history) if decisions else None
    n_notes = notes.count()
    n_decided = decided.count() if decided is not None else 0
    total = n_notes + n_decided
    start = page * PAGE_SIZE
    page_notes = list(notes[start : start + PAGE_SIZE]) if start < n_notes else []
    page_decided: list[Proposal] = []
    room = PAGE_SIZE - len(page_notes)
    if decided is not None and room > 0:
        offset = max(0, start - n_notes)
        page_decided = list(decided[offset : offset + room])
    site = site_of(request)
    items = w3c.annotations_for(
        page_notes,
        page_decided,
        site=site,
        source=lambda d: ids.safe_iri(d.uri),
        secret=secret_lookup(),
    )
    params = {
        k: v
        for k, v in {
            "collection": collection,
            "document": document,
            "history": "true" if history else None,
            "decisions": None if decisions else "false",
        }.items()
        if v
    }
    base = f"{site}/api/annotations/w3c"
    query = urlencode(params)
    collection_id = base + (f"?{query}" if query else "")

    def page_url(n: int) -> str:
        return base + "?" + urlencode({**params, "page": n})

    body: dict[str, Any] = {
        "@context": w3c.ANNO_CONTEXT,
        "id": page_url(page),
        "type": "AnnotationPage",
        "partOf": {
            "id": collection_id,
            "type": "AnnotationCollection",
            "label": label,
            "total": total,
        },
        "startIndex": start,
        "items": items,
    }
    if start + PAGE_SIZE < total:
        body["next"] = page_url(page + 1)
    if page > 0:
        body["prev"] = page_url(page - 1)
    return HttpResponse(
        json.dumps(body, ensure_ascii=False), content_type=w3c.CONTENT_TYPE, status=200
    )


def _payload(request: HttpRequest) -> Any:
    upload = request.FILES.get("file")
    if upload is not None:
        if upload.size and upload.size > MAX_IMPORT_BYTES:
            raise HttpError(413, f"files up to {MAX_IMPORT_BYTES // (1024 * 1024)} MB")
        raw = upload.read(MAX_IMPORT_BYTES + 1)
    elif (request.content_type or "").startswith("multipart/"):
        raise HttpError(422, "send the annotations as a JSON body or as a multipart `file`")
    else:
        raw = request.body
    if len(raw) > MAX_IMPORT_BYTES:
        raise HttpError(413, f"files up to {MAX_IMPORT_BYTES // (1024 * 1024)} MB")
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HttpError(422, f"not JSON: {exc}") from exc


def _match(items: list[w3c.Incoming], coll: Collection) -> dict[str, Document]:
    """Documents of the collection the annotations' sources name, keyed by source."""

    # an exported source is the document's URI made a valid IRI: match it either way
    wanted = {i.source for i in items} | {unquote(i.source) for i in items}
    by_uri: dict[str, Document] = {}
    for d in Document.objects.filter(collection=coll, uri__in=wanted).exclude(
        status=Document.Status.DELETED
    ):
        by_uri[d.uri] = by_uri[ids.safe_iri(d.uri)] = d
    keys = {k for i in items for k in (w3c.document_key(i.source), w3c.document_key(i.scope)) if k}
    by_id = {
        d.id: d
        for d in Document.objects.filter(collection=coll, pk__in=keys).exclude(
            status=Document.Status.DELETED
        )
    }
    out: dict[str, Document] = {}
    for i in items:
        doc = by_uri.get(i.source) or by_uri.get(unquote(i.source))
        for key in (w3c.document_key(i.source), w3c.document_key(i.scope)):
            if doc is None and key:
                doc = by_id.get(key)
        if doc is not None:
            out[i.id] = doc
    return out


def _already_imported(coll: Collection) -> set[tuple[str, str]]:
    """(document id, original id) of imports whose note still exists."""

    rows = Proposal.objects.filter(
        collection=coll, kind=Proposal.Kind.ADD_NOTE, params__has_key="imported"
    ).values_list("document_id", "target", "params")
    pks: dict[int, tuple[str, str]] = {}
    for doc_id, target, params in rows:
        original = ((params or {}).get("imported") or {}).get("id")
        if doc_id and original and str(target).startswith("annotation:"):
            try:
                pks[int(str(target).split(":", 1)[1])] = (str(doc_id), str(original))
            except ValueError:
                continue
    alive = set(Annotation.objects.filter(pk__in=list(pks)).values_list("pk", flat=True))
    return {pair for pk, pair in pks.items() if pk in alive}


def import_annotations(request: HttpRequest, collection: str, dry_run: bool = False) -> ImportOut:
    """Import W3C Web Annotations or a Hypothesis export as notes on the documents of one
    collection, matched by target URI. JSON body, or a multipart `file` up to 20 MB."""

    coll = get_object_or_404(Collection, slug=collection)
    check(request, "curator", coll.slug)
    raw_items = w3c.annotation_items(_payload(request))
    if len(raw_items) > MAX_IMPORT:
        raise HttpError(413, f"at most {MAX_IMPORT} annotations per request")
    problems: list[ImportProblem] = []
    items: list[w3c.Incoming] = []
    for raw in raw_items:
        item = w3c.parse(raw)
        if item is None:
            problems.append(ImportProblem(id=str(raw.get("id", "?")), reason="no target"))
        else:
            items.append(item)
    matched = _match(items, coll)
    done = _already_imported(coll)
    out = ImportOut(
        received=len(raw_items),
        imported=0,
        already_imported=0,
        unmatched=len(raw_items) - len(items),
        anchored=0,
        unanchored=0,
        dry_run=dry_run,
        problems=[],
    )
    user = request.user if isinstance(request.user, User) else None
    seen: set[tuple[str, str]] = set()
    for item in items:
        doc = matched.get(item.id)
        if doc is None:
            out.unmatched += 1
            problems.append(ImportProblem(id=item.id, reason=f"no document with URI {item.source}"))
            continue
        key = (doc.id, item.id)
        if key in done or key in seen:
            out.already_imported += 1
            continue
        seen.add(key)
        span = w3c.locate(doc.text, item)
        if span is None and item.exact:
            problems.append(
                ImportProblem(id=item.id, reason="quote not found; imported for the whole document")
            )
        if span:
            out.anchored += 1
        else:
            out.unanchored += 1
        out.imported += 1
        if dry_run:
            continue
        with transaction.atomic():
            note = Annotation.objects.create(
                document=doc, author=user, text=item.text, tags=item.tags, span=span
            )
            propose(
                coll,
                Proposal.Kind.ADD_NOTE,
                f"annotation:{note.id}",
                params={
                    "text": item.text,
                    "tags": item.tags,
                    "span": span,
                    "imported": {
                        "id": item.id,
                        "format": item.format,
                        "source": item.source,
                        "creator": item.creator,
                        "created": item.created,
                    },
                },
                reason="imported Web Annotation",
                document=doc,
                user=user,
            )
    out.problems = problems[:MAX_REPORTED]
    return out


def mount(router: Router) -> None:
    router.get("/annotations/w3c", tags=["annotations"])(export_annotations)
    router.post("/annotations/w3c/import", response=ImportOut, tags=["annotations"])(
        import_annotations
    )
