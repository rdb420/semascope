"""Helpers the benchmark scripts share: compose access, the API, tables and the hardware block.

The scripts run on the Docker host, next to a compose project of Semascope, and reach the
stores through `docker compose exec` (no published ports needed) and the API through Caddy.
They refuse to touch the compose project named `semascope` unless `--allow-default-project`
is given, so a benchmark cannot disturb a working installation by accident.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PROTECTED_PROJECTS = ("semascope",)


def pct(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile; 0 for an empty list."""

    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round((p / 100) * (len(ordered) - 1))))
    return float(ordered[k])


def table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    """A Markdown table."""

    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} GB"


def read_env(path: Path | None) -> dict[str, str]:
    """KEY=VALUE lines of an env file; a ` #` starts a comment, as Compose reads them."""

    out: dict[str, str] = {}
    if not path or not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    return out


class Compose:
    """`docker compose` for one project, with the files and env file of the benchmark stack."""

    def __init__(
        self,
        project: str,
        files: Sequence[str] = (),
        env_file: Path | None = None,
        allow_default_project: bool = False,
    ) -> None:
        if project in PROTECTED_PROJECTS and not allow_default_project:
            raise SystemExit(
                f"refusing to benchmark the compose project {project!r}; pass --project "
                "for a separate stack (or --allow-default-project if you mean it)"
            )
        self.project = project
        self.files = list(files)
        self.env_file = env_file
        self.env = read_env(env_file)

    def base(self) -> list[str]:
        cmd = ["docker", "compose", "-p", self.project]
        for f in self.files:
            cmd += ["-f", f]
        if self.env_file:
            cmd += ["--env-file", str(self.env_file)]
        return cmd

    def run(
        self, *args: str, check: bool = True, input: str | None = None, timeout: float = 600
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed docker argv, no shell
            [*self.base(), *args],
            cwd=ROOT,
            check=check,
            capture_output=True,
            text=True,
            input=input,
            timeout=timeout,
        )

    def exec(self, service: str, *cmd: str, env: dict[str, str] | None = None) -> str:
        flags = [f"-e{k}={v}" for k, v in (env or {}).items()]
        return self.run("exec", "-T", *flags, service, *cmd).stdout

    def sh(self, service: str, script: str, env: dict[str, str] | None = None) -> str:
        return self.exec(service, "sh", "-c", script, env=env)

    def psql(self, sql: str) -> list[list[str]]:
        out = self.exec(
            "postgres", "psql", "-U", "semascope", "-d", "semascope", "-At", "-F", "\t", "-c", sql
        )
        return [line.split("\t") for line in out.splitlines() if line]

    def cypher(self, query: str) -> list[list[str]]:
        out = self.sh(
            "neo4j",
            'cypher-shell -u neo4j -p "${NEO4J_AUTH#neo4j/}" --format plain "$Q"',
            env={"Q": query},
        )
        rows = [r for r in out.splitlines()[1:] if r.strip()]  # the first line is the header
        return [[c.strip().strip('"') for c in r.split(", ")] for r in rows]

    def solr(self, path: str) -> dict[str, Any]:
        password = self.env.get("SOLR_PASSWORD", "")
        auth = '-u "semascope:$P" ' if password else ""
        out = self.sh(
            "solr",
            f'curl -s {auth}"http://localhost:8983/solr/$U"',
            env={"U": path, **({"P": password} if password else {})},
        )
        return dict(json.loads(out))

    def du(self, service: str, *paths: str) -> dict[str, int]:
        """Bytes on disk per path (globs allowed); missing paths are left out."""

        out = self.sh(service, "du -sb " + " ".join(paths) + " 2>/dev/null; true")
        sizes = {}
        for line in out.splitlines():
            size, _, path = line.partition("\t")
            if size.isdigit():
                sizes[path] = int(size)
        return sizes

    def container(self, service: str) -> str:
        return self.run("ps", "-q", service).stdout.strip().splitlines()[0]

    def image_of(self, service: str) -> str:
        """The image digest the service runs: the registry digest when known, else the ID."""

        cid = self.container(service)
        image = subprocess.run(  # noqa: S603
            ["docker", "inspect", "--format", "{{.Image}}", cid],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        digests = subprocess.run(  # noqa: S603
            ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        repo = json.loads(digests or "[]")
        return str(repo[0]) if repo else image


def api_client(base_url: str, token: str) -> Any:
    import httpx

    return httpx.Client(
        base_url=base_url.rstrip("/") + "/api",
        headers={"Authorization": f"Bearer {token}"},
        timeout=300,
        verify=False,  # noqa: S501 - Caddy's internal CA on a local stack
    )


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _first(cmd: list[str]) -> str:
    if not shutil.which(cmd[0]):
        return ""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip()


def hardware_block(
    compose: Compose | None = None, corpus_manifests: Sequence[Path] = ()
) -> list[tuple[str, str]]:
    """What the benchmark ran on, as (item, value) rows; see docs/assess/benchmarks.md."""

    cpu = platform.processor() or ""
    if Path("/proc/cpuinfo").exists():
        m = re.search(r"model name\s*:\s*(.+)", Path("/proc/cpuinfo").read_text())
        cpu = m.group(1).strip() if m else cpu
    ram = ""
    if Path("/proc/meminfo").exists():
        m = re.search(r"MemTotal:\s*(\d+) kB", Path("/proc/meminfo").read_text())
        ram = f"{int(m.group(1)) / 1024 / 1024:.0f} GiB" if m else ""
    os_name = platform.platform()
    if Path("/etc/os-release").exists():
        m = re.search(r'PRETTY_NAME="([^"]+)"', Path("/etc/os-release").read_text())
        os_name = f"{m.group(1)}, kernel {platform.release()}" if m else os_name
    docker_root = _first(["docker", "info", "--format", "{{.DockerRootDir}}"]) or "/var/lib/docker"
    disk = _first(["df", "-h", "--output=source,size,fstype", docker_root]).splitlines()[-1:]
    source = disk[0].split()[0] if disk else ""
    model = ""
    if source:
        model = _first(["lsblk", "-sno", "MODEL,TYPE", source]).replace("disk", "").strip()
        model = " ".join(
            dict.fromkeys(x for x in model.split() if x not in ("lvm", "crypt", "part"))
        )
    gpus = [
        line.split(": ", 1)[-1]
        for line in _first(["lspci"]).splitlines()
        if re.search(r"VGA|3D controller|Display", line)
    ]
    nvidia = _first(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
    gpu = "; ".join(gpus + ([f"nvidia-smi: {nvidia}"] if nvidia else []))
    rows = [
        ("CPU", cpu),
        ("Cores (logical)", str(os.cpu_count() or "")),
        ("RAM", ram),
        ("Disk (Docker root)", f"{' '.join(disk[0].split()[1:]) if disk else ''} {model}".strip()),
        ("GPUs on the host", gpu or "none found"),
        ("OS", os_name),
        ("Docker", _first(["docker", "version", "--format", "{{.Server.Version}}"])),
        ("Compose", _first(["docker", "compose", "version", "--short"])),
    ]
    if compose is not None:
        env = compose.env
        rows += [
            ("Compose project", compose.project),
            ("oss init --size", env.get("SEMASCOPE_SIZE", "small (default)")),
            ("Runtime", env.get("SEMASCOPE_RUNTIME", "cpu (default)")),
            ("Worker concurrency", env.get("SEMASCOPE_WORKER_CONCURRENCY", "2 (compose default)")),
            ("Worker memory cap", env.get("SEMASCOPE_WORKER_MEMORY", "4g (compose default)")),
            (
                "Heaps (Solr/Neo4j/Tika)",
                "/".join(env.get(k, "1g") for k in ("SOLR_HEAP", "NEO4J_HEAP", "TIKA_HEAP")),
            ),
        ]
        try:
            rows.append(("Image", compose.image_of("worker")))
        except (subprocess.CalledProcessError, IndexError):
            rows.append(("Image", "unknown (no worker container)"))
    for manifest in corpus_manifests:
        rows.append((f"Corpus manifest {manifest.name}", f"sha256 {file_sha256(manifest)}"))
    return rows


def compose_from_args(args: Any) -> Compose:
    return Compose(
        args.project,
        files=args.compose_file or [],
        env_file=Path(args.env_file) if args.env_file else None,
        allow_default_project=args.allow_default_project,
    )


def add_stack_args(ap: Any) -> None:
    """The options every script takes to find the stack under test."""

    ap.add_argument("--project", default="sem-bench", help="compose project (not 'semascope')")
    ap.add_argument("--compose-file", action="append", help="compose files (repeatable)")
    ap.add_argument("--env-file", help="the stack's env file (secrets, size, runtime)")
    ap.add_argument("--api-url", help="default: https://localhost:$SEMASCOPE_HTTPS_PORT")
    ap.add_argument("--allow-default-project", action="store_true", help=argparse_hidden())


def argparse_hidden() -> str:
    import argparse

    return argparse.SUPPRESS


def api_from_args(args: Any, compose: Compose) -> Any:
    url = args.api_url or f"https://localhost:{compose.env.get('SEMASCOPE_HTTPS_PORT', '8443')}"
    token = compose.env.get("SEMASCOPE_API_TOKEN") or os.environ.get("SEMASCOPE_API_TOKEN", "")
    return api_client(url, token)
