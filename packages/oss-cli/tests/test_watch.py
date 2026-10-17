"""`oss watch`: changes under a source's folder start one run of it once they settle."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from oss_cli import watch
from oss_cli.watch import Debouncer, Watched, Watcher, ignored, owners, translate


def test_temporary_names_are_ignored() -> None:
    assert ignored("/import/a/.~lock.report.odt#") and ignored("/x/report.pdf.part")
    assert ignored("/x/notes.txt~") and ignored("/x/.hidden")
    assert not ignored("/x/report.pdf")


def test_paths_are_translated_by_the_longest_prefix() -> None:
    mapping = {"/import": "/srv/in", "/import/mail": "/mnt/mail"}
    assert translate("/import/docs/a", mapping) == Path("/srv/in/docs/a")
    assert translate("/import/mail/box.mbox", mapping) == Path("/mnt/mail/box.mbox")
    assert translate("/importer/x", mapping) == Path("/importer/x")


def test_a_change_belongs_to_the_sources_whose_folder_holds_it(tmp_path: Path) -> None:
    (tmp_path / "docs" / "sub").mkdir(parents=True)
    box = tmp_path / "box.mbox"
    box.write_text("From x")
    watched = [
        Watched(1, "c", "docs", tmp_path / "docs"),
        Watched(2, "c", "all", tmp_path),
        Watched(3, "c", "mail", box),
    ]
    assert owners(tmp_path / "docs" / "sub" / "a.pdf", watched) == [1, 2]
    assert owners(box, watched) == [2, 3]
    assert owners(tmp_path / "other.txt", watched) == [2]


def test_the_debouncer_waits_for_quiet() -> None:
    d = Debouncer(5.0)
    d.touch(1, now=100.0)
    d.touch(1, now=103.0)
    d.touch(2, now=101.0)
    assert d.due(now=105.0) == []
    assert d.due(now=106.5) == [2]
    assert d.due(now=108.0) == [1, 2]
    d.done(2)
    assert d.pending() == 1


class Api:
    """The Semascope API as `oss watch` uses it: sources, and runs (409 while one runs)."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.runs: list[int] = []
        self.busy = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/collections":
            return httpx.Response(200, json=[{"slug": "c"}, {"slug": "other"}])
        if path == "/api/collections/c/sources":
            return httpx.Response(
                200,
                json=[
                    {"id": 7, "name": "docs", "connector": "filesystem", "enabled": True,
                     "settings": {"path": "/import/docs"}},
                    {"id": 8, "name": "site", "connector": "web", "enabled": True,
                     "settings": {"urls": ["https://example.org/"]}},
                    {"id": 9, "name": "off", "connector": "filesystem", "enabled": False,
                     "settings": {"path": "/import/docs"}},
                ],
            )  # fmt: skip
        if path == "/api/collections/other/sources":
            return httpx.Response(200, json=[])
        if path == "/api/sources/7/run":
            if self.busy:
                self.busy -= 1
                return httpx.Response(409, json={"detail": "running"})
            self.runs.append(7)
            return httpx.Response(202, json={"source_id": 7, "task_id": "t"})
        return httpx.Response(404)


@pytest.mark.parametrize("poll", [False, True])
def test_a_burst_of_changes_starts_one_run(tmp_path: Path, poll: bool) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    api = Api(docs)
    client = httpx.Client(base_url="http://api.test/api", transport=httpx.MockTransport(api))
    mapping = {"/import": str(tmp_path)}
    found = watch.sources(client, mapping)
    assert [(w.source_id, w.path) for w in found] == [(7, docs)]
    watcher = Watcher(client, lambda: found, debounce=0.5, poll=poll)
    api.busy = 1  # the first request finds the source running: asked again later

    def scenario() -> bool:
        if not hasattr(scenario, "started"):
            scenario.started = time.monotonic()  # type: ignore[attr-defined]
            time.sleep(0.3)  # the observer is up
            for n in range(20):
                (docs / f"file-{n}.txt").write_text(f"text {n}")
            (docs / "copy.part").write_text("ignored")
        return len(api.runs) >= 1 or time.monotonic() - scenario.started > 15  # type: ignore[attr-defined]

    watcher.run(until=scenario, step=0.1)
    assert api.runs == [7]


def test_the_command_is_registered() -> None:
    from typer.testing import CliRunner

    from oss_cli.main import app

    result = CliRunner().invoke(app, ["watch", "--help"])
    assert result.exit_code == 0 and "--debounce" in result.output


def test_a_bad_map_is_refused() -> None:
    import typer

    with pytest.raises(typer.BadParameter):
        watch._mapping(["/import"])


def test_ignored_paths_do_not_mark_a_source(tmp_path: Path) -> None:
    watcher = Watcher(httpx.Client(), lambda: [], debounce=0.1)
    watcher.watched = [Watched(1, "c", "d", tmp_path)]
    event: Any = type("E", (), {"src_path": str(tmp_path / "x.tmp"), "dest_path": ""})()
    watcher._event(event)
    assert watcher.debounce.pending() == 0
    event.src_path = str(tmp_path / "x.pdf")
    watcher._event(event)
    assert watcher.debounce.pending() == 1
