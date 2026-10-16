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
