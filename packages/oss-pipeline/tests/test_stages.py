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
    assert "preview" in PROFILES["default"]
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


def test_preview_renders_pdf_and_image(tmp_path: Path) -> None:
    import pypdfium2 as pdfium
    from PIL import Image

    from oss_pipeline.stages.preview import PreviewStage

    pdf = pdfium.PdfDocument.new()
    pdf.new_page(200, 300)
    pdf.new_page(200, 300)
    pdf_path = tmp_path / "two.pdf"
    pdf.save(str(pdf_path))
    img_path = tmp_path / "pic.png"
    Image.new("RGB", (1600, 800), "white").save(img_path)
    ctx = StageContext(
        collection="c",
        profile="default",
        settings={"blob_dir": str(tmp_path), "preview_pages": 1, "preview_width": 300},
    )

    doc = _doc("")
    doc.media_type = "application/pdf"
    doc.fields["blob_path"] = str(pdf_path)
    assert PreviewStage().run(doc, ctx).fields["preview_pages"] == 1
    assert (tmp_path / "previews" / "h" / "1.webp").exists()

    doc2 = _doc("")
    doc2.content_hash = "i"
    doc2.media_type = "image/png"
    doc2.fields["blob_path"] = str(img_path)
    assert PreviewStage().run(doc2, ctx).fields["preview_pages"] == 1
    with Image.open(tmp_path / "previews" / "i" / "1.webp") as out:
        assert out.width == 300


def test_solr_document_carries_pack_tags() -> None:
    from oss_core.contracts import Tag

    doc = _doc("x")
    doc.tags = [
        Tag(
            iri="https://p/themes#flood",
            label="Flood",
            kind="concept",
            pack_id="p",
            pack_version="1",
            stage="tag",
        ),
        Tag(
            iri="https://p#odysseus",
            label="Odysseus",
            kind="entity",
            pack_id="p",
            pack_version="1",
            stage="tag",
        ),
        Tag(
            iri="https://semascope.dev/ontology/base#Person/x",
            label="X",
            kind="entity",
            stage="ner",
        ),
    ]
    out = to_solr_document(doc)
    assert out["concept_iris_ss"] == ["https://p/themes#flood"]
    assert out["entity_iris_ss"] == ["https://p#odysseus"] and out["entity_labels_ss"] == [
        "Odysseus"
    ]
    assert out["pack_versions_ss"] == ["p@1"]


def test_ner_name_filter() -> None:
    from oss_pipeline.stages.ner import looks_like_name

    assert (
        looks_like_name("Odysseus")
        and looks_like_name("Mount Olympus")
        and looks_like_name("A. T. Bartholomew")
    )
    assert (
        not looks_like_name("thou")
        and not looks_like_name("bush")
        and not looks_like_name("2.6.19")
    )
    assert not looks_like_name("--with-autologon") and not looks_like_name("/dev/ scd0")
    assert not looks_like_name("a " * 10)


def test_curated_decisions_shape_the_index() -> None:
    from oss_core.contracts import Tag
    from oss_pipeline.stages.index import effective_tags

    doc = _doc("x")
    doc.tags = [
        Tag(
            iri="https://p#a", label="A", kind="concept", pack_id="p", pack_version="1", stage="tag"
        ),
        Tag(
            iri="https://p#b", label="B", kind="concept", pack_id="p", pack_version="1", stage="tag"
        ),
    ]
    doc.fields["curated"] = {
        "https://p#b": {"decision": "reject"},
        "https://p#c": {"decision": "accept", "label": "C", "kind": "concept"},
    }
    assert sorted(t.iri for t in effective_tags(doc)) == ["https://p#a", "https://p#c"]
    out = to_solr_document(doc)
    assert out["concept_iris_ss"] == ["https://p#a", "https://p#c"]
    assert out["curated_ss"] == ["https://p#b:reject", "https://p#c:accept"]
