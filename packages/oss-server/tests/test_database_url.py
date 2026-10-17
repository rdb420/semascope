"""The Django database settings decode the password compose or a secret file put in the URL."""

from __future__ import annotations

import oss_server.settings as django_settings
from oss_core.config import Settings


def test_the_django_database_settings_decode_the_password() -> None:
    original = django_settings._settings
    try:
        django_settings._settings = Settings.model_validate(
            {"database_url": "postgresql://semascope:@db/semascope", "postgres_password": "a b/c"}
        )
        assert django_settings._database()["PASSWORD"] == "a b/c"  # noqa: S105 - test value
    finally:
        django_settings._settings = original
