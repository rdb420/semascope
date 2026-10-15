"""Discover connectors and stages through entry points, with the built-ins always present."""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import Any

from oss_core.contracts import Connector, Profile, Stage

log = logging.getLogger(__name__)

PROFILES: dict[str, list[str]] = {
    "fast": ["fetch", "extract", "structure", "tag", "index"],
    "default": [
        "fetch",
        "extract",
        "structure",
        "patterns",
        "tag",
        "ner",
        "link",
        "preview",
        "index",
    ],
    "full": ["fetch", "extract", "structure", "patterns", "tag", "ner", "link", "preview", "index"],
}


def _load(group: str) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for ep in entry_points(group=group):
        try:
            found[ep.name] = ep.load()
        except Exception as exc:  # noqa: BLE001 - a broken plugin must not take the worker down
            log.warning("plugin %s in %s failed to load: %s", ep.name, group, exc)
    return found


def connectors() -> dict[str, Connector]:
    from oss_pipeline.connectors.filesystem import FilesystemConnector
    from oss_pipeline.connectors.upload import UploadConnector
    from oss_pipeline.connectors.web import WebConnector

    found: dict[str, Any] = {
        "filesystem": FilesystemConnector,
        "web": WebConnector,
        "upload": UploadConnector,
    }
    found.update(_load("oss.connectors"))
    return {name: cls() for name, cls in found.items()}


def stages() -> dict[str, Stage]:
    from oss_pipeline.stages.extract import ExtractStage
    from oss_pipeline.stages.fetch import FetchStage
    from oss_pipeline.stages.index import IndexStage
    from oss_pipeline.stages.link import LinkStage
    from oss_pipeline.stages.ner import NerStage
    from oss_pipeline.stages.patterns import PatternsStage
    from oss_pipeline.stages.preview import PreviewStage
    from oss_pipeline.stages.structure import StructureStage
    from oss_pipeline.stages.tag import TagStage
    from oss_pipeline.stages.validate import ValidateStage

    found: dict[str, Any] = {
        "fetch": FetchStage,
        "extract": ExtractStage,
        "structure": StructureStage,
        "patterns": PatternsStage,
        "tag": TagStage,
        "ner": NerStage,
        "link": LinkStage,
        "validate": ValidateStage,
        "preview": PreviewStage,
        "index": IndexStage,
    }
    found.update(_load("oss.stages"))
    return {name: cls() for name, cls in found.items()}


def profile(name: str, available: dict[str, Stage] | None = None) -> list[Stage]:
    available = available or stages()
    prof = Profile(name=name, stages=PROFILES.get(name, PROFILES["default"]))
    problems = prof.validate_against(available)
    if problems:
        raise ValueError(f"profile {name}: {problems}")
    return [available[s] for s in prof.stages]
