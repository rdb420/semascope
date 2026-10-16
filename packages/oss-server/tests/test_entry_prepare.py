"""The server prepares the stack in a child process, so its master never holds the pack store."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

from oss_server import entry


def test_prepare_runs_in_a_child_process(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append((argv, kw)))
    entry.prepare_in_child()
    ((argv, kwargs),) = calls
    assert argv[0] == sys.executable and "entry.prepare()" in argv[-1]
    assert kwargs["check"] is True


@pytest.mark.django_db
def test_a_failed_solr_bootstrap_stops_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before rc.4 the error was logged and the server came up healthy without collections."""

    import django.core.management

    from oss_server import services

    def unreachable() -> list[str]:
        raise ConnectionError("solr: 401 Unauthorized")

    imported: list[str] = []
    monkeypatch.setattr(django.core.management, "call_command", lambda *a, **kw: None)
    monkeypatch.setattr(services, "bootstrap_solr", unreachable)
    monkeypatch.setattr(services, "import_bundled_packs", lambda: imported.append("x") or [])
    with pytest.raises(SystemExit, match="Solr bootstrap failed: solr: 401 Unauthorized"):
        entry.prepare()
    assert imported == []


def test_beat_health_follows_the_schedule_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    import os
    import time

    schedule = tmp_path / "celerybeat-schedule"
    monkeypatch.setattr(entry, "beat_schedule_path", lambda: schedule)
    assert entry.health("beat") == 1  # not written yet
    schedule.write_bytes(b"")
    assert entry.health("beat") == 0
    old = time.time() - entry.BEAT_STALE_S - 10
    os.utime(schedule, (old, old))
    assert entry.health("beat") == 1
    assert entry.health("nope") == 2


def test_worker_health_pings_this_containers_node(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    from oss_server.celery import app

    asked: list[Any] = []

    def ping(destination: list[str], timeout: float) -> list[dict[str, Any]]:
        asked.append(destination)
        return [{destination[0]: {"ok": "pong"}}] if answer else []

    monkeypatch.setattr(app.control, "ping", ping)
    answer = True
    assert entry.health("worker") == 0
    assert asked == [[f"celery@{socket.gethostname()}"]]
    answer = False
    assert entry.health("worker") == 1
