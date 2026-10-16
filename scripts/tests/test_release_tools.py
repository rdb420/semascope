"""Tests for the CI and release tooling: the licence gate, the version check and the
clean-room check's refusal to run without a legacy tree."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(path: str) -> ModuleType:
    file = ROOT / path
    spec = importlib.util.spec_from_file_location(file.stem, file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[file.stem] = module
    spec.loader.exec_module(module)
    return module


gate = load(".github/scripts/licence_gate.py")
versions = load(".github/scripts/check_version.py")


@pytest.fixture(scope="module")
def policy() -> object:
    return gate.Policy.load(ROOT / ".github" / "licence-policy.toml")


@pytest.mark.parametrize(
    "licence",
    [
        "MIT",
        "Apache-2.0 OR BSD-3-Clause",
        "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0",
        "(MIT OR Apache-2.0)",
        "MIT OR GPL-3.0-only",  # a permissive alternative is enough
        "Apache-2.0 WITH LLVM-exception",
        "MIT License",  # an alias
    ],
)
def test_permissive_licences_pass(policy: object, licence: str) -> None:
    assert gate.judge("anything", licence, policy).ok


@pytest.mark.parametrize(
    "licence",
    [
        "GPL-3.0-only",
        "GPL-2.0-or-later",
        "AGPL-3.0-or-later",
        "MIT AND GPL-2.0-only",  # bundled GPL code taints the package
        "LGPL-3.0-only",  # no exception for this package
        "Proprietary",
        "SEE LICENSE IN LICENSE.txt",
        "",
    ],
)
def test_copyleft_and_unknown_licences_fail(policy: object, licence: str) -> None:
    assert not gate.judge("some-package", licence, policy).ok


def test_lgpl_passes_only_for_the_package_an_adr_names(policy: object) -> None:
    verdict = gate.judge("psycopg", "LGPL-3.0-only", policy)
    assert verdict.ok
    assert "ADR-0011" in verdict.notes[0]
    assert gate.judge("psycopg_binary", "LGPL-3.0-only", policy).ok  # name normalised
    # the exception is for that licence only: a change of licence is reviewed again
    assert not gate.judge("psycopg", "LGPL-2.1-or-later", policy).ok
    assert not gate.judge("psycopg", "GPL-3.0-only", policy).ok


def test_policy_refuses_gpl_exceptions(tmp_path: Path) -> None:
    bad = tmp_path / "policy.toml"
    bad.write_text(
        'allowed = ["MIT"]\n[[exception]]\npackage = "x"\nlicence = "GPL-3.0-only"\nadr = "ADR-1"\n'
    )
    with pytest.raises(SystemExit, match="only LGPL"):
        gate.Policy.load(bad)
    bad.write_text('allowed = []\n[[exception]]\npackage = "x"\nlicence = "LGPL-3.0-only"\n')
    with pytest.raises(SystemExit, match="names no ADR"):
        gate.Policy.load(bad)


def test_pnpm_report(policy: object, tmp_path: Path) -> None:
    report = tmp_path / "licences.json"
    report.write_text(
        json.dumps(
            {
                "MIT": [{"name": "react", "versions": ["19.2.8"]}],
                "(MIT OR Apache-2.0)": [{"name": "@maplibre/mlt", "versions": ["1.0.0"]}],
                "GPL-3.0": [{"name": "evil", "versions": ["1.0.0"]}],
            }
        )
    )
    failures, passed, seen = gate.check_pnpm(report, policy)
    assert len(passed) == 2
    assert [f.split(":")[0] for f in failures] == ["evil@1.0.0"]
    assert "react" in seen


def test_shipped_dependencies_meet_the_policy(policy: object) -> None:
    """The gate itself, over this checkout's runtime set (needs uv and the synced env)."""

    if shutil.which("uv") is None:
        pytest.skip("uv is not on PATH")
    failures, passed, _ = gate.check_python(policy)
    assert failures == []
    assert any(p.startswith("psycopg==") for p in passed)


@pytest.mark.parametrize(
    ("pep440", "semver"),
    [("1.0.0rc3", "1.0.0-rc.3"), ("1.2.0", "1.2.0"), ("2.0.0a1", "2.0.0-alpha.1")],
)
def test_semver_form(pep440: str, semver: str) -> None:
    assert versions.semver(pep440) == semver


def test_versions_agree_in_this_checkout() -> None:
    assert versions.check(ROOT, None) == []
    version = versions.found(ROOT)["packages/oss-core/pyproject.toml"]
    assert versions.check(ROOT, f"v{version}") == []
    assert versions.check(ROOT, f"refs/tags/v{version}") == []
    assert versions.check(ROOT, "v0.0.1") != []


def test_a_stale_compose_default_is_caught(tmp_path: Path) -> None:
    for rel in ["packages/oss-core/pyproject.toml", "packages/oss-web/package.json"]:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "packages/oss-core/pyproject.toml").write_text(
        '[project]\nname = "oss-core"\nversion = "1.0.0rc3"\n'
    )
    (tmp_path / "packages/oss-core/src/oss_core").mkdir(parents=True)
    (tmp_path / "packages/oss-core/src/oss_core/__init__.py").write_text(
        '__version__ = "1.0.0rc3"\n'
    )
    (tmp_path / "packages/oss-web/package.json").write_text('{"version": "1.0.0-rc.3"}')
    (tmp_path / "compose.yaml").write_text("image: x:${SEMASCOPE_IMAGE_TAG:-main}\n")
    (tmp_path / "compose.gpu.yaml").write_text("image: x:${SEMASCOPE_IMAGE_TAG:-1.0.0-rc.3}\n")
    problems = versions.check(tmp_path, None)
    assert problems == ["compose.yaml (image tag default 1): main (expected 1.0.0-rc.3)"]


def test_cleanroom_check_without_a_legacy_tree_fails() -> None:
    run = subprocess.run(  # noqa: S603 - this interpreter running a repository script
        [sys.executable, str(ROOT / "scripts" / "cleanroom_check.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 2
    assert "no --legacy path" in run.stderr
