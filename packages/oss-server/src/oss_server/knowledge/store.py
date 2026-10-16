"""The pack triple store: embedded Oxigraph on the data volume, one named graph per pack version."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyoxigraph as ox

from oss_core.config import get_settings

_store: ox.Store | None = None


def store() -> ox.Store:
    global _store
    if _store is None:
        path = get_settings().semascope_data_dir / "packs.oxigraph"
        path.parent.mkdir(parents=True, exist_ok=True)
        _store = ox.Store(str(path))
    return _store


def graph_iri(pack_id: str, version: str) -> str:
    return f"urn:semascope:pack:{pack_id}:{version}"


def load_bundle(files: dict[str, Path], pack_id: str, version: str) -> int:
    """Load a bundle's RDF files (stem -> file) into the pack's named graph; returns the
    triple count. Turtle loads directly; other serialisations go through rdflib, the
    parser validation used, so both see the same triples."""

    from rdflib import Graph

    from oss_core.pack import parse_rdf

    s = store()
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
    s = store()
    graph = ox.NamedNode(graph_iri(pack_id, version))
    if graph in list(s.named_graphs()):
        s.remove_graph(graph)


def query(
    sparql: str, *, pack_id: str | None = None, version: str | None = None, limit: int = 1000
) -> list[dict[str, Any]]:
    """Run a SELECT; rows as plain dicts. Restricted to one named graph when a pack is given."""

    s = store()
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
    elif isinstance(result, bool):
        rows.append({"ask": result})
    return rows


def ask(sparql: str, *, pack_id: str, version: str) -> bool:
    out = query(sparql, pack_id=pack_id, version=version)
    return bool(out and out[0].get("ask"))


def triples(pack_id: str, version: str) -> Iterator[tuple[str, str, str]]:
    graph = ox.NamedNode(graph_iri(pack_id, version))
    for quad in store().quads_for_pattern(None, None, None, graph):
        yield str(_plain(quad.subject)), str(_plain(quad.predicate)), str(_plain(quad.object))


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
