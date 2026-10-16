"""Annotations in the W3C Web Annotation Data Model (JSON-LD), out and in.

Out: a curator's note becomes a `commenting` annotation whose target is the
document (its original URI as `source`, its Semascope page as `scope`) with a
position and a quote selector when the note is about a passage. Curator
decisions become annotations too:

| decision | motivation | bodies |
|---|---|---|
| accept or add a concept | `classifying` | the concept IRI, its label (`tagging`) |
| accept or add an entity | `identifying` | the entity IRI, its label (`tagging`) |
| reject or remove a tag | `assessing` | the text `rejected` (`assessing`), the IRI |
| link an entity to an individual | `identifying` | its IRI, "linked from <label>" (`describing`) |
| dismiss a SHACL violation | `moderating` | "dismissed: <focus>" (`moderating`) |

A decision's reason is one more textual body (`commenting`). Only the decision
in force per document and target is exported unless the history is asked for.

In: W3C annotations (a single one, a list, an AnnotationCollection or
AnnotationPage) and Hypothesis API exports (`{"rows": [...]}` of annotations
with `uri`, `text`, `tags`, `user` and W3C-style selectors). Each is matched to
a document of one collection by its target's source URI (the document's URI, its
`urn:semascope:doc:` name or its Semascope page), and anchored to the document's
text by its quote selector (exact text, disambiguated by prefix and suffix,
whitespace-tolerant), else by its position selector.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.db.models import Max, QuerySet

from oss_core import __version__
from oss_server.catalogue.models import Document
from oss_server.curation.models import Annotation, Proposal
from oss_server.exports import ids

ANNO_CONTEXT = "http://www.w3.org/ns/anno.jsonld"
CONTENT_TYPE = 'application/ld+json; profile="http://www.w3.org/ns/anno.jsonld"'
GENERATOR = {"id": "https://semascope.dev", "type": "Software", "name": f"Semascope {__version__}"}
CONTEXT_CHARS = 32  # prefix and suffix length of an exported quote selector
MAX_TEXT = 10000
MAX_TAGS = 50
MAX_TAG_CHARS = 100

ACCEPTS = (Proposal.Kind.ACCEPT_TAG, Proposal.Kind.ADD_TAG)
REJECTS = (Proposal.Kind.REJECT_TAG, Proposal.Kind.REMOVE_TAG)
DECISION_KINDS = (*ACCEPTS, *REJECTS, Proposal.Kind.LINK_ENTITY, Proposal.Kind.DISMISS_VIOLATION)

SecretFor = Callable[[Document], str | None]
TargetFor = Callable[[Document], str]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def selectors(text: str, span: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Position and quote selectors for a span of the document's text (none if it is off)."""

    if not span:
        return []
    try:
        start, end = int(span["start"]), int(span["end"])
    except (KeyError, TypeError, ValueError):
        return []
    if not 0 <= start < end <= len(text):
        return []
    return [
        {"type": "TextPositionSelector", "start": start, "end": end},
        {
            "type": "TextQuoteSelector",
            "exact": text[start:end],
            "prefix": text[max(0, start - CONTEXT_CHARS) : start],
            "suffix": text[end : end + CONTEXT_CHARS],
        },
    ]


def _target(doc: Document, source: str, site: str, span: dict[str, Any] | None) -> dict[str, Any]:
    target: dict[str, Any] = {"source": source, "scope": ids.page_url(site, doc.id)}
    found = selectors(doc.text, span)
    if found:
        target["selector"] = found
    return target


def _person(username: str | None) -> dict[str, Any] | None:
    return {"type": "Person", "nickname": username} if username else None


def note_annotation(
    a: Annotation, doc: Document, *, site: str, source: str, origin: dict[str, Any] | None = None
) -> dict[str, Any]:
    """A curator's note (or an imported annotation, with its original creator and id)."""

    bodies: list[dict[str, Any]] = []
    if a.text:
        bodies.append(
            {
                "type": "TextualBody",
                "value": a.text,
                "purpose": "commenting",
                "format": "text/plain",
            }
        )
    for tag in a.tags or []:
        bodies.append({"type": "TextualBody", "value": str(tag), "purpose": "tagging"})
    out: dict[str, Any] = {
        "id": ids.annotation_iri(a.pk),
        "type": "Annotation",
        "motivation": "commenting" if a.text else "tagging",
        "created": _iso(a.created_at),
        "modified": _iso(a.updated_at),
        "generator": GENERATOR,
        "target": _target(doc, source, site, a.span),
    }
    if bodies:
        out["body"] = bodies
    origin = origin or {}
    creator = origin.get("creator") or _person(getattr(a.author, "username", None))
    if creator:
        out["creator"] = creator
    if origin.get("id"):
        out["via"] = str(origin["id"])
    if origin.get("created"):
        out["created"] = str(origin["created"])
    return out


def decision_annotation(
    p: Proposal, doc: Document, *, site: str, source: str, secret: str | None
) -> dict[str, Any] | None:
    """A curator's decision on a tag, a link or a violation as an annotation; None for notes."""

    params = p.params or {}
    label = str(params.get("label") or (params.get("tag") or {}).get("label") or "")
    bodies: list[dict[str, Any]] = []
    if p.kind in ACCEPTS or p.kind in REJECTS:
        iri, hidden = ids.target_iri(p.target, secret)
        kind = str(params.get("kind") or (params.get("tag") or {}).get("kind") or "concept")
        if hidden:
            label = ""
        if p.kind in ACCEPTS:
            motivation = "classifying" if kind == "concept" else "identifying"
            bodies.append({"type": "SpecificResource", "source": iri, "purpose": motivation})
            if label:
                bodies.append({"type": "TextualBody", "value": label, "purpose": "tagging"})
        else:
            motivation = "assessing"
            bodies.append({"type": "TextualBody", "value": "rejected", "purpose": "assessing"})
            bodies.append({"type": "SpecificResource", "source": iri})
    elif p.kind == Proposal.Kind.LINK_ENTITY:
        motivation = "identifying"
        individual = str(params.get("iri") or "")
        if not individual:
            return None
        bodies.append(
            {
                "type": "SpecificResource",
                "source": ids.safe_iri(individual),
                "purpose": "identifying",
            }
        )
        _, hidden = ids.target_iri(p.target, secret)
        original = str((params.get("tag") or {}).get("label") or "")
        if original and not hidden:
            bodies.append(
                {"type": "TextualBody", "value": f"linked from {original}", "purpose": "describing"}
            )
    elif p.kind == Proposal.Kind.DISMISS_VIOLATION:
        motivation = "moderating"
        bodies.append(
            {"type": "TextualBody", "value": f"dismissed: {p.target}", "purpose": "moderating"}
        )
    else:
        return None
    if p.reason:
        bodies.append({"type": "TextualBody", "value": p.reason, "purpose": "commenting"})
    out: dict[str, Any] = {
        "id": ids.decision_iri(p.external_ref),
        "type": "Annotation",
        "motivation": motivation,
        "created": _iso(p.decided_at),
        "generator": GENERATOR,
        "body": bodies,
        "target": _target(doc, source, site, None),
    }
    creator = _person(getattr(p.decided_by, "username", None))
    if creator:
        out["creator"] = creator
    return out


# --- loading -----------------------------------------------------------------------------------


def notes_query(documents: QuerySet[Document]) -> QuerySet[Annotation]:
    return (
        Annotation.objects.filter(document__in=documents)
        .select_related("author", "document__collection")
        .order_by("pk")
    )


def decisions_query(documents: QuerySet[Document], *, history: bool) -> QuerySet[Proposal]:
    """Decisions on these documents, oldest first; only the one in force per target unless
    `history`."""

    qs = Proposal.objects.filter(document__in=documents, kind__in=DECISION_KINDS)
    if not history:
        newest = (
            Proposal.objects.filter(document__in=documents)
            .values("document_id", "target")
            .annotate(newest=Max("pk"))
            .values("newest")
        )
        qs = qs.filter(pk__in=newest)
    return qs.select_related("decided_by", "document__collection").order_by("pk")


def import_origins(notes: Iterable[Annotation]) -> dict[int, dict[str, Any]]:
    """For imported notes: what the import recorded about the original (id, creator, created)."""

    targets = {f"annotation:{a.pk}": a.pk for a in notes}
    if not targets:
        return {}
    out: dict[int, dict[str, Any]] = {}
    for target, params in Proposal.objects.filter(
        kind=Proposal.Kind.ADD_NOTE, target__in=list(targets)
    ).values_list("target", "params"):
        imported = (params or {}).get("imported")
        if isinstance(imported, dict):
            out[targets[target]] = imported
    return out


def annotations_for(
    notes: Iterable[Annotation],
    decisions: Iterable[Proposal],
    *,
    site: str,
    source: TargetFor,
    secret: SecretFor,
) -> list[dict[str, Any]]:
    notes = list(notes)
    origins = import_origins(notes)
    out = [
        note_annotation(
            a, a.document, site=site, source=source(a.document), origin=origins.get(a.pk)
        )
        for a in notes
    ]
    for p in decisions:
        doc = p.document
        if doc is None:
            continue
        item = decision_annotation(p, doc, site=site, source=source(doc), secret=secret(doc))
        if item is not None:
            out.append(item)
    return out


# --- import ------------------------------------------------------------------------------------


@dataclass
class Incoming:
    """One annotation to import, whatever format it came in."""

    id: str
    source: str
    scope: str = ""
    text: str = ""
    tags: list[str] = field(default_factory=list)
    exact: str | None = None
    prefix: str = ""
    suffix: str = ""
    start: int | None = None
    end: int | None = None
    creator: dict[str, Any] | None = None
    created: str | None = None
    format: str = "w3c"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def annotation_items(payload: Any) -> list[dict[str, Any]]:
    """The annotations in a payload: one, a list, a collection, a page or a Hypothesis export."""

    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("rows"), list):  # Hypothesis API search response
        return [x for x in payload["rows"] if isinstance(x, dict)]
    kinds = {str(t) for t in _as_list(payload.get("type") or payload.get("@type"))}
    if kinds & {"AnnotationCollection", "AnnotationPage"} or "items" in payload:
        items = payload.get("items")
        first = payload.get("first")
        if items is None and isinstance(first, dict):
            items = first.get("items")
        return [x for x in _as_list(items) if isinstance(x, dict)]
    return [payload]


def _creator(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str) and value:
        return {"id": value} if ":" in value else {"type": "Person", "nickname": value}
    if isinstance(value, dict):
        keep = {k: value[k] for k in ("id", "type", "name", "nickname") if value.get(k)}
        return keep or None
    if isinstance(value, list) and value:
        return _creator(value[0])
    return None


def _read_selectors(item: Incoming, raw: Any) -> None:
    for sel in _as_list(raw):
        if not isinstance(sel, dict):
            continue
        kind = sel.get("type")
        if kind == "TextQuoteSelector" and isinstance(sel.get("exact"), str) and sel["exact"]:
            item.exact = sel["exact"]
            item.prefix = str(sel.get("prefix") or "")
            item.suffix = str(sel.get("suffix") or "")
        elif kind == "TextPositionSelector":
            try:
                item.start, item.end = int(sel["start"]), int(sel["end"])
            except (KeyError, TypeError, ValueError):
                pass


def _fallback_id(raw: dict[str, Any]) -> str:
    import json

    blob = json.dumps(raw, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()


def parse(raw: dict[str, Any]) -> Incoming | None:
    """One annotation (W3C or Hypothesis) as an Incoming; None if it names no target."""

    hypothesis = "uri" in raw and ("user" in raw or "text" in raw) and "motivation" not in raw
    item = Incoming(
        id=str(raw.get("id") or raw.get("@id") or _fallback_id(raw)),
        source="",
        format="hypothesis" if hypothesis else "w3c",
        created=str(raw["created"]) if raw.get("created") else None,
    )
    targets = _as_list(raw.get("target"))
    for target in targets:
        if isinstance(target, str):
            item.source = item.source or target
        elif isinstance(target, dict):
            src = target.get("source") or target.get("id") or target.get("@id")
            if isinstance(src, str) and not item.source:
                item.source = src
                item.scope = str(target.get("scope") or "")
                _read_selectors(item, target.get("selector"))
    if hypothesis:
        item.source = item.source or str(raw.get("uri") or "")
        item.text = str(raw.get("text") or "")
        item.tags = [str(t) for t in _as_list(raw.get("tags")) if isinstance(t, str)]
        item.creator = _creator(raw.get("user"))
    else:
        texts: list[str] = []
        if isinstance(raw.get("bodyValue"), str):
            texts.append(raw["bodyValue"])
        for body in _as_list(raw.get("body")):
            if not isinstance(body, dict):
                continue
            value = body.get("value", body.get("chars"))
            if not isinstance(value, str) or not value:
                continue
            purposes = {str(p) for p in _as_list(body.get("purpose"))}
            if "tagging" in purposes:
                item.tags.append(value)
            else:
                texts.append(value)
        item.text = "\n\n".join(texts)
        item.creator = _creator(raw.get("creator"))
    if not item.source:
        return None
    item.text = item.text[:MAX_TEXT]
    item.tags = [t.strip()[:MAX_TAG_CHARS] for t in item.tags if t.strip()][:MAX_TAGS]
    return item


def _ws_pattern(exact: str) -> re.Pattern[str]:
    words = exact.split()
    return re.compile(r"\s+".join(re.escape(w) for w in words)) if words else re.compile(r"(?!)")


def locate(text: str, item: Incoming) -> dict[str, int] | None:
    """Where the annotation's passage is in the document text: {start, end}, or None.

    A position selector that agrees with the quote wins; otherwise the quote is
    searched for (exactly, then ignoring differences in whitespace) and the
    occurrence whose surroundings best match the prefix and suffix is chosen.
    Positions alone are trusted only when there is no quote.
    """

    start, end = item.start, item.end
    if item.exact:
        if start is not None and end is not None and text[start:end] == item.exact:
            return {"start": start, "end": end}
        matches = [(m.start(), m.end()) for m in _iter_matches(text, item.exact)]
        if not matches:
            return None

        def fit(m: tuple[int, int]) -> int:
            before = text[max(0, m[0] - len(item.prefix)) : m[0]]
            after = text[m[1] : m[1] + len(item.suffix)]
            return _common_suffix(before, item.prefix) + _common_prefix(after, item.suffix)

        best = max(matches, key=fit)
        return {"start": best[0], "end": best[1]}
    if start is not None and end is not None and 0 <= start < end <= len(text):
        return {"start": start, "end": end}
    return None


def _iter_matches(text: str, exact: str, limit: int = 1000) -> list[re.Match[str]]:
    found = []
    for pattern in (re.compile(re.escape(exact)), _ws_pattern(exact)):
        for m in pattern.finditer(text):
            found.append(m)
            if len(found) >= limit:
                break
        if found:
            break
    return found


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n


def _common_suffix(a: str, b: str) -> int:
    return _common_prefix(a[::-1], b[::-1])


_PAGE = re.compile(r"/documents/([0-9a-f]{32})(?:[/?#]|$)")
_URN = re.compile(r"^urn:semascope:doc:([0-9a-f]{32})$")


def document_key(uri: str) -> str | None:
    """The Semascope document id a URI names directly (its URN or its page), if any."""

    for pattern in (_URN, _PAGE):
        m = pattern.search(uri or "")
        if m:
            return m.group(1)
    return None
