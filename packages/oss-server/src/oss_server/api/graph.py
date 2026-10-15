"""Graph API: neighbourhoods, paths and a staff-only read-only Cypher console."""

from __future__ import annotations

import re
from typing import Any

from django.http import HttpRequest
from ninja import Router, Schema
from ninja.errors import HttpError

from oss_server.services import graph_client

router = Router(tags=["graph"])

LABELS = ("Document", "Concept", "Entity", "Source", "Collection", "Pack")


class Node(Schema):
    id: str
    label: str
    kind: str
    properties: dict[str, Any] = {}


class Edge(Schema):
    source: str
    target: str
    type: str
    properties: dict[str, Any] = {}


class GraphOut(Schema):
    nodes: list[Node]
    edges: list[Edge]


class CypherIn(Schema):
    query: str
    limit: int = 200


def _node(n: Any) -> Node:
    labels = [lbl for lbl in n.labels if lbl in LABELS] if hasattr(n, "labels") else []
    kind = labels[0] if labels else "Node"
    props = dict(n)
    key = (
        props.get("id")
        or props.get("iri")
        or props.get("key")
        or props.get("uri")
        or props.get("slug")
        or str(n.element_id)
    )
    label = (
        props.get("title")
        or props.get("pref_label")
        or props.get("label")
        or props.get("slug")
        or str(key)
    )
    return Node(
        id=f"{kind}:{key}",
        label=str(label)[:120],
        kind=kind,
        properties={k: v for k, v in props.items() if k not in ("text",)},
    )


def _edge(r: Any) -> Edge:
    return Edge(
        source=_node(r.start_node).id, target=_node(r.end_node).id, type=r.type, properties=dict(r)
    )


def _collect(records: list[dict[str, Any]]) -> GraphOut:
    nodes: dict[str, Node] = {}
    edges: dict[tuple[str, str, str], Edge] = {}
    for rec in records:
        for value in rec.values():
            items = value if isinstance(value, list) else [value]
            for item in items:
                if hasattr(item, "nodes") and hasattr(item, "relationships"):  # a path
                    for n in item.nodes:
                        node = _node(n)
                        nodes[node.id] = node
                    for r in item.relationships:
                        e = _edge(r)
                        edges[(e.source, e.target, e.type)] = e
                elif hasattr(item, "start_node"):
                    e = _edge(item)
                    nodes.setdefault(e.source, _node(item.start_node))
                    nodes.setdefault(e.target, _node(item.end_node))
                    edges[(e.source, e.target, e.type)] = e
                elif hasattr(item, "labels"):
                    node = _node(item)
                    nodes[node.id] = node
    return GraphOut(nodes=list(nodes.values()), edges=list(edges.values()))


def _raw(cypher: str, **params: Any) -> list[dict[str, Any]]:
    client = graph_client()
    with client._driver.session() as session:  # noqa: SLF001 - raw records needed for node objects
        return [dict(r) for r in session.run(cypher, **params)]


@router.get("/neighbours", response=GraphOut)
def neighbours(
    request: HttpRequest,
    id: str,
    depth: int = 1,
    limit: int = 150,
    kinds: str = "Concept,Entity,Document",
    collection: str | None = None,
) -> GraphOut:
    """The neighbourhood of a node: `id` is `Document:<id>`, `Concept:<iri>` or `Entity:<key>`."""

    kind, _, key = id.partition(":")
    if kind not in LABELS:
        raise HttpError(422, f"id must start with one of {LABELS}")
    prop = {
        "Document": "id",
        "Concept": "iri",
        "Entity": "key",
        "Source": "uri",
        "Collection": "slug",
        "Pack": "key",
    }[kind]
    depth = max(1, min(depth, 3))
    wanted = [k for k in kinds.split(",") if k in LABELS] or list(LABELS)
    scope = (
        " AND all(x IN nodes(p) WHERE NOT x:Document OR x.collection = $collection)"
        if collection
        else ""
    )
    cypher = (
        f"MATCH (n:{kind} {{{prop}: $key}}) "
        f"MATCH p = (n)-[:TAGGED|MENTIONS|CURATED|FROM|IN*1..{depth}]-(m) "
        f"WHERE any(l IN labels(m) WHERE l IN $wanted){scope} "
        f"RETURN p LIMIT $limit"
    )
    rows = _raw(
        cypher, key=key, wanted=wanted, limit=max(1, min(limit, 1000)), collection=collection
    )
    return _collect(rows)


@router.get("/path", response=GraphOut)
def path(request: HttpRequest, a: str, b: str, max_length: int = 4) -> GraphOut:
    """A shortest path between two nodes through documents, concepts and entities."""

    def match(node_id: str, var: str) -> tuple[str, str, str]:
        kind, _, key = node_id.partition(":")
        if kind not in LABELS:
            raise HttpError(422, f"ids must start with one of {LABELS}")
        prop = {
            "Document": "id",
            "Concept": "iri",
            "Entity": "key",
            "Source": "uri",
            "Collection": "slug",
            "Pack": "key",
        }[kind]
        return f"({var}:{kind} {{{prop}: ${var}_key}})", var, key

    ma, _, ka = match(a, "a")
    mb, _, kb = match(b, "b")
    length = max(1, min(max_length, 6))
    rels = f"[:TAGGED|MENTIONS|CURATED*..{length}]"
    cypher = f"MATCH {ma}, {mb} MATCH p = shortestPath((a)-{rels}-(b)) RETURN p"
    return _collect(_raw(cypher, a_key=ka, b_key=kb))


@router.get("/shared", response=list[dict[str, Any]])
def shared(
    request: HttpRequest,
    collection: str | None = None,
    kind: str = "Entity",
    min_docs: int = 2,
    limit: int = 50,
    linked_only: bool = True,
) -> list[dict[str, Any]]:
    """Concepts or entities that connect the most documents: the hubs of a collection."""

    if kind not in ("Entity", "Concept"):
        raise HttpError(422, "kind must be Entity or Concept")
    rel = "MENTIONS" if kind == "Entity" else "TAGGED"
    clauses = []
    if collection:
        clauses.append("d.collection = $collection")
    if kind == "Entity" and linked_only:
        clauses.append("x.class = 'Individual'")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    cypher = (
        f"MATCH (d:Document)-[:{rel}]->(x:{kind}) {where} "
        f"WITH x, count(DISTINCT d) AS docs WHERE docs >= $min_docs "
        f"RETURN coalesce(x.pref_label, x.label, x.key, x.iri) AS label, "
        f"coalesce(x.iri, x.key) AS key, labels(x) AS labels, docs ORDER BY docs DESC LIMIT $limit"
    )
    rows: list[dict[str, Any]] = graph_client().run(
        cypher, collection=collection, min_docs=min_docs, limit=max(1, min(limit, 500))
    )
    return rows


@router.post("/cypher", response=list[dict[str, Any]])
def cypher(request: HttpRequest, payload: CypherIn) -> list[dict[str, Any]]:
    """Staff only, read only: a MATCH/RETURN query, EXPLAIN-checked, row-capped."""

    if not getattr(request.user, "is_staff", False):
        raise HttpError(403, "staff only")
    q = payload.query.strip().rstrip(";")
    if re.search(
        r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP|CALL\s*\{|LOAD\s+CSV|FOREACH)\b", q, re.I
    ):
        raise HttpError(422, "read-only queries only")
    client = graph_client()
    try:
        client.run(f"EXPLAIN {q}")
        rows = client.run(q)
    except Exception as exc:  # noqa: BLE001
        raise HttpError(422, str(exc)[:300]) from exc
    return [
        {
            k: (v if isinstance(v, str | int | float | bool | list | dict) or v is None else str(v))
            for k, v in r.items()
        }
        for r in rows[: max(1, min(payload.limit, 1000))]
    ]
