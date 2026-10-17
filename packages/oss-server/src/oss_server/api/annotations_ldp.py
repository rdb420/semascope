"""The W3C Web Annotation Protocol's per-annotation operations (parity row ANN-07).

`/api/annotations/w3c` is the annotation container (an LDP basic container):
`GET` pages through it (`annotations_w3c.py`), and this module adds

- `POST   /annotations/w3c`        create one annotation: a note on the document its
                                   target names (curator on its collection)
- `GET    /annotations/w3c/{id}`   one annotation, with its ETag (reader)
- `PUT    /annotations/w3c/{id}`   replace its body and selector (`If-Match` required;
                                   curator, or the note's author)
- `DELETE /annotations/w3c/{id}`   delete it (`If-Match` required; curator or author)

An annotation is a curator's note: its `id` is its URL in the container and its
`canonical` IRI is the `urn:semascope:annotation:` name the exports use. A
target names a document by its `urn:semascope:doc:` IRI, its page in the UI, or
its original URI (with `collection` when that URI is in several collections).
Creating and replacing an annotation record an `add_note` decision, as notes made
in the UI do. ETags change whenever the note does, so a client that read an old
version gets 412 rather than overwriting someone else's edit.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import unquote

from django.contrib.auth.models import User
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from ninja import Router
from ninja.errors import HttpError

from oss_server.api.access import check, read_scope, role_for
from oss_server.catalogue.models import Collection, Document
from oss_server.curation.models import Annotation, Proposal
from oss_server.curation.services import propose
from oss_server.exports import ids, w3c
from oss_server.exports.load import site_of

MAX_BYTES = 1024 * 1024
ALLOW_ONE = "GET, PUT, DELETE, HEAD, OPTIONS"
RESOURCE_LINK = '<http://www.w3.org/ns/ldp#Resource>; rel="type"'


def etag(a: Annotation) -> str:
    state = json.dumps(
        [a.pk, a.updated_at.isoformat() if a.updated_at else "", a.text, a.tags, a.span],
        sort_keys=True,
        default=str,
    )
    return '"' + hashlib.sha256(state.encode()).hexdigest()[:32] + '"'


def annotation_url(site: str, pk: int) -> str:
    return f"{site}/api/annotations/w3c/{pk}"


def representation(request: HttpRequest, a: Annotation) -> dict[str, Any]:
    doc = a.document
    site = site_of(request)
    origins = w3c.import_origins([a])
    body = w3c.note_annotation(
        a, doc, site=site, source=ids.safe_iri(doc.uri), origin=origins.get(a.pk)
    )
    return {
        "@context": w3c.ANNO_CONTEXT,
        **body,
        "id": annotation_url(site, a.pk),
        "canonical": ids.annotation_iri(a.pk),
    }


def _respond(request: HttpRequest, a: Annotation, status: int = 200) -> HttpResponse:
    resp = HttpResponse(
        json.dumps(representation(request, a), ensure_ascii=False),
        content_type=w3c.CONTENT_TYPE,
        status=status,
    )
    resp["ETag"] = etag(a)
    resp["Link"] = RESOURCE_LINK
    resp["Allow"] = ALLOW_ONE
    resp["Vary"] = "Accept"
    if status == 201:
        resp["Location"] = annotation_url(site_of(request), a.pk)
    return resp


def _body(request: HttpRequest) -> dict[str, Any]:
    if len(request.body) > MAX_BYTES:
        raise HttpError(413, "an annotation is at most 1 MB")
    try:
        raw = json.loads(request.body.decode("utf-8-sig") or "null")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HttpError(400, f"not JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise HttpError(400, "send one annotation as a JSON object")
    return raw


def _user(request: HttpRequest) -> User | None:
    user = getattr(request, "user", None)
    return user if isinstance(user, User) and user.is_authenticated else None


def _target_document(request: HttpRequest, item: w3c.Incoming, collection: str | None) -> Document:
    """The document an annotation's target names, after the curator check."""

    if collection:
        coll = get_object_or_404(Collection, slug=collection)
        check(request, "curator", coll.slug)
    docs = Document.objects.select_related("collection").exclude(status=Document.Status.DELETED)
    for key in (w3c.document_key(item.source), w3c.document_key(item.scope)):
        if key:
            doc = docs.filter(pk=key).first()
            if doc is not None and (not collection or doc.collection.slug == collection):
                check(request, "curator", doc.collection.slug)
                return doc
    candidates = docs.filter(uri__in={item.source, unquote(item.source)})
    if collection:
        candidates = candidates.filter(collection__slug=collection)
    else:
        readable = read_scope(request)
        if readable is not None:
            candidates = candidates.filter(collection__slug__in=readable)
    found = list(candidates[:2])
    if not found:
        raise HttpError(404, f"no document you may read has the target {item.source[:200]}")
    if len(found) > 1:
        raise HttpError(409, "the target's URI is in several collections; name one: ?collection=")
    check(request, "curator", found[0].collection.slug)
    return found[0]


def _may_edit(request: HttpRequest, a: Annotation) -> None:
    """Curators of the collection may change any note; authors their own."""

    user = _user(request)
    slug = a.document.collection.slug
    own = user is not None and a.author_id is not None and a.author_id == user.pk
    if not (own and role_for(user, slug)):
        check(request, "curator", slug)


_CODING = re.compile(r'-(?:zstd|gzip|br|deflate)"$')


def tags_of(header: str | None) -> set[str]:
    """The entity tags a conditional header names, compared weakly (`W/` dropped) and
    without the content-coding suffix a compressing proxy adds (Caddy turns `"x"` into
    `"x-zstd"` when it compresses the body)."""

    return {_CODING.sub('"', t.strip().removeprefix("W/")) for t in (header or "").split(",")}


def _precondition(request: HttpRequest, a: Annotation) -> None:
    wanted = request.headers.get("If-Match")
    if not wanted:
        raise HttpError(428, "send If-Match with the annotation's ETag")
    tags = tags_of(wanted)
    if "*" not in tags and etag(a) not in tags:
        raise HttpError(412, "the annotation has changed since that ETag")


def _get(pk: int) -> Annotation:
    return get_object_or_404(
        Annotation.objects.select_related("document__collection", "author"), pk=pk
    )


# audit: a note is a curation decision; propose() records it in the proposal log
def create_annotation(request: HttpRequest, collection: str | None = None) -> HttpResponse:
    """Create one annotation in the container: a note on the document its target names,
    anchored to the passage its quote or position selector gives."""

    raw = _body(request)
    item = w3c.parse(raw)
    if item is None:
        raise HttpError(400, "the annotation names no target")
    doc = _target_document(request, item, collection)
    span = w3c.locate(doc.text, item)
    user = _user(request)
    with transaction.atomic():
        a = Annotation.objects.create(
            document=doc, author=user, text=item.text, tags=item.tags, span=span
        )
        propose(
            doc.collection,
            Proposal.Kind.ADD_NOTE,
            f"annotation:{a.id}",
            params={"text": item.text, "tags": item.tags, "span": span},
            reason="Web Annotation Protocol",
            document=doc,
            user=user,
        )
    return _respond(request, _get(a.pk), 201)


def get_annotation(request: HttpRequest, annotation_id: int) -> HttpResponse:
    """One annotation of the container, with its ETag (304 when If-None-Match has it)."""

    a = _get(annotation_id)
    check(request, "reader", a.document.collection.slug)
    tag = etag(a)
    if tag in tags_of(request.headers.get("If-None-Match")):
        resp = HttpResponse(status=304)
        resp["ETag"] = tag
        return resp
    return _respond(request, a)


# audit: the change is recorded in the proposal log, as every curation edit is
def replace_annotation(request: HttpRequest, annotation_id: int) -> HttpResponse:
    """Replace an annotation's bodies and selector (its target document stays the same)."""

    a = _get(annotation_id)
    _may_edit(request, a)
    _precondition(request, a)
    raw = _body(request)
    if "target" not in raw:  # the protocol sends the whole annotation; keep the target
        raw["target"] = ids.doc_iri(a.document_id)
    item = w3c.parse(raw)
    if item is None:
        raise HttpError(400, "the annotation names no target")
    key = w3c.document_key(item.source) or w3c.document_key(item.scope)
    if key and key != a.document_id:
        raise HttpError(409, "an annotation cannot move to another document")
    span = w3c.locate(a.document.text, item) if (item.exact or item.start is not None) else None
    with transaction.atomic():
        a.text, a.tags, a.span = item.text, item.tags, span
        a.save(update_fields=["text", "tags", "span", "updated_at"])
        propose(
            a.document.collection,
            Proposal.Kind.ADD_NOTE,
            f"annotation:{a.id}",
            params={"text": item.text, "tags": item.tags, "span": span, "replaced": True},
            reason="Web Annotation Protocol (replace)",
            document=a.document,
            user=_user(request),
        )
    return _respond(request, _get(a.pk))


# audit: the audit log records annotation deletes through its model signals
def delete_annotation(request: HttpRequest, annotation_id: int) -> HttpResponse:
    a = _get(annotation_id)
    _may_edit(request, a)
    _precondition(request, a)
    a.delete()
    return HttpResponse(status=204)


def mount(router: Router) -> None:
    router.post("/annotations/w3c", tags=["annotations"])(create_annotation)
    router.get("/annotations/w3c/{annotation_id}", tags=["annotations"])(get_annotation)
    router.put("/annotations/w3c/{annotation_id}", tags=["annotations"])(replace_annotation)
    router.delete("/annotations/w3c/{annotation_id}", tags=["annotations"])(delete_annotation)
