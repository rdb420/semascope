"""Hypothesis connector: annotations of a group or page as documents, through the guard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from oss_core.contracts import SourceConfig
from oss_pipeline.connectors import hypothesis
from oss_pipeline.connectors.guard import SourceNotAllowed
from oss_pipeline.connectors.hypothesis import HypothesisConnector
from oss_pipeline.registry import connectors

API = "https://hyp.test/api"


def row(n: int, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"ann{n}",
        "created": f"2026-10-0{n}T10:00:00.000000+00:00",
        "updated": f"2026-10-0{n}T11:00:00.000000+00:00",
        "user": "acct:ornithologist@hyp.test",
        "group": "grp1",
        "uri": f"https://birds.example/page{n}",
        "text": f"Note {n} about <b>herons</b>.\n\nSecond paragraph.",
        "tags": ["herons", "wetlands"],
        "target": [
            {
                "source": f"https://birds.example/page{n}",
                "selector": [{"type": "TextQuoteSelector", "exact": f"a heron, sighting {n}"}],
            }
        ],
        "document": {"title": [f"Bird page {n}"]},
        "links": {"html": f"https://hyp.test/a/ann{n}"},
        **extra,
    }


class Service:
    """A Hypothesis search API: pages of `rows`, sorted by update, after `search_after`."""

    def __init__(self, rows: list[dict[str, Any]], fail_after: str | None = None) -> None:
        self.rows = rows
        self.fail_after = fail_after
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        q = request.url.params
        after = q.get("search_after")
        if self.fail_after and after == self.fail_after:
            return httpx.Response(503)
        rows = [r for r in self.rows if not after or r["updated"] > after]
        return httpx.Response(200, json={"total": len(rows), "rows": rows[: int(q["limit"])]})


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("SEMASCOPE_ALLOWED_PRIVATE_HOSTS", "hyp.test")
    from oss_pipeline.connectors.guard import guarded_client as real

    def use(svc: Service) -> Service:
        monkeypatch.setattr(
            hypothesis,
            "guarded_client",
            lambda **kw: real(transport=httpx.MockTransport(svc), **kw),
        )
        return svc

    return use


def source(**settings: Any) -> SourceConfig:
    return SourceConfig(
        id="h", collection="c", connector="hypothesis", settings={"api_url": API, **settings}
    )


def test_hypothesis_is_registered() -> None:
    assert "hypothesis" in connectors()


def test_annotations_become_documents(service: Any) -> None:
    svc = service(Service([row(1), row(2), row(3, hidden=True)]))
    connector = HypothesisConnector()
    items = list(connector.discover(source(group="grp1", token="secret-token", tag="herons"), None))  # noqa: S106
    assert [i.uri for i in items] == ["https://hyp.test/a/ann1", "https://hyp.test/a/ann2"]
    first = items[0]
    body = Path(first.metadata["path"]).read_text()
    assert "a heron, sighting 1" in body and "&lt;b&gt;herons&lt;/b&gt;" in body  # escaped
    assert first.metadata["title"] == "Annotation on Bird page 1"
    assert first.metadata["fields"] == {
        "x_annotates_s": "https://birds.example/page1",
        "x_annotation_tags_ss": ["herons", "wetlands"],
        "x_hypothesis_group_s": "grp1",
        "x_annotator_s": "acct:ornithologist@hyp.test",
    }
    assert first.modified_at is not None and first.modified_at.day == 1
    request = svc.requests[0]
    assert request.headers["Authorization"] == "Bearer secret-token"
    assert request.url.params["group"] == "grp1" and request.url.params["tag"] == "herons"
    assert connector.report.complete


def test_pages_follow_search_after_and_a_failed_page_keeps_the_run_incomplete(
    service: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hypothesis, "PAGE", 2)
    rows = [row(n) for n in range(1, 6)]
    svc = service(Service(rows))
    assert (
        len(list(HypothesisConnector().discover(source(uri="https://birds.example/"), None))) == 5
    )
    assert [r.url.params.get("search_after") for r in svc.requests] == [
        None,
        rows[1]["updated"],
        rows[3]["updated"],
    ]
    service(Service(rows, fail_after=rows[1]["updated"]))
    connector = HypothesisConnector()
    assert len(list(connector.discover(source(group="grp1"), None))) == 2
    assert connector.report.unchecked and not connector.report.complete


def test_the_annotation_limit_truncates_the_run(service: Any) -> None:
    service(Service([row(n) for n in range(1, 6)]))
    connector = HypothesisConnector()
    assert len(list(connector.discover(source(group="grp1", max_annotations=3), None))) == 3
    assert connector.report.truncated


def test_a_service_that_cannot_be_read_fails_the_run(service: Any) -> None:
    service(Service([], fail_after=""))

    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"reason": "bad token"})

    service(down)
    with pytest.raises(ConnectionError):
        list(HypothesisConnector().discover(source(group="grp1"), None))


def test_filters_are_required_and_private_services_are_refused(service: Any) -> None:
    with pytest.raises(ValueError, match="group, uri or user"):
        list(HypothesisConnector().discover(source(tag="x"), None))
    with pytest.raises(SourceNotAllowed):
        list(
            HypothesisConnector().discover(source(api_url="http://127.0.0.1/api", group="g"), None)
        )
