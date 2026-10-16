"""Smoke tests for the evidence and release tooling: the demo corpus builders, the image
pinner and the benchmark scripts' pure parts (the parts that need a stack are exercised by
running them, see docs/assess/benchmarks.md)."""

from __future__ import annotations

import csv
import email
import importlib.util
import io
import mailbox
import re
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(path: str) -> ModuleType:
    file = ROOT / path
    spec = importlib.util.spec_from_file_location(file.stem, file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[file.stem] = module
    spec.loader.exec_module(module)
    return module


derive = load("packs/demo/derive.py")
build = load("packs/demo/build.py")
pin = load("deploy/pin_images.py")
benchlib = load("scripts/benchlib.py")
bench_size = load("scripts/bench_size.py")
bench_review = load("scripts/bench_review.py")
bench_recovery = load("scripts/bench_recovery.py")
bench = load("scripts/bench.py")

TEXT = """BOOK I

Sing, O goddess, the anger of Achilles son of Peleus, that brought countless ills
upon the Achaeans. Many a brave soul did it send hurrying down to Hades.

From the day on which the son of Atreus, king of men, and great Achilles, first fell out
with one another. "Quarrel" (they said) & so on <here>.

BOOK II

Now the other gods and the armed warriors on the plain slept soundly, but Jove was wakeful.
"""


@pytest.fixture
def chapter() -> Any:
    paras = derive.paragraphs(TEXT)
    return derive.Chapter("iliad", 1, "The Iliad: Book I", tuple(paras), "The Iliad", "Homer")


# --- derive.py ----------------------------------------------------------------------------------


def test_cut_breaks_before_headings() -> None:
    paras = derive.paragraphs(TEXT)
    assert derive.is_heading("BOOK II") and not derive.is_heading(paras[1])
    chunks = derive.cut(paras, target=200)
    assert [c[0] for c in chunks] == ["BOOK I", "BOOK II"]


def test_every_format_renders_deterministically(chapter: Any) -> None:
    for fmt, render in derive.RENDERERS.items():
        first = render(chapter)
        assert first == render(chapter), fmt
        assert len(first) > 100, fmt


def test_office_and_ebook_formats_are_valid_zips(chapter: Any) -> None:
    for fmt, member in (
        ("docx", "word/document.xml"),
        ("odt", "content.xml"),
        ("epub", "OEBPS/chapter.xhtml"),
        ("zip", "part-1.txt"),
    ):
        with zipfile.ZipFile(io.BytesIO(derive.RENDERERS[fmt](chapter))) as z:
            assert z.testzip() is None
            assert "Achilles" in z.read(member).decode(), fmt
            if fmt in ("odt", "epub"):
                assert z.namelist()[0] == "mimetype"
                assert z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED


def _check_xref(pdf: bytes) -> None:
    start = int(re.search(rb"startxref\n(\d+)", pdf).group(1))  # type: ignore[union-attr]
    assert pdf[start : start + 4] == b"xref"
    offsets = [int(x) for x in re.findall(rb"(\d{10}) 00000 n", pdf)]
    for n, off in enumerate(offsets, start=1):
        assert pdf[off:].startswith(b"%d 0 obj" % n)


def test_pdfs_have_valid_cross_references(chapter: Any) -> None:
    text_pdf = derive.render_pdf(chapter)
    _check_xref(text_pdf)
    assert b"(Sing, O goddess" in text_pdf
    scan = derive.render_scan(chapter)
    _check_xref(scan)
    assert b"/CCITTFaxDecode" in scan and b"Sing" not in scan  # no text layer


def test_mail_csv_and_turtle(chapter: Any) -> None:
    msg = email.message_from_bytes(derive.render_eml(chapter))
    assert msg["Subject"] == chapter.title and "Achilles" in msg.get_payload(decode=True).decode()
    path = Path(__file__).with_name("_tmp.mbox")
    try:
        path.write_bytes(derive.render_mbox(chapter))
        box = mailbox.mbox(path)
        assert len(box) >= 2 and box[1]["In-Reply-To"] == box[0]["Message-ID"]
        box.close()
    finally:
        path.unlink(missing_ok=True)
    rows = list(csv.reader(io.StringIO(derive.render_csv(chapter).decode())))
    assert rows[0] == ["work", "chapter", "paragraph", "text"] and len(rows) == 1 + len(
        chapter.paragraphs
    )
    ttl = derive.render_ttl(chapter).decode()
    assert '\\"Quarrel\\"' in ttl and "schema:position 2" in ttl


def test_manifest_round_trip_and_diff(tmp_path: Path) -> None:
    path = tmp_path / "m.sha256"
    derive.write_manifest(path, {"b/x.pdf": "2" * 64, "a/y.html": "1" * 64})
    assert path.read_text().splitlines()[0].endswith("  a/y.html")
    got = derive.read_manifest(path)
    assert derive.diff_manifests(got, {**got, "a/y.html": "3" * 64, "c": "4"}) == [
        "unexpected: c",
        "changed: a/y.html",
    ]


def test_build_strips_the_licence_and_writes_the_corpus_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = (
        "header\n*** START OF THE PROJECT GUTENBERG EBOOK X ***\nA Project Gutenberg text.\n"
        "*** END OF THE PROJECT GUTENBERG EBOOK X ***\nfooter"
    )
    strip_licence = build.strip
    assert strip_licence(raw) == "A the archive text.\n"
    with pytest.raises(ValueError):
        strip_licence("no markers")
    monkeypatch.setattr(build, "HERE", tmp_path)
    build.write_corpus_manifest([{"slug": "a", "sha256": "f" * 64}, {"slug": "b"}])
    assert (tmp_path / "corpus.sha256").read_text() == f"{'f' * 64}  corpus/a.txt\n"


# --- pin_images.py ------------------------------------------------------------------------------


def test_parse_ref_follows_docker_defaults() -> None:
    r = pin.parse_ref("postgres:17-alpine")
    assert (r.registry, r.repository, r.tag, r.digest) == (
        "registry-1.docker.io",
        "library/postgres",
        "17-alpine",
        None,
    )
    r = pin.parse_ref("ghcr.io/rdb420/semascope:1.0@sha256:" + "a" * 64)
    assert (r.registry, r.repository, r.tag, r.digest[:7]) == (
        "ghcr.io",
        "rdb420/semascope",
        "1.0",
        "sha256:",
    )
    assert pin.parse_ref("localhost:5000/x").repository == "x"
    assert pin.parse_ref("valkey/valkey").tag == "latest"


def test_parse_challenge() -> None:
    header = 'Bearer realm="https://auth.docker.io/token",service="registry.docker.io",scope="repository:library/solr:pull"'
    assert pin.parse_challenge(header)["service"] == "registry.docker.io"
    assert pin.parse_challenge("Basic realm=x") == {}


def test_pin_text_rewrites_fixed_tags_only() -> None:
    text = (
        "services:\n"
        "  a:\n    image: solr:10.0.0  # pinned\n"
        "  b:\n    image: ghcr.io/x/y:${TAG:-main}\n"
        "  c:\n    image: caddy:2-alpine@sha256:" + "0" * 64 + "\n"
    )
    new, log = pin.pin_text(text, lambda ref: "sha256:" + "1" * 64)
    assert "image: solr:10.0.0@sha256:" + "1" * 64 + "  # pinned\n" in new
    assert "${TAG:-main}\n" in new and "caddy:2-alpine@sha256:" + "1" * 64 in new
    assert sum(entry.startswith("pin") for entry in log) == 2
    assert pin.unpinned(new) == [] and pin.unpinned(text) == ["solr:10.0.0"]


def test_committed_compose_files_are_pinned() -> None:
    for path in ROOT.glob("compose*.yaml"):
        assert pin.unpinned(path.read_text()) == [], path.name


# --- benchmark scripts --------------------------------------------------------------------------


def test_benchlib_helpers(tmp_path: Path) -> None:
    assert benchlib.pct([3, 1, 2, 4], 50) in (2, 3) and benchlib.pct([], 95) == 0
    assert benchlib.table(["a", "b"], [[1, 2]]).splitlines() == [
        "| a | b |",
        "|---|---|",
        "| 1 | 2 |",
    ]
    env = tmp_path / "e.env"
    env.write_text("# c\nA=1   # note\nB=x#y\n\nC=\n")
    assert benchlib.read_env(env) == {"A": "1", "B": "x#y", "C": ""}
    with pytest.raises(SystemExit):
        benchlib.Compose("semascope")
    assert benchlib.Compose("sem-bench", ["compose.yaml"], env).base()[-2:] == [
        "--env-file",
        str(env),
    ]


def test_bench_size_delta_and_report() -> None:
    snap = {
        "documents": {
            "indexed": {"count": 4, "bytes": 4_000_000},
            "failed": {"count": 1, "bytes": 0},
        },
        "text_bytes": 3_000_000,
        "solr": {
            "documents": {"bytes": 8_000_000, "docs": 4},
            "chunks": {"bytes": 2_000_000, "docs": 90},
        },
        "postgres": {"database_bytes": 9_000_000, "data_dir_bytes": 50_000_000, "tables": {"t": 1}},
        "blobs": {"blobs": 4_000_000},
        "neo4j": {"labels": {"Document": 4}, "types": {"MENTIONS": 40}, "store_bytes": 1_000_000},
    }
    base = {
        "documents": {"indexed": {"count": 2, "bytes": 1_000_000}},
        "neo4j": {"labels": {"Document": 2}},
    }
    delta = bench_size.minus(snap, base)
    assert delta["documents"]["indexed"]["count"] == 2 and delta["neo4j"]["labels"]["Document"] == 2
    text = bench_size.report(snap, "demo")
    assert "4 documents indexed" in text and "| Solr `documents` | 8.0 MB | 2.0 GB | 2.00 |" in text
    assert "| label `Document` | 4 | 1000 |" in text


def test_review_decisions_are_deterministic() -> None:
    d = bench_review.decision_for
    assert (
        d({"kind": "tag"}, 0)["decision"] == "accept"
        and d({"kind": "tag"}, 1)["decision"] == "reject"
    )
    assert d({"kind": "violation"}, 1)["decision"] == "accept"
    link = {"kind": "link", "payload": {"candidates": [{"iri": "urn:x"}]}}
    assert d(link, 0) == {"decision": "accept", "link_to": "urn:x", "note": "bench"}
    assert d({"kind": "link", "payload": {}}, 0)["decision"] == "reject"


def test_recovery_outcome_and_comparison() -> None:
    clean = {"status": {"indexed": 3}, "solr_docs": 3, "chunks": 9, "graph_docs": 3, "tags": 12}
    out = bench_recovery.Outcome("x", "y")
    bench_recovery.Bench.compare(None, out, {**clean, "duplicated": 0}, clean)  # type: ignore[arg-type]
    assert out.passed and out.lost == 0 and out.half_written == 0
    bad = bench_recovery.Outcome("x", "y", duplicated=1)
    got = {**clean, "status": {"indexed": 2, "processing": 1}, "solr_docs": 2}
    bench_recovery.Bench.compare(None, bad, got, clean)  # type: ignore[arg-type]
    assert bad.passed is False and bad.lost == 1 and bad.row()[-1] == "**fail**"


class FakeApi:
    def post(self, path: str, json: dict[str, Any]) -> Any:
        class R:
            def json(self) -> dict[str, Any]:
                return {
                    "total": 10,
                    "facets": {
                        "language_s": [{"value": "en", "count": 10}],
                        "traditions_paths": [
                            {"value": "0/greek", "count": 10},
                            {"value": "0/norse", "count": 3},
                        ],
                        "themes_paths": [{"value": "0/war", "count": 4}],
                    },
                }

        return R()


def test_bench_prefers_selective_scheme_facets() -> None:
    assert bench.pick_filters(FakeApi(), "demo") == {
        "themes_paths": ["0/war"],
        "traditions_paths": ["0/norse"],
    }
