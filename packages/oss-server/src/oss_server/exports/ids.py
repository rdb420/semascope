"""Identifiers the exports give to Semascope's things.

Documents, collections, sources and stage runs keep the `urn:semascope:` names the
validate stage already uses for its SHACL graph, so an export and a validation
report talk about the same nodes. Tags are named after the document and the IRI
they assert (stable across reindexing, unlike a list position).

Named entities found by NER have IRIs of the form `<base>#<Class>/<label>`; they
leave as `urn:semascope:entity:<Class>/<label>` (percent-encoded), or, in a
collection that pseudonymises, as the same HMAC key the graph stage uses and
without their label.
"""

from __future__ import annotations

import hashlib
from urllib.parse import quote

from oss_core.contracts import Tag
from oss_pipeline.stages.graph import entity_key

BASE = "https://semascope.dev/ontology/base#"
_IRI_SAFE = ":/?#[]@!$&'()*+,;=%~"


def doc_iri(doc_id: str) -> str:
    return f"urn:semascope:doc:{doc_id}"


def collection_iri(slug: str) -> str:
    return f"urn:semascope:collection:{slug}"


def source_iri(source_id: int | str) -> str:
    return f"urn:semascope:source:{source_id}"


def page_iri(doc_id: str, page: int) -> str:
    return f"urn:semascope:doc:{doc_id}:page:{page}"


def tag_iri(doc_id: str, target: str) -> str:
    return f"urn:semascope:tag:{doc_id}:{hashlib.sha256(target.encode()).hexdigest()[:16]}"


def pack_iri(pack_id: str, version: str | None) -> str:
    return f"urn:semascope:pack:{quote(pack_id, safe='')}@{quote(version or '', safe='')}"


def stage_iri(stage: str, version: str | None) -> str:
    suffix = f":{quote(version, safe='')}" if version else ""
    return f"urn:semascope:stage:{quote(stage, safe='')}{suffix}"


def decision_iri(external_ref: str) -> str:
    return f"urn:semascope:decision:{external_ref}"


def annotation_iri(pk: int) -> str:
    return f"urn:semascope:annotation:{pk}"


def safe_iri(uri: str) -> str:
    """A URI with the characters an IRI may not hold (spaces, quotes, angle brackets)
    percent-encoded; characters that are already escapes stay as they are."""

    return quote(uri, safe=_IRI_SAFE)


def page_url(site: str, doc_id: str) -> str:
    """The document's page in the browser UI."""

    return f"{site.rstrip('/')}/documents/{doc_id}"


def is_ner_entity(iri: str) -> bool:
    return iri.startswith(BASE) and "/" in iri.removeprefix(BASE)


def entity_class(iri: str) -> str:
    """The base class of a NER entity IRI (`Person`, `Place`, ...)."""

    return iri.removeprefix(BASE).split("/", 1)[0]


def target_iri(iri: str, secret: str | None) -> tuple[str, bool]:
    """The IRI an export uses for a tag's target, and whether its label must be withheld.

    Concepts and pack individuals keep their IRI. NER entities get a URN; under
    pseudonymisation the URN holds the graph stage's HMAC key and the label is withheld.
    """

    if not is_ner_entity(iri):
        return safe_iri(iri), False
    probe = Tag(iri=iri, label="", kind="entity", stage="ner")
    key = entity_key(probe, secret)
    return f"urn:semascope:entity:{quote(key, safe='/:')}", bool(secret)
