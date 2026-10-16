"""Settings from files (`<NAME>_FILE`, Docker secrets) and the store URLs they complete."""

from __future__ import annotations

from pathlib import Path

import pytest

from oss_core.config import Settings, SettingsFileError


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No .env file and none of the settings this module sets from the caller's environment."""

    monkeypatch.chdir(tmp_path)
    for name in (
        "DATABASE_URL",
        "VALKEY_URL",
        "POSTGRES_PASSWORD",
        "VALKEY_PASSWORD",
        "SOLR_PASSWORD",
        "SEMASCOPE_SECRET_KEY",
        "SEMASCOPE_PSEUDONYM_KEY",
        "SEMASCOPE_API_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"{name}_FILE", raising=False)


def secret_file(tmp_path: Path, name: str, content: str) -> str:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_a_secret_is_read_from_its_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # `echo pw > file` leaves a line break, which is not part of the secret
    monkeypatch.setenv("SOLR_PASSWORD_FILE", secret_file(tmp_path, "solr", "Solr-From-File-1\n"))
    monkeypatch.setenv("SOLR_PASSWORD", "change-me")  # the file wins over the variable
    assert Settings().solr_password.get_secret_value() == "Solr-From-File-1"


def test_any_setting_can_come_from_a_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("semascope_host_file", secret_file(tmp_path, "host", "search.example\r\n"))
    assert Settings().semascope_host == "search.example"


def test_an_unreadable_file_names_the_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("NEO4J_PASSWORD_FILE", str(tmp_path / "missing"))
    with pytest.raises(SettingsFileError, match="NEO4J_PASSWORD_FILE: cannot read"):
        Settings()


def test_store_passwords_from_files_complete_the_urls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # what compose passes when the passwords are Docker secrets: URLs with an empty password
    monkeypatch.setenv("DATABASE_URL", "postgresql://semascope:@postgres:5432/semascope")
    monkeypatch.setenv("VALKEY_URL", "redis://:@valkey:6379/0")
    monkeypatch.setenv("POSTGRES_PASSWORD_FILE", secret_file(tmp_path, "pg", "pg pw/1\n"))
    monkeypatch.setenv("VALKEY_PASSWORD_FILE", secret_file(tmp_path, "vk", "Vk-1\n"))
    settings = Settings()
    assert settings.database_url == "postgresql://semascope:pg%20pw%2F1@postgres:5432/semascope"
    assert settings.valkey_url == "redis://:Vk-1@valkey:6379/0"
    assert "POSTGRES_PASSWORD" not in settings.insecure_secrets()
    assert "VALKEY_PASSWORD" not in settings.insecure_secrets()


def test_a_url_with_its_own_password_is_kept() -> None:
    own = Settings.model_validate(
        {"database_url": "postgresql://u:own@db/x", "postgres_password": "other"}
    )
    assert own.database_url == "postgresql://u:own@db/x"


def test_a_placeholder_api_token_is_refused() -> None:
    assert (
        "SEMASCOPE_API_TOKEN"
        in Settings.model_validate({"semascope_api_token": "oss_change-me"}).insecure_secrets()
    )
    assert (
        "SEMASCOPE_API_TOKEN"
        not in Settings.model_validate(
            {"semascope_api_token": "oss_Xq3vLr8TzN1mK0pW5yHs"}
        ).insecure_secrets()
    )
    assert "SEMASCOPE_API_TOKEN" not in Settings.model_validate({}).insecure_secrets()


def test_the_pseudonym_key_falls_back_to_the_secret_key() -> None:
    assert Settings.model_validate({"semascope_secret_key": "sk-1"}).pseudonym_key == "sk-1"
    own = {"semascope_secret_key": "sk-1", "semascope_pseudonym_key": "pk-1"}
    assert Settings.model_validate(own).pseudonym_key == "pk-1"


def test_a_placeholder_pseudonym_key_is_refused() -> None:
    weak = Settings.model_validate({"semascope_pseudonym_key": "change-me"}).insecure_secrets()
    assert "SEMASCOPE_PSEUDONYM_KEY" in weak
    assert "SEMASCOPE_PSEUDONYM_KEY" not in Settings.model_validate({}).insecure_secrets()
