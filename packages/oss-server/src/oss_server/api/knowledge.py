"""Packs, concepts and hierarchical facets."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError

from oss_core.config import get_settings
from oss_core.pack import load_pack
from oss_pipeline.clients.solr import quote_term
from oss_server.api.access import check, read_scope
from oss_server.knowledge import store
from oss_server.knowledge.importer import activate_pack, derive, hierarchy, import_pack
from oss_server.knowledge.models import Concept, Pack, PackDelta
from oss_server.knowledge.upgrade import collections_using, concept_row, upgrade_plan

router = Router(tags=["knowledge"])


class PackOut(Schema):
    id: int
    pack_id: str
    version: str
    title: str
    base_iri: str
    status: str
    bundle_status: str
    licence: str
    counts: dict[str, Any]
    findings: list[dict[str, Any]]
    cq_results: dict[str, Any]
    imported_at: datetime
    activated_at: datetime | None


class PackDetailOut(PackOut):
    requirements: dict[str, Any]
    decisions: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    schemes: list[dict[str, Any]]


class ConceptOut(Schema):
    iri: str
    kind: str
    pref_label: str
    lang: str
    alt_labels: list[str]
    hidden_labels: list[str]
    definition: str
    scheme: str
    scheme_slug: str
    type_iri: str
    type_label: str
    broader: list[str]
    path: str
    depth: int
    taggable: bool
    support: str
    term_status: str
    pack_id: str
    pack_version: str


class ActivationOut(PackOut):
    plan: dict[str, Any] | None = None


class PlanRequiredOut(Schema):
    detail: str
    plan: dict[str, Any]


class ImportIn(Schema):
    path: str
    activate: bool = True
    reindex: bool = True
    force: bool = False
    confirm: bool = False


class DiffIn(Schema):
    path: str
    force: bool = False


class RemovedOut(Schema):
    pack_id: str
    version: str
    concepts_removed: int
    dictionary_entries_removed: bool
    documents_still_tagged: int | None
    note: str


class SparqlIn(Schema):
    query: str
    limit: int = 200


def _pack_out(p: Pack) -> PackOut:
    return PackOut(
        id=p.id,
        pack_id=p.pack_id,
        version=p.version,
        title=p.title,
        base_iri=p.base_iri,
        status=p.status,
        bundle_status=p.bundle_status,
        licence=p.licence,
        counts=p.counts,
        findings=p.findings,
        cq_results=p.cq_results,
        imported_at=p.imported_at,
        activated_at=p.activated_at,
    )


def _concept_out(c: Concept) -> ConceptOut:
    return ConceptOut(
        iri=c.iri,
        kind=c.kind,
        pref_label=c.pref_label,
        lang=c.lang,
        alt_labels=list(c.alt_labels),
        hidden_labels=list(c.hidden_labels),
        definition=c.definition,
        scheme=c.scheme,
        scheme_slug=c.scheme_slug,
        type_iri=c.type_iri,
        type_label=c.type_label,
        broader=list(c.broader),
        path=c.path,
        depth=c.depth,
        taggable=c.taggable,
        support=c.support,
        term_status=c.term_status,
        pack_id=c.pack.pack_id,
        pack_version=c.pack.version,
    )


@router.get("/packs", response=list[PackOut])
def list_packs(request: HttpRequest, status: str | None = None) -> list[PackOut]:
    qs = Pack.objects.all()
    if status:
        qs = qs.filter(status=status)
    return [_pack_out(p) for p in qs]


def _import_path(raw: str) -> Path:
    """A bundle path the server may read: under the import folder or the shipped packs."""

    allowed = [Path("/import"), Path("/app/packs"), get_settings().semascope_data_dir / "packs"]
    allowed += [Path(__file__).resolve().parents[4] / "packs"]
    path = Path(raw).resolve()
    if not any(path.is_relative_to(a.resolve()) for a in allowed if a.exists()):
        raise HttpError(422, f"path must be under one of {[str(a) for a in allowed if a.exists()]}")
    if not (path / "requirements.yaml").exists():
        raise HttpError(422, "no requirements.yaml at that path")
    return path


def _candidate_plan(path: Path, force: bool) -> dict[str, Any]:
    """Validate a bundle on disk and diff it against the active version of its pack."""

    info = load_pack(path, force=force)
    if not info.ok:
        errors = "; ".join(f.message for f in info.findings if f.severity == "error")
        raise HttpError(422, f"pack {path.name} is not valid: {errors}")
    return upgrade_plan(info.id, info.version, derive(path, info), info.manifest_hash)


def _plan_required(plan: dict[str, Any]) -> tuple[int, PlanRequiredOut]:
    return 409, PlanRequiredOut(
        detail=(
            f"activating {plan['pack_id']}@{plan['candidate_version']} replaces the active "
            f"version {plan['active_version']}; review the plan and repeat with confirm=true"
        ),
        plan=plan,
    )


@router.post("/packs/import", response={201: ActivationOut, 409: PlanRequiredOut})
def import_pack_view(request: HttpRequest, payload: ImportIn) -> tuple[int, Any]:
    """Import a bundle directory. The path must be under the import folder or the shipped packs.

    When `activate` would replace an active version of the pack (a new version, or
    changed content under the same version), the answer is 409 with the diff and
    reindex plan unless `confirm` is true.
    """

    from oss_server.services import sync_dictionary

    check(request, "editor")
    path = _import_path(payload.path)
    plan: dict[str, Any] | None = None
    if payload.activate:
        plan = _candidate_plan(path, payload.force)
        if plan["requires_confirmation"] and not payload.confirm:
            return _plan_required(plan)
    try:
        pack = import_pack(path, activate=payload.activate, force=payload.force)
    except ValueError as exc:
        raise HttpError(422, str(exc)) from exc
    sync_dictionary(pack.pack_id, pack.version)
    if payload.activate and payload.reindex:
        _reindex_collections_using(pack.pack_id)
    return 201, ActivationOut(**_pack_out(pack).model_dump(), plan=plan)


@router.post("/packs/diff", response=dict[str, Any])
def diff_bundle(request: HttpRequest, payload: DiffIn) -> dict[str, Any]:
    """The diff and reindex plan between the active version of a pack and a bundle on disk."""

    check(request, "editor")
    return _candidate_plan(_import_path(payload.path), payload.force)


def _reindex_collections_using(pack_id: str) -> int:
    """Queue a reindex of every collection that tags with this pack (bound, or unbound = all)."""

    from oss_server.pipeline.tasks import reindex_collection

    slugs = collections_using(pack_id)
    for slug in slugs:
        reindex_collection.delay(slug)
    return len(slugs)


@router.get("/packs/{pack_id}", response=PackDetailOut)
def get_pack(request: HttpRequest, pack_id: str, version: str | None = None) -> PackDetailOut:
    qs = Pack.objects.filter(pack_id=pack_id)
    p = get_object_or_404(
        qs.filter(version=version) if version else qs.filter(status=Pack.Status.ACTIVE)
    )
    schemes = [
        {"slug": c.scheme_slug, "iri": c.iri, "label": c.pref_label, "definition": c.definition}
        for c in p.concepts.filter(kind=Concept.Kind.SCHEME)
    ]
    base = _pack_out(p).model_dump()
    return PackDetailOut(
        **base,
        requirements=p.requirements,
        decisions=p.decisions,
        sources=p.sources,
        schemes=schemes,
    )


@router.get("/packs/{pack_id}/diff", response=dict[str, Any])
def diff_version(request: HttpRequest, pack_id: str, version: str) -> dict[str, Any]:
    """The diff and reindex plan between the active version and another imported version."""

    check(request, "editor")
    p = get_object_or_404(Pack, pack_id=pack_id, version=version)
    rows = [concept_row(c) for c in p.concepts.all()]
    return upgrade_plan(p.pack_id, p.version, rows, p.manifest_hash)


@router.post("/packs/{pack_id}/activate", response={200: ActivationOut, 409: PlanRequiredOut})
def activate(
    request: HttpRequest, pack_id: str, version: str, confirm: bool = False
) -> tuple[int, Any]:
    """Make an imported version the active one (upgrade or roll back), then re-sync the
    dictionary and queue a reindex. Replacing another active version answers 409 with
    the diff and reindex plan unless `confirm` is true."""

    from oss_server.services import sync_dictionary

    check(request, "editor")
    p = get_object_or_404(Pack, pack_id=pack_id, version=version)
    plan = upgrade_plan(
        p.pack_id, p.version, [concept_row(c) for c in p.concepts.all()], p.manifest_hash
    )
    if plan["requires_confirmation"] and not confirm:
        return _plan_required(plan)
    activate_pack(p)
    sync_dictionary(p.pack_id, p.version)
    _reindex_collections_using(p.pack_id)
    return 200, ActivationOut(**_pack_out(p).model_dump(), plan=plan)


@router.delete("/packs/{pack_id}/versions/{version}", response=RemovedOut)
def remove_version(request: HttpRequest, pack_id: str, version: str) -> RemovedOut:
    """Remove an imported version that is not active (admin): its named graph, its
    concepts and its dictionary entries. Documents tagged with it keep those tags,
    and its `id@version` in pack_versions_ss, until they are reindexed."""

    from oss_server.knowledge.importer import remove_pack_version
    from oss_server.services import solr_client, solr_documents_collection, solr_entities_collection

    check(request, "admin")
    p = get_object_or_404(Pack, pack_id=pack_id, version=version)
    if p.status == Pack.Status.ACTIVE:
        raise HttpError(409, "the active version cannot be removed; activate another first")
    concepts = remove_pack_version(p)
    removed = True
    still: int | None = None
    try:
        q = f"pack_id:{quote_term(pack_id)} AND pack_version:{quote_term(version)}"
        solr_client().delete_by_query(solr_entities_collection(), q)
        solr_client().commit(solr_entities_collection())
    except Exception:  # noqa: BLE001 - the rows are gone; a re-sync of another version clears it
        removed = False
    try:
        key = quote_term(f"{pack_id}@{version}")
        still = solr_client().count(solr_documents_collection(), f"pack_versions_ss:{key}")
    except Exception:  # noqa: BLE001
        still = None
    return RemovedOut(
        pack_id=pack_id,
        version=version,
        concepts_removed=concepts,
        dictionary_entries_removed=removed,
        documents_still_tagged=still,
        note="documents tagged with this version keep their tags until they are reindexed",
    )


@router.get("/packs/{pack_id}/tree", response=list[dict[str, Any]])
def tree(request: HttpRequest, pack_id: str, scheme: str | None = None) -> list[dict[str, Any]]:
    p = get_object_or_404(Pack, pack_id=pack_id, status=Pack.Status.ACTIVE)
    return hierarchy(p, scheme)


@router.get("/packs/{pack_id}/concepts", response=list[ConceptOut])
def list_concepts(
    request: HttpRequest,
    pack_id: str,
    kind: str | None = None,
    scheme: str | None = None,
    q: str | None = None,
    limit: int = 200,
) -> list[ConceptOut]:
    p = get_object_or_404(Pack, pack_id=pack_id, status=Pack.Status.ACTIVE)
    qs = p.concepts.select_related("pack")
    qs = qs.filter(kind=kind) if kind else qs.exclude(kind=Concept.Kind.SCHEME)
    if scheme:
        qs = qs.filter(scheme_slug=scheme)
    if q:
        qs = qs.filter(pref_label__icontains=q)
    return [_concept_out(c) for c in qs[: min(limit, 1000)]]


@router.get("/concepts", response=ConceptOut)
def get_concept(request: HttpRequest, iri: str, collection: str | None = None) -> ConceptOut:
    """A concept of the active packs. When two packs define the IRI, the one with the
    higher binding precedence (for `collection`, or globally) answers."""

    from oss_server.catalogue.models import Collection
    from oss_server.knowledge.precedence import pack_order, preferred

    scope = get_object_or_404(Collection, slug=collection) if collection else None
    rows = Concept.objects.select_related("pack").filter(pack__status=Pack.Status.ACTIVE, iri=iri)
    c = preferred(rows, pack_order(scope))
    if c is None:
        raise HttpError(404, "no active concept with that IRI")
    return _concept_out(c)


@router.get("/concepts/documents", response=dict[str, Any])
def concept_documents(
    request: HttpRequest, iri: str, collection: str | None = None, rows: int = 10
) -> dict[str, Any]:
    """Documents tagged with a concept or linked to an individual."""

    from oss_server.api.search import HIT_FIELDS, collection_filter
    from oss_server.services import solr_client, solr_documents_collection

    allowed = read_scope(request, collection)
    term = quote_term(iri)
    fq = [f"concept_iris_ss:{term} OR entity_iris_ss:{term}", *collection_filter(allowed)]
    if collection:
        fq.append(f"collection_s:{quote_term(collection)}")
    body = solr_client().select(
        solr_documents_collection(), q="*:*", fq=fq, rows=min(rows, 100), fl=HIT_FIELDS, hl=False
    )
    return {"total": body["response"]["numFound"], "docs": body["response"]["docs"]}


@router.get("/places", response=list[dict[str, Any]])
def places(request: HttpRequest, collection: str | None = None) -> list[dict[str, Any]]:
    """Pack individuals with coordinates and how many documents mention each (for the map)."""

    from oss_server.api.search import collection_filter
    from oss_server.services import solr_client, solr_documents_collection

    allowed = read_scope(request, collection)

    rows = [
        c
        for c in Concept.objects.filter(
            pack__status=Pack.Status.ACTIVE, kind=Concept.Kind.INDIVIDUAL
        )
        if c.extra.get("lat") is not None and c.extra.get("lon") is not None
    ]
    if not rows:
        return []
    fq = collection_filter(allowed)
    if collection:
        fq.append(f'collection_s:"{collection}"')
    body = solr_client().select(
        solr_documents_collection(),
        q="*:*",
        fq=fq,
        rows=0,
        hl=False,
        facet=True,
        **{"facet.field": "entity_iris_ss", "facet.limit": 2000, "facet.mincount": 1},
    )
    flat = body.get("facet_counts", {}).get("facet_fields", {}).get("entity_iris_ss", [])
    counts = {str(flat[i]): int(flat[i + 1]) for i in range(0, len(flat), 2)}
    return [
        {
            "iri": c.iri,
            "label": c.pref_label,
            "type": c.type_label,
            "lat": c.extra["lat"],
            "lon": c.extra["lon"],
            "docs": counts.get(c.iri, 0),
        }
        for c in rows
    ]


_PROLOGUE = re.compile(
    r"\s*(?:#[^\n]*\n|PREFIX\s+[^\s:]*:\s*<[^>]*>|BASE\s*<[^>]*>)", re.IGNORECASE
)


def _query_form(q: str) -> str:
    """The SPARQL query form (SELECT, ASK, …) after comments and PREFIX/BASE declarations."""

    rest = q
    while m := _PROLOGUE.match(rest):
        rest = rest[m.end() :]
    word = rest.strip().split(None, 1)
    return word[0].upper() if word else ""


@router.post("/packs/{pack_id}/sparql", response=list[dict[str, Any]])
def sparql(request: HttpRequest, pack_id: str, payload: SparqlIn) -> list[dict[str, Any]]:
    """Read-only SELECT or ASK over one pack's triples (staff only)."""

    if not getattr(request.user, "is_staff", False):
        raise HttpError(403, "staff only")
    q = payload.query.strip()
    if _query_form(q) not in ("SELECT", "ASK") or any(
        w in q.upper() for w in ("INSERT", "DELETE", "LOAD", "CLEAR", "DROP", "CREATE")
    ):
        raise HttpError(422, "SELECT and ASK only")
    p = get_object_or_404(Pack, pack_id=pack_id, status=Pack.Status.ACTIVE)
    try:
        return store.query(q, pack_id=p.pack_id, version=p.version, limit=min(payload.limit, 1000))
    except Exception as exc:  # noqa: BLE001
        raise HttpError(422, str(exc)[:300]) from exc


# --- curator deltas ---------------------------------------------------------------------------


class DeltaIn(Schema):
    collection: str
    kind: str
    params: dict[str, Any]
    reason: str = ""


class DeltaOut(Schema):
    id: int
    pack_id: str
    pack_version: str
    collection: str
    kind: str
    params: dict[str, Any]
    reason: str
    status: str
    created_at: datetime


def _delta_out(d: PackDelta) -> DeltaOut:
    return DeltaOut(
        id=d.id,
        pack_id=d.pack_id,
        pack_version=d.pack_version,
        collection=d.collection.slug,
        kind=d.kind,
        params=d.params,
        reason=d.reason,
        status=d.status,
        created_at=d.created_at,
    )


@router.get("/packs/{pack_id}/deltas", response=list[DeltaOut])
def list_deltas(request: HttpRequest, pack_id: str, status: str | None = None) -> list[DeltaOut]:
    qs = PackDelta.objects.filter(pack_id=pack_id).select_related("collection")
    allowed = read_scope(request)
    if allowed is not None:
        qs = qs.filter(collection__slug__in=allowed)
    if status:
        qs = qs.filter(status=status)
    return [_delta_out(d) for d in qs]


@router.post("/packs/{pack_id}/deltas", response={201: DeltaOut})
def create_delta(request: HttpRequest, pack_id: str, payload: DeltaIn) -> tuple[int, DeltaOut]:
    """Propose a change to a pack: add_label, new_concept, retire_label or note."""

    from oss_server.catalogue.models import Collection

    pack = get_object_or_404(Pack, pack_id=pack_id, status=Pack.Status.ACTIVE)
    collection = get_object_or_404(Collection, slug=payload.collection)
    check(request, "curator", collection.slug)
    if payload.kind not in PackDelta.Kind.values:
        raise HttpError(422, f"kind must be one of {PackDelta.Kind.values}")
    required = {
        "add_label": ("iri", "label"),
        "new_concept": ("label", "scheme_slug"),
        "retire_label": ("iri", "label"),
        "note": ("iri", "text"),
    }[payload.kind]
    missing = [k for k in required if not payload.params.get(k)]
    if missing:
        raise HttpError(422, f"params missing {missing}")
    if (
        "iri" in payload.params
        and not Concept.objects.filter(pack=pack, iri=payload.params["iri"]).exists()
    ):
        raise HttpError(422, "iri is not a concept of this pack")
    delta = PackDelta.objects.create(
        pack_id=pack.pack_id,
        pack_version=pack.version,
        collection=collection,
        kind=payload.kind,
        params=payload.params,
        reason=payload.reason,
        proposed_by=_user_or_none(request),
    )
    if payload.kind == "add_label":
        _apply_draft_label(pack, collection.slug, payload.params)
    return 201, _delta_out(delta)


def _user_or_none(request: HttpRequest) -> Any:
    user = getattr(request, "user", None)
    return user if user is not None and getattr(user, "is_authenticated", False) else None


def _apply_draft_label(pack: Pack, collection_slug: str, params: dict[str, Any]) -> None:
    """A proposed label tags the proposing collection at once, as a draft dictionary entry
    scoped to that collection (`collection_scope`); `sync_dictionary` rebuilds it."""

    from oss_server.knowledge.importer import draft_entry
    from oss_server.services import solr_client, solr_entities_collection

    c = Concept.objects.get(pack=pack, iri=params["iri"])
    if not c.taggable:
        return
    entry = draft_entry(pack, c, collection_slug, params)
    solr_client().add(solr_entities_collection(), [entry], commit_within_ms=1000)


def _draft_deltas(request: HttpRequest, pack_id: str) -> tuple[Pack, list[PackDelta]]:
    pack = get_object_or_404(Pack, pack_id=pack_id, status=Pack.Status.ACTIVE)
    qs = PackDelta.objects.filter(pack_id=pack_id, status=PackDelta.Status.DRAFT).select_related(
        "collection"
    )
    allowed = read_scope(request)
    if allowed is not None:
        qs = qs.filter(collection__slug__in=allowed)
    return pack, list(qs)


@router.get("/packs/{pack_id}/deltas/export", response=dict[str, str])
def export_deltas(request: HttpRequest, pack_id: str) -> dict[str, str]:
    """The pending deltas as a bundle fragment (taxonomy.delta.ttl, model.delta.yaml).

    Read-only: the deltas stay drafts. POST to the same path to export and mark them.
    """

    from oss_server.knowledge.deltas import build_delta

    pack, deltas = _draft_deltas(request, pack_id)
    return build_delta(pack, deltas)


@router.post("/packs/{pack_id}/deltas/export", response=dict[str, str])
def export_and_mark_deltas(request: HttpRequest, pack_id: str) -> dict[str, str]:
    """Export the pending deltas and mark exactly those as exported (editor)."""

    from django.utils import timezone

    from oss_server.knowledge.deltas import build_delta

    check(request, "editor")
    pack, deltas = _draft_deltas(request, pack_id)
    files = build_delta(pack, deltas)
    PackDelta.objects.filter(pk__in=[d.pk for d in deltas]).update(
        status=PackDelta.Status.EXPORTED, exported_at=timezone.now()
    )
    return files
