"""`oss export`: documents as RDF and annotations as W3C Web Annotations, through the API.

    oss export search "flood" --collection demo --format turtle --out flood.ttl
    oss export document <id> --format jsonld
    oss export annotations --collection demo --out notes.jsonld

Exports stream to a file (or standard output) as the server writes them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import IO, Any

import httpx
import typer

app = typer.Typer(help="Export documents as RDF and annotations as W3C Web Annotations.")


def _client() -> httpx.Client:
    from oss_cli.main import _api

    return _api()


def _fail(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        resp.read()
        typer.echo(f"{resp.status_code} {resp.text[:500]}", err=True)
        raise typer.Exit(code=1)


def _sink(out: Path | None) -> IO[bytes]:
    return out.open("wb") if out is not None else sys.stdout.buffer


def _stream(resp: httpx.Response, out: Path | None) -> int:
    _fail(resp)
    written = 0
    sink = _sink(out)
    try:
        for chunk in resp.iter_bytes():
            sink.write(chunk)
            written += len(chunk)
    finally:
        if out is not None:
            sink.close()
    return written


@app.command("search")
def export_search(
    q: str = typer.Argument("*:*", help="The search, as in `oss search`"),
    collection: str | None = typer.Option(None),
    mode: str = typer.Option("keyword", help="keyword | semantic | hybrid"),
    format: str = typer.Option("turtle", help="turtle | jsonld"),  # noqa: A002
    limit: int = typer.Option(1000, help="Documents to export (at most 10,000)"),
    out: Path | None = typer.Option(None, help="File to write (default: standard output)"),
) -> None:
    """A search's hits with their tags, notes and decisions, as Turtle or JSON-LD."""

    body: dict[str, Any] = {"q": q, "collection": collection, "mode": mode}
    with (
        _client() as api,
        api.stream(
            "POST",
            "/export/search",
            params={"format": format, "limit": limit},
            json=body,
            timeout=None,
        ) as resp,
    ):
        n = _stream(resp, out)
    if out is not None:
        typer.echo(f"wrote {n} bytes to {out}", err=True)


@app.command("document")
def export_document(
    doc_id: str = typer.Argument(..., help="Document id"),
    format: str = typer.Option("turtle", help="turtle | jsonld"),  # noqa: A002
    out: Path | None = typer.Option(None, help="File to write (default: standard output)"),
) -> None:
    """One document with its tags, notes and decisions, as Turtle or JSON-LD."""

    with (
        _client() as api,
        api.stream("GET", f"/export/documents/{doc_id}", params={"format": format}) as resp,
    ):
        _stream(resp, out)


@app.command("annotations")
def export_annotations(
    collection: str | None = typer.Option(None, help="Only this collection"),
    document: str | None = typer.Option(None, help="Only this document"),
    history: bool = typer.Option(False, help="Every decision, not only the ones in force"),
    out: Path | None = typer.Option(None, help="File to write (default: standard output)"),
) -> None:
    """Notes and decisions as one W3C AnnotationCollection (JSON-LD), following every page."""

    params: dict[str, Any] = {"history": history}
    if collection:
        params["collection"] = collection
    if document:
        params["document"] = document
    items: list[dict[str, Any]] = []
    first: dict[str, Any] | None = None
    with _client() as api:
        url: str | None = "/annotations/w3c"
        while url:
            resp = api.get(url, params=params if first is None else None)
            _fail(resp)
            page = resp.json()
            first = first or page
            items.extend(page.get("items") or [])
            nxt = page.get("next")
            url = "/annotations/w3c?" + nxt.split("?", 1)[1] if nxt else None
    assert first is not None
    collection_doc = {
        "@context": first["@context"],
        "id": first["partOf"]["id"],
        "type": "AnnotationCollection",
        "label": first["partOf"].get("label", "annotations"),
        "total": len(items),
        "first": {"type": "AnnotationPage", "startIndex": 0, "items": items},
    }
    data = (json.dumps(collection_doc, ensure_ascii=False, indent=1) + "\n").encode()
    sink = _sink(out)
    sink.write(data)
    if out is not None:
        sink.close()
        typer.echo(f"wrote {len(items)} annotations to {out}", err=True)
