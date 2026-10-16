"""graph: project the document, its tags and their provenance into Neo4j.

Projection (rebuild specification, section 8):

    (:Document {id, collection, title, source_uri, content_hash, language, indexed_at})
      -[:FROM]-> (:Source {uri})
      -[:IN]-> (:Collection {slug})
      -[:TAGGED {pack_version, count, confidence, stage, stage_version}]-> (:Concept {iri, ...})
      -[:MENTIONS {pack_version, count, confidence, stage, stage_version}]-> (:Entity {key, ...})
    (:Entity)-[:DEFINED_IN]->(:Pack {key})  for pack individuals
    (:Concept)-[:IN_SCHEME]->(:Pack {key})

Entity keys: the IRI for pack individuals; `<class>/<normalised label>` for
NER entities, or an HMAC of that when the collection sets `pseudonymise`
(the label then stays in Postgres only). Machine edges are replaced on every
run; `CURATED` edges (Phase 3 review) are never touched here. Spans, matched
labels and `linked_from` stay in PostgreSQL; an edge carries the count only.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

from oss_core.contracts import StageContext, Tag, WorkingDocument
from oss_pipeline.clients.neo4j import GraphClient
from oss_pipeline.stages.index import effective_tags

BASE = "https://semascope.dev/ontology/base#"


def entity_key(tag: Tag, secret: str | None) -> str:
    if tag.pack_id:
        return tag.iri
    raw = tag.iri.removeprefix(BASE)  # "<Class>/<normalised label>"
    if secret:
        return "h:" + hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    return raw


def entity_class(tag: Tag) -> str:
    if tag.pack_id:
        return "Individual"
    local = tag.iri.removeprefix(BASE)
    return local.split("/", 1)[0] if "/" in local else "Entity"


class GraphStage:
    name = "graph"
    version = "2"
    requires = ("tags",)
    provides = ("graph_written",)

    def __init__(self, client: GraphClient | None = None) -> None:
        self._client = client
        self._client_key: tuple[str, ...] | None = None  # None: a client handed in
        self._constraints_done = False

    def _graph(self, ctx: StageContext) -> GraphClient:
        """The process's Neo4j driver (the stage lives as long as its worker process), built
        again only when the connection settings change; constraints once per driver."""

        s = ctx.settings
        key = tuple(str(s.get(k) or "") for k in ("neo4j_uri", "neo4j_user", "neo4j_password"))
        if self._client is None or (self._client_key is not None and self._client_key != key):
            self.close()
            self._client = GraphClient(
                str(s["neo4j_uri"]),
                str(s.get("neo4j_user") or "neo4j"),
                str(s.get("neo4j_password") or ""),
            )
            self._client_key = key
            self._constraints_done = False
        if not self._constraints_done:
            self._client.ensure_constraints()
            self._constraints_done = True
        return self._client

    def close(self) -> None:
        if self._client is not None and self._client_key is not None:
            self._client.close()  # only a driver this stage built
            self._client = None

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        g = self._graph(ctx)
        secret = (
            str(ctx.settings.get("pseudonymise_secret") or "")
            if ctx.settings.get("pseudonymise")
            else None
        )
        now = datetime.now(UTC).isoformat()
        g.write(
            """
            UNWIND $rows AS row
            MERGE (d:Document {id: row.id})
            SET d.collection = row.collection, d.title = row.title, d.source_uri = row.uri,
                d.content_hash = row.content_hash, d.language = row.language,
                d.media_type = row.media_type, d.indexed_at = row.indexed_at
            MERGE (s:Source {uri: row.source_uri})
            MERGE (c:Collection {slug: row.collection})
            MERGE (d)-[:FROM]->(s)
            MERGE (d)-[:IN]->(c)
            WITH d
            OPTIONAL MATCH (d)-[r:TAGGED|MENTIONS]->()
            DELETE r
            """,
            [
                {
                    "id": doc.id,
                    "collection": doc.collection,
                    "title": (doc.title or "")[:500],
                    "uri": doc.uri,
                    "content_hash": doc.content_hash,
                    "language": doc.language,
                    "media_type": doc.media_type,
                    "indexed_at": now,
                    "source_uri": f"source:{doc.source_id}",
                }
            ],
        )
        concepts: list[dict[str, Any]] = []
        entities: list[dict[str, Any]] = []
        for tag in effective_tags(doc):
            count = max(1, len(tag.spans))
            if tag.kind == "concept":
                concepts.append(
                    {
                        "doc": doc.id,
                        "iri": tag.iri,
                        "label": tag.label,
                        "pack_id": tag.pack_id,
                        "pack_key": f"{tag.pack_id}@{tag.pack_version}" if tag.pack_id else None,
                        "pack_version": tag.pack_version,
                        "count": count,
                        "confidence": tag.confidence,
                        "stage": tag.stage,
                        "stage_version": tag.stage_version,
                    }
                )
            else:
                entities.append(
                    {
                        "doc": doc.id,
                        "key": entity_key(tag, secret),
                        "label": "" if secret and not tag.pack_id else tag.label,
                        "class": entity_class(tag),
                        "pack_key": f"{tag.pack_id}@{tag.pack_version}" if tag.pack_id else None,
                        "pack_version": tag.pack_version,
                        "count": count,
                        "confidence": tag.confidence,
                        "stage": tag.stage,
                        "stage_version": tag.stage_version,
                    }
                )
        if concepts:
            g.write(
                """
                UNWIND $rows AS row
                MATCH (d:Document {id: row.doc})
                MERGE (c:Concept {iri: row.iri})
                SET c.pref_label = row.label, c.pack_id = row.pack_id
                MERGE (d)-[t:TAGGED]->(c)
                SET t.count = row.count, t.confidence = row.confidence, t.stage = row.stage,
                    t.stage_version = row.stage_version, t.pack_version = row.pack_version
                WITH c, row WHERE row.pack_key IS NOT NULL
                MERGE (p:Pack {key: row.pack_key})
                MERGE (c)-[:IN_SCHEME]->(p)
                """,
                concepts,
            )
        if entities:
            g.write(
                """
                UNWIND $rows AS row
                MATCH (d:Document {id: row.doc})
                MERGE (e:Entity {key: row.key})
                SET e.label = CASE WHEN row.label <> '' THEN row.label ELSE e.label END,
                    e.class = row.class
                MERGE (d)-[m:MENTIONS]->(e)
                SET m.count = row.count, m.confidence = row.confidence, m.stage = row.stage,
                    m.stage_version = row.stage_version, m.pack_version = row.pack_version
                WITH e, row WHERE row.pack_key IS NOT NULL
                MERGE (p:Pack {key: row.pack_key})
                MERGE (e)-[:DEFINED_IN]->(p)
                """,
                entities,
            )
        doc.fields["graph_written"] = {"concepts": len(concepts), "entities": len(entities)}
        return doc
