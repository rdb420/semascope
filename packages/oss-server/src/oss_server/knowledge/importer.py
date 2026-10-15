"""Import a pack: validate, load triples, derive the vocabulary, run competency tests.

Derivation rules (from the rebuild specification, section 6):

- SKOS concepts and named individuals become taggable `Concept` rows with
  their pref, alt and hidden labels; OWL classes become navigable entity
  types but are not tagged by label.
- `skos:broader` (and `rdfs:subClassOf` for classes) gives the hierarchy;
  the facet path is the chain of slugs from the top concept.
- `model.yaml` support and status mark `proposed` and `disputed` terms,
  which are excluded from tagging by default.
"""

from __future__ import annotations

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

from oss_core.pack import PackInfo, load_pack
from oss_server.knowledge import store
from oss_server.knowledge.models import Concept, Pack

log = logging.getLogger(__name__)
_SLUG = re.compile(r"[^a-z0-9]+")


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


def derive(
    pack_dir: Path, info: PackInfo, policy: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Read the bundle with rdflib and produce Concept rows as dicts (no database access)."""

    policy = policy or tagging_policy(pack_dir)
    g = Graph()
    for name in ("ontology.ttl", "taxonomy.ttl", "instances.ttl"):
        file = pack_dir / name
        if file.exists():
            g.parse(file, format="turtle")

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
        label, lang = pref[0]
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
            "path": "",
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

    cache: dict[str, tuple[str, int]] = {}

    def path_of(iri: str, seen: frozenset[str]) -> tuple[str, int]:
        if iri in cache:
            return cache[iri]
        row = rows[iri]
        parents = [b for b in row["broader"] if b in rows and b not in seen]
        own = slugify(row["pref_label"])
        if not parents:
            cache[iri] = (own, 0)
        else:
            parent_path, depth = path_of(parents[0], seen | {iri})
            cache[iri] = (f"{parent_path}/{own}", depth + 1)
        return cache[iri]

    for iri, row in rows.items():
        if row["kind"] in ("concept", "class"):
            row["path"], row["depth"] = path_of(iri, frozenset())
        elif row["kind"] == "individual":
            row["path"], row["depth"] = slugify(row["pref_label"]), 0

    # tagging policy: which schemes and whether individuals may tag text
    allowed_schemes = policy.get("tag_schemes")
    for row in rows.values():
        if row["kind"] == "concept" and allowed_schemes is not None:
            row["taggable"] = row["taggable"] and row["scheme_slug"] in set(allowed_schemes)
        elif row["kind"] == "individual":
            row["taggable"] = row["taggable"] and bool(policy.get("tag_individuals"))
    return list(rows.values())


def run_cq_tests(pack_dir: Path, pack_id: str, version: str) -> dict[str, Any]:
    """Execute the bundle's competency queries against the pack graph in Oxigraph."""

    manifest = pack_dir / "queries" / "manifest.yaml"
    if not manifest.exists():
        return {"tests": 0, "passed": 0, "results": []}
    spec = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    results: list[dict[str, Any]] = []
    for entry in spec.get("queries") or []:
        file = pack_dir / "queries" / str(entry.get("file"))
        if not file.exists():
            results.append({"cq_id": entry.get("cq_id"), "status": "missing"})
            continue
        if str(entry.get("entailment", "none")) != "none":
            results.append({"cq_id": entry.get("cq_id"), "status": "skipped-reasoner"})
            continue
        sparql = file.read_text(encoding="utf-8")
        try:
            rows = store.query(sparql, pack_id=pack_id, version=version)
        except Exception as exc:  # noqa: BLE001
            results.append(
                {"cq_id": entry.get("cq_id"), "status": "error", "message": str(exc)[:300]}
            )
            continue
        expected = entry.get("expected") or {}
        kind, value = expected.get("type"), expected.get("value")
        ok = True
        if kind == "row_count":
            ok = len(rows) == int(value or 0)
        elif kind == "min_rows":
            ok = len(rows) >= int(value or 0)
        elif kind == "max_rows":
            ok = len(rows) <= int(value or 0)
        elif kind == "ask_true":
            ok = bool(rows and rows[0].get("ask") is True)
        elif kind == "ask_false":
            ok = bool(rows and rows[0].get("ask") is False)
        elif kind == "contains_bindings":
            wanted = [{k: str(v) for k, v in b.items()} for b in (value or [])]
            have = [{k: str(v) for k, v in r.items()} for r in rows]
            ok = all(any(all(r.get(k) == v for k, v in w.items()) for r in have) for w in wanted)
        results.append(
            {"cq_id": entry.get("cq_id"), "status": "pass" if ok else "fail", "rows": len(rows)}
        )
    passed = sum(1 for r in results if r["status"] == "pass")
    return {"tests": len(results), "passed": passed, "results": results}


@transaction.atomic
def import_pack(pack_dir: Path, *, activate: bool = True, force: bool = False) -> Pack:
    """Validate and import a bundle directory; returns the Pack row. Raises on structural errors.

    An unchanged bundle (same manifest hash) is skipped unless `force`, which
    re-derives the vocabulary, for example after an importer change.
    """

    pack_dir = pack_dir.resolve()
    info = load_pack(pack_dir)
    if not info.ok:
        errors = "; ".join(f.message for f in info.findings if f.severity == "error")
        raise ValueError(f"pack {pack_dir.name} is not valid: {errors}")
    existing = Pack.objects.filter(pack_id=info.id, version=info.version).first()
    if (
        existing
        and existing.manifest_hash == info.manifest_hash
        and existing.status != Pack.Status.FAILED
    ):
        log.info("pack %s@%s unchanged; skipping import", info.id, info.version)
        return existing

    triple_count = store.load_bundle(pack_dir, info.id, info.version)
    rows = derive(pack_dir, info)
    cq = run_cq_tests(pack_dir, info.id, info.version)
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


def active_packs() -> list[Pack]:
    return list(Pack.objects.filter(status=Pack.Status.ACTIVE))


def dictionary_entries(pack: Pack) -> list[dict[str, Any]]:
    """Solr `entities` documents for every taggable label of a pack version."""

    out: list[dict[str, Any]] = []
    for concept in pack.concepts.filter(taggable=True).iterator():
        for label_type, labels in (
            ("pref", [concept.pref_label]),
            ("alt", concept.alt_labels),
            ("hidden", concept.hidden_labels),
        ):
            for label in labels:
                label = str(label).strip()
                if len(label) < 2:
                    continue
                out.append(
                    {
                        "id": f"{pack}|{concept.iri}|{label_type}|{slugify(label)}",
                        "iri": concept.iri,
                        "pack_id": pack.pack_id,
                        "pack_version": pack.version,
                        "label": label,
                        "pref_label": concept.pref_label,
                        "label_type": label_type,
                        "lang": concept.lang or "und",
                        "kind": concept.kind,
                        "type_iri": concept.type_iri,
                        "scheme": concept.scheme_slug,
                        "path": concept.path,
                        "case_sensitive": label.isupper() and len(label) <= 6,
                    }
                )
    return out


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
        if parents:
            children[parents[0]].append(c)
        else:
            roots.append(c)

    def node(c: Concept) -> dict[str, Any]:
        return {
            "iri": c.iri,
            "label": c.pref_label,
            "path": c.path,
            "kind": c.kind,
            "children": [node(x) for x in sorted(children[c.iri], key=lambda x: x.pref_label)],
        }

    return [node(r) for r in sorted(roots, key=lambda x: x.pref_label)]
