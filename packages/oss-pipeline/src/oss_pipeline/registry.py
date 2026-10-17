"""Discover connectors and stages through entry points, with the built-ins always present.

`profile()` hands out the stage instances of the running process: they are built
once per process (again after a fork), so a stage's clients (Solr, Neo4j, the NER
service) and their connections live as long as the worker process instead of one
document, and entry points are not rescanned for every task. `stages()` always
builds new instances.
"""

from __future__ import annotations

import atexit
import logging
import os
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
        "embed",
        "index",
        "graph",
    ],
    "full": [
        "fetch",
        "extract",
        "structure",
        "patterns",
        "tag",
        "ner",
        "link",
        "validate",
        "preview",
        "embed",
        "index",
        "graph",
    ],
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
    from oss_pipeline.connectors.archive import ArchiveConnector
    from oss_pipeline.connectors.feed import FeedConnector
    from oss_pipeline.connectors.filesystem import FilesystemConnector
    from oss_pipeline.connectors.hypothesis import HypothesisConnector
    from oss_pipeline.connectors.mail import MailConnector
    from oss_pipeline.connectors.mastodon import MastodonConnector
    from oss_pipeline.connectors.pst import PstConnector
    from oss_pipeline.connectors.rdf import RdfConnector
    from oss_pipeline.connectors.s3 import S3Connector
    from oss_pipeline.connectors.table import TableConnector
    from oss_pipeline.connectors.upload import UploadConnector
    from oss_pipeline.connectors.warc import WarcConnector
    from oss_pipeline.connectors.web import WebConnector

    found: dict[str, Any] = {
        "filesystem": FilesystemConnector,
        "web": WebConnector,
        "upload": UploadConnector,
        "feed": FeedConnector,
        "mail": MailConnector,
        "archive": ArchiveConnector,
        "table": TableConnector,
        "s3": S3Connector,
        "rdf": RdfConnector,
        "warc": WarcConnector,
        "hypothesis": HypothesisConnector,
        "pst": PstConnector,
        "mastodon": MastodonConnector,
    }
    found.update(_load("oss.connectors"))
    return {name: cls() for name, cls in found.items()}


def stages() -> dict[str, Stage]:
    from oss_pipeline.stages.embed import EmbedStage
    from oss_pipeline.stages.extract import ExtractStage
    from oss_pipeline.stages.fetch import FetchStage
    from oss_pipeline.stages.graph import GraphStage
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
        "embed": EmbedStage,
        "index": IndexStage,
        "graph": GraphStage,
    }
    found.update(_load("oss.stages"))
    return {name: cls() for name, cls in found.items()}


class UnknownProfileError(ValueError):
    """A collection names a profile that does not exist (a configuration error)."""


_process: dict[str, Any] = {}  # this process's stage instances: {"pid": int, "stages": dict}


def process_stages() -> dict[str, Stage]:
    """The stage instances of this process, built on first use and after a fork."""

    if _process.get("pid") != os.getpid() or "stages" not in _process:
        _process.update(pid=os.getpid(), stages=stages())
    found: dict[str, Stage] = _process["stages"]
    return found


def close_stages() -> None:
    """Close the clients of this process's stages (at exit; tests call it to start afresh)."""

    built: dict[str, Stage] = _process.pop("stages", None) or {}
    _process.pop("pid", None)
    for stage in built.values():
        close = getattr(stage, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:  # noqa: BLE001 - closing is best effort
                log.debug("closing stage %s: %s", getattr(stage, "name", stage), exc)


atexit.register(close_stages)


def profile(name: str, available: dict[str, Stage] | None = None) -> list[Stage]:
    """The stages of a profile, in order. An unknown profile name raises
    `UnknownProfileError`: a typo in a collection's profile must not quietly run another one."""

    if name not in PROFILES:
        raise UnknownProfileError(
            f"unknown profile {name!r}; the profiles are {', '.join(sorted(PROFILES))}"
        )
    available = available or process_stages()
    prof = Profile(name=name, stages=PROFILES[name])
    problems = prof.validate_against(available)
    if problems:
        raise ValueError(f"profile {name}: {problems}")
    return [available[s] for s in prof.stages]
