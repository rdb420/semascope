"""The CLI waits out the server's rate limits instead of failing (429 with Retry-After)."""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

from oss_cli import main as cli


def test_a_429_is_retried_after_its_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = [
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(429, headers={"Retry-After": "600"}),
        httpx.Response(200, json={"ok": True}),
    ]
    sent: list[httpx.Request] = []
    slept: list[float] = []

    def fake(self: Any, request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return answers.pop(0)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", fake)
    monkeypatch.setattr(time, "sleep", slept.append)
    with httpx.Client(base_url="http://api", transport=cli.PatientTransport()) as client:
        resp = client.post("/search", json={"q": "x"})
    assert resp.status_code == 200 and len(sent) == 3
    assert slept == [7, cli.PatientTransport.MAX_WAIT_S]
    assert all(r.content == b'{"q":"x"}' for r in sent)


def test_it_gives_up_after_a_few_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda self, request: httpx.Response(429)
    )
    monkeypatch.setattr(time, "sleep", lambda s: None)
    with httpx.Client(base_url="http://api", transport=cli.PatientTransport()) as client:
        assert client.get("/search").status_code == 429
