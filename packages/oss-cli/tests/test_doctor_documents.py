"""`oss stats` and `oss doctor` point at graph projections behind and documents held."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import SecretStr

from oss_cli.main import document_notes, document_report
from oss_core.config import Settings


def test_notes_name_pending_and_failed_graphs_and_held_documents() -> None:
    assert document_notes({"graph": {}, "interrupted": 0}) == []
    notes = document_notes({"graph": {"pending": 4, "failed": 1}, "interrupted": 2})
    assert len(notes) == 3
    assert notes[0].startswith("4 indexed documents are not in the graph yet")
    assert "oss reindex" in notes[1] and notes[2].startswith("2 documents are held")


def _client(body: dict[str, Any], status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer t"
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_doctor_reports_document_health_from_the_api() -> None:
    settings = Settings(semascope_api_token=SecretStr("t"))
    summary, notes = document_report(settings, _client({"graph": {"pending": 3}, "interrupted": 0}))
    assert summary == "graph pending 3, graph failed 0, held 0" and len(notes) == 1
    summary, notes = document_report(settings, _client({}, status=503))
    assert summary == "stats unavailable (HTTPStatusError)" and notes == []
    assert document_report(Settings(semascope_api_token=None), _client({})) == ("", [])
