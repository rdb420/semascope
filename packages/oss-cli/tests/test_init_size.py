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
