from pathlib import Path

from typer.testing import CliRunner

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
    assert runner.invoke(app, ["init", "--env-file", str(env)]).exit_code != 0


def test_pack_validate_base() -> None:
    result = runner.invoke(app, ["pack", "validate", str(REPO / "packs" / "base")])
    assert result.exit_code == 0, result.stdout
