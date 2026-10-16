"""A worker pool process is replaced before the worker reaches its memory cap."""

from __future__ import annotations

from pathlib import Path

import pytest

from oss_server import entry

GIB = 1024**3


@pytest.mark.parametrize(
    ("cap_gib", "processes", "mib"),
    [(4, 1, 2816), (6, 2, 2048), (12, 4, 2176)],  # oss init --size small, medium, large
)
def test_each_size_recycles_processes_inside_its_cap(
    cap_gib: int, processes: int, mib: int
) -> None:
    per_child = entry.max_memory_per_child_mib(cap_gib * GIB, processes)
    assert per_child == mib
    worst = entry.MAIN_PROCESS_MIB + processes * (per_child + entry.TASK_GROWTH_MIB)
    assert worst <= cap_gib * 1024


def test_a_tight_cap_still_leaves_room_for_the_models() -> None:
    # a .env from before `--size` set the worker memory: two processes in 4 GB
    assert entry.max_memory_per_child_mib(4 * GIB, 2) == entry.MIN_CHILD_MIB
    assert entry.max_memory_per_child_mib(None, 2) is None


def test_the_cap_comes_from_the_environment_without_a_cgroup_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: "max")
    monkeypatch.setenv("SEMASCOPE_WORKER_MEMORY", "6g")
    assert entry.memory_limit_bytes() == 6 * GIB
    monkeypatch.setenv("SEMASCOPE_WORKER_MEMORY", "")
    assert entry.memory_limit_bytes() is None
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: str(3 * GIB))
    assert entry.memory_limit_bytes() == 3 * GIB
