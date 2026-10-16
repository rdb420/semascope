"""RDF connector: one document per subject from RDF files or a SPARQL endpoint.

Settings: `path` (file or folder of .ttl/.nt/.rdf/.jsonld, under the source
roots), or `endpoint` (SPARQL, a public address unless the operator allows a
private one) with `graph` (optional), `types` (list of class IRIs to select;
empty = every subject with a label) and `max_bytes` (the response cap). Each
subject is rendered as readable "predicate: value" lines with its label as
title, so Tika and the pattern and tag stages see text, and its IRI is kept in
`x_subject_s`.
"""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from oss_core.contracts import Cursor, FetchedItem, SourceConfig, SourceItem
from oss_pipeline.connectors.guard import (
    DEFAULT_MAX_BYTES,
    confine_path,
    confined_files,
    get_capped,
    guarded_client,
    source_roots,
)

LABEL_PREDICATES = (
    "http://www.w3.org/2000/01/rdf-schema#label",
    "http://www.w3.org/2004/02/skos/core#prefLabel",
    "http://purl.org/dc/terms/title",
    "http://purl.org/dc/elements/1.1/title",
    "http://schema.org/name",
    "http://xmlns.com/foaf/0.1/name",
)
FORMATS = {
    ".ttl": "turtle",
    ".nt": "nt",
    ".n3": "n3",
    ".rdf": "xml",
    ".owl": "xml",
    ".jsonld": "json-ld",
    ".trig": "trig",
}


def _local(iri: str) -> str:
    return iri.rstrip("/#").rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def _subjects_from_graph(
    g: Any, types: list[str]
) -> Iterator[tuple[str, str, list[tuple[str, str]]]]:
    from rdflib import RDF, Literal, URIRef

    seen: set[str] = set()
    candidates: Iterable[Any]
    if types:
        candidates = (s for t in types for s in g.subjects(RDF.type, URIRef(t)))
    else:
        candidates = (s for s in g.subjects() if isinstance(s, URIRef))
    for s in candidates:
        if not isinstance(s, URIRef) or str(s) in seen:
            continue
        seen.add(str(s))
        label = ""
        rows: list[tuple[str, str]] = []
        for p, o in g.predicate_objects(s):
            pred = str(p)
            value = str(o) if isinstance(o, Literal) else _local(str(o))
            if pred in LABEL_PREDICATES and not label:
                label = str(o)
            rows.append((_local(pred), value))
        if not rows:
            continue
        yield str(s), label or _local(str(s)), rows


def _subjects_from_endpoint(
    endpoint: str, graph: str | None, types: list[str], limit: int, max_bytes: int
) -> Iterator[tuple[str, str, list[tuple[str, str]]]]:
    import json

    type_filter = " UNION ".join(f"{{ ?s a <{t}> }}" for t in types) if types else ""
    from_clause = f"FROM <{graph}>" if graph else ""
    where = f"WHERE {{ {type_filter} ?s ?p ?o . FILTER(isIRI(?s)) }}"
    query = f"SELECT ?s ?p ?o {from_clause} {where} LIMIT {limit}"
    with guarded_client(timeout=120.0) as client:
        resp, body = get_capped(
            client,
            endpoint,
            max_bytes,
            params={"query": query},
            headers={"Accept": "application/sparql-results+json"},
        )
        resp.raise_for_status()
        bindings = json.loads(body).get("results", {}).get("bindings", [])
    by_subject: dict[str, list[tuple[str, str]]] = {}
    labels: dict[str, str] = {}
    for b in bindings:
        s, p, o = b["s"]["value"], b["p"]["value"], b["o"]["value"]
        if p in LABEL_PREDICATES and s not in labels:
            labels[s] = o
        by_subject.setdefault(s, []).append(
            (_local(p), o if b["o"]["type"] == "literal" else _local(o))
        )
    for s, rows in by_subject.items():
        yield s, labels.get(s, _local(s)), rows


class RdfConnector:
    name = "rdf"

    def discover(self, source: SourceConfig, since: Cursor | None) -> Iterable[SourceItem]:
        settings = source.settings
        types = [str(t) for t in settings.get("types") or []]
        tmp = Path(tempfile.gettempdir()) / "semascope-rdf"
        tmp.mkdir(parents=True, exist_ok=True)
        if settings.get("endpoint"):
            subjects: Iterable[tuple[str, str, list[tuple[str, str]]]] = _subjects_from_endpoint(
                str(settings["endpoint"]),
                settings.get("graph"),
                types,
                int(settings.get("limit", 100000)),
                int(settings.get("max_bytes", 4 * DEFAULT_MAX_BYTES)),
            )
        else:
            from rdflib import Graph

            roots = source_roots()
            root = confine_path(str(settings["path"]), roots)
            files = (
                [root]
                if root.is_file()
                else sorted(p for p in root.rglob("*") if p.suffix.lower() in FORMATS)
            )
            files = list(confined_files(files, roots))
            g = Graph()
            for file in files:
                g.parse(str(file), format=FORMATS[file.suffix.lower()])
            subjects = _subjects_from_graph(g, types)
        for iri, label, rows in subjects:
            text = f"{label}\n\n" + "\n".join(f"{p}: {v}" for p, v in rows) + f"\n\nIRI: {iri}\n"
            content = text.encode("utf-8")
            digest = hashlib.sha256(content).hexdigest()
            out = tmp / f"{digest}.txt"
            out.write_bytes(content)
            yield SourceItem(
                uri=iri,
                content_hash=digest,
                size=len(content),
                metadata={
                    "path": str(out),
                    "media_type": "text/plain",
                    "title": label,
                    "fields": {"x_subject_s": iri},
                },
            )

    def fetch(self, item: SourceItem) -> FetchedItem:
        return FetchedItem(item=item, path=str(item.metadata["path"]), media_type="text/plain")

    def deleted(self, source: SourceConfig, since: Cursor) -> Iterable[str]:
        return []
