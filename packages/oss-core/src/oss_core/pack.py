"""The pack contract: an `ontology` skill project bundle.

A pack is read, hashed and validated here without touching Solr, Neo4j or
Oxigraph. Structural checks mirror the skill's `validate_project.py` gates so a
bundle that passes the skill passes here, and vice versa.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from rdflib import OWL, RDF, RDFS, SKOS, Graph, Namespace

REQUIRED_FILES = (
    "requirements.yaml",
    "sources.yaml",
    "evidence.jsonl",
    "decisions.yaml",
    "model.yaml",
    "ontology.ttl",
    "taxonomy.ttl",
    "shapes.ttl",
    "instances.ttl",
    "queries/manifest.yaml",
    "README.md",
)

TERM_KINDS = {
    "class",
    "object_property",
    "data_property",
    "annotation_property",
    "skos_concept",
    "individual",
}
TERM_SUPPORT = {
    "source_supported",
    "derived",
    "external",
    "modelling_construct",
    "proposed",
    "disputed",
}
SH = Namespace("http://www.w3.org/ns/shacl#")


@dataclass
class Finding:
    severity: str  # error | warning | info
    code: str
    message: str


@dataclass
class PackInfo:
    path: Path
    id: str
    version: str
    title: str
    base_iri: str
    status: str
    licence: str | None
    files: dict[str, str] = field(default_factory=dict)  # relative path -> sha256
    counts: dict[str, int] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    @property
    def manifest_hash(self) -> str:
        payload = json.dumps(sorted(self.files.items())).encode()
        return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PackYamlError(f"{path.name}: {str(exc).splitlines()[0]}") from exc


class PackYamlError(ValueError):
    pass


def load_pack(path: Path) -> PackInfo:
    """Read, hash and structurally validate a pack. Never raises on content errors."""

    path = path.resolve()
    findings: list[Finding] = []
    for rel in REQUIRED_FILES:
        if not (path / rel).exists():
            findings.append(Finding("error", "missing_file", f"{rel} is required"))

    req: dict[str, Any] = {}
    if (path / "requirements.yaml").exists():
        try:
            req = _load_yaml(path / "requirements.yaml")
        except PackYamlError as exc:
            findings.append(Finding("error", "yaml", str(exc)))
    onto = req.get("ontology") or {}
    info = PackInfo(
        path=path,
        id=str(onto.get("id") or path.name),
        version=str(onto.get("version") or "0.0.0"),
        title=str(onto.get("title") or path.name),
        base_iri=str(onto.get("base_iri") or ""),
        status=str(onto.get("status") or "draft"),
        licence=onto.get("licence"),
        findings=findings,
    )
    for key in ("id", "version", "base_iri"):
        if not onto.get(key):
            findings.append(Finding("error", "missing_metadata", f"ontology.{key} is required"))

    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        rel = file.relative_to(path).as_posix()
        if rel.startswith("validation/") or rel == "release-manifest.json":
            continue
        info.files[rel] = _sha256(file)

    # RDF files must parse; count what they declare.
    graph = Graph()
    for name in ("ontology.ttl", "taxonomy.ttl", "shapes.ttl", "instances.ttl"):
        file = path / name
        if not file.exists():
            continue
        try:
            graph.parse(file, format="turtle")
        except Exception as exc:  # noqa: BLE001 - report, don't crash
            findings.append(Finding("error", "rdf_parse", f"{name}: {exc}"))
    info.counts = {
        "classes": len(set(graph.subjects(RDF.type, OWL.Class))),
        "object_properties": len(set(graph.subjects(RDF.type, OWL.ObjectProperty))),
        "data_properties": len(set(graph.subjects(RDF.type, OWL.DatatypeProperty))),
        "concept_schemes": len(set(graph.subjects(RDF.type, SKOS.ConceptScheme))),
        "concepts": len(set(graph.subjects(RDF.type, SKOS.Concept))),
        "shapes": len(set(graph.subjects(RDF.type, SH.NodeShape))),
        "individuals": len(set(graph.subjects(RDF.type, OWL.NamedIndividual))),
    }

    # Every declared class and property needs a label and a definition.
    for kind, rdf_type in (
        ("class", OWL.Class),
        ("object property", OWL.ObjectProperty),
        ("datatype property", OWL.DatatypeProperty),
    ):
        for subject in graph.subjects(RDF.type, rdf_type):
            if not str(subject).startswith(info.base_iri.rstrip("#/")):
                continue
            if graph.value(subject, RDFS.label) is None:
                findings.append(Finding("error", "no_label", f"{kind} {subject} has no rdfs:label"))
            if (
                graph.value(subject, RDFS.comment) is None
                and graph.value(subject, SKOS.definition) is None
            ):
                findings.append(
                    Finding("error", "no_definition", f"{kind} {subject} has no definition")
                )

    # model.yaml terms: kinds, support, status, evidence or rationale.
    model: dict[str, Any] = {}
    if (path / "model.yaml").exists():
        try:
            model = _load_yaml(path / "model.yaml")
        except PackYamlError as exc:
            findings.append(Finding("error", "yaml", str(exc)))
    if model:
        for term in model.get("terms") or []:
            tid = term.get("id", "?")
            if term.get("kind") not in TERM_KINDS:
                findings.append(
                    Finding("error", "term_kind", f"{tid}: kind {term.get('kind')!r} unknown")
                )
            if term.get("support") not in TERM_SUPPORT:
                findings.append(
                    Finding(
                        "error", "term_support", f"{tid}: support {term.get('support')!r} unknown"
                    )
                )
            if term.get("status") == "accepted":
                if term.get("support") == "modelling_construct" and not term.get("rationale"):
                    findings.append(
                        Finding(
                            "error", "no_rationale", f"{tid}: modelling_construct needs a rationale"
                        )
                    )
                if term.get("support") in {"source_supported", "derived"} and not term.get(
                    "evidence"
                ):
                    findings.append(
                        Finding(
                            "error", "no_evidence", f"{tid}: {term.get('support')} needs evidence"
                        )
                    )
                if term.get("support") in {"proposed", "disputed"}:
                    findings.append(
                        Finding(
                            "error",
                            "accepted_unsupported",
                            f"{tid}: accepted term cannot be {term.get('support')}",
                        )
                    )

    if info.status not in {"draft", "candidate", "release_candidate", "released"}:
        findings.append(Finding("warning", "status", f"unusual status {info.status!r}"))
    return info


def write_manifest(info: PackInfo, out: Path | None = None) -> Path:
    """Write a content-hash manifest in the same shape as the skill's release_manifest.py."""

    out = out or info.path / "release-manifest.json"
    payload = {
        "ontology_id": info.id,
        "ontology_version": info.version,
        "project_status": info.status,
        "contract_version": "2.2.0",
        "files": [{"path": rel, "sha256": digest} for rel, digest in sorted(info.files.items())],
    }
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out
