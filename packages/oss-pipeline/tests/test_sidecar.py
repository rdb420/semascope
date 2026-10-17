"""Sidecar metadata files: merged into their file's fields, never documents themselves."""

from __future__ import annotations

import json
from pathlib import Path

from oss_core.contracts import SourceConfig
from oss_pipeline import sidecar
from oss_pipeline.connectors.filesystem import FilesystemConnector
from oss_pipeline.stages.index import custom_fields

XMP = b"""<?xpacket begin='' id='W5M0MpCehiHzreSzNTczkc9d'?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="4">
   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">Egret at dawn</rdf:li></rdf:Alt></dc:title>
   <dc:subject><rdf:Bag><rdf:li>egret</rdf:li><rdf:li>wetland</rdf:li></rdf:Bag></dc:subject>
   <dc:creator><rdf:Seq><rdf:li>Pat Field</rdf:li></rdf:Seq></dc:creator>
   <dc:description><rdf:Alt><rdf:li>Morning light</rdf:li></rdf:Alt></dc:description>
   <xmp:Label>Red</xmp:Label>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end='w'?>"""


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "docs"
    root.mkdir()
    (root / "report.pdf").write_bytes(b"%PDF-1.4 report")
    (root / "report.pdf.meta.json").write_text(
        json.dumps(
            {
                "title": "Quarterly heron report",
                "owner": "Wetlands team",
                "pages": 12,
                "approved": True,
                "score": 4.5,
                "topics": ["herons", "egrets"],
                "case": {"id": "C-7", "year": 2026},
                "due_dt": "2026-11-01T00:00:00Z",
                "text_txt": "may not replace the text",
                "nested": {"deep": {"ignored": 1}},
            }
        )
    )
    (root / "egret.cr2").write_bytes(b"raw photo bytes")
    (root / "egret.xmp").write_bytes(XMP)
    (root / "notes.txt").write_text("no sidecar")
    (root / "orphan.xmp").write_bytes(XMP)  # no file of its own: a document like any other
    return root


def _items(root: Path, **settings: object) -> dict[str, dict[str, object]]:
    src = SourceConfig(
        id="s", collection="c", connector="filesystem", settings={"path": str(root), **settings}
    )
    return {
        Path(i.metadata["path"]).name: i.metadata for i in FilesystemConnector().discover(src, None)
    }


def test_sidecars_are_merged_and_not_indexed_themselves(tmp_path: Path) -> None:
    items = _items(_tree(tmp_path))
    assert set(items) == {"report.pdf", "egret.cr2", "notes.txt", "orphan.xmp"}
    report = items["report.pdf"]
    assert report["title"] == "Quarterly heron report"
    fields = report["fields"]
    assert isinstance(fields, dict)
    assert fields["x_owner_s"] == "Wetlands team"
    assert (
        fields["x_pages_l"] == 12 and fields["x_approved_b"] is True and fields["x_score_d"] == 4.5
    )
    assert fields["x_topics_ss"] == ["herons", "egrets"]
    assert fields["x_case_id_s"] == "C-7" and fields["x_case_year_l"] == 2026
    assert fields["x_due_dt"] == "2026-11-01T00:00:00Z"
    assert fields["x_folder_paths"] == ["docs"] and fields["x_extension_s"] == "pdf"
    assert str(fields["x_sidecar_s"]).startswith("report.pdf.meta.json@")
    assert "x_text_txt" not in custom_fields(fields)  # never one of the index's own fields
    assert not any(k.startswith("x_nested") for k in fields)
    photo = items["egret.cr2"]
    assert photo["title"] == "Egret at dawn"
    assert photo["fields"] == {
        "x_keywords_ss": ["egret", "wetland"],
        "x_creator_ss": ["Pat Field"],
        "x_description_s": "Morning light",
        "x_label_s": "Red",
        "x_rating_l": 4,
        "x_folder_paths": ["docs"],
        "x_extension_s": "cr2",
        "x_sidecar_s": photo["fields"]["x_sidecar_s"],  # type: ignore[index]
    }
    assert "x_sidecar_s" not in items["notes.txt"]["fields"]  # type: ignore[operator]


def test_a_source_can_turn_sidecars_off(tmp_path: Path) -> None:
    items = _items(_tree(tmp_path), sidecars=False)
    assert "report.pdf.meta.json" in items and "title" not in items["report.pdf"]


def test_a_broken_sidecar_is_ignored(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "report.pdf.meta.json").write_text("{not json")
    (root / "egret.xmp").write_bytes(b"<!DOCTYPE x [<!ENTITY a 'b'>]><x>&a;</x>")  # refused
    items = _items(root)
    assert "title" not in items["report.pdf"] and "title" not in items["egret.cr2"]


def test_a_changed_sidecar_is_a_change() -> None:
    stored = {"x_sidecar_s": "a.meta.json@1111"}
    assert not sidecar.changed(stored, {"fields": {"x_sidecar_s": "a.meta.json@1111"}})
    assert sidecar.changed(stored, {"fields": {"x_sidecar_s": "a.meta.json@2222"}})
    assert sidecar.changed(stored, {"fields": {}})  # removed
    assert sidecar.changed({}, {"fields": {"x_sidecar_s": "a.meta.json@1111"}})  # added
    assert not sidecar.changed({}, {"fields": {}})
