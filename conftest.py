from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _source_roots(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Sources in the tests read from pytest's temporary folders (the stack uses /import)."""

    monkeypatch.setenv("SEMASCOPE_SOURCE_ROOTS", str(tmp_path_factory.getbasetemp()))
    # connectors write their work files under the data folder: never the checkout's ./data
    monkeypatch.setenv("SEMASCOPE_DATA_DIR", str(tmp_path_factory.mktemp("data")))
    yield
