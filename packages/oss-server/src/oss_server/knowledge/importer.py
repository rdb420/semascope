"""Import a pack: validate, load triples, derive the vocabulary, run competency tests.

Derivation rules (from the rebuild specification, section 6):

- SKOS concepts and named individuals become taggable `Concept` rows with
  their pref, alt and hidden labels; OWL classes become navigable entity
  types but are not tagged by label.
- `skos:broader` (and `rdfs:subClassOf` for classes) gives the hierarchy;
  a facet path is a chain of slugs from a top concept. A concept with several
  broader concepts (polyhierarchy) has one path per chain (`paths`); `path` is
  the first of them. `skos:related` is kept (`related`), in both directions.
- `model.yaml` support and status mark `proposed` and `disputed` terms,
  which are excluded from tagging by default.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml
from django.db import transaction
from django.utils import timezone
from rdflib import OWL, RDF, RDFS, SKOS, Graph, Literal, URIRef

from oss_core.pack import PackInfo, load_pack, parse_rdf, rdf_files, run_competency_questions
from oss_server.knowledge import store
from oss_server.knowledge.models import Concept, Pack

log = logging.getLogger(__name__)
_SLUG = re.compile(r"[^a-z0-9]+")
MAX_PATHS = 20  # facet paths kept per concept in a polyhierarchy


def slugify(value: str) -> str:
    """ASCII slug: accents folded (Väinämöinen -> vainamoinen), everything else to hyphens."""

    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return _SLUG.sub("-", folded.lower()).strip("-")[:64] or "x"


def local_name(iri: str) -> str:
    return re.split(r"[#/]", iri.rstrip("/#"))[-1] or iri


def _labels(g: Graph, node: URIRef, predicate: URIRef) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for value in g.objects(node, predicate):
        if isinstance(value, Literal):
            out.append((str(value).strip(), str(value.language or "")))
    return out


def _pref_order(item: tuple[str, str]) -> tuple[int, str, str]:
    """Which preferred label is the main one: English, then untagged, then by language."""

    label, lang = item
    return (0 if lang.lower().startswith("en") else 1 if not lang else 2, lang, label)


def _first(g: Graph, node: URIRef, *predicates: URIRef) -> str:
    for p in predicates:
        for value in g.objects(node, p):
            return str(value)
    return ""


def tagging_policy(pack_dir: Path) -> dict[str, Any]:
    """The `semascope:` block of requirements.yaml: which labels may tag text.

    The ontology-skill validator ignores unknown top-level keys, so a pack can
    declare `semascope: {tag_schemes: [...], tag_individuals: true}` without
    leaving the contract. Defaults: every SKOS scheme tags, individuals do not
    (the contract says instances.ttl holds fixtures), classes never do.
    """

    req_path = pack_dir / "requirements.yaml"
    req = yaml.safe_load(req_path.read_text(encoding="utf-8")) if req_path.exists() else {}
    block = (req or {}).get("semascope") or {}
    return {
        "tag_schemes": block.get("tag_schemes"),  # None = all schemes
        "tag_individuals": bool(block.get("tag_individuals", False)),
    }


_LAT = {"latitude", "lat"}
_LON = {"longitude", "long", "lon", "lng"}


def _coordinates(g: Graph, node: URIRef) -> tuple[float, float] | None:
    """(lat, lon) from any predicate whose local name is latitude/longitude."""

    lat = lon = None
    for p, o in g.predicate_objects(node):
        name = local_name(str(p)).lower()
        try:
            if name in _LAT:
                lat = float(str(o))
            elif name in _LON:
                lon = float(str(o))
        except ValueError:
            continue
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def derive(
    pack_dir: Path, info: PackInfo, policy: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Read the bundle with rdflib and produce Concept rows as dicts (no database access)."""

    policy = policy or tagging_policy(pack_dir)
    g = Graph()
    files = info.rdf or rdf_files(pack_dir)[0]
    for stem in ("ontology", "taxonomy", "instances"):
        if stem in files:
            parse_rdf(g, files[stem])

    terms: dict[str, dict[str, Any]] = {}
    model_path = pack_dir / "model.yaml"
    if model_path.exists():
        model = yaml.safe_load(model_path.read_text(encoding="utf-8")) or {}
        for term in model.get("terms") or []:
            iri = str(term.get("iri") or "")
            if iri.startswith("ex:"):
                iri = info.base_iri + iri[3:]
            iri = iri.strip("<>")
            terms[iri] = term

    rows: dict[str, dict[str, Any]] = {}

    def add(node: URIRef, kind: str, **extra: Any) -> None:
        iri = str(node)
        pref = _labels(g, node, SKOS.prefLabel) or _labels(g, node, RDFS.label)
        if not pref:
            if kind == "individual":
                pref = _labels(g, node, URIRef("http://purl.org/dc/terms/title"))
            if not pref:
                pref = [(local_name(iri), "")]
        pref = sorted(set(pref), key=_pref_order)
        label, lang = pref[0]
        # further preferred labels (one per language in SKOS) tag text too, with their language
        others = [{"label": v, "lang": lg} for v, lg in pref[1:] if v and v != label]
        notation = sorted(str(v) for v in g.objects(node, SKOS.notation))
        if others:
            extra["pref_labels"] = others
        if notation:
            extra["notation"] = notation
        term = terms.get(iri, {})
        rows[iri] = {
            "iri": iri,
            "kind": kind,
            "pref_label": label[:500],
            "lang": lang[:8],
            "alt_labels": sorted({v for v, _ in _labels(g, node, SKOS.altLabel)} - {label}),
            "hidden_labels": sorted({v for v, _ in _labels(g, node, SKOS.hiddenLabel)}),
            "definition": _first(
                g,
                node,
                SKOS.definition,
                RDFS.comment,
                URIRef("http://purl.org/dc/terms/description"),
            )[:4000],
            "scheme": "",
            "scheme_slug": "",
            "type_iri": "",
            "type_label": "",
            "broader": [],
            "related": [],
            "path": "",
            "paths": [],
            "depth": 0,
            "taggable": kind in ("concept", "individual"),
            "support": str(term.get("support") or ""),
            "term_status": str(term.get("status") or ""),
            "extra": extra,
        }
        if rows[iri]["support"] in ("proposed", "disputed"):
            rows[iri]["taggable"] = False

    for scheme in g.subjects(RDF.type, SKOS.ConceptScheme):
        if isinstance(scheme, URIRef):
            add(scheme, "scheme")
            rows[str(scheme)]["taggable"] = False
            rows[str(scheme)]["scheme_slug"] = slugify(rows[str(scheme)]["pref_label"])

    for concept in g.subjects(RDF.type, SKOS.Concept):
        if not isinstance(concept, URIRef):
            continue
        add(concept, "concept")
        row = rows[str(concept)]
        scheme_iri = _first(g, concept, SKOS.inScheme) or _first(g, concept, SKOS.topConceptOf)
        row["scheme"] = scheme_iri
        row["broader"] = sorted(
            str(b) for b in g.objects(concept, SKOS.broader) if isinstance(b, URIRef)
        )
        # skos:related is symmetric; a pack may state it in one direction only
        related = {str(r) for r in g.objects(concept, SKOS.related) if isinstance(r, URIRef)}
        related |= {str(r) for r in g.subjects(SKOS.related, concept) if isinstance(r, URIRef)}
        row["related"] = sorted(related - {str(concept)})

    for cls in g.subjects(RDF.type, OWL.Class):
        if not isinstance(cls, URIRef) or str(cls) in rows:
            continue
        add(cls, "class")
        rows[str(cls)]["broader"] = sorted(
            str(b) for b in g.objects(cls, RDFS.subClassOf) if isinstance(b, URIRef)
        )

    for ind in g.subjects(RDF.type, OWL.NamedIndividual):
        if not isinstance(ind, URIRef) or str(ind) in rows:
            continue
        types = [
            str(t)
            for t in g.objects(ind, RDF.type)
            if isinstance(t, URIRef) and t != OWL.NamedIndividual
        ]
        if any(t in (str(SKOS.Concept), str(SKOS.ConceptScheme)) for t in types):
            continue
        add(ind, "individual")
        row = rows[str(ind)]
        type_iri = next((t for t in types if t in rows), types[0] if types else "")
        row["type_iri"] = type_iri
        row["type_label"] = (
            rows[type_iri]["pref_label"] if type_iri in rows else local_name(type_iri)
        )
        coords = _coordinates(g, ind)
        if coords:
            row["extra"]["lat"], row["extra"]["lon"] = coords

    # scheme slugs for concepts, then paths from the top down
    for row in rows.values():
        if row["kind"] == "concept" and row["scheme"] and row["scheme"] in rows:
            row["scheme_slug"] = rows[row["scheme"]]["scheme_slug"]
        elif row["kind"] == "concept":
            row["scheme_slug"] = slugify(local_name(row["scheme"])) if row["scheme"] else "concepts"
        elif row["kind"] == "class":
            row["scheme_slug"] = "classes"
        elif row["kind"] == "individual":
            row["scheme_slug"] = slugify(row["type_label"] or "individuals")

    cache: dict[str, list[tuple[str, int]]] = {}

    def paths_of(iri: str, seen: frozenset[str]) -> tuple[list[tuple[str, int]], bool]:
        """Every (path, depth) from a top concept down to this one, first parent first, and
        whether the answer is complete (no parent left out to break a cycle)."""

        if iri in cache:
            return cache[iri], True
        row = rows[iri]
        known = [b for b in row["broader"] if b in rows]
        parents = [b for b in known if b not in seen]
        complete = len(parents) == len(known)
        own = slugify(row["pref_label"])
        out: list[tuple[str, int]] = []
        for parent in parents:
            found, done = paths_of(parent, seen | {iri})
            complete = complete and done
            for parent_path, depth in found:
                path = (f"{parent_path}/{own}", depth + 1)
                if path not in out and len(out) < MAX_PATHS:
                    out.append(path)
        result = out or [(own, 0)]
        if complete:
            cache[iri] = result
        return result, complete

    for iri, row in rows.items():
        if row["kind"] in ("concept", "class"):
            found, _ = paths_of(iri, frozenset())
            row["path"], row["depth"] = found[0]
            row["paths"] = [p for p, _ in found]
        elif row["kind"] == "individual":
            row["path"], row["depth"] = slugify(row["pref_label"]), 0
            row["paths"] = [row["path"]]

    # tagging policy: which schemes and whether individuals may tag text
    allowed_schemes = policy.get("tag_schemes")
    for row in rows.values():
        if row["kind"] == "concept" and allowed_schemes is not None:
            row["taggable"] = row["taggable"] and row["scheme_slug"] in set(allowed_schemes)
        elif row["kind"] == "individual":
            row["taggable"] = row["taggable"] and bool(policy.get("tag_individuals"))
    return list(rows.values())


def run_cq_tests(pack_dir: Path) -> dict[str, Any]:
    """Run the bundle's competency queries, each over the files its manifest entry names
    (fixture, ontology and taxonomy unless excluded, extra graphs), as the skill does."""

    return run_competency_questions(pack_dir)


@transaction.atomic
def import_pack(pack_dir: Path, *, activate: bool = True, force: bool = False) -> Pack:
    """Validate and import a bundle directory; returns the Pack row. Raises on structural errors.

    An unchanged bundle (same manifest hash) is skipped unless `force`, which
    re-derives the vocabulary, for example after an importer change. `force`
    also accepts files that differ from release-manifest.json (a warning, not
    an error).
    """

    pack_dir = pack_dir.resolve()
    info = load_pack(pack_dir, force=force)
    if not info.ok:
        errors = "; ".join(f.message for f in info.findings if f.severity == "error")
        raise ValueError(f"pack {pack_dir.name} is not valid: {errors}")
    existing = Pack.objects.filter(pack_id=info.id, version=info.version).first()
    unchanged = (
        existing is not None
        and existing.manifest_hash == info.manifest_hash
        and existing.status != Pack.Status.FAILED
    )
    if existing is not None and unchanged and not force:
        log.info("pack %s@%s unchanged; skipping import", info.id, info.version)
        if activate and existing.status != Pack.Status.ACTIVE:
            activate_pack(existing)
        return existing

    triple_count = store.load_bundle(info.rdf, info.id, info.version)
    rows = derive(pack_dir, info)
    cq = run_cq_tests(pack_dir)
    requirements = (
        yaml.safe_load((pack_dir / "requirements.yaml").read_text(encoding="utf-8")) or {}
    )
    decisions = (
        yaml.safe_load((pack_dir / "decisions.yaml").read_text(encoding="utf-8")) or {}
    ).get("decisions") or []
    sources = (yaml.safe_load((pack_dir / "sources.yaml").read_text(encoding="utf-8")) or {}).get(
        "sources"
    ) or []

    pack, _ = Pack.objects.update_or_create(
        pack_id=info.id,
        version=info.version,
        defaults={
            "title": info.title,
            "base_iri": info.base_iri,
            "bundle_status": info.status,
            "licence": str(info.licence or ""),
            "status": Pack.Status.IMPORTED,
            "manifest_hash": info.manifest_hash,
            "graph_iri": store.graph_iri(info.id, info.version),
            "source_path": str(pack_dir),
            "counts": {**info.counts, "triples": triple_count, "terms": len(rows)},
            "findings": [
                {"severity": f.severity, "code": f.code, "message": f.message}
                for f in info.findings
            ],
            "cq_results": cq,
            "requirements": json.loads(json.dumps(requirements, default=str)),
            "decisions": json.loads(json.dumps(decisions, default=str)),
            "sources": json.loads(json.dumps(sources, default=str)),
        },
    )
    pack.concepts.all().delete()
    Concept.objects.bulk_create([Concept(pack=pack, **row) for row in rows], batch_size=500)
    if activate:
        activate_pack(pack)
    return pack


def activate_pack(pack: Pack) -> None:
    """Make this version the active one for its pack id; retire the others."""

    Pack.objects.filter(pack_id=pack.pack_id).exclude(pk=pack.pk).filter(
        status=Pack.Status.ACTIVE
    ).update(status=Pack.Status.RETIRED)
    pack.status = Pack.Status.ACTIVE
    pack.activated_at = timezone.now()
    pack.save(update_fields=["status", "activated_at"])


def remove_pack_version(pack: Pack) -> int:
    """Delete a pack version that is not active: its named graph and its Concept rows.
    Returns the number of concepts removed. The caller clears the Solr dictionary."""

    if pack.status == Pack.Status.ACTIVE:
        raise ValueError(f"{pack} is active; activate another version first")
    store.drop_graph(pack.pack_id, pack.version)
    count = pack.concepts.count()
    pack.delete()
    return count


def active_packs() -> list[Pack]:
    return list(Pack.objects.filter(status=Pack.Status.ACTIVE))


def concept_paths(concept: Concept) -> list[str]:
    """Every facet path of a concept (one per broader chain); the single `path` for rows
    imported before polyhierarchy was kept."""

    return [str(p) for p in concept.paths or []] or ([concept.path] if concept.path else [])


def dictionary_entries(pack: Pack) -> list[dict[str, Any]]:
    """Solr `entities` documents for every taggable label of a pack version."""

    out: list[dict[str, Any]] = []
    for concept in pack.concepts.filter(taggable=True).iterator():
        lang = concept.lang or "und"
        labelled: list[tuple[str, str, str]] = [("pref", concept.pref_label, lang)]
        labelled += [
            ("pref", str(p.get("label") or ""), str(p.get("lang") or "und"))
            for p in (concept.extra or {}).get("pref_labels") or []
        ]
        labelled += [("alt", a, lang) for a in concept.alt_labels]
        labelled += [("hidden", h, lang) for h in concept.hidden_labels]
        for label_type, label, label_lang in labelled:
            label = str(label).strip()
            if len(label) < 2:
                continue
            # slug plus a digest: labels in non-Latin scripts slugify alike
            digest = hashlib.sha1(label.encode()).hexdigest()[:10]  # noqa: S324 - an id, not security
            out.append(
                {
                    "id": f"{pack}|{concept.iri}|{label_type}|{slugify(label)}-{digest}",
                    "iri": concept.iri,
                    "pack_id": pack.pack_id,
                    "pack_version": pack.version,
                    "label": label,
                    "pref_label": concept.pref_label,
                    "label_type": label_type,
                    "lang": label_lang,
                    "kind": concept.kind,
                    "type_iri": concept.type_iri,
                    "scheme": concept.scheme_slug,
                    "path": concept.path,
                    "paths": concept_paths(concept),
                    "case_sensitive": label.isupper() and len(label) <= 6,
                }
            )
    return out


def draft_entry(
    pack: Pack, concept: Concept, collection_slug: str, params: dict[str, Any]
) -> dict[str, Any]:
    """The dictionary entry of a curator's proposed label, scoped to the proposing collection."""

    label = str(params["label"]).strip()
    return {
        "id": f"{pack}|{concept.iri}|draft|{collection_slug}|{label.lower()}",
        "iri": concept.iri,
        "pack_id": pack.pack_id,
        "pack_version": pack.version,
        "label": label,
        "pref_label": concept.pref_label,
        "label_type": "draft",
        "lang": str(params.get("lang") or concept.lang or "und"),
        "kind": concept.kind,
        "type_iri": concept.type_iri,
        "scheme": concept.scheme_slug,
        "path": concept.path,
        "paths": concept_paths(concept),
        "case_sensitive": False,
        "collection_scope": collection_slug,
    }


def draft_entries(pack: Pack) -> list[dict[str, Any]]:
    """Draft entries for this pack version from the pending `add_label` deltas.

    A delta stays a draft entry until a pack version accepts or rejects it, so a
    dictionary sync (and a new pack version) keeps it; Postgres is the record.
    """

    from oss_server.knowledge.models import PackDelta

    deltas = PackDelta.objects.filter(
        pack_id=pack.pack_id,
        kind=PackDelta.Kind.ADD_LABEL,
        status__in=(PackDelta.Status.DRAFT, PackDelta.Status.EXPORTED),
    ).select_related("collection")
    concepts = {c.iri: c for c in pack.concepts.filter(taggable=True)}
    out: dict[str, dict[str, Any]] = {}
    for d in deltas.order_by("created_at", "pk"):
        concept = concepts.get(str(d.params.get("iri") or ""))
        if concept is None or len(str(d.params.get("label") or "").strip()) < 2:
            continue
        entry = draft_entry(pack, concept, d.collection.slug, d.params)
        out[entry["id"]] = entry
    return list(out.values())


def hierarchy(pack: Pack, scheme_slug: str | None = None) -> list[dict[str, Any]]:
    """Tree of concepts for the UI: children nested under broader."""

    qs = pack.concepts.filter(kind__in=["concept", "class"])
    if scheme_slug:
        qs = qs.filter(scheme_slug=scheme_slug)
    by_iri = {c.iri: c for c in qs}
    children: dict[str, list[Concept]] = defaultdict(list)
    roots: list[Concept] = []
    for c in by_iri.values():
        parents = [b for b in c.broader if b in by_iri]
        # in a polyhierarchy a concept sits under each of its broader concepts
        for parent in parents:
            children[parent].append(c)
        if not parents:
            roots.append(c)

    def node(c: Concept, seen: frozenset[str]) -> dict[str, Any]:
        below = [x for x in children[c.iri] if x.iri not in seen]
        return {
            "iri": c.iri,
            "label": c.pref_label,
            "path": c.path,
            "kind": c.kind,
            "children": [
                node(x, seen | {x.iri}) for x in sorted(below, key=lambda x: x.pref_label)
            ],
        }

    return [node(r, frozenset({r.iri})) for r in sorted(roots, key=lambda x: x.pref_label)]
