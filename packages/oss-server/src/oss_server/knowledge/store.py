"""The pack triple store: embedded Oxigraph on the data volume, one named graph per pack version.

Oxigraph keeps its data in RocksDB, which one process at a time may open:
the store holds a lock file for as long as it is open. The server runs
several worker processes, so a store kept open by one of them made every
pack operation in the others fail ("While lock file: …/LOCK"). Each
operation therefore opens the store under an exclusive file lock, works,
and drops it (which closes RocksDB) before the lock is released. Results
leave as plain Python values, never as store objects.
"""

from __future__ import annotations

import fcntl
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pyoxigraph as ox

from oss_core.config import get_settings


def _path() -> Path:
    path = get_settings().semascope_data_dir / "packs.oxigraph"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def store() -> ox.Store:
    """Open the store. Callers go through `_using`, which closes it again."""

    return ox.Store(str(_path()))


def _using[T](work: Callable[[ox.Store], T]) -> T:
    """Run `work` on a freshly opened store while holding the cross-process lock. The store
    is referenced only by `work`'s frame, so it is closed when `work` returns."""

    lock = _path().with_suffix(".lock")
    with lock.open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            return work(store())
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def graph_iri(pack_id: str, version: str) -> str:
    return f"urn:semascope:pack:{pack_id}:{version}"


def load_bundle(files: dict[str, Path], pack_id: str, version: str) -> int:
    """Load a bundle's RDF files (stem -> file) into the pack's named graph; returns the
    triple count. Turtle loads directly; other serialisations go through rdflib, the
    parser validation used, so both see the same triples."""

    return _using(lambda s: _load(s, files, pack_id, version))


def _load(s: ox.Store, files: dict[str, Path], pack_id: str, version: str) -> int:
    from rdflib import Graph

    from oss_core.pack import parse_rdf

    graph = ox.NamedNode(graph_iri(pack_id, version))
    s.remove_graph(graph) if graph in list(s.named_graphs()) else None
    for stem in ("ontology", "taxonomy", "shapes", "instances"):
        file = files.get(stem)
        if file is None or not file.exists():
            continue
        if file.suffix == ".ttl":
            with file.open("rb") as fh:
                s.bulk_load(fh, ox.RdfFormat.TURTLE, to_graph=graph)
        else:
            g = Graph()
            parse_rdf(g, file)
            data = g.serialize(format="nt", encoding="utf-8")
            s.bulk_load(data, ox.RdfFormat.N_TRIPLES, to_graph=graph)
    return sum(1 for _ in s.quads_for_pattern(None, None, None, graph))


def drop_graph(pack_id: str, version: str) -> None:
    graph = ox.NamedNode(graph_iri(pack_id, version))

    def work(s: ox.Store) -> None:
        if graph in list(s.named_graphs()):
            s.remove_graph(graph)

    _using(work)


def query(
    sparql: str, *, pack_id: str | None = None, version: str | None = None, limit: int = 1000
) -> list[dict[str, Any]]:
    """Run a SELECT; rows as plain dicts. Restricted to one named graph when a pack is given."""

    kwargs: dict[str, Any] = {}
    if pack_id and version:
        kwargs["default_graph"] = [ox.NamedNode(graph_iri(pack_id, version))]
    return _using(lambda s: _rows(s.query(sparql, **kwargs), limit))


def _rows(result: Any, limit: int) -> list[dict[str, Any]]:
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


def ask(sparql: str, *, pack_id: str, version: str) -> bool:
    out = query(sparql, pack_id=pack_id, version=version)
    return bool(out and out[0].get("ask"))


def triples(pack_id: str, version: str) -> Iterator[tuple[str, str, str]]:
    graph = ox.NamedNode(graph_iri(pack_id, version))

    def work(s: ox.Store) -> list[tuple[str, str, str]]:
        return [
            (str(_plain(q.subject)), str(_plain(q.predicate)), str(_plain(q.object)))
            for q in s.quads_for_pattern(None, None, None, graph)
        ]

    yield from _using(work)


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
