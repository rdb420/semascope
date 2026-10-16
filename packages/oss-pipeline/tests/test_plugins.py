"""Connector and stage plugins: `oss.connectors` and `oss.stages` entry points join the built-ins.

Each test replaces `entry_points` with a list of its own.
"""

from __future__ import annotations

from collections.abc import Iterable
from importlib.metadata import EntryPoint
from typing import Any

import pytest

from oss_core.contracts import Cursor, FetchedItem, SourceConfig, SourceItem
from oss_pipeline import registry


class EchoConnector:
    """A plugin connector: one item per configured name."""

    name = "echo"

    def discover(self, source: SourceConfig, since: Cursor | None) -> Iterable[SourceItem]:
        for n in source.settings.get("names", []):
            yield SourceItem(uri=f"echo://{n}", content_hash=n, size=len(n))

    def fetch(self, item: SourceItem) -> FetchedItem:
        return FetchedItem(item=item, path="/dev/null")

    def deleted(self, source: SourceConfig, since: Cursor) -> Iterable[str]:
        return []


def _entry_points(groups: dict[str, list[EntryPoint]]) -> Any:
    def entry_points(group: str) -> list[EntryPoint]:
        return groups.get(group, [])

    return entry_points


def test_a_connector_plugin_is_discovered_and_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    ep = EntryPoint("echo", f"{__name__}:EchoConnector", "oss.connectors")
    monkeypatch.setattr(registry, "entry_points", _entry_points({"oss.connectors": [ep]}))
    found = registry.connectors()
    assert isinstance(found["echo"], EchoConnector)
    assert {"filesystem", "web", "s3"} <= set(found)  # the built-ins stay
    source = SourceConfig(id="1", collection="c", connector="echo", settings={"names": ["a", "b"]})
    assert [i.uri for i in found["echo"].discover(source, None)] == ["echo://a", "echo://b"]


def test_a_plugin_can_replace_a_builtin(monkeypatch: pytest.MonkeyPatch) -> None:
    ep = EntryPoint("web", f"{__name__}:EchoConnector", "oss.connectors")
    monkeypatch.setattr(registry, "entry_points", _entry_points({"oss.connectors": [ep]}))
    assert isinstance(registry.connectors()["web"], EchoConnector)


def test_a_broken_plugin_is_skipped_not_fatal(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    broken = EntryPoint("broken", "no_such_module_for_semascope:Thing", "oss.connectors")
    stage = EntryPoint("broken-stage", "no_such_module_for_semascope:Stage", "oss.stages")
    monkeypatch.setattr(
        registry,
        "entry_points",
        _entry_points({"oss.connectors": [broken], "oss.stages": [stage]}),
    )
    assert "broken" not in registry.connectors()
    assert "broken-stage" not in registry.stages() and "tag" in registry.stages()
    assert "failed to load" in caplog.text
