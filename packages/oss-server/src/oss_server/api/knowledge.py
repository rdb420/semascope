"""Packs, concepts and hierarchical facets."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError

from oss_core.config import get_settings
from oss_server.knowledge import store
from oss_server.knowledge.importer import activate_pack, hierarchy, import_pack
from oss_server.knowledge.models import Concept, Pack, PackDelta

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


class ImportIn(Schema):
    path: str
    activate: bool = True
    reindex: bool = True


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


@router.post("/packs/import", response={201: PackOut})
def import_pack_view(request: HttpRequest, payload: ImportIn) -> tuple[int, PackOut]:
    """Import a bundle directory. The path must be under the import folder or the shipped packs."""

    from oss_server.services import sync_dictionary

    allowed = [Path("/import"), Path("/app/packs"), get_settings().semascope_data_dir / "packs"]
    allowed += [Path(__file__).resolve().parents[4] / "packs"]
    path = Path(payload.path).resolve()
    if not any(str(path).startswith(str(a.resolve())) for a in allowed if a.exists()):
        raise HttpError(422, f"path must be under one of {[str(a) for a in allowed if a.exists()]}")
    if not (path / "requirements.yaml").exists():
        raise HttpError(422, "no requirements.yaml at that path")
    try:
        pack = import_pack(path, activate=payload.activate)
    except ValueError as exc:
        raise HttpError(422, str(exc)) from exc
    sync_dictionary(pack.pack_id, pack.version)
    if payload.activate and payload.reindex:
        _reindex_collections_using(pack.pack_id)
    return 201, _pack_out(pack)


def _reindex_collections_using(pack_id: str) -> int:
    """Queue a reindex of every collection that tags with this pack (bound, or unbound = all)."""

    from oss_server.catalogue.models import Collection
    from oss_server.knowledge.models import PackBinding
    from oss_server.pipeline.tasks import reindex_collection

    bound = set(
        PackBinding.objects.filter(pack_id=pack_id, enabled=True).values_list(
            "collection__slug", flat=True
        )
    )
    unbound = set(
        Collection.objects.exclude(pack_bindings__isnull=False).values_list("slug", flat=True)
    )
    slugs = sorted(bound | unbound)
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


@router.post("/packs/{pack_id}/activate", response=PackOut)
def activate(request: HttpRequest, pack_id: str, version: str) -> PackOut:
    from oss_server.services import sync_dictionary

    p = get_object_or_404(Pack, pack_id=pack_id, version=version)
    activate_pack(p)
    sync_dictionary(p.pack_id, p.version)
    _reindex_collections_using(p.pack_id)
    return _pack_out(p)


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
def get_concept(request: HttpRequest, iri: str) -> ConceptOut:
    c = get_object_or_404(
        Concept.objects.select_related("pack").filter(pack__status=Pack.Status.ACTIVE), iri=iri
    )
    return _concept_out(c)


@router.get("/concepts/documents", response=dict[str, Any])
def concept_documents(
    request: HttpRequest, iri: str, collection: str | None = None, rows: int = 10
) -> dict[str, Any]:
    """Documents tagged with a concept or linked to an individual."""

    from oss_server.api.search import HIT_FIELDS
    from oss_server.services import solr_client, solr_documents_collection

    fq = [f'concept_iris_ss:"{iri}" OR entity_iris_ss:"{iri}"']
    if collection:
        fq.append(f'collection_s:"{collection}"')
    body = solr_client().select(
        solr_documents_collection(), q="*:*", fq=fq, rows=min(rows, 100), fl=HIT_FIELDS, hl=False
    )
    return {"total": body["response"]["numFound"], "docs": body["response"]["docs"]}


@router.post("/packs/{pack_id}/sparql", response=list[dict[str, Any]])
def sparql(request: HttpRequest, pack_id: str, payload: SparqlIn) -> list[dict[str, Any]]:
    """Read-only SELECT or ASK over one pack's triples (staff only)."""

    if not getattr(request.user, "is_staff", False):
        raise HttpError(403, "staff only")
    q = payload.query.strip()
    if not q.lower().lstrip("prefix ").startswith(("select", "ask", "prefix")) or any(
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
    if status:
        qs = qs.filter(status=status)
    return [_delta_out(d) for d in qs]


@router.post("/packs/{pack_id}/deltas", response={201: DeltaOut})
def create_delta(request: HttpRequest, pack_id: str, payload: DeltaIn) -> tuple[int, DeltaOut]:
    """Propose a change to a pack: add_label, new_concept, retire_label or note."""

    from oss_server.catalogue.models import Collection

    pack = get_object_or_404(Pack, pack_id=pack_id, status=Pack.Status.ACTIVE)
    collection = get_object_or_404(Collection, slug=payload.collection)
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
    """A proposed label tags the proposing collection at once, as a draft dictionary entry."""

    from oss_server.services import solr_client, solr_entities_collection

    c = Concept.objects.get(pack=pack, iri=params["iri"])
    if not c.taggable:
        return
    label = str(params["label"]).strip()
    entry = {
        "id": f"{pack}|{c.iri}|draft|{collection_slug}|{label.lower()}",
        "iri": c.iri,
        "pack_id": pack.pack_id,
        "pack_version": pack.version,
        "label": label,
        "pref_label": c.pref_label,
        "label_type": "draft",
        "lang": str(params.get("lang") or c.lang or "und"),
        "kind": c.kind,
        "type_iri": c.type_iri,
        "scheme": c.scheme_slug,
        "path": c.path,
        "case_sensitive": False,
    }
    solr_client().add(solr_entities_collection(), [entry], commit_within_ms=1000)


@router.get("/packs/{pack_id}/deltas/export", response=dict[str, str])
def export_deltas(request: HttpRequest, pack_id: str, mark: bool = False) -> dict[str, str]:
    """The pending deltas as a bundle fragment (taxonomy.delta.ttl, model.delta.yaml)."""

    from django.utils import timezone

    from oss_server.knowledge.deltas import build_delta

    pack = get_object_or_404(Pack, pack_id=pack_id, status=Pack.Status.ACTIVE)
    deltas = list(
        PackDelta.objects.filter(pack_id=pack_id, status=PackDelta.Status.DRAFT).select_related(
            "collection"
        )
    )
    files = build_delta(pack, deltas)
    if mark:
        PackDelta.objects.filter(pk__in=[d.pk for d in deltas]).update(
            status=PackDelta.Status.EXPORTED, exported_at=timezone.now()
        )
    return files
