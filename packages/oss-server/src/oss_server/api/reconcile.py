"""W3C Reconciliation Service API (0.2) over the active packs' concepts and individuals.

GET /api/reconcile returns the service manifest; POST /api/reconcile with
`queries` (form-encoded JSON, as OpenRefine sends it, or a JSON body) returns
candidates per query. Matching is by normalised label on pref, alt and hidden
labels, with a type filter on the pack class or SKOS scheme.

Callers sign in (a bearer token or a session) and need a role on some collection,
like every other vocabulary route: list packs can name people and clients. An
operator who wants OpenRefine to reach it without a token sets
`SEMASCOPE_RECONCILE_PUBLIC=true`; it is then rate-limited per client address. A
batch holds at most `MAX_QUERIES` queries of at most `MAX_QUERY_CHARS` characters,
each asking for at most `MAX_LIMIT` candidates; anything else is a 400.
"""

from __future__ import annotations

import json
import re
from typing import Any

from django.db.models import Q
from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError
from ninja.security import SessionAuth

from oss_core.config import get_settings
from oss_server.api.access import check_member
from oss_server.api.auth import TokenAuth
from oss_server.knowledge.models import Concept, Pack

MAX_QUERIES = 50  # OpenRefine sends 10 per batch
MAX_QUERY_CHARS = 500
MAX_LIMIT = 25


class PublicIfConfigured:
    """Lets anyone through when SEMASCOPE_RECONCILE_PUBLIC is true, else no one (the next
    authentication method decides)."""

    def __call__(self, request: HttpRequest) -> Any:
        if get_settings().semascope_reconcile_public:
            return "public"  # any truthy value: the request may go on, anonymous
        return None


# public first: when it is on, a session's CSRF check never refuses an anonymous POST
RECONCILE_AUTH = [PublicIfConfigured(), TokenAuth(), SessionAuth()]

router = Router(tags=["reconcile"], auth=RECONCILE_AUTH)


def _allowed(request: HttpRequest) -> None:
    if not get_settings().semascope_reconcile_public:
        check_member(request)


_WS = re.compile(r"\s+")


def _norm(value: str) -> str:
    return _WS.sub(" ", value.strip().lower())


def manifest(request: HttpRequest) -> dict[str, Any]:
    base = request.build_absolute_uri("/").rstrip("/")
    types = [
        {"id": c.iri, "name": c.pref_label}
        for c in Concept.objects.filter(
            pack__status=Pack.Status.ACTIVE, kind__in=[Concept.Kind.CLASS, Concept.Kind.SCHEME]
        )
    ]
    return {
        "versions": ["0.2"],
        "name": "Semascope",
        "identifierSpace": base + "/concepts?iri=",
        "schemaSpace": base + "/concepts?iri=",
        "defaultTypes": types[:20],
        "view": {"url": base + "/concepts?iri={{id}}"},
        "preview": {"url": base + "/api/reconcile/preview?iri={{id}}", "width": 400, "height": 200},
    }


def reconcile_one(query: str, type_iri: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
    needle = _norm(query)
    if not needle:
        return []
    qs = (
        Concept.objects.filter(pack__status=Pack.Status.ACTIVE)
        .exclude(kind=Concept.Kind.SCHEME)
        .select_related("pack")
    )
    if type_iri:
        qs = qs.filter(Q(type_iri=type_iri) | Q(scheme=type_iri) | Q(iri=type_iri))
    qs = qs.filter(
        Q(pref_label__icontains=query)
        | Q(alt_labels__icontains=query)
        | Q(hidden_labels__icontains=query)
    )
    out: list[dict[str, Any]] = []
    for c in qs[:200]:
        labels = [_norm(x) for x in c.labels]
        if needle in labels:
            score = 100.0 if _norm(c.pref_label) == needle else 90.0
            match = True
        elif any(needle in x for x in labels):
            score = 60.0
            match = False
        else:
            continue
        out.append(
            {
                "id": c.iri,
                "name": c.pref_label,
                "score": score,
                "match": match,
                "type": [{"id": c.type_iri or c.scheme, "name": c.type_label or c.scheme_slug}],
                "description": c.definition[:200],
            }
        )
    out.sort(key=lambda x: -float(x["score"]))
    if len([x for x in out if x["match"]]) > 1:
        for x in out:
            x["match"] = False  # ambiguous: let the user choose
    return out[:limit]


@router.get("")
def get_manifest(request: HttpRequest) -> dict[str, Any]:
    _allowed(request)
    return manifest(request)


def _queries(request: HttpRequest) -> dict[str, Any]:
    raw = request.POST.get("queries") if request.POST else None
    try:
        if raw is None:
            body = json.loads(request.body or b"{}")
            queries = body.get("queries", body) if isinstance(body, dict) else None
        else:
            queries = json.loads(raw)
    except ValueError as exc:
        raise HttpError(400, "queries must be a JSON object") from exc
    if not isinstance(queries, dict):
        raise HttpError(400, "queries must be a JSON object")
    if len(queries) > MAX_QUERIES:
        raise HttpError(400, f"at most {MAX_QUERIES} queries per request")
    return queries


def _limit(value: Any) -> int:
    try:
        limit = int(value or 5)
    except (TypeError, ValueError) as exc:
        raise HttpError(400, "limit must be a whole number") from exc
    return max(1, min(limit, MAX_LIMIT))


@router.post("")
def reconcile(request: HttpRequest) -> dict[str, Any]:
    _allowed(request)
    result: dict[str, Any] = {}
    for key, q in _queries(request).items():
        if isinstance(q, str):
            q = {"query": q}
        if not isinstance(q, dict):
            raise HttpError(400, f"query {str(key)[:40]} must be a string or an object")
        text = str(q.get("query") or "")
        if len(text) > MAX_QUERY_CHARS:
            raise HttpError(400, f"queries are at most {MAX_QUERY_CHARS} characters")
        type_iri = q.get("type")
        if type_iri is not None and not isinstance(type_iri, str):
            raise HttpError(400, "type must be a string")
        result[str(key)] = {"result": reconcile_one(text, type_iri, _limit(q.get("limit")))}
    return result


@router.get("/preview")
def preview(request: HttpRequest, iri: str) -> dict[str, Any]:
    _allowed(request)
    from oss_server.knowledge.precedence import pack_order, preferred

    c = preferred(
        Concept.objects.select_related("pack").filter(pack__status=Pack.Status.ACTIVE, iri=iri),
        pack_order(),
    )
    if c is None:
        return {}
    return {"iri": c.iri, "label": c.pref_label, "definition": c.definition, "also": c.alt_labels}
