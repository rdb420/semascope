"""Neo4j client for the graph projection: constraints, batched merges, read queries.

One driver per process. Writes are `UNWIND` batches with MERGE on the key
property, retried on transient errors by the driver's managed transactions.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from neo4j import Driver, GraphDatabase

CONSTRAINTS = {
    "Document": "id",
    "Chunk": "id",
    "Concept": "iri",
    "Entity": "key",
    "Pack": "key",
    "Source": "uri",
    "Collection": "slug",
}


class GraphClient:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self._driver: Driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        self._driver.close()

    def verify(self) -> None:
        self._driver.verify_connectivity()

    def ensure_constraints(self) -> None:
        with self._driver.session() as session:
            for label, key in CONSTRAINTS.items():
                session.run(
                    f"CREATE CONSTRAINT {label.lower()}_{key}_unique IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.{key} IS UNIQUE"
                )
            session.run("CREATE INDEX entity_label IF NOT EXISTS FOR (n:Entity) ON (n.label)")
            session.run(
                "CREATE INDEX document_collection IF NOT EXISTS FOR (n:Document) ON (n.collection)"
            )

    def write(self, cypher: str, rows: Iterable[dict[str, Any]], *, batch: int = 500) -> int:
        """Run `cypher` with `$rows` in batches; the statement starts with UNWIND $rows AS row."""

        rows = list(rows)
        total = 0
        with self._driver.session() as session:
            for i in range(0, len(rows), batch):
                chunk = rows[i : i + batch]

                def _write(tx: Any, rows_chunk: list[dict[str, Any]] = chunk) -> None:
                    tx.run(cypher, rows=rows_chunk).consume()

                session.execute_write(_write)
                total += len(chunk)
        return total

    def run(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        with self._driver.session() as session:
            result = session.execute_read(lambda tx: [r.data() for r in tx.run(cypher, **params)])
        return list(result)

    def execute(self, cypher: str, **params: Any) -> None:
        with self._driver.session() as session:
            session.execute_write(lambda tx: tx.run(cypher, **params).consume())
