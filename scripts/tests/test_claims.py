"""Claims check: every capability the matrix calls implemented names evidence that exists.

docs/assess/capabilities.md holds the states; docs/assess/evidence.yaml maps each
row ("<section> | <first cell>") to the tests, scripts or recorded checks that
prove it. This fails when an implemented row has no evidence, when an evidence
key names no row, or when an item names a test or file that does not exist.

Unit-test ids are checked against what pytest collects. Integration tests need a
live stack to collect (their conftest exits without one), so their ids are checked
by parsing the test file for the function instead.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from functools import cache
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs" / "assess" / "capabilities.md"
EVIDENCE = ROOT / "docs" / "assess" / "evidence.yaml"
PARSED_ONLY = ("tests/integration/", "tests/e2e/")  # not collectable without a stack


def matrix_rows(text: str) -> dict[str, str]:
    """Row key -> state, for every capability table under a `## ` section."""

    rows: dict[str, str] = {}
    section = None
    header = True
    for line in text.splitlines():
        if line.startswith("## "):
            section, header = line[3:].strip(), True
            continue
        if section is None or not line.startswith("|"):
            header = True
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header:  # the header row; the separator row follows
            header = False
            continue
        if set(cells[0]) <= set("-: "):
            continue
        key = f"{section} | {cells[0]}"
        assert key not in rows, f"two rows named {key!r}"
        rows[key] = cells[1]
    return rows


def evidence() -> dict[str, list[str]]:
    data = yaml.safe_load(EVIDENCE.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "evidence.yaml must map row keys to lists"
    return {str(k): [str(i) for i in (v or [])] for k, v in data.items()}


def implemented(state: str) -> bool:
    return "implemented" in state.lower()


def defined_in(path: Path, name: str) -> bool:
    """Whether `name` (a function, or Class::method) is defined at the top of a Python file."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    scope: list[ast.stmt] = tree.body
    *classes, func = name.split("::")
    for cls in classes:
        found = [n for n in scope if isinstance(n, ast.ClassDef) and n.name == cls]
        if not found:
            return False
        scope = found[0].body
    return any(
        isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == func for n in scope
    )


@cache
def collected(files: tuple[str, ...]) -> frozenset[str]:
    """The node ids pytest collects from these files, without parametrisation."""

    proc = subprocess.run(  # noqa: S603 - this interpreter running pytest on repository files
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]
        + ["-o", "addopts=", *files],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [line.strip() for line in proc.stdout.splitlines() if "::" in line]
    ids = {re.sub(r"\[.*\]$", "", line) for line in lines}
    if not ids:
        pytest.fail(f"pytest collected nothing from {files}:\n{proc.stdout}\n{proc.stderr}")
    return frozenset(ids)


def problems(rows: dict[str, str], claims: dict[str, list[str]]) -> list[str]:
    out: list[str] = []
    for key, state in rows.items():
        if implemented(state) and not claims.get(key):
            out.append(f"implemented without evidence: {key!r}")
    for key in claims:
        if key not in rows:
            out.append(f"evidence for a row the matrix does not have: {key!r}")
    items = [item for items in claims.values() for item in items]
    unit = sorted({i.split("::")[0] for i in items if "::" in i and not i.startswith(PARSED_ONLY)})
    ids = collected(tuple(unit)) if unit else frozenset()
    for key, items in claims.items():
        for item in items:
            if item.startswith("check:"):
                if not item.removeprefix("check:").strip():
                    out.append(f"{key!r}: an empty check")
                continue
            path, _, name = item.partition("::")
            file = ROOT / path
            if not file.is_file():
                out.append(f"{key!r}: no file {path}")
            elif name and path.startswith(PARSED_ONLY):
                if not defined_in(file, name):
                    out.append(f"{key!r}: {path} defines no {name}")
            elif name and item not in ids:
                out.append(f"{key!r}: pytest collects no {item}")
    return out


def test_every_implemented_row_has_evidence_that_exists() -> None:
    rows = matrix_rows(MATRIX.read_text(encoding="utf-8"))
    assert len(rows) > 50, f"the matrix parsed into only {len(rows)} rows"
    found = problems(rows, evidence())
    assert not found, "capability claims without evidence:\n" + "\n".join(found)


def test_the_check_catches_missing_and_stale_evidence(tmp_path: Path) -> None:
    rows = matrix_rows(
        "# M\n\n| State | Meaning |\n|---|---|\n| implemented | x |\n\n"
        "## Inputs\n\n| Capability | State | Tests |\n|---|---|---|\n"
        "| Folder | implemented | ci |\n| Docling | planned | — |\n"
        "| Feeds | experimental | — |\n| Suggest | implemented (API) | ci |\n"
    )
    assert rows == {
        "Inputs | Folder": "implemented",
        "Inputs | Docling": "planned",
        "Inputs | Feeds": "experimental",
        "Inputs | Suggest": "implemented (API)",
    }
    claims = {
        "Inputs | Folder": [
            "packages/oss-pipeline/tests/test_filesystem.py::test_discover_respects_globs_and_hashes",
            "tests/integration/test_api.py::test_keyword_search",
            "tests/integration/test_api.py::test_no_such_test",
            "packages/oss-pipeline/tests/test_filesystem.py::test_no_such_test",
            "scripts/no_such_script.py",
            "check: ",
        ],
        "Inputs | Gone": ["scripts/bench.py"],
    }
    found = problems(rows, claims)
    assert sorted(found) == sorted(
        [
            "implemented without evidence: 'Inputs | Suggest'",
            "evidence for a row the matrix does not have: 'Inputs | Gone'",
            "'Inputs | Folder': tests/integration/test_api.py defines no test_no_such_test",
            "'Inputs | Folder': pytest collects no "
            "packages/oss-pipeline/tests/test_filesystem.py::test_no_such_test",
            "'Inputs | Folder': no file scripts/no_such_script.py",
            "'Inputs | Folder': an empty check",
        ]
    ), found
