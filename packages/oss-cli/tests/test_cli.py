import json
import re
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import oss_cli.main as cli
from oss_cli.main import app

REPO = Path(__file__).resolve().parents[3]
runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "semascope" in result.stdout


def test_init_writes_secrets(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    result = runner.invoke(app, ["init", "--env-file", str(env)])
    assert result.exit_code == 0, result.stdout
    text = env.read_text()
    assert "POSTGRES_PASSWORD=" in text and "change-me" not in text
    assert re.search(r"^VALKEY_PASSWORD=[A-Za-z0-9_-]{24,}$", text, re.M), text
    assert "\nSEMASCOPE_DEFAULT_ROLE=none\n" in text
    assert runner.invoke(app, ["init", "--env-file", str(env)]).exit_code != 0
    from oss_core.config import Settings

    # every secret the server checks at start-up is generated
    assert Settings(_env_file=env).insecure_secrets() == []  # type: ignore[call-arg]


def test_pack_validate_base() -> None:
    result = runner.invoke(app, ["pack", "validate", str(REPO / "packs" / "base")])
    assert result.exit_code == 0, result.stdout


def test_init_runtime_and_provider_flags(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    result = runner.invoke(
        app,
        [
            "init",
            "--env-file",
            str(env),
            "--runtime",
            "remote",
            "--extract",
            "tika",
            "--embed-url",
            "http://embed.test",
            "--ner-url",
            "http://ner.test",
            "--remote-token",
            "t0k",
        ],
    )
    assert result.exit_code == 0, result.stdout
    text = env.read_text()
    assert "SEMASCOPE_RUNTIME=remote" in text
    assert "SEMASCOPE_EMBEDDER=sidecar" in text and "SEMASCOPE_NER_PROVIDER=remote" in text
    assert "SEMASCOPE_EXTRACT_PROVIDER=tika" in text and "SEMASCOPE_REMOTE_API_KEY=t0k" in text
    # a remote provider without its URL is refused (wide, plain output: the error box wraps
    # and colours its text on a narrow terminal)
    wide = CliRunner(env={"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"})
    bad = wide.invoke(app, ["init", "--env-file", str(tmp_path / "b.env"), "--runtime", "remote"])
    assert bad.exit_code != 0 and "embed-url" in re.sub(r"\x1b\[[0-9;]*m", "", bad.output)
    gpu = runner.invoke(app, ["init", "--env-file", str(tmp_path / "g.env"), "--runtime", "gpu"])
    assert (
        gpu.exit_code == 0 and "SEMASCOPE_NER_PROVIDER=gliner" in (tmp_path / "g.env").read_text()
    )


def test_demo_only_limits_the_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/knowledge/packs/import"):
            return httpx.Response(202, json={"id": 1, "status": "queued"})
        if path.endswith("/knowledge/pack-jobs/1"):
            result = {"pack_id": "demo", "version": "1", "status": "active"}
            return httpx.Response(200, json={"id": 1, "status": "done", "result": result})
        if path.endswith("/collections/demo"):
            return httpx.Response(200, json={"slug": "demo"})
        if path.endswith("/collections/demo/sources") and request.method == "GET":
            return httpx.Response(200, json=[])
        if path.endswith("/collections/demo/sources"):
            created.append(json.loads(request.content))
            return httpx.Response(201, json={"id": 7, **created[-1]})
        if path.endswith("/sources/7/run"):
            return httpx.Response(202, json={"source_id": 7, "task_id": "t"})
        return httpx.Response(404)

    monkeypatch.setattr(
        cli,
        "_api",
        lambda: httpx.Client(base_url="http://api/api", transport=httpx.MockTransport(handler)),
    )
    result = runner.invoke(app, ["demo", "--only", "odyssey", "--only", "iliad"])
    assert result.exit_code == 0, result.stdout
    assert created[0]["settings"] == {
        "path": "/app/packs/demo/corpus",
        "include": ["odyssey.txt", "iliad.txt"],
    }
    assert "2 texts" in result.stdout
