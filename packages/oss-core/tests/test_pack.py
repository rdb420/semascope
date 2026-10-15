from pathlib import Path

from oss_core.pack import load_pack, write_manifest

REPO = Path(__file__).resolve().parents[3]


def test_base_pack_passes_structural_validation() -> None:
    info = load_pack(REPO / "packs" / "base")
    assert info.ok, [f.message for f in info.findings if f.severity == "error"]
    assert info.id == "semascope-base"
    assert info.counts["classes"] >= 10


def test_missing_pack_reports_errors(tmp_path: Path) -> None:
    info = load_pack(tmp_path)
    assert not info.ok
    codes = {f.code for f in info.findings}
    assert "missing_file" in codes


def test_manifest_is_deterministic(tmp_path: Path) -> None:
    info = load_pack(REPO / "packs" / "base")
    out = write_manifest(info, tmp_path / "m.json")
    assert out.read_text().count('"sha256"') == len(info.files)
