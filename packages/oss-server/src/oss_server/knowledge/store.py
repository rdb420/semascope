"""The pack triple store: embedded Oxigraph on the data volume, one named graph per pack version.

Oxigraph keeps its data in RocksDB, which constrains how processes may use it:

- one process at a time may open the store (it holds a LOCK file while open);
  the server runs several gunicorn workers, so a store kept open by one of
  them made pack operations in the others fail ("While lock file: …/LOCK");
- RocksDB's background threads do not survive a fork. The gunicorn master
  imports the bundled packs before it forks its workers, so a worker that
  wrote to the store afterwards waited forever for a flush thread that
  exists only in the master.

Each operation on the disk store therefore runs in a fresh Python process
(`python -m oss_server.knowledge.store`), which takes an exclusive file lock,
opens the store, works, closes it and exits; arguments and results cross as
JSON. That costs a process start per pack import, removal or SPARQL query.
Tests that substitute `store` with an in-memory store run in-process.
"""

from __future__ import annotations

import fcntl
import json
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pyoxigraph as ox

_TIMEOUT_S = 600


def _path() -> Path:
    from oss_core.config import get_settings

    path = get_settings().semascope_data_dir / "packs.oxigraph"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def store() -> ox.Store:
    """Open the disk store (in the operation's own process)."""

    return ox.Store(str(_path()))


_DISK_STORE = store


def graph_iri(pack_id: str, version: str) -> str:
    return f"urn:semascope:pack:{pack_id}:{version}"


# --- operations, run against an open store and returning JSON-friendly values -----------------


def _op_load(s: ox.Store, files: dict[str, str], pack_id: str, version: str) -> int:
    from rdflib import Graph

    from oss_core.pack import parse_rdf

    graph = ox.NamedNode(graph_iri(pack_id, version))
    if graph in list(s.named_graphs()):
        s.remove_graph(graph)
    for stem in ("ontology", "taxonomy", "shapes", "instances"):
        name = files.get(stem)
        if not name or not Path(name).exists():
            continue
        file = Path(name)
        if file.suffix == ".ttl":
            with file.open("rb") as fh:
                s.bulk_load(fh, ox.RdfFormat.TURTLE, to_graph=graph)
        else:
            g = Graph()
            parse_rdf(g, file)
            data = g.serialize(format="nt", encoding="utf-8")
            s.bulk_load(data, ox.RdfFormat.N_TRIPLES, to_graph=graph)
    return sum(1 for _ in s.quads_for_pattern(None, None, None, graph))


def _op_drop(s: ox.Store, pack_id: str, version: str) -> None:
    graph = ox.NamedNode(graph_iri(pack_id, version))
    if graph in list(s.named_graphs()):
        s.remove_graph(graph)


def _op_query(
    s: ox.Store, sparql: str, pack_id: str | None, version: str | None, limit: int
) -> list[dict[str, Any]]:
    kwargs: dict[str, Any] = {}
    if pack_id and version:
        kwargs["default_graph"] = [ox.NamedNode(graph_iri(pack_id, version))]
    result = s.query(sparql, **kwargs)
    rows: list[dict[str, Any]] = []
    if isinstance(result, ox.QuerySolutions):
        variables = [v.value for v in result.variables]
        for solution in result:
            rows.append({v: _plain(solution[v]) for v in variables})
            if len(rows) >= limit:
                break
    elif isinstance(result, ox.QueryBoolean | bool):
        # pyoxigraph answers ASK with a QueryBoolean, not a bool
        rows.append({"ask": bool(result)})
    return rows


def _op_triples(s: ox.Store, pack_id: str, version: str) -> list[list[str]]:
    graph = ox.NamedNode(graph_iri(pack_id, version))
    return [
        [str(_plain(q.subject)), str(_plain(q.predicate)), str(_plain(q.object))]
        for q in s.quads_for_pattern(None, None, None, graph)
    ]


_OPS: dict[str, Callable[..., Any]] = {
    "load": _op_load,
    "drop": _op_drop,
    "query": _op_query,
    "triples": _op_triples,
}


def _run(op: str, *args: Any) -> Any:
    if store is not _DISK_STORE:  # a test substituted an in-memory store
        return _OPS[op](store(), *args)
    path = _path()
    proc = subprocess.run(  # noqa: S603 - our interpreter and module, JSON on stdin
        [sys.executable, "-m", "oss_server.knowledge.store", str(path)],
        input=json.dumps({"op": op, "args": list(args)}),
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_S,
        check=False,
    )
    lines = proc.stdout.strip().splitlines()
    try:
        out = json.loads(lines[-1]) if lines else {}
    except json.JSONDecodeError:
        out = {}
    if proc.returncode != 0 or "error" in out or "result" not in out:
        message = out.get("error") or proc.stderr.strip()[-500:] or f"exit {proc.returncode}"
        raise RuntimeError(f"pack store {op}: {message}")
    return out["result"]


def _serve(path: Path, request: dict[str, Any]) -> Any:
    """In the operation's process: lock, open, run, close."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        s = ox.Store(str(path))
        try:
            return _OPS[request["op"]](s, *request["args"])
        finally:
            s.flush()
            del s


# --- public API --------------------------------------------------------------------------------


def load_bundle(files: dict[str, Path], pack_id: str, version: str) -> int:
    """Load a bundle's RDF files (stem -> file) into the pack's named graph; returns the
    triple count. Turtle loads directly; other serialisations go through rdflib, the
    parser validation used, so both see the same triples."""

    return int(_run("load", {k: str(v) for k, v in files.items()}, pack_id, version))


def drop_graph(pack_id: str, version: str) -> None:
    _run("drop", pack_id, version)


def query(
    sparql: str, *, pack_id: str | None = None, version: str | None = None, limit: int = 1000
) -> list[dict[str, Any]]:
    """Run a SELECT or ASK; rows as plain dicts (ASK: one row `{"ask": bool}`). Restricted
    to one named graph when a pack is given."""

    rows: list[dict[str, Any]] = _run("query", sparql, pack_id, version, limit)
    return rows


def ask(sparql: str, *, pack_id: str, version: str) -> bool:
    out = query(sparql, pack_id=pack_id, version=version)
    return bool(out and out[0].get("ask"))


def triples(pack_id: str, version: str) -> Iterator[tuple[str, str, str]]:
    yield from (tuple(t) for t in _run("triples", pack_id, version))


def _plain(term: Any) -> Any:
    if term is None:
        return None
    if isinstance(term, ox.NamedNode):
        return term.value
    if isinstance(term, ox.Literal):
        return term.value
    if isinstance(term, ox.BlankNode):
        return f"_:{term.value}"
    return str(term)


def main() -> int:
    """`python -m oss_server.knowledge.store PATH` with {"op", "args"} as JSON on stdin."""

    try:
        result = _serve(Path(sys.argv[1]), json.loads(sys.stdin.read()))
    except Exception as exc:  # noqa: BLE001 - reported to the caller as JSON
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"[:1000]}))
        return 1
    print(json.dumps({"result": result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
