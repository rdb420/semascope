"""The web, feed and SPARQL connectors against real HTTP servers on the loopback interface.

The site, sitemaps and feeds are the integration stack's fixtures
(tests/integration/stack), served by the same fixture server in a thread here.
Loopback is a private address, so it is allowed the way an operator allows an
internal host: SEMASCOPE_ALLOWED_PRIVATE_HOSTS.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from oss_core.contracts import SourceConfig, SourceItem
from oss_pipeline.connectors.feed import FeedConnector
from oss_pipeline.connectors.rdf import RdfConnector
from oss_pipeline.connectors.web import WebConnector

ROOT = Path(__file__).resolve().parents[3]
STACK = ROOT / "tests" / "integration" / "stack"
BIRDS = "https://example.test/birds#"


def _load_fixture_server() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fixture_server", STACK / "fixture_server.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["fixture_server"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server = _load_fixture_server().serve(STACK / "web", host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(autouse=True)
def allow_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEMASCOPE_ALLOWED_PRIVATE_HOSTS", "127.0.0.1/32")


def _src(connector: str, **settings: Any) -> SourceConfig:
    return SourceConfig(id="s", collection="c", connector=connector, settings=settings)


def _web(site: str, **settings: Any) -> dict[str, SourceItem]:
    settings.setdefault("delay_seconds", 0)
    items = WebConnector().discover(_src("web", **settings), None)
    return {i.uri.removeprefix(site): i for i in items}


# --- web -----------------------------------------------------------------------------------


def test_crawl_stays_on_the_host_within_depth_robots_and_size(site: str) -> None:
    found = _web(site, urls=[f"{site}/index.html"], mode="crawl", max_depth=2, max_bytes=50_000)
    assert sorted(found) == [
        "/deep/level1.html",
        "/deep/level2.html",
        "/index.html",
        "/page-a.html",
        "/page-b.html",  # the #top fragment is dropped
    ]
    assert {i.metadata["depth"] for i in found.values()} == {0, 1, 2}
    page_a = found["/page-a.html"]
    assert page_a.metadata["media_type"] == "text/html"
    assert b"aardvark" in Path(page_a.metadata["path"]).read_bytes()
    assert WebConnector().fetch(page_a).path == page_a.metadata["path"]
    assert page_a.modified_at is not None and page_a.modified_at.year == 2026


def test_crawl_stops_at_max_pages(site: str) -> None:
    found = _web(site, urls=[f"{site}/index.html"], mode="crawl", max_pages=2)
    assert sorted(found) == ["/index.html", "/page-a.html"]


def test_include_and_exclude_patterns(site: str) -> None:
    found = _web(site, urls=[f"{site}/index.html"], mode="crawl", exclude=[r"/deep/"])
    assert not [u for u in found if u.startswith("/deep/")]
    only = _web(site, urls=[f"{site}/index.html"], mode="crawl", include=[r"index|page-b"])
    assert sorted(only) == ["/index.html", "/page-b.html"]


def test_robots_can_be_ignored_in_pages_mode(site: str) -> None:
    urls = [f"{site}/page-a.html", f"{site}/private/secret.html"]
    assert sorted(_web(site, urls=urls)) == ["/page-a.html"]
    assert sorted(_web(site, urls=urls, respect_robots=False)) == [
        "/page-a.html",
        "/private/secret.html",
    ]


def test_sitemap_index_is_followed_and_robots_applied(site: str) -> None:
    found = _web(site, urls=[f"{site}/sitemap-index.xml"], mode="sitemap")
    assert sorted(found) == ["/page-a.html", "/page-b.html", "/sitemap-only.html"]


def test_pages_over_the_cap_are_skipped_with_or_without_a_length(site: str) -> None:
    urls = [f"{site}/big.html", f"{site}/big-stream.html", f"{site}/page-b.html"]
    assert sorted(_web(site, urls=urls, max_bytes=50_000)) == ["/page-b.html"]
    assert len(_web(site, urls=urls, max_bytes=500_000)) == 3


def test_a_redirect_to_a_private_address_is_not_followed(site: str) -> None:
    assert _web(site, urls=[f"{site}/redirect-private.html", f"{site}/page-a.html"]) == {
        "/page-a.html": _web(site, urls=[f"{site}/page-a.html"])["/page-a.html"]
    }


def test_a_private_seed_fails_the_run(monkeypatch: pytest.MonkeyPatch, site: str) -> None:
    from oss_pipeline.connectors.guard import SourceNotAllowed

    monkeypatch.setenv("SEMASCOPE_ALLOWED_PRIVATE_HOSTS", "")
    with pytest.raises(SourceNotAllowed, match="private address"):
        _web(site, urls=[f"{site}/index.html"])


# --- feeds ---------------------------------------------------------------------------------


def _feed(**settings: Any) -> list[SourceItem]:
    return list(FeedConnector().discover(_src("feed", **settings), None))


def test_rss_entries_become_html_documents(site: str) -> None:
    items = {i.uri.removeprefix(site): i for i in _feed(urls=[f"{site}/feeds/rss.xml"])}
    assert sorted(items) == ["/news/heron", "/news/ibis"]
    heron = items["/news/heron"]
    body = Path(heron.metadata["path"]).read_text()
    assert "<title>Heron sighted at the weir</title>" in body and "grey heron" in body
    assert "Forty ibis" in Path(items["/news/ibis"].metadata["path"]).read_text()
    assert heron.modified_at is not None and heron.modified_at.isoformat().startswith(
        "2026-02-02T09:30"
    )
    assert heron.metadata["feed"] == f"{site}/feeds/rss.xml"


def test_atom_entries_with_linked_pages(site: str) -> None:
    items = {
        i.uri.removeprefix(site): i
        for i in _feed(urls=[f"{site}/feeds/atom.xml"], fetch_links=True)
    }
    assert sorted(items) == ["/page-a.html", "/sitemap-only.html"]
    assert b"linked from nowhere" in Path(items["/sitemap-only.html"].metadata["path"]).read_bytes()
    assert items["/page-a.html"].metadata["title"] == "Page A changed"


def test_feed_max_entries_and_an_unreachable_feed(site: str) -> None:
    assert len(_feed(urls=[f"{site}/feeds/rss.xml"], max_entries=1)) == 1
    assert _feed(urls=[f"{site}/feeds/missing.xml"]) == []


# --- SPARQL --------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sparql() -> Iterator[str]:
    """A SPARQL 1.1 protocol endpoint (GET /query) over pyoxigraph, with the birds graph."""

    import pyoxigraph as ox

    store = ox.Store()
    store.load(
        path=str(STACK / "sparql.ttl"),
        format=ox.RdfFormat.TURTLE,
        to_graph=ox.NamedNode("https://example.test/graphs/birds"),
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

        def do_GET(self) -> None:  # noqa: N802
            query = parse_qs(urlparse(self.path).query)["query"][0]
            results = store.query(query)
            assert isinstance(results, ox.QuerySolutions | ox.QueryBoolean)
            body = results.serialize(format=ox.QueryResultsFormat.JSON)
            assert body is not None
            self.send_response(200)
            self.send_header("Content-Type", "application/sparql-results+json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/query"
    server.shutdown()


def test_sparql_endpoint_subjects_of_the_chosen_types(sparql: str) -> None:
    items = RdfConnector().discover(
        _src(
            "rdf",
            endpoint=sparql,
            graph="https://example.test/graphs/birds",
            types=[f"{BIRDS}Bird"],
        ),
        None,
    )
    by_iri = {i.uri: i for i in items}
    assert sorted(by_iri) == [f"{BIRDS}kookaburra", f"{BIRDS}lyrebird"]
    lyrebird = by_iri[f"{BIRDS}lyrebird"]
    assert lyrebird.metadata["title"] == "Superb lyrebird"
    assert lyrebird.metadata["fields"] == {"x_subject_s": f"{BIRDS}lyrebird"}
    text = Path(lyrebird.metadata["path"]).read_text()
    assert "habitat: rainforest" in text and "chainsaws" in text


def test_sparql_without_types_reads_every_subject_and_caps_the_response(sparql: str) -> None:
    items = list(
        RdfConnector().discover(
            _src("rdf", endpoint=sparql, graph="https://example.test/graphs/birds"), None
        )
    )
    assert {i.uri.rsplit("#", 1)[-1] for i in items} == {"kookaburra", "lyrebird", "woodland"}
    from oss_pipeline.connectors.guard import TooLarge

    with pytest.raises(TooLarge):
        list(
            RdfConnector().discover(
                _src(
                    "rdf", endpoint=sparql, graph="https://example.test/graphs/birds", max_bytes=100
                ),
                None,
            )
        )
