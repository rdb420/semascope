"""URL and upload sources against real servers: web, feeds, S3 (MinIO), SPARQL (Oxigraph), upload.

The servers are the test services of tests/integration/stack/compose.yaml. The
worker reaches them by service name (allowed through
SEMASCOPE_ALLOWED_PRIVATE_HOSTS); the tests reach them on their published
ports to load data. Every source runs in its own collection with the `fast`
profile, and all of them start together in `runs`, so they process in parallel.
"""

from __future__ import annotations

import io
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from _stack import (
    add_source,
    doc_id,
    documents_settled,
    ensure_collection,
    search_uris,
    service_url,
    source_settled,
    wait_for,
)

SITE = "http://fixtures:8000"  # as the worker sees it
OXIGRAPH = "http://oxigraph:7878"
MINIO = "http://minio:9000"
MINIO_USER, MINIO_SECRET = "semascope-it", "semascope-it-minio-secret"  # noqa: S105 - fixture
BUCKET = "it-docs"
BIRDS = "https://example.test/birds#"
BIRDS_GRAPH = "https://example.test/graphs/birds"
STACK = Path(__file__).resolve().parent / "stack"
TIMEOUT = float(os.environ.get("SEMASCOPE_IT_TIMEOUT", "900"))

S3_OBJECTS = {
    "inbox/kestrel.txt": b"A kestrel hovers over the motorway verge.",
    "inbox/sub/lorikeet.html": b"<html><body><p>A rainbow lorikeet feeds.</p></body></html>",
    "inbox/skip-me.txt": b"excluded by the exclude glob",
    "inbox/empty.txt": b"",
    "inbox/data.bin": b"\x00\x01 not matched by the include globs",
    "outside/other.txt": b"outside the prefix",
}

# collection -> (source name, connector, settings)
SOURCES: dict[str, list[tuple[str, str, dict[str, Any]]]] = {
    "it-web-crawl": [
        (
            "site",
            "web",
            {
                "urls": [f"{SITE}/index.html"],
                "mode": "crawl",
                "max_depth": 2,
                "delay_seconds": 0,
                "max_bytes": 50_000,
            },
        )
    ],
    "it-web-limit": [
        (
            "site",
            "web",
            {"urls": [f"{SITE}/index.html"], "mode": "crawl", "max_pages": 2, "delay_seconds": 0},
        )
    ],
    "it-web-pages": [
        (
            "pages",
            "web",
            {
                "urls": [f"{SITE}/page-a.html", f"{SITE}/private/secret.html"],
                "respect_robots": False,
                "delay_seconds": 0,
            },
        )
    ],
    "it-web-sitemap": [
        (
            "sitemap",
            "web",
            {"urls": [f"{SITE}/sitemap-index.xml"], "mode": "sitemap", "delay_seconds": 0},
        )
    ],
    "it-web-private": [("internal", "web", {"urls": ["http://postgres:5432/"]})],
    "it-feed": [
        ("rss", "feed", {"urls": [f"{SITE}/feeds/rss.xml"]}),
        ("atom", "feed", {"urls": [f"{SITE}/feeds/atom.xml"], "fetch_links": True}),
    ],
    "it-s3": [
        (
            "bucket",
            "s3",
            {
                "bucket": BUCKET,
                "prefix": "inbox/",
                "endpoint_url": MINIO,
                "region": "us-east-1",
                "access_key": MINIO_USER,
                "secret_key": MINIO_SECRET,
                "include": ["*.txt", "*.html"],
                "exclude": ["*/skip-*"],
            },
        )
    ],
    "it-sparql": [
        (
            "birds",
            "rdf",
            {"endpoint": f"{OXIGRAPH}/query", "graph": BIRDS_GRAPH, "types": [f"{BIRDS}Bird"]},
        )
    ],
}


def load_minio() -> None:
    import boto3

    s3 = boto3.client(
        "s3",
        endpoint_url=service_url("minio", 9000),
        aws_access_key_id=MINIO_USER,
        aws_secret_access_key=MINIO_SECRET,
        region_name="us-east-1",
    )

    def up() -> bool:
        try:
            s3.list_buckets()
            return True
        except Exception:  # noqa: BLE001 - MinIO still starting
            return False

    wait_for(up, timeout=120, every=2.0, what="MinIO")
    if BUCKET not in {b["Name"] for b in s3.list_buckets().get("Buckets", [])}:
        s3.create_bucket(Bucket=BUCKET)
    for key, body in S3_OBJECTS.items():
        s3.put_object(Bucket=BUCKET, Key=key, Body=body)


def load_oxigraph() -> None:
    base = service_url("oxigraph", 7878)

    def up() -> bool:
        try:
            return httpx.get(base + "/query", params={"query": "ASK {}"}).status_code == 200
        except httpx.HTTPError:
            return False

    wait_for(up, timeout=120, every=2.0, what="Oxigraph")
    resp = httpx.post(
        base + "/store",
        params={"graph": BIRDS_GRAPH},
        content=(STACK / "sparql.ttl").read_bytes(),
        headers={"Content-Type": "text/turtle"},
    )
    assert resp.status_code in (200, 201, 204), resp.text


@pytest.fixture(scope="module")
def runs(api: httpx.Client) -> dict[str, dict[str, Any]]:
    """Start every source; returns them by "<collection>/<source name>"."""

    service_url("fixtures", 8000)  # skip early when the test services are absent
    load_minio()
    load_oxigraph()
    out: dict[str, dict[str, Any]] = {}
    for slug, sources in SOURCES.items():
        ensure_collection(api, slug)
        for name, connector, settings in sources:
            out[f"{slug}/{name}"] = add_source(api, slug, name, connector, settings)
    # never started by hand: the beat's tick starts it (a fresh name, so it is due at once)
    ensure_collection(api, "it-scheduled")
    out["it-scheduled/feed"] = add_source(
        api,
        "it-scheduled",
        "feed-" + secrets.token_hex(3),
        "feed",
        {"urls": [f"{SITE}/feeds/rss.xml"]},
        schedule="every 15m",
        run=False,
    )
    return out


def finished(api: httpx.Client, runs: dict[str, dict[str, Any]], key: str) -> dict[str, Any]:
    slug = key.split("/")[0]
    source: dict[str, Any] = wait_for(
        lambda: source_settled(api, slug, runs[key]["id"]),
        timeout=TIMEOUT,
        what=f"the {key} source run",
    )
    return source


def wait_indexed(api: httpx.Client, slug: str, uris: list[str]) -> dict[str, dict[str, Any]]:
    docs: dict[str, dict[str, Any]] = wait_for(
        lambda: documents_settled(api, slug, uris),
        timeout=TIMEOUT,
        what=f"{len(uris)} documents in {slug}",
    )
    failed = {u: d["error"] for u, d in docs.items() if d["status"] != "indexed"}
    assert not failed, f"documents not indexed: {failed}"
    return docs


def absent(api: httpx.Client, slug: str, uris: list[str]) -> None:
    present = [u for u in uris if api.get(f"/documents/{doc_id(slug, u)}").status_code != 404]
    assert not present, f"{slug}: documents that should not exist: {present}"


def found_by(api: httpx.Client, slug: str, q: str, uris: set[str]) -> None:
    """The search finds exactly these documents once Solr's soft commit has made them visible."""

    wait_for(
        lambda: search_uris(api, slug, q) == uris, timeout=60, every=1.0, what=f"{q} in {slug}"
    )


def text_of(api: httpx.Client, doc: dict[str, Any]) -> str:
    return str(api.get(f"/documents/{doc['id']}/text").raise_for_status().json()["text"])


# --- web -----------------------------------------------------------------------------------


def test_web_crawl_is_bounded_and_respects_robots_and_caps(
    api: httpx.Client, runs: dict[str, dict[str, Any]]
) -> None:
    slug = "it-web-crawl"
    source = finished(api, runs, f"{slug}/site")
    assert source["status"] == "idle" and not source["last_error"], source
    want = ["index.html", "page-a.html", "page-b.html", "deep/level1.html", "deep/level2.html"]
    docs = wait_indexed(api, slug, [f"{SITE}/{p}" for p in want])
    assert all(d["media_type"].startswith("text/html") for d in docs.values()), docs
    assert "aardvark" in text_of(api, docs[f"{SITE}/page-a.html"])
    absent(
        api,
        slug,
        [
            f"{SITE}/private/secret.html",  # robots.txt
            f"{SITE}/deep/level3.html",  # past max_depth
            f"{SITE}/big.html",  # over max_bytes, with a Content-Length
            f"{SITE}/big-stream.html",  # over max_bytes, without one
            f"{SITE}/redirect-private.html",  # redirects to a private address
            "http://other.invalid/elsewhere.html",  # another host
            f"{SITE}/logo.png",  # skipped by extension
        ],
    )
    found_by(api, slug, "echidna", {f"{SITE}/deep/level2.html"})
    assert source["document_count"] == len(want)


def test_web_crawl_stops_at_max_pages(api: httpx.Client, runs: dict[str, dict[str, Any]]) -> None:
    slug = "it-web-limit"
    source = finished(api, runs, f"{slug}/site")
    assert source["status"] == "idle", source
    wait_indexed(api, slug, [f"{SITE}/index.html", f"{SITE}/page-a.html"])
    assert source["document_count"] == 2, source


def test_web_pages_mode_and_robots_switch(
    api: httpx.Client, runs: dict[str, dict[str, Any]]
) -> None:
    slug = "it-web-pages"
    source = finished(api, runs, f"{slug}/pages")
    assert source["status"] == "idle", source
    docs = wait_indexed(api, slug, [f"{SITE}/page-a.html", f"{SITE}/private/secret.html"])
    assert "gharial" in text_of(api, docs[f"{SITE}/private/secret.html"])
    # pages mode follows no links
    absent(api, slug, [f"{SITE}/index.html"])
    assert source["document_count"] == 2


def test_web_sitemap_index_and_robots(api: httpx.Client, runs: dict[str, dict[str, Any]]) -> None:
    slug = "it-web-sitemap"
    source = finished(api, runs, f"{slug}/sitemap")
    assert source["status"] == "idle", source
    want = [f"{SITE}/page-a.html", f"{SITE}/page-b.html", f"{SITE}/sitemap-only.html"]
    wait_indexed(api, slug, want)
    absent(api, slug, [f"{SITE}/private/secret.html", f"{SITE}/index.html"])
    assert source["document_count"] == 3
    found_by(api, slug, "cassowary", {f"{SITE}/sitemap-only.html"})


def test_web_source_on_a_private_address_is_refused(
    api: httpx.Client, runs: dict[str, dict[str, Any]]
) -> None:
    slug = "it-web-private"
    source = finished(api, runs, f"{slug}/internal")
    assert source["status"] == "failed", source
    assert "private address" in source["last_error"], source
    assert source["document_count"] == 0


def test_web_source_with_a_non_http_url_is_rejected(api: httpx.Client) -> None:
    ensure_collection(api, "it-web-private")
    resp = api.post(
        "/collections/it-web-private/sources",
        json={"name": "file", "connector": "web", "settings": {"urls": ["file:///etc/passwd"]}},
    )
    assert resp.status_code == 422, resp.text


# --- feeds ---------------------------------------------------------------------------------


def test_rss_feed_entries(api: httpx.Client, runs: dict[str, dict[str, Any]]) -> None:
    slug = "it-feed"
    source = finished(api, runs, f"{slug}/rss")
    assert source["status"] == "idle" and not source["last_error"], source
    heron, ibis = f"{SITE}/news/heron", f"{SITE}/news/ibis"
    docs = wait_indexed(api, slug, [heron, ibis])
    assert "waited at the weir" in text_of(api, docs[heron])  # content:encoded
    assert "Forty ibis" in text_of(api, docs[ibis])  # description
    published = datetime.fromisoformat(docs[heron]["modified_at"]).astimezone(UTC)
    assert published == datetime(2026, 2, 2, 9, 30, tzinfo=UTC), docs[heron]["modified_at"]
    assert source["document_count"] == 2


def test_atom_feed_with_linked_pages(api: httpx.Client, runs: dict[str, dict[str, Any]]) -> None:
    slug = "it-feed"
    source = finished(api, runs, f"{slug}/atom")
    assert source["status"] == "idle" and not source["last_error"], source
    page_a, only = f"{SITE}/page-a.html", f"{SITE}/sitemap-only.html"
    docs = wait_indexed(api, slug, [page_a, only])
    # fetch_links: the linked page is the document, not the entry's summary
    assert "linked from nowhere" in text_of(api, docs[only])
    assert "digs under the fixture site" in text_of(api, docs[page_a])
    updated = datetime.fromisoformat(docs[only]["modified_at"]).astimezone(UTC)
    assert updated == datetime(2026, 2, 5, 8, 0, tzinfo=UTC)
    assert source["document_count"] == 2


# --- S3 ------------------------------------------------------------------------------------


def test_s3_bucket_through_minio(api: httpx.Client, runs: dict[str, dict[str, Any]]) -> None:
    slug = "it-s3"
    source = finished(api, runs, f"{slug}/bucket")
    assert source["status"] == "idle" and not source["last_error"], source
    kestrel, lorikeet = f"s3://{BUCKET}/inbox/kestrel.txt", f"s3://{BUCKET}/inbox/sub/lorikeet.html"
    docs = wait_indexed(api, slug, [kestrel, lorikeet])
    assert "motorway verge" in text_of(api, docs[kestrel])
    assert "rainbow lorikeet" in text_of(api, docs[lorikeet])
    absent(
        api,
        slug,
        [
            f"s3://{BUCKET}/inbox/skip-me.txt",  # exclude glob
            f"s3://{BUCKET}/inbox/empty.txt",  # empty object
            f"s3://{BUCKET}/inbox/data.bin",  # not in the include globs
            f"s3://{BUCKET}/outside/other.txt",  # outside the prefix
        ],
    )
    assert source["document_count"] == 2
    # the secret is written, never read back; nor does it travel with a document
    assert source["settings"]["secret_key"] == "********"  # noqa: S105 - the mask
    for doc in docs.values():
        assert MINIO_SECRET not in str(doc["metadata"])


def test_deleted_objects_are_detected_on_the_next_run(api: httpx.Client) -> None:
    import boto3

    slug, key = "it-s3-churn", f"churn/wren-{secrets.token_hex(3)}.txt"
    s3 = boto3.client(
        "s3",
        endpoint_url=service_url("minio", 9000),
        aws_access_key_id=MINIO_USER,
        aws_secret_access_key=MINIO_SECRET,
        region_name="us-east-1",
    )
    s3.put_object(Bucket=BUCKET, Key=key, Body=b"A fairy-wren on the balcony rail.")
    ensure_collection(api, slug)
    settings = {
        "bucket": BUCKET,
        "prefix": "churn/",
        "endpoint_url": MINIO,
        "access_key": MINIO_USER,
        "secret_key": MINIO_SECRET,
    }
    source = add_source(api, slug, "churn", "s3", settings)
    uri = f"s3://{BUCKET}/{key}"
    wait_indexed(api, slug, [uri])
    s3.delete_object(Bucket=BUCKET, Key=key)
    wait_for(lambda: source_settled(api, slug, source["id"]), timeout=TIMEOUT, what="run one")
    api.post(f"/sources/{source['id']}/run").raise_for_status()
    gone = wait_for(
        lambda: (
            (d := api.get(f"/documents/{doc_id(slug, uri)}").json())["status"] == "deleted" and d
        ),
        timeout=TIMEOUT,
        what="the deleted object to be noticed",
    )
    assert gone["indexed_at"] is None
    wait_for(lambda: uri not in search_uris(api, slug), timeout=60, what="it to leave Solr")


# --- SPARQL --------------------------------------------------------------------------------


def test_rdf_from_a_sparql_endpoint(api: httpx.Client, runs: dict[str, dict[str, Any]]) -> None:
    slug = "it-sparql"
    source = finished(api, runs, f"{slug}/birds")
    assert source["status"] == "idle" and not source["last_error"], source
    kookaburra, lyrebird = f"{BIRDS}kookaburra", f"{BIRDS}lyrebird"
    docs = wait_indexed(api, slug, [kookaburra, lyrebird])
    assert docs[lyrebird]["title"] == "Superb lyrebird", docs[lyrebird]["title"]
    assert docs[kookaburra]["title"] == "Laughing kookaburra"
    text = text_of(api, docs[kookaburra])
    assert "habitat: woodland" in text and "loud laughter" in text, text
    absent(api, slug, [f"{BIRDS}woodland"])  # not of the selected type
    found_by(api, slug, "chainsaws", {lyrebird})


# --- upload --------------------------------------------------------------------------------


def test_upload_endpoint_end_to_end(api: httpx.Client) -> None:
    slug = "it-upload"
    ensure_collection(api, slug)
    files = [
        ("files", ("kingfisher.txt", b"A sacred kingfisher on the fence wire.", "text/plain")),
        ("files", ("notes.html", b"<html><body><p>Notes on a tawny frogmouth.</p></body></html>")),
        ("files", ("empty.txt", b"", "text/plain")),
    ]
    resp = api.post(f"/collections/{slug}/upload", files=files)
    assert resp.status_code == 202, resp.text
    queued = resp.json()
    assert [d["uri"].rsplit("/", 1)[-1] for d in queued] == ["kingfisher.txt", "notes.html"]
    uris = [d["uri"] for d in queued]
    assert all(u.startswith(f"upload://{slug}/") for u in uris)
    docs = wait_indexed(api, slug, uris)
    assert "sacred kingfisher" in text_of(api, docs[uris[0]])
    found_by(api, slug, "frogmouth", {uris[1]})
    sources = api.get(f"/collections/{slug}/sources").json()
    uploads = next(s for s in sources if s["name"] == "uploads")
    assert uploads["connector"] == "upload" and uploads["document_count"] >= 2
    # the same content again is the same document
    again = api.post(f"/collections/{slug}/upload", files=files[:1]).json()
    assert [d["id"] for d in again] == [queued[0]["id"]]
    wait_indexed(api, slug, uris[:1])
    # the same content under another name is another document with the same hash: an exact
    # duplicate, found by filtering on content_hash_s
    copy = api.post(
        f"/collections/{slug}/upload", files=[("files", ("copy.txt", files[0][1][1]))]
    ).json()[0]
    digest = uris[0].split("/")[3]
    assert copy["id"] != queued[0]["id"] and copy["uri"].split("/")[3] == digest
    wait_indexed(api, slug, [copy["uri"]])
    body = {"q": "*:*", "collection": slug, "filters": {"content_hash_s": [digest]}, "facets": []}
    wait_for(
        lambda: (
            {h["uri"] for h in api.post("/search", json=body).json()["hits"]}
            == {uris[0], copy["uri"]}
        ),
        timeout=60,
        every=1.0,
        what="both copies under one content hash",
    )


def test_ocr_of_an_uploaded_image(api: httpx.Client) -> None:
    from PIL import Image, ImageDraw, ImageFont

    slug = "it-upload"
    ensure_collection(api, slug)
    image = Image.new("RGB", (900, 200), "white")
    ImageDraw.Draw(image).text(
        (30, 60), "KESTREL OVER THE WEIR", fill="black", font=ImageFont.load_default(size=56)
    )
    png = io.BytesIO()
    image.save(png, format="PNG")
    resp = api.post(
        f"/collections/{slug}/upload", files=[("files", ("scan.png", png.getvalue(), "image/png"))]
    )
    assert resp.status_code == 202, resp.text
    uri = resp.json()[0]["uri"]
    doc = wait_indexed(api, slug, [uri])[uri]
    assert doc["media_type"] == "image/png", doc["media_type"]
    assert "KESTREL" in text_of(api, doc).upper(), text_of(api, doc)


def test_upload_needs_an_existing_collection(api: httpx.Client) -> None:
    resp = api.post("/collections/it-no-such-collection/upload", files=[("files", ("a.txt", b"x"))])
    assert resp.status_code == 404, resp.text


def test_a_scheduled_source_is_started_by_the_beat(
    api: httpx.Client, runs: dict[str, dict[str, Any]]
) -> None:
    source = runs["it-scheduled/feed"]
    assert source["schedule"] == "every 15m" and source["last_run_at"] is None
    started = wait_for(
        lambda: source_settled(api, "it-scheduled", source["id"]),
        timeout=150,  # the tick runs once a minute
        every=3.0,
        what="the beat to start the scheduled source",
    )
    assert started["status"] == "idle" and started["document_count"] == 2, started
