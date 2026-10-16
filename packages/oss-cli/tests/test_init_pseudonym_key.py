"""`oss init` writes a pseudonymisation key of its own, and `--force` never changes it."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from oss_cli.main import app

runner = CliRunner()
OLD_KEY = "the-old-secret-key-0123456789"  # noqa: S105 - a test value


def values(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.startswith("#"):
            out[key] = value
    return out


def test_init_writes_a_pseudonym_key_apart_from_the_secret_key(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    assert runner.invoke(app, ["init", "--env-file", str(env)]).exit_code == 0
    first = values(env)
    assert len(first["SEMASCOPE_PSEUDONYM_KEY"]) >= 48
    assert first["SEMASCOPE_PSEUDONYM_KEY"] != first["SEMASCOPE_SECRET_KEY"]
    assert runner.invoke(app, ["init", "--env-file", str(env), "--force"]).exit_code == 0
    second = values(env)
    assert second["SEMASCOPE_SECRET_KEY"] != first["SEMASCOPE_SECRET_KEY"]
    assert second["SEMASCOPE_PSEUDONYM_KEY"] == first["SEMASCOPE_PSEUDONYM_KEY"]


def test_force_on_an_older_file_keeps_the_pseudonyms(tmp_path: Path) -> None:
    # before rc.4 the graph pseudonyms were keyed by the secret key
    env = tmp_path / ".env"
    env.write_text(f"SEMASCOPE_SECRET_KEY={OLD_KEY}\nSEMASCOPE_HOST=h\n")
    assert runner.invoke(app, ["init", "--env-file", str(env), "--force"]).exit_code == 0
    new = values(env)
    assert new["SEMASCOPE_PSEUDONYM_KEY"] == OLD_KEY
    assert new["SEMASCOPE_SECRET_KEY"] != OLD_KEY
    assert new["SEMASCOPE_HOST"] == "h"
