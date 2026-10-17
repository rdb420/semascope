"""Mastodon connector: hashtag, account and public timelines as documents, through the guard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from oss_core.contracts import SourceConfig
from oss_pipeline.connectors import mastodon
from oss_pipeline.connectors.guard import SourceNotAllowed
from oss_pipeline.connectors.mastodon import MastodonConnector
from oss_pipeline.registry import connectors

BASE = "https://masto.test"


def status(n: int, **extra: Any) -> dict[str, Any]:
    return {
        "id": str(1000 - n),
        "url": f"{BASE}/@birder/{1000 - n}",
        "uri": f"{BASE}/users/birder/statuses/{1000 - n}",
        "created_at": f"2026-10-{10 + n:02d}T08:00:00.000Z",
        "edited_at": None,
        "visibility": "public",
        "content": f'<p>Spoonbill {n} at the lagoon <a href="https://x.test">#birding</a></p>'
        "<p><script>alert(1)</script>second line</p>",
        "spoiler_text": "",
        "account": {"acct": "birder", "display_name": "A Birder"},
        "tags": [{"name": "birding"}],
        "media_attachments": [{"description": f"A spoonbill wading {n}"}],
        "in_reply_to_id": None,
        "reblog": None,
        **extra,
    }


class Server:
    def __init__(self, posts: list[dict[str, Any]], *, down: tuple[str, ...] = ()) -> None:
        self.posts = posts
        self.down = down
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, q = request.url.path, request.url.params
        if any(path.startswith(d) for d in self.down):
            return httpx.Response(503)
        if path == "/api/v1/accounts/lookup":
            if q["acct"] == "nobody":
                return httpx.Response(404, json={"error": "Record not found"})
            return httpx.Response(200, json={"id": "42", "acct": q["acct"]})
        if path.startswith(("/api/v1/timelines/", "/api/v1/accounts/42/statuses")):
            posts = [p for p in self.posts if not q.get("max_id") or p["id"] < q["max_id"]]
            return httpx.Response(200, json=posts[: int(q["limit"])])
        return httpx.Response(404)


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("SEMASCOPE_ALLOWED_PRIVATE_HOSTS", "masto.test")
    from oss_pipeline.connectors.guard import guarded_client as real

    def use(svc: Server) -> Server:
        monkeypatch.setattr(
            mastodon,
            "guarded_client",
            lambda **kw: real(transport=httpx.MockTransport(svc), **kw),
        )
        return svc

    return use


def source(**settings: Any) -> SourceConfig:
    return SourceConfig(
        id="m", collection="c", connector="mastodon", settings={"instance": BASE, **settings}
    )


def test_mastodon_is_registered() -> None:
    assert "mastodon" in connectors()


def test_hashtag_posts_become_documents(server: Any) -> None:
    boosted = status(9, url=f"{BASE}/@other/1", account={"acct": "other@far.test"})
    posts = [
        status(1),
        status(2, reblog=boosted),
        status(3, visibility="private"),
        status(4, in_reply_to_id="990", spoiler_text="Bird photos"),
    ]
    svc = server(Server(posts))
    connector = MastodonConnector()
    items = list(connector.discover(source(hashtags=["#birding"], token="tok-1"), None))  # noqa: S106
    assert [i.uri for i in items] == [
        f"{BASE}/@birder/999",
        f"{BASE}/@other/1",
        f"{BASE}/@birder/996",
    ]
    first = items[0]
    body = Path(first.metadata["path"]).read_text()
    assert "Spoonbill 1 at the lagoon #birding" in body and "A spoonbill wading 1" in body
    assert "<script>" not in body and "<a " not in body  # no markup from the server survives
    assert first.metadata["fields"] == {
        "x_author_s": "birder@masto.test",
        "x_mastodon_tags_ss": ["birding"],
    }
    assert first.metadata["title"] == "Post by birder@masto.test"
    assert first.modified_at is not None and first.modified_at.day == 11
    assert items[1].metadata["fields"]["x_author_s"] == "other@far.test"  # the boosted post
    assert items[2].metadata["fields"]["x_in_reply_to_s"] == "990"
    assert "Bird photos" in Path(items[2].metadata["path"]).read_text()
    assert svc.requests[0].url.path == "/api/v1/timelines/tag/birding"
    assert svc.requests[0].headers["Authorization"] == "Bearer tok-1"
    assert connector.report.complete and connector.deletes_missing is False


def test_accounts_and_paging(server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mastodon, "PAGE", 2)
    svc = server(Server([status(n) for n in range(1, 6)]))
    connector = MastodonConnector()
    items = list(connector.discover(source(accounts=["@birder", "nobody"], max_statuses=4), None))
    assert len(items) == 4
    pages = [r.url.params.get("max_id") for r in svc.requests if "statuses" in r.url.path]
    assert pages == [None, "998"]
    assert connector.report.unchecked and "@nobody" in connector.report.unchecked[0]


def test_a_timeline_that_cannot_be_read_is_reported_and_none_fails_the_run(server: Any) -> None:
    server(Server([status(1)], down=("/api/v1/timelines/tag/wetlands",)))
    connector = MastodonConnector()
    items = list(connector.discover(source(hashtags=["birding", "wetlands"]), None))
    assert len(items) == 1 and "#wetlands" in connector.report.unchecked[0]
    server(Server([], down=("/api/v1/timelines",)))
    with pytest.raises(ConnectionError):
        list(MastodonConnector().discover(source(timeline="local"), None))


def test_settings_are_required_and_private_servers_refused(server: Any) -> None:
    with pytest.raises(ValueError, match="instance"):
        list(
            MastodonConnector().discover(
                SourceConfig(id="m", collection="c", connector="mastodon"), None
            )
        )
    with pytest.raises(ValueError, match="hashtags"):
        list(MastodonConnector().discover(source(), None))
    with pytest.raises(SourceNotAllowed):
        list(MastodonConnector().discover(source(instance="http://10.0.0.5", hashtags=["x"]), None))
