"""W3C Reconciliation Service API (0.2) over the active packs' concepts and individuals.

GET /api/reconcile returns the service manifest; POST /api/reconcile with
`queries` (form-encoded JSON, as OpenRefine sends it, or a JSON body) returns
candidates per query. Matching is by normalised label on pref, alt and hidden
labels, with a type filter on the pack class or SKOS scheme.
"""

from __future__ import annotations

import json
import re
from typing import Any

from django.db.models import Q
from django.http import HttpRequest
from ninja import Router

from oss_server.knowledge.models import Concept, Pack

router = Router(tags=["reconcile"])
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


@router.get("", auth=None)
def get_manifest(request: HttpRequest) -> dict[str, Any]:
    return manifest(request)


@router.post("", auth=None)
def reconcile(request: HttpRequest) -> dict[str, Any]:
    raw = request.POST.get("queries") if request.POST else None
    if raw is None:
        try:
            body = json.loads(request.body or b"{}")
        except ValueError:
            body = {}
        queries = body.get("queries", body) if isinstance(body, dict) else {}
    else:
        queries = json.loads(raw)
    if not isinstance(queries, dict):
        return {}
    result: dict[str, Any] = {}
    for key, q in queries.items():
        if isinstance(q, str):
            q = {"query": q}
        if not isinstance(q, dict):
            continue
        limit = int(q.get("limit") or 5)
        result[str(key)] = {
            "result": reconcile_one(str(q.get("query") or ""), q.get("type"), limit)
        }
    return result


@router.get("/preview", auth=None)
def preview(request: HttpRequest, iri: str) -> dict[str, Any]:
    from oss_server.knowledge.precedence import pack_order, preferred

    c = preferred(
        Concept.objects.select_related("pack").filter(pack__status=Pack.Status.ACTIVE, iri=iri),
        pack_order(),
    )
    if c is None:
        return {}
    return {"iri": c.iri, "label": c.pref_label, "definition": c.definition, "also": c.alt_labels}
