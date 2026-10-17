"""`oss watch`: run a folder source as soon as its files change, instead of on its schedule.

Watches the folders of the enabled sources that read paths (filesystem, archive, mail,
WARC, PST, table and RDF-file sources) through inotify (watchdog; `--poll` scans instead,
for network shares and Docker Desktop mounts, where change events do not arrive). When a
file under a source's folder is created, changed, moved or deleted, the source is run
through the API once the changes have settled for `--debounce` seconds (5), so a copy of
a thousand files starts one run, not a thousand. A run reads again only what changed
(unchanged files are recognised by size and time) and deletes what is gone, under the
usual rules (docs/adr/0013-deletion-and-retention.md). A source already running is run
again when it finishes. Temporary names (`.part`, `.tmp`, `~`, hidden files) are ignored.

The paths are the sources' own (`/import/...`, as the workers see them): run it where the
import folder is mounted at the same place (the `watch` compose profile does), or give
`--map /import=./data/import` to translate them. The source list is read again every
`--refresh` seconds (300), so new sources are watched without a restart.
"""

from __future__ import annotations

import fnmatch
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import typer
from rich.console import Console

console = Console()

PATH_CONNECTORS = ("filesystem", "archive", "mail", "warc", "pst", "table", "rdf")
IGNORED = (".*", "*~", "*.tmp", "*.part", "*.crdownload", "*.swp", "Thumbs.db")


@dataclass(frozen=True)
class Watched:
    source_id: int
    collection: str
    name: str
    path: Path  # as this process sees it

    @property
    def folder(self) -> Path:
        return self.path if self.path.is_dir() else self.path.parent


def ignored(path: str | Path) -> bool:
    name = Path(path).name
    return any(fnmatch.fnmatch(name, p) for p in IGNORED)


def translate(path: str, mapping: dict[str, str]) -> Path:
    """A source's path as this process sees it, through `--map` prefixes (longest first)."""

    for prefix in sorted(mapping, key=len, reverse=True):
        if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
            return Path(mapping[prefix] + path[len(prefix.rstrip("/")) :])
    return Path(path)


def sources(
    api: httpx.Client,
    mapping: dict[str, str],
    collections: Iterable[str] = (),
    only: Iterable[int] = (),
) -> list[Watched]:
    """The enabled path sources to watch, from the API."""

    wanted_collections, wanted = set(collections), set(only)
    out: list[Watched] = []
    for coll in api.get("/collections").raise_for_status().json():
        slug = str(coll["slug"])
        if wanted_collections and slug not in wanted_collections:
            continue
        for s in api.get(f"/collections/{slug}/sources").raise_for_status().json():
            path = str((s.get("settings") or {}).get("path") or "")
            if (
                s.get("connector") not in PATH_CONNECTORS
                or not s.get("enabled", True)
                or not path
                or (wanted and int(s["id"]) not in wanted)
            ):
                continue
            out.append(
                Watched(int(s["id"]), slug, str(s.get("name") or ""), translate(path, mapping))
            )
    return out


def owners(path: str | Path, watched: Iterable[Watched]) -> list[int]:
    """The sources a changed path belongs to."""

    changed = Path(path)
    return [
        w.source_id
        for w in watched
        if changed == w.path or (w.path.is_dir() and changed.is_relative_to(w.path))
    ]


class Debouncer:
    """Sources with changes, and when each last changed; due once quiet for `delay` s."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self._last: dict[int, float] = {}
        self._lock = threading.Lock()

    def touch(self, source_id: int, now: float | None = None) -> None:
        with self._lock:
            self._last[source_id] = time.monotonic() if now is None else now

    def due(self, now: float | None = None) -> list[int]:
        now = time.monotonic() if now is None else now
        with self._lock:
            return sorted(s for s, t in self._last.items() if now - t >= self.delay)

    def done(self, source_id: int) -> None:
        with self._lock:
            self._last.pop(source_id, None)

    def pending(self) -> int:
        with self._lock:
            return len(self._last)


def start_run(api: httpx.Client, source_id: int) -> bool:
    """Ask for a run; False when the source is running (try again later) or the call failed."""

    try:
        resp = api.post(f"/sources/{source_id}/run")
    except httpx.HTTPError as exc:
        console.print(f"[yellow]source {source_id}: {type(exc).__name__}; trying again[/yellow]")
        return False
    if resp.status_code == 409:
        return False
    if resp.status_code >= 400:
        console.print(f"[red]source {source_id}: {resp.status_code} {resp.text[:200]}[/red]")
        return resp.status_code == 404  # gone: stop asking
    console.print(f"source {source_id}: run started")
    return True


class Watcher:
    """Watches the sources' folders and starts runs through `api` once changes settle."""

    def __init__(
        self,
        api: httpx.Client,
        load: Callable[[], list[Watched]],
        *,
        debounce: float = 5.0,
        poll: bool = False,
        refresh: float = 300.0,
        heartbeat: Path | None = None,
    ) -> None:
        self.api = api
        self.heartbeat = heartbeat
        self.load = load
        self.debounce = Debouncer(debounce)
        self.poll = poll
        self.refresh = refresh
        self.watched: list[Watched] = []
        self._observer: Any = None

    def _event(self, event: Any) -> None:
        for raw in (getattr(event, "src_path", None), getattr(event, "dest_path", None)):
            if not raw or ignored(raw):
                continue
            for source_id in owners(raw, self.watched):
                self.debounce.touch(source_id)

    def _schedule(self) -> None:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
        from watchdog.observers.polling import PollingObserver

        handler = FileSystemEventHandler()
        handler.on_any_event = self._event  # type: ignore[method-assign]
        observer = PollingObserver() if self.poll else Observer()
        folders = sorted({w.folder for w in self.watched if w.folder.is_dir()})
        for folder in folders:
            observer.schedule(handler, str(folder), recursive=True)
        observer.start()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
        self._observer = observer
        for w in self.watched:
            state = "" if w.folder.is_dir() else " [yellow](not found here)[/yellow]"
            console.print(
                f"watching source {w.source_id} ({w.collection}/{w.name}): {w.path}{state}"
            )

    def reload(self) -> None:
        found = self.load()
        if found != self.watched or self._observer is None:
            self.watched = found
            self._schedule()

    def tick(self) -> None:
        for source_id in self.debounce.due():
            if start_run(self.api, source_id):
                self.debounce.done(source_id)
            else:
                self.debounce.touch(source_id)  # running now: again once it may have finished

    def run(self, *, until: Callable[[], bool] = lambda: False, step: float = 0.5) -> None:
        self.reload()
        next_load = time.monotonic() + self.refresh
        try:
            while not until():
                time.sleep(step)
                self.tick()
                if self.heartbeat is not None:
                    self.heartbeat.touch()  # the compose health check reads its age
                if time.monotonic() >= next_load:
                    self.reload()
                    next_load = time.monotonic() + self.refresh
        finally:
            if self._observer is not None:
                self._observer.stop()
                self._observer.join(timeout=5)


def _mapping(values: list[str]) -> dict[str, str]:
    out = {}
    for value in values:
        source, sep, local = value.partition("=")
        if not sep or not source or not local:
            raise typer.BadParameter(f"--map takes SOURCE_PATH=LOCAL_PATH, not {value!r}")
        out[source] = local
    return out


def command(
    collection: list[str] = typer.Option([], help="Only sources of this collection (repeatable)"),
    source: list[int] = typer.Option([], help="Only this source id (repeatable)"),
    debounce: float = typer.Option(5.0, help="Seconds of quiet before a source runs"),
    poll: bool = typer.Option(False, help="Scan for changes instead of inotify events"),
    refresh: float = typer.Option(300.0, help="Seconds between re-reading the source list"),
    map_: list[str] = typer.Option(
        [], "--map", help="Translate a source path prefix: /import=./data/import"
    ),
    heartbeat: Path | None = typer.Option(
        None, help="Touch this file on every loop (the compose health check reads its age)"
    ),
) -> None:
    """Run folder sources as soon as their files change (Ctrl-C stops)."""

    from oss_cli.main import _api

    mapping = _mapping(map_)
    with _api() as api:
        watcher = Watcher(
            api,
            lambda: sources(api, mapping, collection, source),
            debounce=debounce,
            poll=poll,
            refresh=refresh,
            heartbeat=heartbeat,
        )
        try:
            watcher.run()
        except KeyboardInterrupt:
            console.print("stopped")
