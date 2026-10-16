"""Data loss on a live stack: what a source run may delete, one run at a time, and what a delete
removes (docs/adr/0013-deletion-and-retention.md).

The web and feed sources read pages the fixture server serves from memory (`PUT /files/...`)
and can be told to answer 503 (`POST /answer`), so a site going down, a page going away and
a feed rolling over happen between two runs. Archives are written into the import folder the
worker mounts at /import.
"""

from __future__ import annotations

import io
import os
import secrets
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from _stack import (
    ROOT,
    add_source,
    compose,
    doc_id,
    documents_settled,
    ensure_collection,
    env_value,
    search_uris,
    service_url,
    source_settled,
    wait_for,
)

SITE = "http://fixtures:8000"  # as the worker sees it
TIMEOUT = float(os.environ.get("SEMASCOPE_IT_TIMEOUT", "900"))


@pytest.fixture(scope="module")
def fixtures() -> str:
    return service_url("fixtures", 8000)


def serve(fixtures: str, path: str, body: str, ctype: str = "text/html") -> None:
    httpx.put(f"{fixtures}/files{path}", content=body, headers={"Content-Type": ctype})


def unserve(fixtures: str, path: str) -> None:
    httpx.delete(f"{fixtures}/files{path}")


def answer(fixtures: str, path: str, code: int) -> None:
    httpx.post(f"{fixtures}/answer", params={"path": path, "code": code}).raise_for_status()


def run(api: httpx.Client, slug: str, source: dict[str, Any]) -> dict[str, Any]:
    """Start a run once the last one is over; the source when this one has finished."""

    def idle() -> dict[str, Any] | None:
        for s in api.get(f"/collections/{slug}/sources").raise_for_status().json():
            if s["id"] == source["id"] and s["status"] != "running":
                return dict(s)
        return None

    before: dict[str, Any] = wait_for(idle, timeout=TIMEOUT, what="the last run")
    api.post(f"/sources/{source['id']}/run").raise_for_status()
    after: dict[str, Any] = wait_for(
        lambda: (
            (s := source_settled(api, slug, source["id"]))
            and s["last_run_at"] != before["last_run_at"]
            and s
        ),
        timeout=TIMEOUT,
        what=f"a run of {slug}",
    )
    return after


def statuses(api: httpx.Client, slug: str, uris: list[str]) -> dict[str, str]:
    out = {}
    for uri in uris:
        resp = api.get(f"/documents/{doc_id(slug, uri)}")
        out[uri] = resp.json()["status"] if resp.status_code == 200 else "absent"
    return out


def settled(api: httpx.Client, slug: str, uris: list[str]) -> dict[str, dict[str, Any]]:
    docs: dict[str, dict[str, Any]] = wait_for(
        lambda: documents_settled(api, slug, uris), timeout=TIMEOUT, what=f"{uris} in {slug}"
    )
    return docs


def page(word: str) -> str:
    return f"<html><head><title>{word}</title></head><body><p>A {word} page.</p></body></html>"


# --- a site going down (SEMA-010) ------------------------------------------------------------


def test_a_site_that_goes_down_loses_nothing_and_pages_come_back(
    api: httpx.Client, fixtures: str
) -> None:
    slug, tag = "it-retain-web", secrets.token_hex(3)
    paths = [f"/retain-{tag}/a.html", f"/retain-{tag}/b.html"]
    uris = [SITE + p for p in paths]
    for path, word in zip(paths, ("quoll", "bilby"), strict=True):
        serve(fixtures, path, page(f"{word}{tag}"))
    ensure_collection(api, slug)
    settings = {"urls": uris, "delay_seconds": 0, "respect_robots": False}
    source = add_source(api, slug, f"site-{tag}", "web", settings, run=False)
    first = run(api, slug, source)
    assert first["status"] == "idle" and not first["last_error"], first
    assert {d["status"] for d in settled(api, slug, uris).values()} == {"indexed"}

    # the whole site answers 503: the run fails and nothing is deleted
    for path in paths:
        answer(fixtures, path, 503)
    down = run(api, slug, source)
    assert down["status"] == "failed" and "no page could be read" in down["last_error"], down
    assert statuses(api, slug, uris) == dict.fromkeys(uris, "indexed")

    # one page answers 503: the run reads the other and deletes nothing
    answer(fixtures, paths[0], 0)
    half = run(api, slug, source)
    assert half["status"] == "idle" and "nothing was deleted" in half["last_error"], half
    assert statuses(api, slug, uris) == dict.fromkeys(uris, "indexed")

    # the page is really gone (404): now it is deleted, from search too
    answer(fixtures, paths[1], 0)
    unserve(fixtures, paths[1])
    gone = run(api, slug, source)
    assert gone["status"] == "idle" and not gone["last_error"], gone
    wait_for(
        lambda: statuses(api, slug, uris)[uris[1]] == "deleted",
        timeout=TIMEOUT,
        what="the removed page to be deleted",
    )
    wait_for(lambda: uris[1] not in search_uris(api, slug), timeout=60, what="it to leave Solr")

    # and back, with the same content: deleted documents come back (SEMA-034)
    serve(fixtures, paths[1], page(f"bilby{tag}"))
    run(api, slug, source)
    back = settled(api, slug, [uris[1]])[uris[1]]
    assert back["status"] == "indexed", back
    wait_for(
        lambda: search_uris(api, slug, f"bilby{tag}") == {uris[1]},
        timeout=60,
        what="it to be searchable again",
    )


# --- a feed rolling over (SEMA-034) ----------------------------------------------------------


def rss(entries: list[str]) -> str:
    items = "".join(
        f"<item><title>{e}</title><link>{SITE}/news/{e}</link><guid>{SITE}/news/{e}</guid>"
        f"<description>News of the {e}.</description></item>"
        for e in entries
    )
    return (
        f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{items}</channel></rss>'
    )


def test_articles_that_roll_off_a_feed_are_kept(api: httpx.Client, fixtures: str) -> None:
    slug, tag = "it-retain-feed", secrets.token_hex(3)
    path = f"/feeds/rolling-{tag}.xml"
    old, new = [f"numbat{tag}", f"dingo{tag}"], [f"wombat{tag}"]
    serve(fixtures, path, rss(old), "application/rss+xml")
    ensure_collection(api, slug)
    source = add_source(api, slug, f"news-{tag}", "feed", {"urls": [SITE + path]}, run=False)
    run(api, slug, source)
    settled(api, slug, [f"{SITE}/news/{e}" for e in old])
    serve(fixtures, path, rss(new), "application/rss+xml")  # yesterday's entries rolled off
    after = run(api, slug, source)
    assert after["status"] == "idle" and not after["last_error"], after
    uris = [f"{SITE}/news/{e}" for e in old + new]
    docs = settled(api, slug, uris)
    assert {u: d["status"] for u, d in docs.items()} == dict.fromkeys(uris, "indexed")


# --- one run per source (SEMA-012) -------------------------------------------------------------


def test_a_source_cannot_be_started_while_it_runs(api: httpx.Client, fixtures: str) -> None:
    slug, tag = "it-retain-busy", secrets.token_hex(3)
    paths = [f"/busy-{tag}/{n}.html" for n in range(4)]
    for n, path in enumerate(paths):
        serve(fixtures, path, page(f"page{n}{tag}"))
    ensure_collection(api, slug)
    settings = {"urls": [SITE + p for p in paths], "delay_seconds": 2, "respect_robots": False}
    source = add_source(api, slug, f"slow-{tag}", "web", settings, run=False)
    api.post(f"/sources/{source['id']}/run").raise_for_status()
    wait_for(
        lambda: next(
            s["status"] == "running"
            for s in api.get(f"/collections/{slug}/sources").json()
            if s["id"] == source["id"]
        ),
        timeout=120,
        every=0.5,
        what="the run to start",
    )
    second = api.post(f"/sources/{source['id']}/run")
    assert second.status_code == 409, second.text
    done = wait_for(
        lambda: source_settled(api, slug, source["id"]), timeout=TIMEOUT, what="the run"
    )
    assert done["status"] == "idle" and done["document_count"] == 4, done


# --- archive limits (SEMA-017) and the folder facet ------------------------------------------


def import_dir() -> Path:
    raw = env_value("SEMASCOPE_IMPORT_DIR") or "./data/import"
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def test_an_archive_bomb_is_expanded_within_its_budget(api: httpx.Client) -> None:
    slug, tag = "it-retain-archive", secrets.token_hex(3)
    folder = import_dir() / f"archives-{tag}"
    folder.mkdir(parents=True)
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("reports/kept.txt", f"A short report about the platypus{tag}.")
        for n in range(20):  # 20 x 20 MB of zeros: 400 MB claimed in about 400 kB
            with zf.open(f"zeros/{n}.bin", "w") as member:
                for _ in range(20):
                    member.write(b"\0" * 1_000_000)
    with zipfile.ZipFile(folder / "bomb.zip", "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("inner.zip", inner.getvalue())
    ensure_collection(api, slug)
    source = add_source(
        api, slug, f"bomb-{tag}", "archive", {"path": f"/import/archives-{tag}"}, run=False
    )
    done = run(api, slug, source)
    assert done["status"] == "idle", done
    assert done["document_count"] == 1, done  # every zero member is over the ratio limit
    uri = f"file:///import/archives-{tag}/bomb.zip!/inner.zip!/reports/kept.txt"
    assert settled(api, slug, [uri])[uri]["status"] == "indexed"
    body = {"q": "*:*", "collection": slug, "facets": ["folder_paths"]}
    facets = wait_for(
        lambda: api.post("/search", json=body).json()["facets"].get("folder_paths"),
        timeout=60,
        what="the folder facet",
    )
    values = {v["value"] for v in facets}
    assert f"archives-{tag}/bomb.zip/inner.zip/reports" in values, values
    worker = compose("ps", "--format", "{{.Service}} {{.State}}", "worker")
    assert "running" in worker.stdout, worker.stdout


# --- what a delete removes (SEMA-011) --------------------------------------------------------


def worker_has(path: str) -> bool:
    proc = compose("exec", "-T", "worker", "test", "-e", path, timeout=60)
    return proc.returncode == 0


def test_deleting_a_collection_removes_its_files_and_graph(api: httpx.Client) -> None:
    slug, tag = f"it-purge-{secrets.token_hex(3)}", secrets.token_hex(3)
    ensure_collection(api, slug, profile="default")  # with the graph stage
    content = f"Echidna{tag} notes for a collection that is deleted.".encode()
    queued = api.post(
        f"/collections/{slug}/upload", files=[("files", ("notes.txt", content, "text/plain"))]
    )
    assert queued.status_code == 202, queued.text
    uri = queued.json()[0]["uri"]
    doc = settled(api, slug, [uri])[uri]
    assert doc["status"] == "indexed", doc
    digest = uri.split("/")[3]
    blob = f"/data/blobs/{digest[:2]}/{digest}.txt"
    assert worker_has(blob) and worker_has(f"/data/uploads/{slug}")
    graph = {"query": f"MATCH (d:Document {{id: '{doc['id']}'}}) RETURN count(d) AS n"}
    wait_for(
        lambda: api.post("/graph/cypher", json=graph).json() == [{"n": 1}],
        timeout=120,
        what="the graph projection",
    )

    assert api.delete(f"/collections/{slug}").status_code == 204
    found = api.post("/search", json={"q": f"echidna{tag}", "facets": []}).json()
    assert found["total"] == 0, found  # out of search at once
    wait_for(lambda: not worker_has(blob), timeout=120, what="the blob to go")
    wait_for(lambda: not worker_has(f"/data/uploads/{slug}"), timeout=120, what="the uploads")
    wait_for(
        lambda: api.post("/graph/cypher", json=graph).json() == [{"n": 0}],
        timeout=120,
        what="the graph nodes to go",
    )
