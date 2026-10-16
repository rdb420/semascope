"""Transient failures are retried with backoff; permanent ones are not."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from oss_core.contracts import StageContext, Tag, WorkingDocument
from oss_pipeline.clients.mineru import MineruError
from oss_pipeline.clients.solr import SolrError
from oss_pipeline.clients.tika import TikaError
from oss_pipeline.retry import backoff, is_transient
from oss_pipeline.stages.base import TRANSIENT_FAILURES, run_stage

REQ = httpx.Request("POST", "http://svc/x")


def _status(code: int) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError("x", request=REQ, response=httpx.Response(code, request=REQ))


def _wrapped(cause: BaseException) -> RuntimeError:
    try:
        raise RuntimeError("stage failed") from cause
    except RuntimeError as exc:
        return exc


@pytest.mark.parametrize(
    ("exc", "transient"),
    [
        (httpx.ConnectError("refused", request=REQ), True),
        (httpx.ReadTimeout("slow", request=REQ), True),
        (_status(503), True),
        (_status(429), True),
        (_status(400), False),
        (_status(404), False),
        (SolrError("down", 503), True),
        (SolrError("bad query", 400), False),
        (TikaError("busy", 503), True),
        (TikaError("cannot parse", 422), False),
        (TikaError("parser crashed", 500), False),
        (TikaError("too large"), False),
        (MineruError("parse failed: bad pdf"), False),
        (_wrapped(httpx.ConnectError("refused", request=REQ)), True),
        (_wrapped(ValueError("no")), False),
        (ConnectionRefusedError(), True),
        (ValueError("SEMASCOPE_EMBED_BASE_URL is not set"), False),
        (FileNotFoundError("gone"), False),
    ],
)
def test_transient_or_permanent(exc: BaseException, transient: bool) -> None:
    assert is_transient(exc) is transient


def test_neo4j_unavailable_is_transient() -> None:
    from neo4j.exceptions import ServiceUnavailable

    assert is_transient(ServiceUnavailable("no route"))


def test_backoff_doubles_with_bounded_jitter() -> None:
    for attempt, expected in ((1, 2.0), (2, 4.0), (3, 8.0)):
        assert expected * 0.75 <= backoff(attempt, 2.0) <= expected * 1.25


class Flaky:
    name = "flaky"
    version = "1"

    def __init__(self, errors: list[BaseException]) -> None:
        self.errors = errors
        self.calls = 0

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        self.calls += 1
        doc.tags.append(Tag(iri=f"x{self.calls}", label="x", kind="concept", stage="flaky"))
        if self.errors:
            raise self.errors.pop(0)
        return doc


def _run(stage: Flaky, **settings: Any) -> WorkingDocument:
    doc = WorkingDocument(id="d", collection="c", source_id="1", uri="u", content_hash="h")
    ctx = StageContext(
        collection="c", profile="p", settings={"stage_retry_backoff_s": 0.0, **settings}
    )
    return run_stage(stage, doc, ctx)  # type: ignore[arg-type]


def test_a_transient_failure_is_retried_on_a_clean_copy() -> None:
    stage = Flaky([httpx.ConnectError("refused", request=REQ), _status(503)])
    out = _run(stage)
    assert stage.calls == 3 and out.stage_runs[-1].outcome == "ok"
    assert [t.iri for t in out.tags] == ["x3"], "failed attempts leave nothing behind"
    assert TRANSIENT_FAILURES not in out.fields


def test_a_permanent_failure_is_not_retried() -> None:
    stage = Flaky([TikaError("cannot parse", 422)])
    out = _run(stage)
    assert stage.calls == 1 and out.stage_runs[-1].outcome == "failed"
    assert TRANSIENT_FAILURES not in out.fields and out.tags == []


def test_retries_are_bounded_and_reported() -> None:
    stage = Flaky([SolrError("down", 503) for _ in range(10)])
    out = _run(stage, stage_retries=2)
    assert stage.calls == 3
    run = out.stage_runs[-1]
    assert run.outcome == "failed" and "after 3 attempts" in (run.message or "")
    assert out.fields[TRANSIENT_FAILURES] == ["flaky"]
    stage = Flaky([SolrError("down", 503) for _ in range(10)])
    _run(stage, stage_retries=0)
    assert stage.calls == 1
