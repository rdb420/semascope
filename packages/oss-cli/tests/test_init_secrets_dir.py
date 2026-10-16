"""`oss init --secrets-dir`: one file per secret for compose.secrets.yaml (Docker secrets)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from oss_cli.main import FILE_SECRETS, app
from oss_core.config import Settings

runner = CliRunner()


def test_secrets_go_to_files_and_the_settings_read_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    result = runner.invoke(app, ["init", "--env-file", str(env), "--secrets-dir", "secrets"])
    assert result.exit_code == 0, result.stdout
    folder = tmp_path / "secrets"
    assert folder.stat().st_mode & 0o777 == 0o700
    assert {p.name for p in folder.iterdir()} == {k.lower() for k in FILE_SECRETS}
    assert all(p.stat().st_mode & 0o777 == 0o644 for p in folder.iterdir())
    text = env.read_text()
    for key in FILE_SECRETS:
        assert f"\n{key}=" not in f"\n{text}", key
    assert "\nSEMASCOPE_SECRETS_DIR=secrets\n" in text
    assert "\nGRAFANA_PASSWORD=" in text  # the observability profile reads it from the env file

    # what compose.secrets.yaml gives the containers: <NAME>_FILE for each secret
    monkeypatch.chdir(tmp_path)
    for key in FILE_SECRETS:
        monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv(f"{key}_FILE", str(folder / key.lower()))
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.insecure_secrets() == []
    assert settings.solr_password.get_secret_value() == (folder / "solr_password").read_text()

    again = runner.invoke(
        app, ["init", "--env-file", str(env), "--force", "--secrets-dir", "secrets"]
    )
    assert again.exit_code == 0, again.stdout
    assert (folder / "semascope_pseudonym_key").read_text() == settings.pseudonym_key


def test_existing_secret_files_need_force(tmp_path: Path) -> None:
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "postgres_password").write_text("keep-me")
    env = tmp_path / ".env"
    result = runner.invoke(app, ["init", "--env-file", str(env), "--secrets-dir", "secrets"])
    assert result.exit_code != 0
    assert (tmp_path / "secrets" / "postgres_password").read_text() == "keep-me"
