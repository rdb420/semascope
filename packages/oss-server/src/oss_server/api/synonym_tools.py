"""Synonym tooling (parity rows FUZZ-05, FUZZ-06, APP-08, APP-09: synonyms curators can see
and grow).

- `GET /synonyms/expand?q=`     what a query expands to: the pack labels it matches and
                                the labels they bring in (with their weight), the curators'
                                labels still waiting for a pack version, and the tokens
                                Solr's query analysis actually produces
- `GET /synonyms/suggest?iri=`  candidate labels for a concept or individual: spelling
                                variants of its labels among the names the index holds,
                                names that co-occur with it far more than elsewhere, and
                                the significant words of the documents tagged with it

A curator turns a candidate into a synonym by proposing it as an alternative
or hidden label (`POST /api/knowledge/packs/{pack}/deltas`, kind `add_label`):
it tags the proposing collection's documents at once, and becomes a query-time
synonym when a pack version that accepts it is activated
(`knowledge.jobs.on_pack_activated` regenerates the synonyms).
"""

from __future__ import annotations

import logging
from typing import Any

from django.http import HttpRequest
from ninja import Router, Schema
from ninja.errors import HttpError

from oss_server import services
from oss_server.api import search as search_mod
from oss_server.api.access import read_scope
from oss_server.api.schemas import SearchIn
from oss_server.knowledge import expansion

log = logging.getLogger(__name__)

NAME_FIELDS = ("persons_ss", "places_ss", "organisations_ss", "events_ss", "entity_labels_ss")
MAX_CANDIDATES = 100
FOREGROUND_VALUES = 50  # co-occurring names read per field
VARIANT_VALUES = 100  # names read per field and label prefix


class ExpandLabel(Schema):
    label: str
    type: str  # pref, alt, hidden
    weight: float


class ExpandMatch(Schema):
    phrase: str
    start: int
    end: int
    iri: str
    pref_label: str
    kind: str
    pack_id: str
    matched_label: str
    matched_type: str
    expands_to: list[ExpandLabel]


class DraftLabel(Schema):
    iri: str
    label: str
    label_type: str
    collection: str
    pack_id: str


class Token(Schema):
    text: str
    position: int
    type: str


class ExpandOut(Schema):
    q: str
    words: list[str]
    matches: list[ExpandMatch]
    drafts: list[DraftLabel]  # proposed labels that apply once a pack version accepts them
    tokens: list[Token]  # the query as Solr's analysis of the text field ends it
    analysis_error: str | None = None


class Candidate(Schema):
    label: str
    reasons: list[str]  # spelling, co-occurs, significant word
    documents: int  # documents of the concept (in scope) that hold it
    distance: int | None = None  # edit distance to the nearest existing label
    lift: float | None = None  # how much more often it occurs with the concept than without
    field: str | None = None


class SuggestOut(Schema):
    iri: str
    pref_label: str
    pack_id: str
    kind: str
    labels: list[str]
    documents: int  # documents in scope tagged with it
    candidates: list[Candidate]


def _analysis(q: str) -> list[Token]:
    """The query analysis of the documents' main text field: the final tokens, synonyms
    included (type SYNONYM), with their positions."""

    client = services.solr_client()
    get = getattr(client, "_get", None)
    if get is None:
        raise RuntimeError("the search client cannot run an analysis request")
    body = get(
        f"/{services.solr_documents_collection()}/analysis/field",
        **{"analysis.fieldname": "text_txt", "analysis.query": q, "analysis.showmatch": "false"},
    )
    stages = (((body.get("analysis") or {}).get("field_names") or {}).get("text_txt") or {}).get(
        "query"
    ) or []
    last = next((s for s in reversed(stages) if isinstance(s, list)), [])
    return [
        Token(
            text=str(t.get("text", "")),
            position=int(t.get("position", 0)),
            type=str(t.get("type", "")),
        )
        for t in last
        if isinstance(t, dict)
    ]


def expand_query(
    request: HttpRequest, q: str, collection: str | None = None, analyse: bool = True
) -> ExpandOut:
    """The pack labels a query matches and what each brings in, the pending labels curators
    proposed for the matched concepts (in the collections the caller may read), and the
    tokens Solr's query analysis makes of it."""

    from oss_server.knowledge.models import PackDelta

    q = q.strip()
    if not q or len(q) > 500:
        raise HttpError(422, "q must be 1 to 500 characters")
    allowed = read_scope(request, collection)
    words, found = expansion.expansions(q)
    matches = [
        ExpandMatch(
            phrase=e.phrase,
            start=e.start,
            end=e.end,
            iri=e.concept.iri,
            pref_label=e.concept.pref_label,
            kind=e.concept.kind,
            pack_id=e.concept.pack.pack_id,
            matched_label=e.matched.label,
            matched_type=e.matched.label_type,
            expands_to=[
                ExpandLabel(label=label, type=kind, weight=e.weight_of_others)
                for label, kind in e.others
            ],
        )
        for e in found
    ]
    keys = {expansion.key(w) for w in words} | {expansion.key(m.phrase) for m in matches}
    iris = {m.iri for m in matches}
    drafts_qs = PackDelta.objects.filter(
        kind=PackDelta.Kind.ADD_LABEL, status=PackDelta.Status.DRAFT
    ).select_related("collection")
    if allowed is not None:
        drafts_qs = drafts_qs.filter(collection__slug__in=allowed)
    if collection:
        drafts_qs = drafts_qs.filter(collection__slug=collection)
    drafts = [
        DraftLabel(
            iri=str(d.params.get("iri", "")),
            label=str(d.params.get("label", "")),
            label_type=str(d.params.get("label_type") or "alt"),
            collection=d.collection.slug,
            pack_id=d.pack_id,
        )
        for d in drafts_qs[:500]
        if str(d.params.get("iri", "")) in iris
        or expansion.key(str(d.params.get("label", ""))) in keys
    ]
    out = ExpandOut(q=q, words=words, matches=matches, drafts=drafts, tokens=[])
    if analyse:
        try:
            out.tokens = _analysis(q)
        except Exception as exc:  # noqa: BLE001 - the label matches still answer
            log.info("synonyms: query analysis unavailable: %s", exc)
            out.analysis_error = str(exc)[:300]
    return out


def _scope_fq(allowed: list[str] | None, collection: str | None) -> list[str]:
    fq = search_mod.collection_filter(allowed)
    if collection:
        fq.append(f"collection_s:{search_mod._escape(collection)}")
    return fq


def _facets(body: dict[str, Any]) -> dict[str, list[tuple[str, int]]]:
    flat = (body.get("facet_counts") or {}).get("facet_fields") or {}
    return {f: [(str(v[i]), int(v[i + 1])) for i in range(0, len(v), 2)] for f, v in flat.items()}


# WS-8: rate limit
def suggest_synonyms(
    request: HttpRequest, iri: str, collection: str | None = None, top: int = 30
) -> SuggestOut:
    """Candidate labels for a concept or individual of an active pack, from the documents in
    the collections the caller may read (or `collection`)."""

    from oss_server.api.searchplus import terms as significant_terms
    from oss_server.knowledge.models import Concept, Pack

    concept = (
        Concept.objects.filter(iri=iri, pack__status=Pack.Status.ACTIVE)
        .select_related("pack")
        .first()
    )
    if concept is None:
        raise HttpError(404, "no concept or individual of an active pack has that IRI")
    allowed = read_scope(request, collection)
    top = max(1, min(top, MAX_CANDIDATES))
    labels = [label for label, _ in expansion.typed_labels(concept)]
    known = {expansion.key(label) for label in labels}
    field = "concept_iris_ss" if concept.kind == Concept.Kind.CONCEPT else "entity_iris_ss"
    scope = _scope_fq(allowed, collection)
    tagged = f"{field}:{search_mod._escape(iri)}"
    client = services.solr_client()
    docs = services.solr_documents_collection()
    fg = client.select(
        docs,
        q="*:*",
        fq=[*scope, tagged],
        rows=0,
        hl=False,
        facet=True,
        **{
            "facet.field": list(NAME_FIELDS),
            "facet.limit": FOREGROUND_VALUES,
            "facet.mincount": 1,
        },
    )
    fg_total = int(fg.get("response", {}).get("numFound", 0))
    found: dict[str, Candidate] = {}

    def add(label: str, reason: str, n: int, **extra: Any) -> None:
        k = expansion.key(label)
        if not k or k in known:
            return
        item = found.get(k)
        if item is None:
            item = found[k] = Candidate(label=label, reasons=[], documents=n)
        if reason not in item.reasons:
            item.reasons.append(reason)
        item.documents = max(item.documents, n)
        for name, value in extra.items():
            if value is not None and getattr(item, name) is None:
                setattr(item, name, value)

    # 1. names in the index that are spelling variants of a label
    prefixes = sorted({expansion.key(label)[:3] for label in labels if len(label) >= 3})
    for prefix in prefixes[:10]:
        body = client.select(
            docs,
            q="*:*",
            fq=scope,
            rows=0,
            hl=False,
            facet=True,
            **{
                "facet.field": list(NAME_FIELDS),
                "facet.contains": prefix,
                "facet.contains.ignoreCase": "true",
                "facet.limit": VARIANT_VALUES,
                "facet.mincount": 1,
            },
        )
        for name, values in _facets(body).items():
            for value, n in values:
                d = expansion.near(value, labels)
                if d is not None:
                    add(value, "spelling", n, distance=d, field=name)
    # 2. names that occur with the concept far more than without it
    if fg_total:
        fg_values = {
            (name, value): n for name, values in _facets(fg).items() for value, n in values
        }
        queries = {
            f"{{!term f={name}}}{value}": (name, value)
            for (name, value) in fg_values
            if expansion.key(value) not in known
        }
        if queries:
            bg = client.select(
                docs,
                q="*:*",
                fq=scope,
                rows=0,
                hl=False,
                facet=True,
                **{"facet.query": list(queries)},
            )
            bg_total = max(1, int(bg.get("response", {}).get("numFound", 0)))
            counts = (bg.get("facet_counts") or {}).get("facet_queries") or {}
            for query, (name, value) in queries.items():
                n = fg_values[(name, value)]
                in_bg = int(counts.get(query, n))
                outside = max(0, in_bg - n)
                rest = max(1, bg_total - fg_total)
                lift = (n / fg_total) / ((outside + 1) / (rest + 1))
                if n >= min(2, fg_total) and lift >= 2.0:
                    add(value, "co-occurs", n, lift=round(lift, 2), field=name)
    # 3. significant words of the tagged documents (the result-set terms machinery)
    if fg_total:
        try:
            words = significant_terms(
                request,
                SearchIn(q="*:*", collection=collection, filters={field: [iri]}, facets=[]),
                top=60,
                sample=30,
            )
        except Exception as exc:  # noqa: BLE001 - the other signals still answer
            log.info("synonyms: significant words unavailable: %s", exc)
            words = []
        for w in words:
            d = expansion.near(str(w["term"]), labels)
            if d is not None:
                add(str(w["term"]), "spelling", int(w["docs"]), distance=d)
            elif float(w.get("score", 0)) > 0:
                add(str(w["term"]), "significant word", int(w["docs"]))

    order = {"spelling": 0, "co-occurs": 1, "significant word": 2}
    ranked = sorted(
        found.values(),
        key=lambda c: (
            min(order[r] for r in c.reasons),
            c.distance if c.distance is not None else 9,
            -(c.lift or 0.0),
            -c.documents,
            c.label.lower(),
        ),
    )
    return SuggestOut(
        iri=concept.iri,
        pref_label=concept.pref_label,
        pack_id=concept.pack.pack_id,
        kind=concept.kind,
        labels=labels,
        documents=fg_total,
        candidates=ranked[:top],
    )


def mount(router: Router) -> None:
    router.get("/synonyms/expand", response=ExpandOut, tags=["knowledge"])(expand_query)
    router.get("/synonyms/suggest", response=SuggestOut, tags=["knowledge"])(suggest_synonyms)
