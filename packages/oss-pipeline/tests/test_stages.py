from pathlib import Path

from oss_core.contracts import StageContext, WorkingDocument
from oss_pipeline.media import content_type_group
from oss_pipeline.registry import PROFILES, profile, stages
from oss_pipeline.stages.base import run_profile
from oss_pipeline.stages.index import to_solr_document
from oss_pipeline.stages.patterns import PatternsStage
from oss_pipeline.stages.structure import StructureStage, chunk_words, split_pages

FIXTURES = Path(__file__).parent / "fixtures"


def _doc(text: str) -> WorkingDocument:
    return WorkingDocument(
        id="d1", collection="c", source_id="s", uri="file:///x.txt", content_hash="h", text=text
    )


def test_patterns_find_every_entity_type() -> None:
    doc = PatternsStage().run(
        _doc(FIXTURES.joinpath("sample.txt").read_text()),
        StageContext(collection="c", profile="default"),
    )
    assert doc.fields["emails"] == ["test@example.org"]
    assert doc.fields["urls"] == ["https://example.org/page"]
    assert doc.fields["phones"] == ["+61 7 3000 0000"]
    assert doc.fields["money"] == ["$1,250.50"]
    assert doc.fields["hashtags"] == ["#phase1"]
    assert all(t.spans for t in doc.tags)


def test_structure_pages_and_chunks() -> None:
    text = "page one words " * 50 + "\f" + "page two words " * 50
    doc = StructureStage().run(
        _doc(text),
        StageContext(
            collection="c", profile="default", settings={"chunk_words": 60, "chunk_overlap": 10}
        ),
    )
    assert len(doc.pages) == 2
    assert doc.fields["chunk_pages"][0] == 1 and doc.fields["chunk_pages"][-1] == 2
    assert all(len(c.split()) <= 60 for c in doc.chunks)
    assert split_pages("") == []
    assert chunk_words("a b c", 2, 1) == ["a b", "b c"]


def test_solr_document_shape() -> None:
    doc = _doc("hello")
    doc.fields["emails"] = ["a@b.c"]
    doc.fields["x_owner_s"] = "ops"
    out = to_solr_document(doc)
    assert out["id"] == "d1" and out["emails_ss"] == ["a@b.c"] and out["owner_s"] == "ops"
    assert out["language_s"] == "und" and "indexed_dt" in out


def test_media_groups() -> None:
    assert content_type_group("application/pdf") == "text"
    assert content_type_group("image/png; charset=binary") == "image"
    assert content_type_group("text/html") == "web"
    assert content_type_group("application/zip") == "archive"
    assert content_type_group(None) == "text"


def test_profiles_validate_as_dags() -> None:
    available = stages()
    for name in PROFILES:
        assert [s.name for s in profile(name, available)] == PROFILES[name]


def test_run_profile_records_failures_and_stops_early() -> None:
    doc = _doc("x")
    # fetch fails (no local_path); extract must not run afterwards
    out = run_profile(
        profile("fast"),
        doc,
        StageContext(collection="c", profile="fast", settings={"blob_dir": "/nonexistent"}),
    )
    assert [r.outcome for r in out.stage_runs] == ["failed"]
    assert out.stage_runs[0].stage == "fetch"


def test_expand_paths() -> None:
    from oss_pipeline.stages.index import expand_paths

    assert expand_paths(["a/b/c", "a/d"]) == ["a", "a/b", "a/b/c", "a/d"]
    doc = _doc("x")
    doc.fields["x_tradition_paths"] = ["indian/buddhism"]
    assert to_solr_document(doc)["tradition_paths"] == ["indian", "indian/buddhism"]


def test_solr_dates_are_normalised() -> None:
    from oss_pipeline.stages.index import _iso

    assert _iso("2026-10-15T10:28:48.675313+00:00") == "2026-10-15T10:28:48Z"
    assert _iso("2026-10-15T20:28:48+10:00") == "2026-10-15T10:28:48Z"
    assert _iso("not a date") is None
    doc = _doc("x")
    doc.metadata["modified_at"] = "2026-01-02T03:04:05Z"
    assert to_solr_document(doc)["modified_dt"] == "2026-01-02T03:04:05Z"


def test_media_type_normalisation() -> None:
    from oss_pipeline.clients.tika import _normalise_media_type

    assert _normalise_media_type("image/ocr-png") == "image/png"
    assert _normalise_media_type("application/pdf; charset=binary") == "application/pdf"
    assert _normalise_media_type(None) is None
