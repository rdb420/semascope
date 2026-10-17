"""Packs, concepts and hierarchical facets."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from ninja import Router, Schema, Status
from ninja.errors import HttpError

from oss_core.config import get_settings
from oss_core.pack import load_pack
from oss_pipeline.clients.solr import quote_term
from oss_server.api.access import check, check_member, read_scope
from oss_server.api.errors import console_error
from oss_server.audit.log import record
from oss_server.knowledge import store
from oss_server.knowledge.importer import concept_paths, derive, hierarchy
from oss_server.knowledge.models import Concept, Pack, PackDelta, PackJob
from oss_server.knowledge.upgrade import concept_row, upgrade_plan

router = Router(tags=["knowledge"])
log = logging.getLogger(__name__)


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
    related: list[str] = []
    path: str
    paths: list[str] = []
    depth: int
    taggable: bool
    support: str
    term_status: str
    pack_id: str
    pack_version: str


class PackJobOut(Schema):
    """A pack import or activation running in a worker; follow it at `/pack-jobs/{pack_job_id}`."""

    id: int
    kind: str
    status: str
    pack_id: str
    version: str
    params: dict[str, Any]
    plan: dict[str, Any] | None
    result: dict[str, Any]
    error: str
    created_by: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class PlanRequiredOut(Schema):
    detail: str
    plan: dict[str, Any]


class ImportIn(Schema):
    path: str
    activate: bool = True
    reindex: bool = True
    force: bool = False
    confirm: bool = False


class ListImportIn(Schema):
    """A plain list of names as a pack: one line per concept, `Label | other name | …`."""

    pack_id: str
    version: str = "1.0.0"
    title: str = ""
    scheme: str = ""
    text: str
    lang: str = "en"
    licence: str = ""
    source: str = ""
    activate: bool = True
    reindex: bool = True
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
        related=list(c.related or []),
        path=c.path,
        paths=concept_paths(c),
        depth=c.depth,
        taggable=c.taggable,
        support=c.support,
        term_status=c.term_status,
        pack_id=c.pack.pack_id,
        pack_version=c.pack.version,
    )


@router.get("/packs", response=list[PackOut])
def list_packs(request: HttpRequest, status: str | None = None) -> list[PackOut]:
    check_member(request)
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


def _plan_required(plan: dict[str, Any]) -> Status[PlanRequiredOut]:
    return Status(
        409,
        PlanRequiredOut(
            detail=(
                f"activating {plan['pack_id']}@{plan['candidate_version']} replaces the active "
                f"version {plan['active_version']}; review the plan and repeat with confirm=true"
            ),
            plan=plan,
        ),
    )


def _job_out(j: PackJob) -> PackJobOut:
    return PackJobOut(
        id=j.id,
        kind=j.kind,
        status=j.status,
        pack_id=j.pack_id,
        version=j.version,
        params=j.params,
        plan=j.plan,
        result=j.result,
        error=j.error,
        created_by=getattr(j.created_by, "username", None),
        created_at=j.created_at,
        started_at=j.started_at,
        finished_at=j.finished_at,
    )


def _bundle_identity(path: Path) -> tuple[str, str]:
    """The pack id and version a bundle declares (requirements.yaml), without validating it."""

    import yaml

    try:
        req = yaml.safe_load((path / "requirements.yaml").read_text(encoding="utf-8")) or {}
        onto = req.get("ontology") or {}
        return str(onto.get("id") or path.name), str(onto.get("version") or "")
    except (OSError, yaml.YAMLError, AttributeError) as exc:
        raise HttpError(422, f"requirements.yaml is unreadable: {exc}") from exc


def _queue_job(
    request: HttpRequest,
    kind: str,
    pack_id: str,
    version: str,
    params: dict[str, Any],
    plan: dict[str, Any] | None,
) -> PackJob:
    """Record a pack job and hand it to a worker; one job per pack at a time (409)."""

    from oss_server.knowledge.jobs import busy
    from oss_server.knowledge.tasks import run_pack_job

    running = busy(pack_id)
    if running is not None:
        raise HttpError(409, f"pack job {running.pk} on {pack_id} is still {running.status}")
    job = PackJob.objects.create(
        kind=kind,
        pack_id=pack_id,
        version=version,
        params=params,
        plan=plan,
        created_by=_user_or_none(request),
    )
    run_pack_job.delay(job.pk)
    return job


def _import(request: HttpRequest, path: Path, payload: ImportIn | ListImportIn) -> Status[Any]:
    """The plan check (409 until confirmed), then the import as a job (202)."""

    force = bool(getattr(payload, "force", False))
    plan: dict[str, Any] | None = None
    if payload.activate:
        plan = _candidate_plan(path, force)
        if plan["requires_confirmation"] and not payload.confirm:
            return _plan_required(plan)
    pack_id, version = _bundle_identity(path)
    params = {
        "path": str(path),
        "activate": payload.activate,
        "reindex": payload.reindex,
        "force": force,
    }
    job = _queue_job(request, PackJob.Kind.IMPORT, pack_id, version, params, plan)
    record(
        "pack.import",
        target=job,
        target_name=f"{pack_id}@{version}",
        detail={"activate": payload.activate, "reindex": payload.reindex, "force": force},
    )
    return Status(202, _job_out(job))


@router.post("/packs/import", response={202: PackJobOut, 409: PlanRequiredOut})
def import_pack_view(request: HttpRequest, payload: ImportIn) -> Status[Any]:
    """Import a bundle directory. The path must be under the import folder or the shipped packs.

    When `activate` would replace an active version of the pack (a new version, or
    changed content under the same version), the answer is 409 with the diff and
    reindex plan unless `confirm` is true. Otherwise the import runs as a job in a
    worker (loading the triple store, deriving the vocabulary, running the competency
    questions, syncing the dictionary and queuing the reindex): the answer is 202
    with the job, which `GET /pack-jobs/{id}` follows to `done` or `failed`.
    """

    check(request, "editor")
    return _import(request, _import_path(payload.path), payload)


@router.post("/packs/import-list", response={202: PackJobOut, 409: PlanRequiredOut})
def import_list(request: HttpRequest, payload: ListImportIn) -> Status[Any]:
    """Import a plain list of names as a pack (editor): one SKOS concept per line, the first
    name its preferred label and the others (after `|`) alternative labels. The list is
    written as a bundle on the data volume and imported like any pack: the same plan,
    confirmation and job."""

    from oss_server.knowledge.listpack import ListError, write_list_bundle

    check(request, "editor")
    try:
        path = write_list_bundle(
            pack_id=payload.pack_id,
            version=payload.version,
            title=payload.title,
            scheme=payload.scheme,
            text=payload.text,
            lang=payload.lang,
            licence=payload.licence,
            source=payload.source,
        )
    except ListError as exc:
        raise HttpError(422, str(exc)) from exc
    return _import(request, path, payload)


@router.get("/pack-jobs", response=list[PackJobOut])
def list_pack_jobs(
    request: HttpRequest, pack_id: str | None = None, limit: int = 20
) -> list[PackJobOut]:
    """Pack imports and activations, newest first (editor)."""

    check(request, "editor")
    qs = PackJob.objects.select_related("created_by")
    if pack_id:
        qs = qs.filter(pack_id=pack_id)
    return [_job_out(j) for j in qs[: min(max(limit, 1), 200)]]


@router.get("/pack-jobs/{pack_job_id}", response=PackJobOut)
def get_pack_job(request: HttpRequest, pack_job_id: int) -> PackJobOut:
    check(request, "editor")
    job = get_object_or_404(PackJob.objects.select_related("created_by"), pk=pack_job_id)
    return _job_out(job)


@router.post("/packs/diff", response=dict[str, Any])
def diff_bundle(request: HttpRequest, payload: DiffIn) -> dict[str, Any]:
    """The diff and reindex plan between the active version of a pack and a bundle on disk."""

    check(request, "editor")
    return _candidate_plan(_import_path(payload.path), payload.force)


@router.get("/packs/{pack_id}", response=PackDetailOut)
def get_pack(request: HttpRequest, pack_id: str, version: str | None = None) -> PackDetailOut:
    check_member(request)
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


@router.post("/packs/{pack_id}/activate", response={202: PackJobOut, 409: PlanRequiredOut})
def activate(
    request: HttpRequest, pack_id: str, version: str, confirm: bool = False, reindex: bool = True
) -> Status[Any]:
    """Make an imported version the active one (upgrade or roll back). Replacing another
    active version answers 409 with the diff and reindex plan unless `confirm` is true.
    The activation, the dictionary sync and the reindex run as a job (202)."""

    check(request, "editor")
    p = get_object_or_404(Pack, pack_id=pack_id, version=version)
    plan = upgrade_plan(
        p.pack_id, p.version, [concept_row(c) for c in p.concepts.all()], p.manifest_hash
    )
    if plan["requires_confirmation"] and not confirm:
        return _plan_required(plan)
    params = {"version": p.version, "reindex": reindex}
    job = _queue_job(request, PackJob.Kind.ACTIVATE, p.pack_id, p.version, params, plan)
    record(
        "pack.activate",
        target=job,
        target_name=f"{p.pack_id}@{p.version}",
        detail={"reindex": reindex, "confirmed": confirm},
    )
    return Status(202, _job_out(job))


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
    record(
        "pack.remove",
        target_type="pack",
        target_name=f"{pack_id}@{version}",
        detail={"concepts_removed": concepts},
    )
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
    check_member(request)
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
    check_member(request)
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

    check_member(request)
    if collection:  # a collection's pack bindings are read with that collection
        check(request, "reader", collection)
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
            pack__status=Pack.Status.ACTIVE,
            kind=Concept.Kind.INDIVIDUAL,
            extra__has_key="lat",
        )
        .filter(extra__has_key="lon")
        .only("iri", "pref_label", "type_label", "extra")
        if c.extra.get("lat") is not None and c.extra.get("lon") is not None
    ]
    if not rows:
        return []
    fq = collection_filter(allowed)
    if collection:
        fq.append(f"collection_s:{quote_term(collection)}")
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
    record("console.sparql", target=p, detail={"query": q})
    try:
        return store.query(q, pack_id=p.pack_id, version=p.version, limit=min(payload.limit, 1000))
    except Exception as exc:  # noqa: BLE001
        raise console_error("SPARQL", exc) from exc


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
def create_delta(request: HttpRequest, pack_id: str, payload: DeltaIn) -> Status[DeltaOut]:
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
    _check_delta_params(payload.params)
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
    return Status(201, _delta_out(delta))


def _check_delta_params(params: dict[str, Any]) -> None:
    """Values that end up in the exported Turtle or the dictionary: well-formed or refused."""

    from oss_server.knowledge.deltas import valid_iri, valid_lang

    for key in ("label", "text", "definition", "reason"):
        if key in params and (not isinstance(params[key], str) or len(params[key]) > 4000):
            raise HttpError(422, f"{key} must be text of at most 4000 characters")
    if "label" in params and len(str(params["label"]).strip()) < 2:
        raise HttpError(422, "label must be at least two characters")
    for key in ("iri", "broader"):
        if params.get(key) and not valid_iri(params[key]):
            raise HttpError(422, f"{key} must be an absolute IRI")
    if params.get("lang") and not valid_lang(params["lang"]):
        raise HttpError(422, "lang must be a language tag such as en or pt-BR")
    if params.get("label_type", "alt") not in ("alt", "hidden"):
        raise HttpError(422, "label_type must be alt or hidden")


def _user_or_none(request: HttpRequest) -> Any:
    user = getattr(request, "user", None)
    return user if user is not None and getattr(user, "is_authenticated", False) else None


def _apply_draft_label(pack: Pack, collection_slug: str, params: dict[str, Any]) -> None:
    """A proposed label becomes a draft dictionary entry scoped to the proposing collection
    (`collection_scope`; `sync_dictionary` rebuilds it), and the collection's documents
    that contain it are queued for the tag stage, so they are tagged with it once that
    replay has run (SEMA-024); documents indexed later are tagged as they are processed."""

    from oss_server.knowledge.importer import draft_entry
    from oss_server.knowledge.tasks import retag_label
    from oss_server.services import solr_client, solr_entities_collection

    c = Concept.objects.get(pack=pack, iri=params["iri"])
    if not c.taggable:
        return
    entry = draft_entry(pack, c, collection_slug, params)
    solr_client().add(solr_entities_collection(), [entry], commit_within_ms=1000)
    # after the entry is visible to the Tagger (commitWithin above)
    try:
        retag_label.apply_async((collection_slug, str(params["label"])), countdown=5)
    except Exception:  # noqa: BLE001 - the label is recorded; a reindex tags with it too
        log.exception("could not queue the retag for a new label in %s", collection_slug)


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
    record("pack.deltas_export", target=pack, detail={"deltas": len(deltas)})
    PackDelta.objects.filter(pk__in=[d.pk for d in deltas]).update(
        status=PackDelta.Status.EXPORTED, exported_at=timezone.now()
    )
    return files
