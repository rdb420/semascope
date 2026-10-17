"""Ingestion parity on a live stack: OCR later, WARC archives, sidecar metadata, extractors.

Each test uses its own collection. Files for the path sources are written into the import
folder the worker mounts at /import and removed afterwards.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import secrets
import shutil
from pathlib import Path
from typing import Any

import httpx
from _stack import (
    ROOT,
    add_source,
    doc_id,
    documents_settled,
    ensure_collection,
    env_value,
    source_settled,
    wait_for,
)

TIMEOUT = float(os.environ.get("SEMASCOPE_IT_TIMEOUT", "900"))


def import_dir() -> Path:
    raw = env_value("SEMASCOPE_IMPORT_DIR") or "./data/import"
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def create_collection(api: httpx.Client, slug: str, profile: str, pipeline: dict[str, Any]) -> None:
    if api.get(f"/collections/{slug}").status_code == 404:
        body = {"slug": slug, "profile": profile, "settings": {"pipeline": pipeline}}
        api.post("/collections", json=body).raise_for_status()


def settled(api: httpx.Client, slug: str, uris: list[str]) -> dict[str, dict[str, Any]]:
    docs: dict[str, dict[str, Any]] = wait_for(
        lambda: documents_settled(api, slug, uris), timeout=TIMEOUT, what=f"{uris} in {slug}"
    )
    return docs


def run_once(api: httpx.Client, slug: str, source: dict[str, Any]) -> dict[str, Any]:
    """Start a run of an idle source and wait until it has finished."""

    before = next(
        s for s in api.get(f"/collections/{slug}/sources").json() if s["id"] == source["id"]
    )
    api.post(f"/sources/{source['id']}/run").raise_for_status()
    done: dict[str, Any] = wait_for(
        lambda: (
            (s := source_settled(api, slug, source["id"]))
            and s["last_run_at"] != before["last_run_at"]
            and s
        ),
        timeout=TIMEOUT,
        what=f"a run of {slug}",
    )
    return done


def search(api: httpx.Client, slug: str, q: str, **extra: Any) -> list[dict[str, Any]]:
    body = {"q": q, "collection": slug, "page_size": 50, "facets": [], **extra}
    hits: list[dict[str, Any]] = api.post("/search", json=body).raise_for_status().json()["hits"]
    return hits


# --- OCR later (ETL-11, ADR-0014) ------------------------------------------------------------


def test_ocr_later_indexes_first_and_ocrs_on_a_low_priority_pass(api: httpx.Client) -> None:
    from PIL import Image, ImageDraw, ImageFont

    slug = f"it-ocr-later-{secrets.token_hex(3)}"
    create_collection(api, slug, "fast", {"ocr": "later"})
    image = Image.new("RGB", (1000, 200), "white")
    ImageDraw.Draw(image).text(
        (30, 60), "PELICAN ON THE JETTY", fill="black", font=ImageFont.load_default(size=56)
    )
    png = io.BytesIO()
    image.save(png, format="PNG")
    resp = api.post(
        f"/collections/{slug}/upload", files=[("files", ("scan.png", png.getvalue(), "image/png"))]
    )
    assert resp.status_code == 202, resp.text
    uri = resp.json()[0]["uri"]

    def ocred() -> dict[str, Any] | None:
        doc = api.get(f"/documents/{doc_id(slug, uri)}").json()
        if doc["status"] != "indexed" or doc["metadata"].get("ocr_pending"):
            return None
        return dict(doc)

    doc = wait_for(ocred, timeout=TIMEOUT, what="the OCR pass")
    assert doc["metadata"]["ocr"] is True and "ocr_error" not in doc["metadata"], doc["metadata"]
    # the OCR pass replays extract and what follows from the stored original: no fetch
    stages = [r["stage"] for r in doc["stage_runs"]]
    assert "extract" in stages and "fetch" not in stages, stages
    text = api.get(f"/documents/{doc['id']}/text").json()["text"]
    assert "PELICAN" in text.upper(), text
    wait_for(lambda: search(api, slug, "pelican"), timeout=60, what="the OCRed text in search")
    assert not search(api, slug, "*:*", filters={"ocr_pending_b": ["true"]})
    stats = api.get("/stats").json()
    assert "ocr_pending" in stats


# --- WARC (ENR-93) ---------------------------------------------------------------------------


def _warc(path: Path, tag: str) -> None:
    from warcio.statusandheaders import StatusAndHeaders
    from warcio.warcwriter import WARCWriter

    with path.open("wb") as fh:
        writer = WARCWriter(fh, gzip=True)
        pages = [
            ("https://birds.example/wetlands/egret.html", f"A great egret{tag} wades.", False),
            ("https://birds.example/", f"The bird index{tag}.", True),
        ]
        for uri, words, compress in pages:
            body = f"<html><head><title>{words}</title></head><body><p>{words}</p></body></html>"
            payload = body.encode()
            headers = [("Content-Type", "text/html; charset=utf-8")]
            if compress:
                payload = gzip.compress(payload)
                headers.append(("Content-Encoding", "gzip"))
            record = writer.create_warc_record(
                uri,
                "response",
                payload=io.BytesIO(payload),
                http_headers=StatusAndHeaders("200 OK", headers, protocol="HTTP/1.1"),
                warc_headers_dict={"WARC-Date": "2026-03-04T05:06:07Z"},
            )
            writer.write_record(record)


def test_a_warc_archive_is_one_document_per_page(api: httpx.Client) -> None:
    tag = secrets.token_hex(3)
    slug = f"it-warc-{tag}"
    folder = import_dir() / f"warc-{tag}"
    folder.mkdir(parents=True)
    try:
        _warc(folder / "crawl.warc.gz", tag)
        ensure_collection(api, slug)
        source = add_source(api, slug, "crawl", "warc", {"path": f"/import/warc-{tag}"}, run=False)
        done = run_once(api, slug, source)
        assert done["status"] == "idle" and done["document_count"] == 2, done
        uris = ["https://birds.example/wetlands/egret.html", "https://birds.example/"]
        docs = settled(api, slug, uris)
        for uri in uris:
            assert docs[uri]["status"] == "indexed", docs[uri]
            assert docs[uri]["modified_at"].startswith("2026-03-04T05:06:07"), docs[uri]
        hits = wait_for(lambda: search(api, slug, f"egret{tag}"), timeout=60, what="egret")
        assert [h["uri"] for h in hits] == [uris[0]]
        assert search(api, slug, f"index{tag}"), "the gzip-encoded page was not decoded"
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# --- sidecars (ENR-96, ENR-XMP-01) and extractors (ENR-94, ENR-LAW-01, ENR-RGX-01) -------------


def test_sidecar_fields_and_extractor_facets(api: httpx.Client) -> None:
    tag = secrets.token_hex(3)
    slug = f"it-sidecar-{tag}"
    folder = import_dir() / f"sidecar-{tag}"
    folder.mkdir(parents=True)
    try:
        (folder / "report.txt").write_text(
            f"Heron report{tag}. Pay AUD 1,250.50 into DE89 3704 0044 0532 0130 00 under "
            "section 12 of the Privacy Act 1988 (Cth). Ticket TCK-4471.\n"
        )
        sidecar = folder / "report.txt.meta.json"
        sidecar.write_text(json.dumps({"title": f"Heron report {tag}", "owner": "Wetlands team"}))
        create_collection(
            api, slug, "default", {"regex_lists": {"ticket": r"TCK-\d+"}, "embed": False}
        )
        source = add_source(
            api, slug, "files", "filesystem", {"path": f"/import/sidecar-{tag}"}, run=False
        )
        done = run_once(api, slug, source)
        assert done["document_count"] == 1, done  # the sidecar is not a document
        uri = f"file:///import/sidecar-{tag}/report.txt"
        doc = settled(api, slug, [uri])[uri]
        assert doc["status"] == "indexed", doc
        assert doc["title"] == f"Heron report {tag}"
        fields = doc["fields"]
        assert fields["x_owner_s"] == "Wetlands team"
        assert fields["x_iban_ss"] == ["DE89370400440532013000"]
        assert fields["x_currency_ss"] == ["AUD"] and fields["x_amount_ss"] == ["AUD 1250.50"]
        assert fields["x_ticket_ss"] == ["TCK-4471"]
        assert any("Privacy Act 1988" in v for v in fields["x_law_ss"]), fields["x_law_ss"]
        facets = wait_for(
            lambda: (
                api.post(
                    "/search",
                    json={
                        "q": "*:*",
                        "collection": slug,
                        "facets": ["iban_ss", "law_ss", "ticket_ss"],
                    },
                )
                .json()["facets"]
                .get("iban_ss")
            ),
            timeout=60,
            what="the IBAN facet",
        )
        assert facets[0]["value"] == "DE89370400440532013000"
        assert search(api, slug, "*:*", filters={"owner_s": ["Wetlands team"]})

        # editing only the sidecar is a change: the next run processes the file again
        sidecar.write_text(json.dumps({"title": f"Heron report {tag}", "owner": "Finance"}))
        run_once(api, slug, source)
        wait_for(
            lambda: (
                (d := api.get(f"/documents/{doc['id']}").json())["status"] == "indexed"
                and d["fields"].get("x_owner_s") == "Finance"
            ),
            timeout=TIMEOUT,
            what="the new sidecar value",
        )
        wait_for(
            lambda: search(api, slug, "*:*", filters={"owner_s": ["Finance"]}),
            timeout=60,
            what="the new value in search",
        )
    finally:
        shutil.rmtree(folder, ignore_errors=True)
