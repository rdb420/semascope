"""`oss export documents`, `oss export entities` and `oss export graph` (ETL-EXP-08, DM-20,
ETL-EXP-04): the request each sends and the file each streams to."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import oss_cli.main as cli
from oss_cli.main import app

runner = CliRunner(env={"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"})


def _mock(monkeypatch: pytest.MonkeyPatch, status: int = 200) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if status != 200:
            return httpx.Response(status, json={"detail": "reader role required on demo"})
        return httpx.Response(200, content=b"PAR1...PAR1")

    monkeypatch.setattr(
        cli,
        "_api",
        lambda: httpx.Client(base_url="http://h/api", transport=httpx.MockTransport(handler)),
    )
    return seen


def test_export_documents_as_parquet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _mock(monkeypatch)
    out = tmp_path / "d.parquet"
    result = runner.invoke(
        app,
        [
            "export", "documents", "flood", "--collection", "demo", "--format", "parquet",
            "--text-chars", "500", "--out", str(out),
        ],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == b"PAR1...PAR1"
    req = seen[0]
    assert req.method == "POST" and req.url.path == "/api/export/documents"
    assert req.url.params["format"] == "parquet" and req.url.params["text_chars"] == "500"
    assert json.loads(req.content) == {"q": "flood", "collection": "demo", "mode": "keyword"}


def test_export_entities_and_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _mock(monkeypatch)
    result = runner.invoke(app, ["export", "entities", "--collection", "demo", "--limit", "5"])
    assert result.exit_code == 0, result.output
    assert seen[0].url.path == "/api/export/entities" and seen[0].url.params["limit"] == "5"
    assert seen[0].url.params["format"] == "csv"
    out = tmp_path / "g.nq"
    result = runner.invoke(
        app, ["export", "graph", "--collection", "demo", "--format", "nquads", "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert seen[1].method == "GET" and seen[1].url.path == "/api/export/graph"
    assert dict(seen[1].url.params) == {"collection": "demo", "format": "nquads", "limit": "10000"}


def test_a_refused_export_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock(monkeypatch, status=403)
    result = runner.invoke(app, ["export", "graph", "--collection", "demo"])
    assert result.exit_code == 1 and "reader role required" in result.output
