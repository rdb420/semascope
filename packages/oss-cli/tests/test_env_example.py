""".env.example lists every setting, and the compose variables, with safe values.

It is the proposed content of `.env.example` (agents cannot write `.env*` files; the
operator moves it into place). Each setting appears as `NAME=value`, or commented as
`# NAME=default` when it is optional.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from oss_core.config import Settings

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / ".env.example"
# variables the shim or compose read from the process, never from the env file
NOT_IN_THE_FILE = {"SEMASCOPE_ENV_FILE"}


def listed() -> dict[str, tuple[str, bool]]:
    """NAME -> (value, active) for every `NAME=` line, commented or not."""

    out: dict[str, tuple[str, bool]] = {}
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^(#\s*)?([A-Z][A-Z0-9_]*)=(.*)$", line)
        if match:
            name = match.group(2)
            assert name not in out, f"{name} listed twice"
            out[name] = (match.group(3), match.group(1) is None)
    return out


def test_every_setting_is_listed() -> None:
    names = listed()
    missing = sorted(f.upper() for f in Settings.model_fields if f.upper() not in names)
    assert missing == [], f"add to {EXAMPLE.name}: {missing}"


def test_every_compose_variable_is_listed() -> None:
    names = listed()
    used: set[str] = set()
    for path in REPO.glob("compose*.yaml"):
        # ${NAME} is compose's; $${NAME} is the container shell's, not a setting
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r"(?<!\$)\$\{([A-Z][A-Z0-9_]*)", text))
    missing = sorted(used - set(names) - NOT_IN_THE_FILE)
    assert missing == [], f"add to {EXAMPLE.name}: {missing}"


def test_secrets_are_placeholders_the_server_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copy of the example as .env loads, and every required secret in it is refused."""

    active = {k: v for k, (v, on) in listed().items() if on}
    for name in active:
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text("".join(f"{k}={v}\n" for k, v in active.items()), encoding="utf-8")
    settings = Settings(_env_file=env)  # type: ignore[call-arg]
    assert set(settings.insecure_secrets()) == {
        "SEMASCOPE_SECRET_KEY",
        "POSTGRES_PASSWORD",
        "NEO4J_PASSWORD",
        "SOLR_PASSWORD",
        "VALKEY_PASSWORD",
        "SEMASCOPE_ADMIN_PASSWORD",
        "SEMASCOPE_API_TOKEN",
        "SEMASCOPE_PSEUDONYM_KEY",
    }
    assert settings.semascope_default_role == "none"
    assert settings.semascope_max_attempts == 3 and settings.semascope_stale_after_s == 300


def test_the_listed_defaults_are_the_settings_defaults() -> None:
    names = listed()
    fields = Settings.model_fields
    for name, field in fields.items():
        value, active = names[name.upper()]
        default = field.default
        if active or default in (None, "") or hasattr(default, "get_secret_value"):
            continue
        shown = str(default).lower() if isinstance(default, bool) else str(default)
        if name == "semascope_data_dir":
            shown = "./data"
        assert value == shown, (name, value, shown)
