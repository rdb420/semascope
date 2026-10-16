"""The server, worker and beat refuse to start with an empty or placeholder secret.

`oss_server.settings` checks the secrets when it is imported (every Django
process: server, worker, beat, oss-manage) unless debug mode is on, as in
`settings_test`. Each case runs in a fresh interpreter because the settings
module reads the environment once.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from oss_core.config import Settings, is_placeholder

GOOD = {
    "SEMASCOPE_SECRET_KEY": "kq3Lr0Yy8H5mN2vT9wXcB7aQ1eZ4uJ6s",
    "POSTGRES_PASSWORD": "pg-Wv8nQ2rT5yU9iO3p",
    "NEO4J_PASSWORD": "neo-Hj4kL7mN1bV5cX8z",
    "SOLR_PASSWORD": "solr-Qa2Ws3Ed4Rf5Tg6Y",
    "VALKEY_PASSWORD": "valkey-Ug7Yh8Ij9Ok0Pl1A",
    "SEMASCOPE_ADMIN_PASSWORD": "admin-Zx1Cv2Bn3Mq4We5",
}


def start(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    clean = {
        k: v
        for k, v in os.environ.items()
        if k not in GOOD and not k.startswith(("SEMASCOPE_", "DATABASE_", "VALKEY_", "NEO4J_"))
    }
    clean.update(
        DATABASE_URL="sqlite://",
        SEMASCOPE_DATA_DIR=str(tmp_path),
        DJANGO_SETTINGS_MODULE="oss_server.settings",
        **env,
    )
    return subprocess.run(  # noqa: S603 - this interpreter, a fixed script
        [sys.executable, "-c", "import django; django.setup(); print('started')"],
        env=clean,
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )


def test_generated_secrets_start(tmp_path: Path) -> None:
    proc = start(tmp_path, **GOOD)
    assert proc.returncode == 0 and "started" in proc.stdout, proc.stderr[-2000:]


@pytest.mark.parametrize("name", sorted(GOOD))
@pytest.mark.parametrize("value", ["", "change-me"])
def test_a_placeholder_secret_refuses_to_start(tmp_path: Path, name: str, value: str) -> None:
    proc = start(tmp_path, **{**GOOD, name: value})
    assert proc.returncode != 0
    assert "ImproperlyConfigured" in proc.stderr and "refusing to start" in proc.stderr
    assert name in proc.stderr
    assert value == "" or GOOD[name] not in proc.stderr  # never prints the other secrets


def test_the_defaults_name_every_missing_secret(tmp_path: Path) -> None:
    proc = start(tmp_path)
    assert proc.returncode != 0
    for name in GOOD:
        assert name in proc.stderr, proc.stderr[-2000:]


def test_debug_mode_starts_with_placeholders(tmp_path: Path) -> None:
    proc = start(tmp_path, SEMASCOPE_DEBUG="true")
    assert proc.returncode == 0, proc.stderr[-2000:]


def settings(**values: str) -> Settings:
    """Settings from these values only (no environment, no .env file)."""

    return Settings.model_validate(values)


def test_passwords_inside_the_store_urls_count() -> None:
    others = {k.lower(): v for k, v in GOOD.items()}
    del others["postgres_password"], others["valkey_password"]
    weak = settings(
        **others,
        database_url="postgresql://semascope:change-me@postgres:5432/semascope",
        valkey_url="redis://valkey:6379/0",
    )
    assert weak.insecure_secrets() == ["POSTGRES_PASSWORD", "VALKEY_PASSWORD"]
    strong = settings(
        **others,
        database_url="postgresql://semascope:Pg-Strong-1@postgres:5432/semascope",
        valkey_url="redis://:Vk-Strong-1@valkey:6379/0",
    )
    assert strong.insecure_secrets() == []


def test_valkey_password_goes_into_the_broker_url() -> None:
    with_password = {"valkey_url": "redis://valkey:6379/0", "valkey_password": "a b/c"}
    assert settings(**with_password).valkey_url == "redis://:a%20b%2Fc@valkey:6379/0"
    own = {"valkey_url": "redis://:own@valkey:6379/0", "valkey_password": "other"}
    assert settings(**own).valkey_url == "redis://:own@valkey:6379/0"


def test_placeholders() -> None:
    assert all(is_placeholder(v) for v in ("", " ", "change-me", "Change-Me-Please", "secret"))
    assert not is_placeholder(GOOD["SOLR_PASSWORD"])
