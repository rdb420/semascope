from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from oss_cli.main import app

REPO = Path(__file__).resolve().parents[3]
runner = CliRunner()


@pytest.mark.parametrize(
    ("size", "workers", "memory", "heap"),
    [("small", "1", "4g", "1g"), ("medium", "2", "6g", "2g"), ("large", "4", "12g", "4g")],
)
def test_init_size_sets_heaps_and_workers(
    tmp_path: Path, size: str, workers: str, memory: str, heap: str
) -> None:
    env = tmp_path / ".env"
    result = runner.invoke(app, ["init", "--env-file", str(env), "--size", size])
    assert result.exit_code == 0, result.stdout
    values = dict(line.split("=", 1) for line in env.read_text().splitlines())
    assert values["SEMASCOPE_WORKER_CONCURRENCY"] == workers
    assert values["SEMASCOPE_WORKER_MEMORY"] == memory
    assert values["SOLR_HEAP"] == values["NEO4J_HEAP"] == values["TIKA_HEAP"] == heap


def test_compose_worker_reads_the_size_settings() -> None:
    worker = yaml.safe_load((REPO / "compose.yaml").read_text())["services"]["worker"]
    assert "--concurrency ${SEMASCOPE_WORKER_CONCURRENCY:-2}" in worker["command"]
    assert worker["deploy"]["resources"]["limits"]["memory"] == "${SEMASCOPE_WORKER_MEMORY:-4g}"
    example = (REPO / ".env.example").read_text()
    assert "SEMASCOPE_WORKER_CONCURRENCY=1" in example


def _values(env: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in env.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )


def test_init_force_rotates_secrets_and_keeps_every_other_setting(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    first = runner.invoke(
        app, ["init", "--env-file", str(env), "--size", "large", "--host", "search.example.org"]
    )
    assert first.exit_code == 0, first.stdout
    edited = env.read_text().replace("SEMASCOPE_DEFAULT_ROLE=none", "SEMASCOPE_DEFAULT_ROLE=reader")
    edited += "SEMASCOPE_OIDC_CLIENT_ID=semascope\nSEMASCOPE_ALLOWED_PRIVATE_HOSTS=minio.lan\n"
    edited += "SEMASCOPE_LLM_MODEL=local-model\n"
    edited = edited.replace("SEMASCOPE_LLM_MODEL=\n", "")
    env.write_text(edited)
    before = _values(env)

    result = runner.invoke(app, ["init", "--env-file", str(env), "--force"])
    assert result.exit_code == 0, result.stdout
    after = _values(env)
    for key in (
        "SEMASCOPE_SECRET_KEY",
        "POSTGRES_PASSWORD",
        "NEO4J_PASSWORD",
        "SOLR_PASSWORD",
        "VALKEY_PASSWORD",
        "SEMASCOPE_ADMIN_PASSWORD",
        "SEMASCOPE_API_TOKEN",
        "GRAFANA_PASSWORD",
    ):
        assert after[key] and after[key] != before[key], key
    unchanged = {k: v for k, v in before.items() if k in after and after[k] == v}
    assert set(before) == set(after)
    assert set(before) - set(unchanged) == {
        "SEMASCOPE_SECRET_KEY",
        "POSTGRES_PASSWORD",
        "NEO4J_PASSWORD",
        "SOLR_PASSWORD",
        "VALKEY_PASSWORD",
        "SEMASCOPE_ADMIN_PASSWORD",
        "SEMASCOPE_API_TOKEN",
        "GRAFANA_PASSWORD",
    }
    assert after["SEMASCOPE_DEFAULT_ROLE"] == "reader"
    assert after["SEMASCOPE_SIZE"] == "large" and after["SOLR_HEAP"] == "4g"
    assert after["SEMASCOPE_HOST"] == "search.example.org"
    assert after["SEMASCOPE_OIDC_CLIENT_ID"] == "semascope"
    assert after["SEMASCOPE_ALLOWED_PRIVATE_HOSTS"] == "minio.lan"
    assert after["SEMASCOPE_LLM_MODEL"] == "local-model"

    # an option given with --force changes its own lines, nothing else
    resized = runner.invoke(app, ["init", "--env-file", str(env), "--force", "--size", "small"])
    assert resized.exit_code == 0, resized.stdout
    small = _values(env)
    assert small["SEMASCOPE_SIZE"] == "small" and small["SOLR_HEAP"] == "1g"
    assert small["SEMASCOPE_HOST"] == "search.example.org"
    assert small["SEMASCOPE_DEFAULT_ROLE"] == "reader"
