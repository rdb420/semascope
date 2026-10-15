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
    # a remote provider without its URL is refused
    bad = runner.invoke(app, ["init", "--env-file", str(tmp_path / "b.env"), "--runtime", "remote"])
    assert bad.exit_code != 0 and "--embed-url" in bad.output
    gpu = runner.invoke(app, ["init", "--env-file", str(tmp_path / "g.env"), "--runtime", "gpu"])
    assert (
        gpu.exit_code == 0 and "SEMASCOPE_NER_PROVIDER=gliner" in (tmp_path / "g.env").read_text()
    )
