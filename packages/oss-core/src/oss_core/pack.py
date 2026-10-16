"""The pack contract: an `ontology` skill project bundle.

A pack is read, hashed and validated here without touching Solr, Neo4j or
Oxigraph. The checks implement the skill's own gates (structure, labels,
term support and evidence, the query manifest, SHACL over the fixtures, the
release manifest) without running the skill's code; see ADR-0007 and
docs/assess/pack-spec.md for what is and is not covered.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from rdflib import OWL, RDF, RDFS, SKOS, Graph, Namespace, URIRef

from oss_core import PACK_CONTRACT_VERSION as CONTRACT_VERSION

# The four RDF files, by stem. Each may be Turtle, N-Triples, RDF/XML or JSON-LD
# (chosen by extension), except shapes, which the validate stage concatenates
# with other packs' shapes as Turtle at run time.
RDF_STEMS = ("ontology", "taxonomy", "shapes", "instances")
RDF_FORMATS = {
    ".ttl": "turtle",
    ".nt": "nt",
    ".rdf": "xml",
    ".owl": "xml",
    ".xml": "xml",
    ".jsonld": "json-ld",
}
TURTLE_ONLY = {"shapes"}

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

# Excluded from the content hash and the release manifest, as the skill's
# release_manifest.py excludes them.
EXCLUDE_DIRS = {"validation", ".git", "__pycache__"}
EXCLUDE_FILES = {"release-manifest.json"}

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
EXPECTATIONS = {"row_count", "min_rows", "max_rows", "ask_true", "ask_false", "contains_bindings"}
STATUSES = {"draft", "candidate", "release_candidate", "released"}
SH = Namespace("http://www.w3.org/ns/shacl#")

_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}$")
_PACK_ID = re.compile(r"^[-a-zA-Z0-9_]{1,64}$")
_MAX_SHACL_FINDINGS = 50


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
    rdf: dict[str, Path] = field(default_factory=dict)  # stem -> file

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    @property
    def manifest_hash(self) -> str:
        payload = json.dumps(sorted(self.files.items())).encode()
        return hashlib.sha256(payload).hexdigest()


class PackYamlError(ValueError):
    pass


class RdfFileError(ValueError):
    pass


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


def excluded(rel: str) -> bool:
    """True for paths that are not pack content (validation reports, the manifest itself)."""

    parts = rel.split("/")
    return any(p in EXCLUDE_DIRS for p in parts) or parts[-1] in EXCLUDE_FILES


def rdf_files(path: Path) -> tuple[dict[str, Path], list[Finding]]:
    """The pack's RDF file per stem, whatever its serialisation, and any ambiguity."""

    found: dict[str, Path] = {}
    findings: list[Finding] = []
    for stem in RDF_STEMS:
        candidates = [
            path / f"{stem}{ext}" for ext in RDF_FORMATS if (path / f"{stem}{ext}").is_file()
        ]
        if not candidates:
            continue
        if len(candidates) > 1:
            names = ", ".join(c.name for c in candidates)
            findings.append(
                Finding("error", "ambiguous_rdf", f"{stem} is given more than once: {names}")
            )
            continue
        if stem in TURTLE_ONLY and candidates[0].suffix != ".ttl":
            findings.append(
                Finding(
                    "error",
                    "rdf_format",
                    f"{candidates[0].name}: shapes must be Turtle (shapes.ttl); the validate "
                    "stage concatenates every active pack's shapes as Turtle",
                )
            )
            continue
        found[stem] = candidates[0]
    return found, findings


def rdf_format(file: Path) -> str:
    """rdflib format name for a pack RDF file, by extension."""

    fmt = RDF_FORMATS.get(file.suffix.lower())
    if fmt is None:
        raise RdfFileError(f"{file.name}: unsupported RDF serialisation {file.suffix!r}")
    return fmt


def remote_contexts(node: Any) -> list[str]:
    """String (remote) @context references anywhere in a JSON-LD document."""

    out: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("@context", "@import"):
                items = value if isinstance(value, list) else [value]
                out += [v for v in items if isinstance(v, str)]
                out += [r for v in items if isinstance(v, dict) for r in remote_contexts(v)]
            else:
                out += remote_contexts(value)
    elif isinstance(node, list):
        for value in node:
            out += remote_contexts(value)
    return out


def parse_rdf(graph: Graph, file: Path) -> None:
    """Parse one pack RDF file into `graph`. JSON-LD must carry its context inline:
    a remote context would make validation and import fetch URLs from the network."""

    fmt = rdf_format(file)
    if fmt == "json-ld":
        try:
            doc = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RdfFileError(f"{file.name}: {exc}") from exc
        remote = remote_contexts(doc)
        if remote:
            raise RdfFileError(
                f"{file.name}: remote JSON-LD contexts are not fetched ({remote[0]}); "
                "inline the context"
            )
    graph.parse(file, format=fmt)


def load_pack(path: Path, *, force: bool = False) -> PackInfo:
    """Read, hash and validate a pack. Never raises on content errors.

    `force` turns a release-manifest mismatch into a warning (the operator
    accepts files that differ from the ones the manifest was written for).
    """

    path = path.resolve()
    findings: list[Finding] = []
    rdf, rdf_findings = rdf_files(path)
    findings += rdf_findings
    for rel in REQUIRED_FILES:
        stem = rel.removesuffix(".ttl")
        if stem in RDF_STEMS:
            if stem not in rdf and not any(f.message.startswith(stem) for f in rdf_findings):
                exts = "|".join(e.lstrip(".") for e in RDF_FORMATS)
                findings.append(Finding("error", "missing_file", f"{stem}.{{{exts}}} is required"))
        elif not (path / rel).exists():
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
        rdf=rdf,
    )
    for key in ("id", "version", "base_iri"):
        if not onto.get(key):
            findings.append(Finding("error", "missing_metadata", f"ontology.{key} is required"))
    _check_identity(info, onto, findings)

    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        rel = file.relative_to(path).as_posix()
        if excluded(rel):
            continue
        info.files[rel] = _sha256(file)
    findings += verify_manifest(info, force=force)

    # RDF files must parse; count what they declare.
    graphs: dict[str, Graph] = {}
    for stem, file in rdf.items():
        g = Graph()
        try:
            parse_rdf(g, file)
        except Exception as exc:  # noqa: BLE001 - report, don't crash
            findings.append(Finding("error", "rdf_parse", f"{file.name}: {exc}"))
            continue
        graphs[stem] = g
    graph = Graph()
    for g in graphs.values():
        graph += g
    info.counts = {
        "classes": len(set(graph.subjects(RDF.type, OWL.Class))),
        "object_properties": len(set(graph.subjects(RDF.type, OWL.ObjectProperty))),
        "data_properties": len(set(graph.subjects(RDF.type, OWL.DatatypeProperty))),
        "concept_schemes": len(set(graph.subjects(RDF.type, SKOS.ConceptScheme))),
        "concepts": len(set(graph.subjects(RDF.type, SKOS.Concept))),
        "shapes": len(set(graph.subjects(RDF.type, SH.NodeShape))),
        "individuals": len(set(graph.subjects(RDF.type, OWL.NamedIndividual))),
    }
    _check_rdf(info, graph, findings)

    model: dict[str, Any] = {}
    if (path / "model.yaml").exists():
        try:
            model = _load_yaml(path / "model.yaml")
        except PackYamlError as exc:
            findings.append(Finding("error", "yaml", str(exc)))
    evidence_ids = _check_evidence(path, findings)
    if model:
        _check_model(model, evidence_ids, findings)

    _check_queries(path, req, findings)
    if all(stem in graphs for stem in ("shapes", "instances")):
        findings += shacl_findings(graphs)

    if info.status not in STATUSES:
        findings.append(Finding("warning", "status", f"unusual status {info.status!r}"))
    return info


def _check_identity(info: PackInfo, onto: dict[str, Any], findings: list[Finding]) -> None:
    if onto.get("id") and not _PACK_ID.match(info.id):
        findings.append(
            Finding(
                "error",
                "bad_id",
                f"ontology.id {info.id!r} must be 1-64 letters, digits, hyphens or underscores",
            )
        )
    if not onto.get("version"):
        return
    if not _VERSION.match(info.version):
        findings.append(
            Finding(
                "error",
                "bad_version",
                f"ontology.version {info.version!r} must be 1-32 characters from "
                "letters, digits and . + _ -",
            )
        )
    elif not _SEMVER.match(info.version):
        findings.append(
            Finding(
                "warning",
                "version_format",
                f"ontology.version {info.version!r} is not semantic versioning (MAJOR.MINOR.PATCH)",
            )
        )


def _check_rdf(info: PackInfo, graph: Graph, findings: list[Finding]) -> None:
    # Every declared class and property needs a label and a definition.
    base = info.base_iri.rstrip("#/")
    for kind, rdf_type in (
        ("class", OWL.Class),
        ("object property", OWL.ObjectProperty),
        ("datatype property", OWL.DatatypeProperty),
    ):
        for subject in graph.subjects(RDF.type, rdf_type):
            if not base or not str(subject).startswith(base):
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
    for concept in sorted(set(graph.subjects(RDF.type, SKOS.Concept)), key=str):
        if isinstance(concept, URIRef) and graph.value(concept, SKOS.prefLabel) is None:
            findings.append(
                Finding("error", "no_preflabel", f"SKOS concept {concept} has no skos:prefLabel")
            )
    for target in sorted({str(o) for o in graph.objects(None, OWL.imports)}):
        findings.append(
            Finding(
                "warning",
                "owl_imports",
                f"owl:imports <{target}> is not followed; only this pack's own files are loaded",
            )
        )


def _check_evidence(path: Path, findings: list[Finding]) -> set[str]:
    file = path / "evidence.jsonl"
    if not file.exists():
        return set()
    ids: list[str] = []
    for lineno, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            findings.append(
                Finding("error", "evidence_json", f"evidence.jsonl line {lineno}: {exc}")
            )
            continue
        if not isinstance(item, dict) or not item.get("id"):
            findings.append(
                Finding("error", "evidence_json", f"evidence.jsonl line {lineno} has no id")
            )
            continue
        ids.append(str(item["id"]))
    for ident, n in sorted(Counter(ids).items()):
        if n > 1:
            findings.append(
                Finding("error", "duplicate_id", f"evidence id {ident} appears {n} times")
            )
    return set(ids)


def _refs(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v not in (None, "")]
    return [str(value)]


def _check_model(model: dict[str, Any], evidence_ids: set[str], findings: list[Finding]) -> None:
    terms = [t for t in model.get("terms") or [] if isinstance(t, dict)]
    for ident, n in sorted(Counter(str(t.get("id")) for t in terms if t.get("id")).items()):
        if n > 1:
            findings.append(Finding("error", "duplicate_id", f"term id {ident} appears {n} times"))
    for term in terms:
        tid = term.get("id", "?")
        if term.get("kind") not in TERM_KINDS:
            findings.append(
                Finding("error", "term_kind", f"{tid}: kind {term.get('kind')!r} unknown")
            )
        if term.get("support") not in TERM_SUPPORT:
            findings.append(
                Finding("error", "term_support", f"{tid}: support {term.get('support')!r} unknown")
            )
        if term.get("status") == "accepted":
            if term.get("support") == "modelling_construct" and not term.get("rationale"):
                findings.append(
                    Finding(
                        "error", "no_rationale", f"{tid}: modelling_construct needs a rationale"
                    )
                )
            if term.get("support") in {"source_supported", "derived"} and not term.get("evidence"):
                findings.append(
                    Finding("error", "no_evidence", f"{tid}: {term.get('support')} needs evidence")
                )
            if term.get("support") in {"proposed", "disputed"}:
                findings.append(
                    Finding(
                        "error",
                        "accepted_unsupported",
                        f"{tid}: accepted term cannot be {term.get('support')}",
                    )
                )
    items = terms + [a for a in model.get("axioms") or [] if isinstance(a, dict)]
    for item in items:
        missing = sorted(set(_refs(item.get("evidence"))) - evidence_ids)
        if missing:
            findings.append(
                Finding(
                    "error",
                    "unknown_evidence",
                    f"{item.get('id', '?')}: evidence {', '.join(missing)} not in evidence.jsonl",
                )
            )


def _inside(path: Path, file: Path) -> bool:
    try:
        file.resolve().relative_to(path)
    except ValueError:
        return False
    return True


def _named_rdf(path: Path, name: str, rdf: dict[str, Path]) -> Path:
    """A file a query manifest names. `instances.ttl` also finds instances.jsonld (or any
    other serialisation of one of the four RDF files), so a converted pack keeps its manifest."""

    file = path / name
    ref = Path(name)
    if (
        not file.exists()
        and ref.parent == Path(".")
        and ref.suffix.lower() in RDF_FORMATS
        and ref.stem in rdf
    ):
        return rdf[ref.stem]
    return file


def _check_queries(path: Path, req: dict[str, Any], findings: list[Finding]) -> None:
    manifest = path / "queries" / "manifest.yaml"
    if not manifest.exists():
        return
    try:
        spec = _load_yaml(manifest)
    except PackYamlError as exc:
        findings.append(Finding("error", "yaml", str(exc)))
        return
    items = spec.get("queries") if isinstance(spec, dict) else None
    if items is None:
        items = []
    if not isinstance(items, list):
        findings.append(
            Finding("error", "cq_manifest", "queries/manifest.yaml: queries is not a list")
        )
        return
    covered: set[str] = set()
    rdf, _ = rdf_files(path)
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            findings.append(Finding("error", "cq_manifest", f"query #{i} is not a mapping"))
            continue
        cq = str(item.get("cq_id") or f"query #{i}")
        covered.add(cq)
        if not item.get("cq_id"):
            findings.append(Finding("error", "cq_manifest", f"{cq} has no cq_id"))
        qfile = item.get("file")
        if not qfile:
            findings.append(Finding("error", "cq_file", f"{cq} has no query file"))
        elif not (path / "queries" / str(qfile)).is_file() or not _inside(
            path, path / "queries" / str(qfile)
        ):
            findings.append(Finding("error", "cq_file", f"{cq}: queries/{qfile} not found"))
        for name in [item.get("fixture"), *(item.get("extra_graphs") or [])]:
            if name in (None, ""):
                continue
            file = _named_rdf(path, str(name), rdf)
            if not _inside(path, file):
                findings.append(Finding("error", "cq_fixture", f"{cq}: {name} is outside the pack"))
            elif not file.is_file():
                findings.append(Finding("error", "cq_fixture", f"{cq}: {name} not found"))
            elif file.suffix.lower() not in RDF_FORMATS:
                findings.append(
                    Finding("error", "cq_fixture", f"{cq}: {name} is not an RDF serialisation")
                )
        expected = item.get("expected")
        if not isinstance(expected, dict):
            findings.append(
                Finding("error", "cq_expected", f"{cq} has no expected-result contract")
            )
        elif expected.get("type") not in EXPECTATIONS:
            findings.append(
                Finding(
                    "error",
                    "cq_expected",
                    f"{cq}: expected type {expected.get('type')!r} unknown "
                    f"(one of {', '.join(sorted(EXPECTATIONS))})",
                )
            )
    for cq_def in req.get("competency_questions") or []:
        if not isinstance(cq_def, dict) or str(cq_def.get("priority", "")).lower() != "core":
            continue
        cid = str(cq_def.get("id"))
        exempt = cq_def.get("queryable") is False and cq_def.get("nonqueryable_rationale")
        if cid not in covered and not exempt:
            findings.append(
                Finding("error", "cq_coverage", f"core competency question {cid} has no query")
            )


def shacl_findings(graphs: dict[str, Graph]) -> list[Finding]:
    """SHACL over the pack's own fixtures, as the skill's run_shacl.py does it: instances as
    the data graph, ontology and taxonomy mixed in, no inference."""

    from pyshacl import validate

    shapes = graphs["shapes"]
    if len(shapes) == 0:
        return []
    ont = Graph()
    for stem in ("ontology", "taxonomy"):
        if stem in graphs:
            ont += graphs[stem]
    try:
        _conforms, report, _text = validate(
            graphs["instances"],
            shacl_graph=shapes,
            ont_graph=ont,
            inference=None,
            advanced=False,
            abort_on_first=False,
        )
    except Exception as exc:  # noqa: BLE001 - a broken shapes file is a finding
        return [Finding("error", "shacl_engine", f"SHACL validation failed to run: {exc}")]
    levels = {SH.Violation: "error", SH.Warning: "warning", SH.Info: "info"}
    out: list[Finding] = []
    counts: Counter[str] = Counter()
    results = sorted(
        report.subjects(RDF.type, SH.ValidationResult),
        key=lambda r: (str(report.value(r, SH.focusNode)), str(report.value(r, SH.resultPath))),
    )
    for result in results:
        level = levels.get(report.value(result, SH.resultSeverity), "error")
        counts[level] += 1
        if counts[level] > _MAX_SHACL_FINDINGS:
            continue
        focus = report.value(result, SH.focusNode)
        rpath = report.value(result, SH.resultPath)
        message = report.value(result, SH.resultMessage)
        where = f"{focus}" + (f" {rpath}" if rpath is not None else "")
        code = {"error": "shacl_violation", "warning": "shacl_warning", "info": "shacl_info"}[level]
        out.append(Finding(level, code, f"{where}: {message or 'does not conform'}"))
    for level, n in counts.items():
        if n > _MAX_SHACL_FINDINGS:
            out.append(
                Finding(level, "shacl_more", f"{n - _MAX_SHACL_FINDINGS} more SHACL {level}s")
            )
    return out


def verify_manifest(info: PackInfo, *, force: bool = False) -> list[Finding]:
    """Compare release-manifest.json with the files on disk.

    Entries the hash excludes (validation reports, written by the skill's
    release_manifest.py too) are ignored. A difference is an error unless
    `force`. A pack without a manifest gets a warning, unless it says it is
    `released`: a released pack's files are what its manifest vouches for, so a
    missing manifest is an error (a warning with `force`).
    """

    severity = "warning" if force else "error"
    file = info.path / "release-manifest.json"
    if not file.exists():
        if info.status == "released":
            return [
                Finding(
                    severity,
                    "no_manifest",
                    "release-manifest.json is absent from a released pack; "
                    "its files cannot be verified",
                )
            ]
        return [
            Finding(
                "warning",
                "no_manifest",
                "release-manifest.json is absent; file hashes are not verified",
            )
        ]
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
        listed = {
            str(e["path"]): str(e["sha256"])
            for e in data.get("files") or []
            if not excluded(str(e["path"]))
        }
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        return [
            Finding(severity, "manifest_mismatch", f"release-manifest.json is unreadable: {exc}")
        ]
    out: list[Finding] = []
    for key, have in (("ontology_id", info.id), ("ontology_version", info.version)):
        if data.get(key) not in (None, have):
            out.append(
                Finding(
                    severity,
                    "manifest_mismatch",
                    f"release-manifest.json {key} is {data.get(key)!r}, the pack says {have!r}",
                )
            )
    changed = sorted(k for k in listed if k in info.files and listed[k] != info.files[k])
    missing = sorted(k for k in listed if k not in info.files)
    unlisted = sorted(k for k in info.files if k not in listed)
    for label, names in (
        ("changed since the manifest was written", changed),
        ("listed in the manifest but missing", missing),
        ("not listed in the manifest", unlisted),
    ):
        if names:
            out.append(Finding(severity, "manifest_mismatch", f"{label}: {', '.join(names)}"))
    return out


def write_manifest(info: PackInfo, out: Path | None = None) -> Path:
    """Write a content-hash manifest in the same shape as the skill's release_manifest.py."""

    out = out or info.path / "release-manifest.json"
    payload = {
        "ontology_id": info.id,
        "ontology_version": info.version,
        "project_status": info.status,
        "contract_version": CONTRACT_VERSION,
        "files": [{"path": rel, "sha256": digest} for rel, digest in sorted(info.files.items())],
    }
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out


# --- competency questions ----------------------------------------------------------------------


def _cq_graph(path: Path, item: dict[str, Any], rdf: dict[str, Path]) -> Graph:
    """The query dataset a manifest entry names: its fixture (default instances), the
    ontology and taxonomy unless excluded, and any extra graphs, as the skill builds it."""

    graph = Graph()
    files: list[Path] = []
    fixture = item.get("fixture")
    if fixture:
        files.append(_named_rdf(path, str(fixture), rdf))
    elif "instances" in rdf:
        files.append(rdf["instances"])
    if item.get("include_ontology", True) and "ontology" in rdf:
        files.append(rdf["ontology"])
    if item.get("include_taxonomy", True) and "taxonomy" in rdf:
        files.append(rdf["taxonomy"])
    files += [_named_rdf(path, str(extra), rdf) for extra in item.get("extra_graphs") or []]
    for file in files:
        if not _inside(path, file) or not file.is_file():
            raise RdfFileError(f"{file.name} not found in the pack")
        parse_rdf(graph, file)
    return graph


def _evaluate(result: Any, expected: dict[str, Any]) -> tuple[bool, int | None, str]:
    kind, value = expected.get("type"), expected.get("value")
    if result.type == "ASK":
        actual = bool(result.askAnswer)
        if kind == "ask_true":
            return actual, None, f"ASK expected true, got {actual}"
        if kind == "ask_false":
            return not actual, None, f"ASK expected false, got {actual}"
        return False, None, f"expectation {kind!r} is not valid for an ASK query"
    if result.type != "SELECT":
        return False, None, "only ASK and SELECT competency queries are supported"
    names = [str(v) for v in result.vars or []]
    rows = [
        {n: (None if row[i] is None else str(row[i])) for i, n in enumerate(names)}
        for row in result
    ]
    n = len(rows)
    if kind == "row_count":
        return n == int(value or 0), n, f"expected {value} rows, got {n}"
    if kind == "min_rows":
        return n >= int(value or 0), n, f"expected at least {value} rows, got {n}"
    if kind == "max_rows":
        return n <= int(value or 0), n, f"expected at most {value} rows, got {n}"
    if kind == "contains_bindings":
        wanted = value if isinstance(value, list) else []
        missing = [
            w
            for w in wanted
            if not any(all(r.get(k) == str(v) for k, v in w.items()) for r in rows)
        ]
        return not missing, n, f"missing bindings: {missing}" if missing else "all bindings present"
    if kind in ("ask_true", "ask_false"):
        return False, n, f"expectation {kind!r} needs an ASK query"
    return False, n, f"unknown expectation type {kind!r}"


def run_competency_questions(path: Path) -> dict[str, Any]:
    """Run the bundle's competency queries, each over the dataset its manifest entry names.

    Statuses: pass, fail, error (the query or its dataset could not be loaded),
    missing (no query file), skipped-reasoner (an entailment regime is
    declared; Semascope does not materialise inferences).
    """

    path = path.resolve()
    manifest = path / "queries" / "manifest.yaml"
    if not manifest.exists():
        return {"tests": 0, "passed": 0, "results": []}
    try:
        spec = _load_yaml(manifest)
    except PackYamlError as exc:
        return {"tests": 0, "passed": 0, "results": [], "error": str(exc)}
    rdf, _ = rdf_files(path)
    results: list[dict[str, Any]] = []
    for item in (spec.get("queries") if isinstance(spec, dict) else None) or []:
        if not isinstance(item, dict):
            results.append({"cq_id": None, "status": "error", "message": "not a mapping"})
            continue
        cq_id = item.get("cq_id")
        qfile = path / "queries" / str(item.get("file"))
        if not item.get("file") or not qfile.is_file() or not _inside(path, qfile):
            results.append({"cq_id": cq_id, "status": "missing", "message": "no query file"})
            continue
        if str(item.get("entailment") or "none").lower() != "none":
            results.append({"cq_id": cq_id, "status": "skipped-reasoner"})
            continue
        expected = item.get("expected")
        if not isinstance(expected, dict) or expected.get("type") not in EXPECTATIONS:
            kind = expected.get("type") if isinstance(expected, dict) else None
            results.append(
                {"cq_id": cq_id, "status": "fail", "message": f"unknown expectation {kind!r}"}
            )
            continue
        try:
            graph = _cq_graph(path, item, rdf)
            result = graph.query(qfile.read_text(encoding="utf-8"))
            ok, rows, message = _evaluate(result, expected)
        except Exception as exc:  # noqa: BLE001 - a broken query is a result, not a crash
            results.append({"cq_id": cq_id, "status": "error", "message": str(exc)[:300]})
            continue
        entry: dict[str, Any] = {"cq_id": cq_id, "status": "pass" if ok else "fail"}
        if rows is not None:
            entry["rows"] = rows
        if not ok:
            entry["message"] = message[:300]
        results.append(entry)
    passed = sum(1 for r in results if r["status"] == "pass")
    return {"tests": len(results), "passed": passed, "results": results}
