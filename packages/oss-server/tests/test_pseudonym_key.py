"""Graph pseudonyms are keyed by SEMASCOPE_PSEUDONYM_KEY, not the rotatable secret key."""

from __future__ import annotations

import pytest

from oss_core.config import get_settings
from oss_server.catalogue.models import Collection
from oss_server.pipeline import tasks


@pytest.mark.django_db
def test_pseudonyms_use_their_own_key(monkeypatch: pytest.MonkeyPatch) -> None:
    key = "pseudonym-key-for-the-test-0123456789"  # noqa: S105 - a test value
    monkeypatch.setenv("SEMASCOPE_PSEUDONYM_KEY", key)
    col = Collection.objects.create(slug="p", name="p", settings={"pseudonymise": True})
    assert tasks.pseudonymise_secret(col) == key
    assert tasks.stage_settings(col)["pseudonymise_secret"] == key
    assert tasks.pseudonymise_secret(col) != get_settings().semascope_secret_key.get_secret_value()
