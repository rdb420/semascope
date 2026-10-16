"""`oss`: operator and developer command line.

`version`, `init`, `doctor` and `pack validate` need no API token; the other
commands call the server's API with SEMASCOPE_API_TOKEN.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

import httpx
import typer
from rich.console import Console
from rich.table import Table

from oss_cli import evaluate, export
from oss_cli.vectors import vector_report
from oss_core import PACK_CONTRACT_VERSION, __version__
from oss_core.config import get_settings
from oss_core.pack import load_pack, run_competency_questions, write_manifest

app = typer.Typer(no_args_is_help=True, help="Semascope command line.")
pack_app = typer.Typer(help="Knowledge-model packs (ontology-skill bundles).")
app.add_typer(pack_app, name="pack")
app.add_typer(export.app, name="export")  # oss_cli/export.py
app.command("eval")(evaluate.command)  # oss_cli/evaluate.py
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
    import_dir: str = typer.Option("./data/import", help="Host folder the workers may read"),
    https_port: int = typer.Option(8443, help="Host port Caddy listens on"),
    runtime: str = typer.Option(
        "cpu", help="cpu (bundled models) | gpu (local NVIDIA card) | remote (HTTP services)"
    ),
    embed: str | None = typer.Option(None, help="fastembed | openai | sidecar; default by runtime"),
    ner: str | None = typer.Option(None, help="spacy | gliner | remote; default by runtime"),
    extract: str | None = typer.Option(None, help="tika | mineru; default by runtime"),
    embed_url: str = typer.Option("", help="Base URL of the openai or sidecar embedding service"),
    embed_model: str | None = typer.Option(None, help="Embedding model name the service expects"),
    ner_url: str = typer.Option("", help="Base URL of the remote entity service (POST /entities)"),
    extract_url: str = typer.Option("", help="Base URL of the MinerU parse server"),
    remote_token: str = typer.Option("", help="Bearer token sent to every remote service"),
    force: bool = typer.Option(False, help="Overwrite an existing file"),
) -> None:
    """Write an environment file with generated secrets."""

    if env_file.exists() and not force:
        raise typer.BadParameter(f"{env_file} exists; pass --force to overwrite")
    heaps = {"small": "1g", "medium": "2g", "large": "4g"}
    if size not in heaps:
        raise typer.BadParameter("size must be small, medium or large")
    providers = runtime_providers(runtime, embed=embed, ner=ner, extract=extract)
    for stage, url, flag in (
        ("embed", embed_url, "--embed-url"),
        ("ner", ner_url, "--ner-url"),
        ("extract", extract_url, "--extract-url"),
    ):
        if providers[stage] in REMOTE_PROVIDERS and not url:
            raise typer.BadParameter(f"{providers[stage]} {stage} needs {flag}")
    if providers["embed"] == "openai" and not embed_model:
        raise typer.BadParameter("openai embeddings need --embed-model")
    model = embed_model or EMBED_MODELS[providers["embed"]]
    lines = [
        f"SEMASCOPE_HOST={host}",
        f"SEMASCOPE_HTTPS_PORT={https_port}",
        f"SEMASCOPE_SIZE={size}",
        f"SEMASCOPE_SECRET_KEY={secrets.token_urlsafe(48)}",
        f"POSTGRES_PASSWORD={secrets.token_urlsafe(24)}",
        f"NEO4J_PASSWORD={secrets.token_urlsafe(24)}",
        f"SOLR_PASSWORD={secrets.token_urlsafe(24)}",
        f"SEMASCOPE_ADMIN_PASSWORD={secrets.token_urlsafe(18)}",
        f"SEMASCOPE_API_TOKEN=oss_{secrets.token_urlsafe(32)}",
        f"SEMASCOPE_IMPORT_DIR={import_dir}",
        f"SOLR_HEAP={heaps[size]}",
        f"NEO4J_HEAP={heaps[size]}",
        f"TIKA_HEAP={heaps[size]}",
        f"SEMASCOPE_WORKER_CONCURRENCY={WORKERS[size][0]}",
        f"SEMASCOPE_WORKER_MEMORY={WORKERS[size][1]}",
        f"SEMASCOPE_RUNTIME={runtime}",
        f"SEMASCOPE_EMBEDDER={providers['embed']}",
        f"SEMASCOPE_EMBED_MODEL={model}",
        f"SEMASCOPE_EMBED_BASE_URL={embed_url}",
        f"SEMASCOPE_NER_PROVIDER={providers['ner']}",
        f"SEMASCOPE_NER_BASE_URL={ner_url}",
        f"SEMASCOPE_EXTRACT_PROVIDER={providers['extract']}",
        f"SEMASCOPE_EXTRACT_BASE_URL={extract_url}",
        f"SEMASCOPE_REMOTE_API_KEY={remote_token}",
        "SEMASCOPE_LLM_BASE_URL=",
        "SEMASCOPE_LLM_API_KEY=",
        "SEMASCOPE_LLM_MODEL=",
    ]
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env_file.chmod(0o600)
    console.print(f"wrote {env_file} (secrets generated; keep it out of git)")
    console.print("admin login: user 'admin', password SEMASCOPE_ADMIN_PASSWORD in that file")
    console.print(
        f"runtime {runtime}: embed={providers['embed']} ner={providers['ner']} "
        f"extract={providers['extract']}"
    )


# Worker processes and the worker's memory cap per size. Each pool process loads its own spaCy
# and embedding models; on the CPU runtime one grew to about 2.2 GB resident over 1,000 mixed
# documents (docs/quality.md, 2026-10-16), and two processes reached a 4 GB cap. So 1 process
# with 4 GB for a 2-core laptop, 2 with 6 GB for a 4 to 8 core server, 4 with 12 GB for 8+.
WORKERS = {"small": (1, "4g"), "medium": (2, "6g"), "large": (4, "12g")}

RUNTIME_DEFAULTS = {
    "cpu": {"embed": "fastembed", "ner": "spacy", "extract": "tika"},
    "gpu": {"embed": "fastembed", "ner": "gliner", "extract": "tika"},
    "remote": {"embed": "sidecar", "ner": "remote", "extract": "mineru"},
}
PROVIDERS = {
    "embed": ("fastembed", "openai", "sidecar"),
    "ner": ("spacy", "gliner", "remote"),
    "extract": ("tika", "mineru"),
}
REMOTE_PROVIDERS = ("openai", "sidecar", "remote", "mineru")
EMBED_MODELS = {
    "fastembed": "BAAI/bge-small-en-v1.5",
    "sidecar": "sentence-transformers/all-MiniLM-L6-v2",
    "openai": "",
}


def runtime_providers(runtime: str, **chosen: str | None) -> dict[str, str]:
    """The provider of each heavy stage: the runtime's defaults, overridden per stage."""

    if runtime not in RUNTIME_DEFAULTS:
        raise typer.BadParameter("runtime must be cpu, gpu or remote")
    out = dict(RUNTIME_DEFAULTS[runtime])
    for stage, value in chosen.items():
        if value is None:
            continue
        if value not in PROVIDERS[stage]:
            raise typer.BadParameter(f"{stage} must be one of {', '.join(PROVIDERS[stage])}")
        out[stage] = value
    return out


@app.command()
def doctor() -> None:
    """Check that every service answers and report versions."""

    settings = get_settings()
    table = Table("service", "endpoint", "status")
    checks: dict[str, tuple[str, dict[str, str] | None]] = {
        "server": ("http://server:8000/healthz", None),
        "solr": (f"{settings.solr_url}/admin/info/system?wt=json", _solr_auth(settings)),
        "tika": (f"{settings.tika_url}/version", None),
        "neo4j": (settings.neo4j_uri.replace("bolt://", "http://").replace(":7687", ":7474"), None),
    }
    checks.update(remote_checks(settings))
    failures = 0
    with httpx.Client(timeout=5.0) as client:
        for name, (url, headers) in checks.items():
            try:
                resp = client.get(url, headers=headers or {})
                status = f"{resp.status_code}"
                if resp.status_code >= 400:
                    failures += 1
            except httpx.HTTPError as exc:
                status = f"unreachable ({type(exc).__name__})"
                failures += 1
            table.add_row(name, url, status)
        vectors, vectors_ok, notes = vector_report(settings, client)
        failures += not vectors_ok
        table.add_row("vectors", settings.semascope_embed_model, vectors)
        documents, doc_notes = document_report(settings, client)
        if documents:
            table.add_row("documents", "/api/stats", documents)
            notes += doc_notes
    console.print(table)
    for note in notes:
        console.print(f"[yellow]{note}[/yellow]")
    console.print(
        f"runtime {settings.semascope_runtime}: embed={settings.semascope_embedder} "
        f"ner={settings.semascope_ner_provider} extract={settings.semascope_extract_provider}"
    )
    if failures:
        raise typer.Exit(code=1)


def document_report(settings: Any, client: httpx.Client) -> tuple[str, list[str]]:
    """Graph projections behind and held documents, from the API; empty without a token.

    Not a failure of `doctor`: a pending projection catches up by itself, and a held or
    failed document is the data's problem, not the stack's.
    """

    token = settings.semascope_api_token.get_secret_value() if settings.semascope_api_token else ""
    if not token:
        return "", []
    try:
        resp = client.get(
            f"{settings.semascope_api_url.rstrip('/')}/api/stats",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        stats = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        return f"stats unavailable ({type(exc).__name__})", []
    notes = document_notes(stats)
    graph = stats.get("graph") or {}
    summary = (
        f"graph pending {graph.get('pending', 0)}, graph failed {graph.get('failed', 0)}, "
        f"held {stats.get('interrupted', 0)}"
    )
    return summary, notes


def _solr_auth(settings: Any) -> dict[str, str]:
    """The basic-auth header the stack's Solr expects (see deploy/solr/init-security.sh)."""

    import base64

    password = settings.solr_password.get_secret_value() if settings.solr_password else ""
    token = base64.b64encode(f"{settings.solr_user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def remote_checks(settings: Any) -> dict[str, tuple[str, dict[str, str] | None]]:
    """Health URLs of the configured remote services, with their bearer token."""

    out: dict[str, tuple[str, dict[str, str] | None]] = {}

    def auth(stage: Any) -> dict[str, str] | None:
        key = settings.remote_key(stage)
        return {"Authorization": f"Bearer {key}"} if key else None

    base = (settings.semascope_embed_base_url or "").rstrip("/")
    if settings.semascope_embedder == "sidecar" and base:
        out["embed (sidecar)"] = (f"{base}/health", auth("embed"))
    elif settings.semascope_embedder == "openai" and base:
        out["embed (openai)"] = (f"{base}/models", auth("embed"))
    base = (settings.semascope_ner_base_url or "").rstrip("/")
    if settings.semascope_ner_provider == "remote" and base:
        out["ner (remote)"] = (f"{base}/health", auth("ner"))
    base = (settings.semascope_extract_base_url or "").rstrip("/")
    if settings.semascope_extract_provider == "mineru" and base:
        out["extract (mineru)"] = (f"{base}/v1/health", auth("extract"))
    return out


# --- API-backed commands -----------------------------------------------------------------------


def _api() -> httpx.Client:
    settings = get_settings()
    token = settings.semascope_api_token.get_secret_value() if settings.semascope_api_token else ""
    if not token:
        raise typer.BadParameter(
            "SEMASCOPE_API_TOKEN is not set; run `oss init` or create a token in the admin"
        )
    return httpx.Client(
        base_url=f"{settings.semascope_api_url.rstrip('/')}/api",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
    )


def _check(resp: httpx.Response) -> Any:
    if resp.status_code >= 400:
        console.print(f"[red]{resp.status_code}[/red] {resp.text[:500]}")
        raise typer.Exit(code=1)
    return resp.json() if resp.content else {}


@app.command()
def index(
    path: Path = typer.Argument(..., exists=True, help="File or directory to index"),
    collection: str = typer.Option("default", help="Collection slug (created if missing)"),
    name: str | None = typer.Option(None, help="Source name (defaults to the path's name)"),
    include: list[str] = typer.Option([], help="Include globs, e.g. --include '*.pdf'"),
    wait: bool = typer.Option(True, help="Poll until the source run finishes"),
) -> None:
    """Index a directory into a collection through the filesystem connector."""

    path = path.resolve()
    directory = path if path.is_dir() else path.parent
    settings: dict[str, Any] = {"path": str(directory)}
    if path.is_file():
        settings["include"] = [path.name]
    elif include:
        settings["include"] = include
    with _api() as api:
        if api.get(f"/collections/{collection}").status_code == 404:
            _check(api.post("/collections", json={"slug": collection}))
            console.print(f"created collection {collection}")
        source_name = name or path.name
        sources = _check(api.get(f"/collections/{collection}/sources"))
        existing = next(
            (s for s in sources if isinstance(sources, list) and s["name"] == source_name), None
        )
        if existing is None:
            created = _check(
                api.post(
                    f"/collections/{collection}/sources",
                    json={"name": source_name, "connector": "filesystem", "settings": settings},
                )
            )
            source_id = int(created["id"])
        else:
            source_id = int(existing["id"])
        run = _check(api.post(f"/sources/{source_id}/run"))
        console.print(
            f"queued source {source_id} (task {run['task_id'] if isinstance(run, dict) else '?'})"
        )
        if not wait:
            return
        import time

        last = ""
        for _ in range(600):
            time.sleep(2)
            src = next(
                s
                for s in _check(api.get(f"/collections/{collection}/sources"))
                if s["id"] == source_id
            )
            stats = _check(api.get("/stats"))
            docs = stats["documents"]
            line = f"{src['status']}  documents={docs}"
            if line != last:
                console.print(line)
                last = line
            if src["status"] != "running" and src["last_run_at"]:
                if not docs.get("queued") and not docs.get("processing"):
                    break
        if src["status"] == "failed":
            console.print(f"[red]{src['last_error']}[/red]")
            raise typer.Exit(code=1)


@app.command()
def upload(
    files: list[Path] = typer.Argument(..., exists=True, dir_okay=False),
    collection: str = typer.Option("default"),
) -> None:
    """Upload files through the API (no import folder needed)."""

    with _api() as api:
        if api.get(f"/collections/{collection}").status_code == 404:
            _check(api.post("/collections", json={"slug": collection}))
        payload = [("files", (f.name, f.read_bytes())) for f in files]
        out = _check(api.post(f"/collections/{collection}/upload", files=payload))
    for doc in out:
        console.print(f"queued {doc['id']}  {doc['uri']}")


@app.command()
def demo(
    collection: str = typer.Option("demo", help="Collection to load the demo corpus into"),
    corpus: Path = typer.Option(
        Path("/app/packs/demo/corpus"), help="Demo corpus folder as seen by the server"
    ),
    pack: Path = typer.Option(
        Path("/app/packs/demo/pack"), help="Demo pack folder as seen by the server"
    ),
    only: list[str] = typer.Option(
        [],
        envvar="SEMASCOPE_DEMO_ONLY",
        help="Load only these texts by slug, e.g. --only odyssey (env: space-separated slugs)",
    ),
) -> None:
    """Load the public-domain demo corpus and its knowledge model."""

    settings: dict[str, Any] = {"path": str(corpus)}
    if only:
        settings["include"] = [f"{slug}.txt" for slug in only]
    with _api() as api:
        out = _check(
            api.post(
                "/knowledge/packs/import",
                # the demo seeds its own pack: no plan to review
                json={"path": str(pack), "activate": True, "reindex": False, "confirm": True},
            )
        )
        console.print(f"pack {out['pack_id']}@{out['version']} {out['status']}")
        if api.get(f"/collections/{collection}").status_code == 404:
            _check(
                api.post(
                    "/collections",
                    json={"slug": collection, "name": "Demo: sacred texts and mythology"},
                )
            )
        sources = _check(api.get(f"/collections/{collection}/sources"))
        src = next((s for s in sources if s["name"] == "demo-corpus"), None)
        if src is None:
            src = _check(
                api.post(
                    f"/collections/{collection}/sources",
                    json={
                        "name": "demo-corpus",
                        "connector": "filesystem",
                        "settings": settings,
                    },
                )
            )
        elif only and src.get("settings") != settings:
            console.print("[yellow]the demo source exists; --only applies to a new one[/yellow]")
        run = _check(api.post(f"/sources/{src['id']}/run"))
        subset = f", {len(only)} texts" if only else ""
        console.print(f"queued the demo corpus (source {src['id']}, task {run['task_id']}{subset})")
        console.print(
            "open the UI, pick the demo collection and try the traditions and themes facets"
        )


@app.command()
def reindex(
    collection: str = typer.Option(..., help="Collection slug"),
    stage: list[str] = typer.Option(
        [], help="Only these stages, e.g. --stage embed after a change of embedding model"
    ),
) -> None:
    """Replay enrichment and indexing for a collection from stored text (no Tika)."""

    params = {"stages": ",".join(stage)} if stage else {}
    with _api() as api:
        _check(api.post(f"/collections/{collection}/reindex", params=params))
    console.print(f"queued a reindex of {collection}" + (f" ({', '.join(stage)})" if stage else ""))


@app.command()
def search(
    q: str = typer.Argument("*:*"),
    collection: str | None = typer.Option(None),
    rows: int = typer.Option(10),
    facets: bool = typer.Option(True),
    mode: str = typer.Option("keyword", help="keyword | semantic | hybrid"),
) -> None:
    """Search the index and print hits with snippets (passages in semantic modes)."""

    with _api() as api:
        body = {"q": q, "collection": collection, "page_size": rows, "mode": mode}
        out = _check(api.post("/search", json=body))
    console.print(f"{out['total']} hits in {out['took_ms']} ms")
    for hit in out["hits"]:
        console.print(f"[bold]{hit['title']}[/bold]  [dim]{hit['uri']}[/dim]")
        for snippet in hit["snippets"][:2]:
            console.print(
                "   " + snippet.replace("<mark>", "[yellow]").replace("</mark>", "[/yellow]")
            )
        for passage in (hit.get("passages") or [])[:2]:
            console.print("   [dim]" + passage[:300] + "[/dim]")
    if facets and out.get("facets"):
        table = Table("facet", "values")
        for field, values in out["facets"].items():
            table.add_row(field, ", ".join(f"{v['value']} ({v['count']})" for v in values[:8]))
        console.print(table)


@app.command()
def stats() -> None:
    """Counts of collections, sources and documents by status."""

    with _api() as api:
        out = _check(api.get("/stats"))
    console.print(out)
    for note in document_notes(out):
        console.print(f"[yellow]{note}[/yellow]")


def document_notes(stats: dict[str, Any]) -> list[str]:
    """What in `/stats` needs the operator: graph projections behind, documents held."""

    notes = []
    graph = stats.get("graph") or {}
    if graph.get("pending"):
        notes.append(
            f"{graph['pending']} indexed documents are not in the graph yet (Neo4j was "
            "unavailable); they are searchable and are projected again automatically"
        )
    if graph.get("failed"):
        notes.append(
            f"{graph['failed']} indexed documents failed their graph projection; "
            "`oss reindex --collection <slug>` projects them again"
        )
    if stats.get("interrupted"):
        notes.append(
            f"{stats['interrupted']} documents are held after their runs kept stopping the "
            "worker (memory, a crash or the time limit); see their errors, then `oss reindex`"
        )
    return notes


def _print_plan(out: dict[str, Any]) -> None:
    """A pack diff and its reindex plan, as `oss pack diff` and a refused activation show it."""

    label = f"{out['pack_id']}@{out['candidate_version']}"
    if out.get("active_version") is None:
        console.print(f"{label}: no active version; activating it changes no existing tags")
    else:
        same = " (same content)" if out.get("same_content") else ""
        console.print(f"{out['pack_id']}: active {out['active_version']} -> {label}{same}")
    diff = out.get("diff") or {}
    summary = diff.get("summary") or {}
    table = Table("change", "count")
    for key in ("added", "removed", "changed"):
        kinds = summary.get(key) or {}
        detail = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) or "0"
        table.add_row(key, detail)
    for key in ("labels_added", "labels_removed", "became_taggable", "no_longer_taggable"):
        table.add_row(key.replace("_", " "), str(summary.get(key, 0)))
    console.print(table)
    for key, colour in (("removed", "red"), ("added", "green")):
        for term in (diff.get(key) or [])[:20]:
            console.print(f"  [{colour}]{key}[/{colour}] {term['kind']} {term['label']}")
    for term in (diff.get("changed") or [])[:20]:
        console.print(
            f"  [yellow]changed[/yellow] {term['kind']} {term['label']}: "
            + ", ".join(sorted(term["fields"]))
        )
    if diff.get("truncated"):
        console.print("  (lists truncated; the API returns up to 200 of each)")
    plan = out.get("plan") or {}
    if plan.get("collections"):
        rows = Table("collection", "reindex", "documents", "tagged by active", "affected")
        for c in plan["collections"]:
            rows.add_row(
                c["collection"],
                "yes" if c["reindex"] else "no",
                str(c["documents"]),
                str(c["tagged_documents"]),
                str(c["affected_documents"]),
            )
        console.print(rows)
    elif plan.get("counts_error"):
        console.print(f"[yellow]document counts unavailable:[/yellow] {plan['counts_error']}")
    if plan.get("reindex"):
        console.print(f"activation reindexes: {', '.join(plan['reindex'])}")


def _plan_or_exit(resp: httpx.Response) -> Any:
    """Print the plan of a 409 (confirmation needed) and stop; otherwise check as usual."""

    if resp.status_code == 409:
        body = resp.json()
        _print_plan(body.get("plan") or {})
        console.print(f"[yellow]{body.get('detail', 'confirmation required')}[/yellow]")
        console.print("rerun with --yes to proceed")
        raise typer.Exit(code=1)
    return _check(resp)


@pack_app.command("import")
def pack_import(
    path: Path = typer.Argument(..., exists=True, file_okay=False),
    activate: bool = typer.Option(True, help="Make this version the active one"),
    reindex: bool = typer.Option(True, help="Reindex the collections that use this pack"),
    force: bool = typer.Option(
        False, help="Re-import an unchanged bundle; accept a release-manifest mismatch"
    ),
    yes: bool = typer.Option(
        False, "--yes", help="Confirm replacing the active version (see `oss pack diff`)"
    ),
) -> None:
    """Import a bundle directory into the running Semascope (path as seen by the server).

    Replacing an active version prints the diff and reindex plan and stops, unless --yes.
    """

    body = {
        "path": str(path),
        "activate": activate,
        "reindex": reindex,
        "force": force,
        "confirm": yes,
    }
    with _api() as api:
        out = _plan_or_exit(api.post("/knowledge/packs/import", json=body))
    if out.get("plan") and out["plan"].get("requires_confirmation"):
        _print_plan(out["plan"])
    terms = out["counts"].get("terms")
    label = f"{out['pack_id']}@{out['version']}"
    console.print(f"[green]imported[/green] {label} status={out['status']} terms={terms}")
    cq = out.get("cq_results") or {}
    if cq.get("tests"):
        console.print(f"competency tests: {cq.get('passed')}/{cq.get('tests')} passed")


@pack_app.command("diff")
def pack_diff(
    path: Path = typer.Argument(..., help="Bundle directory as seen by the server"),
    force: bool = typer.Option(False, help="Accept a release-manifest mismatch"),
) -> None:
    """Diff a bundle against the active version of its pack, with the reindex plan."""

    with _api() as api:
        out = _check(api.post("/knowledge/packs/diff", json={"path": str(path), "force": force}))
    _print_plan(out)


@pack_app.command("activate")
def pack_activate(
    pack_id: str = typer.Argument(..., help="Pack id"),
    version: str = typer.Option(..., help="Imported version to activate"),
    yes: bool = typer.Option(False, "--yes", help="Confirm replacing the active version"),
) -> None:
    """Activate an imported version (upgrade or roll back); shows the plan, needs --yes."""

    with _api() as api:
        out = _plan_or_exit(
            api.post(
                f"/knowledge/packs/{pack_id}/activate",
                params={"version": version, "confirm": "true" if yes else "false"},
            )
        )
    if out.get("plan") and out["plan"].get("requires_confirmation"):
        _print_plan(out["plan"])
    console.print(f"[green]activated[/green] {out['pack_id']}@{out['version']}")


@pack_app.command("remove")
def pack_remove(
    pack_id: str = typer.Argument(..., help="Pack id"),
    version: str = typer.Option(..., help="Version to remove (not the active one)"),
    yes: bool = typer.Option(False, "--yes", help="Do not ask for confirmation"),
) -> None:
    """Remove an imported pack version (admin). Tagged documents keep their tags until reindex."""

    if not yes and not typer.confirm(f"remove {pack_id}@{version}?", default=False):
        raise typer.Exit(code=1)
    with _api() as api:
        out = _check(api.delete(f"/knowledge/packs/{pack_id}/versions/{version}"))
    console.print(f"[green]removed[/green] {pack_id}@{version}: {out['concepts_removed']} concepts")
    still = out.get("documents_still_tagged")
    if still:
        console.print(f"{still} documents keep tags from {pack_id}@{version} until reindexed")
    if not out.get("dictionary_entries_removed"):
        console.print("[yellow]the dictionary entries could not be removed from Solr[/yellow]")


@pack_app.command("list")
def pack_list() -> None:
    """Imported packs and their status."""

    with _api() as api:
        packs = _check(api.get("/knowledge/packs"))
    table = Table("pack", "version", "status", "terms", "triples", "imported")
    for p in packs:
        table.add_row(
            p["pack_id"],
            p["version"],
            p["status"],
            str(p["counts"].get("terms")),
            str(p["counts"].get("triples")),
            p["imported_at"][:19],
        )
    console.print(table)


@pack_app.command("validate")
def pack_validate(
    path: Path = typer.Argument(..., exists=True, file_okay=False),
    manifest: bool = typer.Option(
        False, help="Write release-manifest.json (a stale one is then not an error)"
    ),
    force: bool = typer.Option(False, help="Report a release-manifest mismatch as a warning"),
) -> None:
    """Validate a pack against the ontology-skill bundle contract and run its competency
    questions. Exits 1 on any error or failed question."""

    info = load_pack(path, force=force or manifest)
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
    results: list[dict[str, Any]] = run_competency_questions(path)["results"] if info.ok else []
    failed = [r for r in results if r["status"] not in ("pass", "skipped-reasoner")]
    for r in results:
        colour = "green" if r["status"] == "pass" else "yellow" if r in failed else "white"
        message = f" {r['message']}" if r.get("message") else ""
        console.print(f"[{colour}]{r['status']}[/{colour}] {r['cq_id']}{message}")
    if manifest and info.ok:
        console.print(f"wrote {write_manifest(info)}")
    if not info.ok or failed:
        raise typer.Exit(code=1)
    console.print(f"[green]ok[/green] {info.id} {info.version} ({len(info.files)} files)")
