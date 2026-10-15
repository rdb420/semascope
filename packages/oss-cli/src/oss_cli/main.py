"""`oss`: operator and developer command line.

Phase 0 ships `version`, `init`, `doctor` and `pack validate`. The other
commands listed in the README arrive with the phase that implements them.
"""

from __future__ import annotations

import secrets
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.table import Table

from oss_core import PACK_CONTRACT_VERSION, __version__
from oss_core.config import get_settings
from oss_core.pack import load_pack, write_manifest

app = typer.Typer(no_args_is_help=True, help="Semascope command line.")
pack_app = typer.Typer(help="Knowledge-model packs (ontology-skill bundles).")
app.add_typer(pack_app, name="pack")
console = Console()


@app.command()
def version() -> None:
    """Print the product and pack-contract versions."""

    console.print(f"semascope {__version__} (pack contract {PACK_CONTRACT_VERSION})")


@app.command()
def init(
    env_file: Path = typer.Option(Path(".env"), help="Where to write the environment file"),
    host: str = typer.Option("localhost", help="Host name the reverse proxy answers for"),
    size: str = typer.Option("small", help="small | medium | large"),
    force: bool = typer.Option(False, help="Overwrite an existing file"),
) -> None:
    """Write an environment file with generated secrets."""

    if env_file.exists() and not force:
        raise typer.BadParameter(f"{env_file} exists; pass --force to overwrite")
    heaps = {"small": "1g", "medium": "2g", "large": "4g"}
    if size not in heaps:
        raise typer.BadParameter("size must be small, medium or large")
    lines = [
        f"SEMASCOPE_HOST={host}",
        "SEMASCOPE_HTTPS_PORT=8443",
        f"SEMASCOPE_SIZE={size}",
        f"SEMASCOPE_SECRET_KEY={secrets.token_urlsafe(48)}",
        f"POSTGRES_PASSWORD={secrets.token_urlsafe(24)}",
        f"NEO4J_PASSWORD={secrets.token_urlsafe(24)}",
        f"SOLR_PASSWORD={secrets.token_urlsafe(24)}",
        f"SOLR_HEAP={heaps[size]}",
        f"NEO4J_HEAP={heaps[size]}",
        f"TIKA_HEAP={heaps[size]}",
        "SEMASCOPE_LLM_BASE_URL=",
        "SEMASCOPE_LLM_API_KEY=",
        "SEMASCOPE_LLM_MODEL=",
    ]
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env_file.chmod(0o600)
    console.print(f"wrote {env_file} (secrets generated; keep it out of git)")


@app.command()
def doctor() -> None:
    """Check that every service answers and report versions."""

    settings = get_settings()
    table = Table("service", "endpoint", "status")
    checks = {
        "server": ("http://server:8000/healthz", None),
        "solr": (f"{settings.solr_url}/admin/info/system?wt=json", None),
        "tika": (f"{settings.tika_url}/version", None),
        "neo4j": (settings.neo4j_uri.replace("bolt://", "http://").replace(":7687", ":7474"), None),
    }
    failures = 0
    with httpx.Client(timeout=5.0) as client:
        for name, (url, _) in checks.items():
            try:
                resp = client.get(url)
                status = f"{resp.status_code}"
                if resp.status_code >= 400 and name != "solr":
                    failures += 1
            except httpx.HTTPError as exc:
                status = f"unreachable ({type(exc).__name__})"
                failures += 1
            table.add_row(name, url, status)
    console.print(table)
    if failures:
        raise typer.Exit(code=1)


@pack_app.command("validate")
def pack_validate(
    path: Path = typer.Argument(..., exists=True, file_okay=False),
    manifest: bool = typer.Option(False, help="Also write release-manifest.json"),
) -> None:
    """Validate a pack against the ontology-skill bundle contract."""

    info = load_pack(path)
    table = Table("item", "value")
    table.add_row("id", info.id)
    table.add_row("version", info.version)
    table.add_row("status", info.status)
    for key, value in info.counts.items():
        table.add_row(key, str(value))
    console.print(table)
    for finding in info.findings:
        colour = {"error": "red", "warning": "yellow"}.get(finding.severity, "white")
        console.print(f"[{colour}]{finding.severity}[/{colour}] {finding.code}: {finding.message}")
    if manifest and info.ok:
        console.print(f"wrote {write_manifest(info)}")
    if not info.ok:
        raise typer.Exit(code=1)
    console.print(f"[green]ok[/green] {info.id} {info.version} ({len(info.files)} files)")
