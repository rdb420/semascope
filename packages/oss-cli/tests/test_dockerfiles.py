"""The Dockerfiles: BuildKit sees the syntax line, and a failed bake fails the build."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCKER = Path(__file__).resolve().parents[3] / "deploy" / "docker"
FILES = ["Dockerfile", "Dockerfile.gpu"]


@pytest.mark.parametrize("name", FILES)
def test_the_syntax_line_comes_first(name: str) -> None:
    first = (DOCKER / name).read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("# syntax=docker/dockerfile:")


@pytest.mark.parametrize("name", FILES)
def test_no_step_swallows_its_failure(name: str) -> None:
    text = (DOCKER / name).read_text(encoding="utf-8").replace("\\\n", " ")
    runs = [line for line in text.splitlines() if line.startswith("RUN ")]
    assert runs
    for run in runs:
        assert not re.search(r"\|\|\s*(echo|true|:)", run), run


def test_the_gpu_image_names_its_base_explicitly() -> None:
    text = (DOCKER / "Dockerfile.gpu").read_text(encoding="utf-8")
    assert re.search(r"^ARG BASE_IMAGE$", text, re.M)
    assert ":main" not in text
