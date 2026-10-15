from pathlib import Path

from oss_core.contracts import StageContext, Tag, WorkingDocument
from oss_pipeline.stages.validate import ValidateStage, document_graph

REPO = Path(__file__).resolve().parents[3]


def test_document_graph_conforms_to_base_shapes() -> None:
    shapes = (REPO / "packs" / "base" / "shapes.ttl").read_text()
    doc = WorkingDocument(
        id="d1", collection="c", source_id="1", uri="file:///x", content_hash="a" * 64, title="T"
    )
    doc.tags.append(
        Tag(
            iri="https://p/themes#flood", label="Flood", kind="concept", stage="tag", confidence=1.0
        )
    )
    out = ValidateStage().run(
        doc, StageContext(collection="c", profile="full", settings={"shapes_ttl": shapes})
    )
    assert out.fields["shacl_conforms"] is True, out.fields["shacl_violations"]


def test_document_graph_reports_violations() -> None:
    shapes = (REPO / "packs" / "base" / "shapes.ttl").read_text()
    doc = WorkingDocument(
        id="d2", collection="c", source_id="1", uri="file:///x", content_hash="not-a-hash"
    )
    doc.tags.append(Tag(iri="https://p#x", label="X", kind="entity", stage="ner", confidence=1.5))
    out = ValidateStage().run(
        doc, StageContext(collection="c", profile="full", settings={"shapes_ttl": shapes})
    )
    assert out.fields["shacl_conforms"] is False
    paths = {v["path"].rsplit("#", 1)[-1] for v in out.fields["shacl_violations"]}
    assert "contentHash" in paths and "confidence" in paths
    assert "base:Document" in document_graph(doc)
