from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _source_roots(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Sources in the tests read from pytest's temporary folders (the stack uses /import)."""

    monkeypatch.setenv("SEMASCOPE_SOURCE_ROOTS", str(tmp_path_factory.getbasetemp()))
    yield
