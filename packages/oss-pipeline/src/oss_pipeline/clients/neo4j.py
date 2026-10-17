"""Neo4j client for the graph projection: constraints, batched merges, read queries.

One driver per client; the graph stage keeps its client for the life of the
worker process. Writes are `UNWIND` batches with MERGE on the key
property, retried on transient errors by the driver's managed transactions.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from neo4j import Driver, GraphDatabase
from neo4j.exceptions import ClientError

CONSTRAINTS = {
    "Document": "id",
    "Chunk": "id",
    "Concept": "iri",
    "Entity": "key",
    "Pack": "key",
    "Source": "uri",
    "Collection": "slug",
}


_CONSTRAINED: set[str] = set()


def uri_key(driver: Driver) -> str:
    return str(id(driver))


class GraphClient:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self._driver: Driver = GraphDatabase.driver(
            uri, auth=(user, password), notifications_min_severity="WARNING"
        )

    def close(self) -> None:
        self._driver.close()

    def verify(self) -> None:
        self._driver.verify_connectivity()

    def ensure_constraints(self) -> None:
        """Create the constraints once per process; later calls return at once."""

        if uri_key(self._driver) in _CONSTRAINED:
            return
        statements = [
            f"CREATE CONSTRAINT {label.lower()}_{key}_unique IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n.{key} IS UNIQUE"
            for label, key in CONSTRAINTS.items()
        ]
        statements += [
            "CREATE INDEX entity_label IF NOT EXISTS FOR (n:Entity) ON (n.label)",
            "CREATE INDEX document_collection IF NOT EXISTS FOR (n:Document) ON (n.collection)",
        ]
        with self._driver.session() as session:
            for statement in statements:
                try:
                    session.run(statement).consume()
                except ClientError as exc:
                    # two worker processes creating the same index at once on a new database:
                    # `IF NOT EXISTS` does not cover the race, and the other one won
                    if "AlreadyExists" not in str(exc.code):
                        raise
        _CONSTRAINED.add(uri_key(self._driver))  # only once they are all in place

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
