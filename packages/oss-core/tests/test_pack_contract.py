"""The stricter pack checks: release manifest, SHACL, competency questions, the gap list
from docs/assess/pack-spec.md, and the RDF serialisations other than Turtle."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from rdflib import Graph

from oss_core.pack import load_pack, run_competency_questions, write_manifest

REPO = Path(__file__).resolve().parents[3]
DEMO = REPO / "packs" / "demo" / "pack"


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    out = tmp_path / "pack"
    shutil.copytree(DEMO, out)
    return out


def _codes(path: Path, severity: str = "error", **kwargs: bool) -> set[str]:
    return {f.code for f in load_pack(path, **kwargs).findings if f.severity == severity}


def _reseal(path: Path) -> None:
    """Rewrite release-manifest.json after an edit, so only the edit is under test."""

    write_manifest(load_pack(path, force=True))


def test_shipped_packs_pass_every_check() -> None:
    for path in (REPO / "packs" / "base", DEMO):
        info = load_pack(path)
        assert info.ok, [f.message for f in info.findings if f.severity == "error"]
        assert not [f for f in info.findings if f.severity == "warning"], info.findings
        cq = run_competency_questions(path)
        assert cq["tests"] and cq["passed"] == cq["tests"], cq


# --- release manifest ---------------------------------------------------------------------------


def test_manifest_mismatch_is_an_error_unless_forced(pack: Path) -> None:
    (pack / "README.md").write_text("changed\n", encoding="utf-8")
    (pack / "notes.txt").write_text("new\n", encoding="utf-8")
    info = load_pack(pack)
    mismatches = [f.message for f in info.findings if f.code == "manifest_mismatch"]
    assert not info.ok
    assert any("README.md" in m and "changed" in m for m in mismatches)
    assert any("notes.txt" in m and "not listed" in m for m in mismatches)
    forced = load_pack(pack, force=True)
    assert forced.ok and "manifest_mismatch" in _codes(pack, "warning", force=True)


def test_manifest_version_and_missing_files(pack: Path) -> None:
    manifest = json.loads((pack / "release-manifest.json").read_text())
    manifest["ontology_version"] = "9.9.9"
    manifest["files"].append({"path": "gone.ttl", "sha256": "0" * 64})
    # the skill's release_manifest.py lists validation reports; they are not content
    manifest["files"].append({"path": "validation/x.json", "sha256": "1" * 64})
    (pack / "release-manifest.json").write_text(json.dumps(manifest))
    messages = [f.message for f in load_pack(pack).findings if f.code == "manifest_mismatch"]
    assert any("9.9.9" in m for m in messages)
    assert any("gone.ttl" in m and "missing" in m for m in messages)
    assert not any("validation/x.json" in m for m in messages)


def test_no_manifest_is_a_warning(pack: Path) -> None:
    (pack / "release-manifest.json").unlink()
    assert load_pack(pack).ok and "no_manifest" in _codes(pack, "warning")


# --- SHACL over the pack's own fixtures ---------------------------------------------------------


def test_shacl_violations_in_instances_are_errors(pack: Path) -> None:
    with (pack / "instances.ttl").open("a", encoding="utf-8") as fh:
        # a Place with two preferred labels and a latitude out of range
        fh.write(
            '\nex:atlantis a owl:NamedIndividual, ex:Place ; skos:prefLabel "Atlantis"@en, '
            '"Atlantide"@en ; ex:latitude "123.0"^^xsd:decimal .\n'
        )
    _reseal(pack)
    info = load_pack(pack)
    violations = [f.message for f in info.findings if f.code == "shacl_violation"]
    assert not info.ok and violations
    assert all("atlantis" in m for m in violations)


def test_shacl_warning_severity_is_a_warning(pack: Path) -> None:
    with (pack / "shapes.ttl").open("a", encoding="utf-8") as fh:
        fh.write(
            "\nex:TextShape a sh:NodeShape ; sh:targetClass ex:Text ;\n"
            "    sh:property [ sh:path skos:definition ; sh:minCount 1 ;\n"
            "                  sh:severity sh:Warning ] .\n"
        )
    _reseal(pack)
    info = load_pack(pack)
    assert info.ok, [f.message for f in info.findings if f.severity == "error"]
    assert "shacl_warning" in _codes(pack, "warning")


# --- competency questions -----------------------------------------------------------------------


def _manifest(pack: Path) -> dict[str, Any]:
    import yaml

    data: dict[str, Any] = yaml.safe_load((pack / "queries" / "manifest.yaml").read_text())
    return data


def _write_manifest(pack: Path, data: dict[str, Any]) -> None:
    import yaml

    (pack / "queries" / "manifest.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    _reseal(pack)


def test_unknown_expectation_fails(pack: Path) -> None:
    data = _manifest(pack)
    data["queries"][0]["expected"] = {"type": "rows_at_least", "value": 1}
    _write_manifest(pack, data)
    assert "cq_expected" in _codes(pack)
    result = run_competency_questions(pack)["results"][0]
    assert result["status"] == "fail" and "rows_at_least" in result["message"]


def test_missing_expectation_and_query_file_fail(pack: Path) -> None:
    data = _manifest(pack)
    del data["queries"][0]["expected"]
    data["queries"][1]["file"] = "nope.rq"
    _write_manifest(pack, data)
    assert {"cq_expected", "cq_file"} <= _codes(pack)


def test_core_question_without_a_query_is_an_error(pack: Path) -> None:
    data = _manifest(pack)
    data["queries"] = [q for q in data["queries"] if q["cq_id"] != "CQ-001"]
    _write_manifest(pack, data)
    assert "cq_coverage" in _codes(pack)


def test_query_dataset_honours_the_manifest(pack: Path) -> None:
    (pack / "queries" / "classes.rq").write_text(
        "PREFIX owl: <http://www.w3.org/2002/07/owl#>\nSELECT ?c WHERE { ?c a owl:Class }\n"
    )
    (pack / "queries" / "concepts.rq").write_text(
        "PREFIX skos: <http://www.w3.org/2004/02/skos/core#>\n"
        "SELECT ?c WHERE { ?c a skos:Concept }\n"
    )
    (pack / "fixture.ttl").write_text(
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n<urn:x> a owl:Class .\n"
    )
    data = _manifest(pack)
    data["queries"] += [
        {
            "cq_id": "T-1",
            "file": "classes.rq",
            "include_ontology": False,
            "expected": {"type": "row_count", "value": 0},
        },
        {"cq_id": "T-2", "file": "classes.rq", "expected": {"type": "min_rows", "value": 5}},
        {
            "cq_id": "T-3",
            "file": "concepts.rq",
            "include_taxonomy": False,
            "expected": {"type": "row_count", "value": 0},
        },
        {
            "cq_id": "T-4",
            "file": "classes.rq",
            "fixture": "fixture.ttl",
            "include_ontology": False,
            "expected": {"type": "contains_bindings", "value": [{"c": "urn:x"}]},
        },
    ]
    _write_manifest(pack, data)
    assert load_pack(pack).ok
    by_id = {r["cq_id"]: r for r in run_competency_questions(pack)["results"]}
    assert all(by_id[t]["status"] == "pass" for t in ("T-1", "T-2", "T-3", "T-4")), by_id


def test_fixture_outside_the_pack_is_refused(pack: Path) -> None:
    data = _manifest(pack)
    data["queries"][0]["fixture"] = "../../outside.ttl"
    _write_manifest(pack, data)
    assert "cq_fixture" in _codes(pack)
    assert run_competency_questions(pack)["results"][0]["status"] == "error"


# --- gaps from pack-spec.md ---------------------------------------------------------------------


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_version_format(pack: Path) -> None:
    _edit(pack / "requirements.yaml", "version: 0.1.0", "version: '2026-10'")
    _reseal(pack)
    assert load_pack(pack).ok and "version_format" in _codes(pack, "warning")
    _edit(pack / "requirements.yaml", "version: '2026-10'", "version: 'v 1 <x>'")
    assert "bad_version" in _codes(pack, force=True)


def test_duplicate_term_ids(pack: Path) -> None:
    _edit(pack / "model.yaml", "{id: TERM-002,", "{id: TERM-001,")
    _reseal(pack)
    assert "duplicate_id" in _codes(pack)


def test_concept_without_preflabel(pack: Path) -> None:
    _edit(pack / "taxonomy.ttl", 'skos:prefLabel "Judaism"@en ;', "")
    _reseal(pack)
    assert "no_preflabel" in _codes(pack)


def test_unknown_evidence(pack: Path) -> None:
    _edit(pack / "evidence.jsonl", '{"id":"EV-001"', '{"id":"EV-901"')
    _reseal(pack)
    messages = [f.message for f in load_pack(pack).findings if f.code == "unknown_evidence"]
    assert messages and all("EV-001" in m for m in messages)


def test_owl_imports_warns(pack: Path) -> None:
    with (pack / "ontology.ttl").open("a", encoding="utf-8") as fh:
        fh.write(
            "\n<https://semascope.dev/ontology/demo> owl:imports <http://purl.org/dc/terms/> .\n"
        )
    _reseal(pack)
    info = load_pack(pack)
    assert info.ok and "owl_imports" in _codes(pack, "warning")


# --- serialisations -----------------------------------------------------------------------------


def _convert(pack: Path, stem: str, ext: str, fmt: str) -> None:
    g = Graph().parse(pack / f"{stem}.ttl", format="turtle")
    (pack / f"{stem}.ttl").unlink()
    g.serialize(destination=pack / f"{stem}{ext}", format=fmt)


def test_other_serialisations_load_like_turtle(pack: Path) -> None:
    before = load_pack(DEMO).counts
    _convert(pack, "taxonomy", ".rdf", "xml")
    _convert(pack, "instances", ".jsonld", "json-ld")
    _convert(pack, "ontology", ".nt", "nt")
    _reseal(pack)
    info = load_pack(pack)
    assert info.ok, [f.message for f in info.findings if f.severity == "error"]
    assert info.counts == before
    assert {p.name for p in info.rdf.values()} == {
        "taxonomy.rdf",
        "instances.jsonld",
        "ontology.nt",
        "shapes.ttl",
    }
    cq = run_competency_questions(pack)
    assert cq["passed"] == cq["tests"], cq


def test_remote_jsonld_context_is_refused(pack: Path) -> None:
    (pack / "instances.ttl").unlink()
    (pack / "instances.jsonld").write_text(
        json.dumps({"@context": "https://example.org/ctx.jsonld", "@id": "urn:a"})
    )
    _reseal(pack)
    messages = [f.message for f in load_pack(pack).findings if f.code == "rdf_parse"]
    assert any("remote JSON-LD" in m for m in messages)


def test_ambiguous_and_non_turtle_shapes(pack: Path) -> None:
    shutil.copy(pack / "taxonomy.ttl", pack / "taxonomy.nt")
    _convert(pack, "shapes", ".rdf", "xml")
    _reseal(pack)
    codes = _codes(pack)
    assert {"ambiguous_rdf", "rdf_format"} <= codes
    assert "missing_file" not in codes


def test_a_released_pack_without_its_manifest_is_an_error(pack: Path) -> None:
    """SEMA-039 / N-8: deleting release-manifest.json must not skip verification of a
    released pack; `force` accepts it with a warning."""

    req = pack / "requirements.yaml"
    req.write_text(req.read_text().replace("status: draft", "status: released", 1))
    (pack / "release-manifest.json").unlink()
    info = load_pack(pack)
    assert not info.ok and "no_manifest" in _codes(pack)
    assert load_pack(pack, force=True).ok and "no_manifest" in _codes(pack, "warning", force=True)
