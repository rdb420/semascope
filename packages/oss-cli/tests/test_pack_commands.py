"""`oss pack diff`, `import`, `activate`, `remove` and `validate` against a mocked API."""

import json
import shutil
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

import oss_cli.main as cli
from oss_cli.main import app

REPO = Path(__file__).resolve().parents[3]
runner = CliRunner(env={"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"})

PLAN: dict[str, Any] = {
    "pack_id": "demo",
    "active_version": "0.1.0",
    "candidate_version": "0.2.0",
    "same_content": False,
    "changes": True,
    "requires_confirmation": True,
    "diff": {
        "summary": {
            "added": {"concept": 2},
            "removed": {"concept": 1},
            "changed": {"individual": 1},
            "labels_added": 3,
            "labels_removed": 4,
            "became_taggable": 0,
            "no_longer_taggable": 0,
        },
        "added": [{"iri": "u:n", "kind": "concept", "label": "Oracle"}],
        "removed": [{"iri": "u:i", "kind": "concept", "label": "Islam"}],
        "changed": [
            {"iri": "u:z", "kind": "individual", "label": "Zeus", "fields": {"alt_labels": []}}
        ],
        "truncated": False,
    },
    "plan": {
        "reindex": ["texts"],
        "collections": [
            {
                "collection": "texts",
                "reindex": True,
                "documents": 10,
                "tagged_documents": 7,
                "affected_documents": 2,
            }
        ],
    },
}
PACK = {
    "pack_id": "demo",
    "version": "0.2.0",
    "status": "active",
    "counts": {"terms": 9},
    "cq_results": {},
}
JOB = {"id": 3, "kind": "import", "status": "queued", "pack_id": "demo", "version": "0.2.0"}


def _mock(monkeypatch: pytest.MonkeyPatch, calls: list[httpx.Request]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path.endswith("/knowledge/packs/diff"):
            return httpx.Response(200, json=PLAN)
        confirmed = request.url.params.get("confirm") == "true" or (
            request.content and json.loads(request.content).get("confirm")
        )
        if path.endswith("/knowledge/packs/import") or path.endswith("/activate"):
            if not confirmed:
                return httpx.Response(409, json={"detail": "review the plan", "plan": PLAN})
            kind = "import" if path.endswith("import") else "activate"
            return httpx.Response(202, json={**JOB, "kind": kind, "plan": PLAN})
        if path.endswith(f"/knowledge/pack-jobs/{JOB['id']}"):
            return httpx.Response(200, json={**JOB, "status": "done", "result": PACK})
        if request.method == "DELETE":
            return httpx.Response(
                200,
                json={
                    "pack_id": "demo",
                    "version": "0.1.0",
                    "concepts_removed": 80,
                    "dictionary_entries_removed": True,
                    "documents_still_tagged": 5,
                    "note": "",
                },
            )
        return httpx.Response(404)

    monkeypatch.setattr(
        cli,
        "_api",
        lambda: httpx.Client(base_url="http://api/api", transport=httpx.MockTransport(handler)),
    )


def test_pack_diff_prints_the_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[httpx.Request] = []
    _mock(monkeypatch, calls)
    result = runner.invoke(app, ["pack", "diff", "/import/demo-next"])
    assert result.exit_code == 0, result.output
    assert "active 0.1.0 -> demo@0.2.0" in result.output
    assert "removed concept Islam" in result.output and "texts" in result.output
    assert json.loads(calls[0].content) == {"path": "/import/demo-next", "force": False}


def test_import_and_activate_stop_at_the_plan_without_yes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[httpx.Request] = []
    _mock(monkeypatch, calls)
    refused = runner.invoke(app, ["pack", "import", str(tmp_path)])
    assert refused.exit_code == 1 and "rerun with --yes" in refused.output
    assert "removed concept Islam" in refused.output
    done = runner.invoke(app, ["pack", "import", str(tmp_path), "--yes"])
    assert done.exit_code == 0, done.output
    posted = next(c for c in reversed(calls) if c.method == "POST")
    assert json.loads(posted.content)["confirm"] is True and "imported demo@0.2.0" in done.output
    assert calls[-1].url.path == "/api/knowledge/pack-jobs/3", "the command follows the job"
    refused = runner.invoke(app, ["pack", "activate", "demo", "--version", "0.1.0"])
    assert refused.exit_code == 1 and "rerun with --yes" in refused.output
    done = runner.invoke(app, ["pack", "activate", "demo", "--version", "0.1.0", "--yes"])
    assert done.exit_code == 0 and "activated" in done.output, done.output


def test_pack_remove(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[httpx.Request] = []
    _mock(monkeypatch, calls)
    no = runner.invoke(app, ["pack", "remove", "demo", "--version", "0.1.0"], input="n\n")
    assert no.exit_code == 1 and not calls
    yes = runner.invoke(app, ["pack", "remove", "demo", "--version", "0.1.0", "--yes"])
    assert yes.exit_code == 0, yes.output
    assert calls[0].method == "DELETE"
    assert calls[0].url.path == "/api/knowledge/packs/demo/versions/0.1.0"
    assert "5 documents keep tags" in yes.output


def test_pack_validate_runs_questions_and_checks_the_manifest(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    shutil.copytree(REPO / "packs" / "demo" / "pack", pack)
    ok = runner.invoke(app, ["pack", "validate", str(pack)])
    assert ok.exit_code == 0 and "pass CQ-004" in ok.output, ok.output
    (pack / "README.md").write_text("edited\n")
    stale = runner.invoke(app, ["pack", "validate", str(pack)])
    assert stale.exit_code == 1 and "manifest_mismatch" in stale.output
    assert runner.invoke(app, ["pack", "validate", str(pack), "--force"]).exit_code == 0
    assert runner.invoke(app, ["pack", "validate", str(pack), "--manifest"]).exit_code == 0
    assert runner.invoke(app, ["pack", "validate", str(pack)]).exit_code == 0
    # a failing competency question fails validation
    manifest = pack / "queries" / "manifest.yaml"
    manifest.write_text(manifest.read_text().replace("value: 4", "value: 400"))
    failed = runner.invoke(app, ["pack", "validate", str(pack), "--force"])
    assert failed.exit_code == 1 and "fail CQ-003" in failed.output


def test_a_failed_pack_job_fails_the_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/knowledge/packs/import"):
            return httpx.Response(202, json=JOB)
        return httpx.Response(200, json={**JOB, "status": "failed", "error": "bad turtle"})

    monkeypatch.setattr(
        cli,
        "_api",
        lambda: httpx.Client(base_url="http://api/api", transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr("time.sleep", lambda s: None)
    result = runner.invoke(app, ["pack", "import", str(tmp_path), "--yes"])
    assert result.exit_code == 1 and "bad turtle" in result.output


def test_import_list_sends_the_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/knowledge/packs/import-list"):
            sent.append(json.loads(request.content))
            return httpx.Response(202, json=JOB)
        return httpx.Response(200, json={**JOB, "status": "done", "result": PACK})

    monkeypatch.setattr(
        cli,
        "_api",
        lambda: httpx.Client(base_url="http://api/api", transport=httpx.MockTransport(handler)),
    )
    names = tmp_path / "names.txt"
    names.write_text("Apple | Malus\nPear\n", encoding="utf-8")
    result = runner.invoke(app, ["pack", "import-list", str(names), "--id", "fruit"])
    assert result.exit_code == 0, result.output
    assert sent[0]["pack_id"] == "fruit" and sent[0]["text"].startswith("Apple | Malus")
