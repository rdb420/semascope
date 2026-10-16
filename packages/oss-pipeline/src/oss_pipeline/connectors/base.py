"""What every built-in connector shares: its run report, its deletion policy and its work area.

Run report. A full source run deletes the documents it did not see
(`oss_server.pipeline.tasks.discover_source`), so a connector must never pass over
something it could not read as if it were gone. Each run starts a `RunReport`: what could
not be read (a page that answered 503, a folder it may not list, a corrupt archive) goes in
`unchecked`, and a run that stopped at a limit (`max_pages`, a SPARQL `limit`) sets
`truncated`. Either one means nothing is deleted by absence after that run. A failure that
leaves nothing to report (the seed of a crawl, every feed of a source, a bucket listing)
raises instead, and the run fails.

Deletion policy. `deletes_missing` says whether items a full run no longer yields are
deleted at all: true for sources that list what exists (folders, buckets, archives,
mailboxes, tables, RDF), false for feeds, whose entries roll off while the articles still
exist. A source setting `delete_missing` (true or false) overrides it
(docs/adr/0013-deletion-and-retention.md).

Work area. Connectors that write what they discover for the document task to read
(a downloaded page, an archive member, a mail message) write it under
`<SEMASCOPE_DATA_DIR>/work/<collection>/<source id>/`, on the data volume every worker
shares, never in a container's own `/tmp`. The document task removes the file once the
original is in the blob store; a source's work area goes with the source.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oss_core.contracts import SourceConfig

_SAFE = re.compile(r"[^A-Za-z0-9_-]")


@dataclass
class RunReport:
    """What one discovery run could not read, and whether it stopped at a limit."""

    unchecked: list[str] = field(default_factory=list)
    truncated: bool = False

    def skip(self, what: str, reason: object) -> None:
        if len(self.unchecked) < 1000:
            self.unchecked.append(f"{what}: {reason}"[:500])

    @property
    def complete(self) -> bool:
        return not self.unchecked and not self.truncated


def report_of(connector: Any) -> RunReport | None:
    """The report of a connector's last run, or None for a connector without one (a plugin)."""

    report = getattr(connector, "report", None)
    return report if isinstance(report, RunReport) else None


def deletes_missing(connector: Any, settings: dict[str, Any]) -> bool:
    """Whether a full run deletes what it no longer yields: the source setting, else the
    connector's policy (plugins without one: yes, as before)."""

    explicit = settings.get("delete_missing")
    if isinstance(explicit, bool):
        return explicit
    return bool(getattr(connector, "deletes_missing", True))


def _data_dir() -> Path:
    from oss_core.config import get_settings

    return Path(get_settings().semascope_data_dir)


def work_root() -> Path:
    """`<data>/work`: every source's work area lies under it."""

    return (_data_dir() / "work").resolve()


def source_work_dir(collection: str, source_id: str) -> Path:
    return work_root() / (_SAFE.sub("_", collection) or "_") / (_SAFE.sub("_", source_id) or "_")


def work_dir(source: SourceConfig, kind: str) -> Path:
    """The folder a source's connector writes `kind` files to (created)."""

    path = source_work_dir(source.collection, source.id) / _SAFE.sub("_", kind)
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_work_file(path: str | Path) -> bool:
    try:
        resolved = Path(path).resolve()
    except OSError:
        return False
    return resolved.is_relative_to(work_root())


def remove_work_file(path: str | Path | None) -> bool:
    """Remove a file of the work area (anything else is left alone); True when removed."""

    if not path or not is_work_file(path):
        return False
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return False
    return True


def remove_source_work(collection: str, source_id: str) -> None:
    shutil.rmtree(source_work_dir(collection, source_id), ignore_errors=True)


def remove_collection_work(collection: str) -> None:
    shutil.rmtree(work_root() / (_SAFE.sub("_", collection) or "_"), ignore_errors=True)


# --- path fields ------------------------------------------------------------------------------

MAX_FOLDER_DEPTH = 32
_EXTENSION = re.compile(r"[a-z0-9]{1,10}")


def path_fields(folders: list[str], filename: str = "") -> dict[str, Any]:
    """The path fields of an item: its folder as a hierarchy, and its file extension.

    `folders` runs from the top (a source folder's name, a host, a bucket) down to the folder
    holding the item. They become the custom fields `x_folder_paths` (one value, "a/b/c",
    which the index stage expands to `a`, `a/b`, `a/b/c` in the hierarchical `folder_paths`
    field) and `x_extension_s` (`pdf`), for a folder facet and a file-type filter.
    """

    parts = [p.replace("/", "_").strip()[:200] for p in folders]
    parts = [p for p in parts if p and p not in (".", "..")][:MAX_FOLDER_DEPTH]
    out: dict[str, Any] = {}
    if parts:
        out["x_folder_paths"] = ["/".join(parts)]
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename.strip(".") else ""
    if suffix and _EXTENSION.fullmatch(suffix):
        out["x_extension_s"] = suffix
    return out


# --- walking folders --------------------------------------------------------------------------


def walk_files(
    root: Path, report: RunReport, *, follow_symlinks: bool = False, skip_dir: Any = None
) -> list[Path]:
    """Every file under `root`, sorted; a folder that cannot be listed goes in the report
    (a silent skip would delete everything under it)."""

    if root.is_file():
        return [root]
    out: list[Path] = []

    def failed(exc: OSError) -> None:
        report.skip(str(getattr(exc, "filename", "") or root), f"{type(exc).__name__}: {exc}")

    for dirpath, dirnames, filenames in os.walk(root, onerror=failed, followlinks=follow_symlinks):
        dirnames[:] = sorted(d for d in dirnames if not (skip_dir and skip_dir(d)))
        out.extend(Path(dirpath) / f for f in filenames)
    return sorted(out)
