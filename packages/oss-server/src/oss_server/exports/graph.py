"""A collection's knowledge graph as RDF: its documents, the concepts and entities they hold
in force, and how those concepts sit in their schemes.

Lighter than the document export (no tags as nodes, no provenance, no notes):
one `dcterms:subject` link per document and concept or entity, which is what a
triplestore or a graph tool needs to walk documents through what they share.

    <urn:semascope:doc:ID> a base:Document ; dcterms:title ; dcterms:identifier ;
        base:inCollection <urn:semascope:collection:SLUG> ; dcterms:subject <target> .
    <concept> a skos:Concept ; skos:prefLabel ; skos:inScheme ; skos:broader .
    <individual> a <its pack class> ; rdfs:label .
    <urn:semascope:entity:Person/...> a base:Person ; rdfs:label .

Each concept and entity is described once per export. Under pseudonymisation
named entities are the graph stage's HMAC keys without labels.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from rdflib import RDF, RDFS, SKOS, Graph, Literal, URIRef
from rdflib.namespace import DCTERMS

from oss_pipeline.provenance import curated_tags
from oss_server.catalogue.models import Collection, Document
from oss_server.exports import ids
from oss_server.exports.rdf import BASE, new_graph

MAX_DESCRIBED = 200_000  # concepts and entities described per export


def _concepts(iris: set[str]) -> dict[str, Any]:
    from oss_server.knowledge.models import Concept, Pack

    if not iris:
        return {}
    rows = Concept.objects.filter(iri__in=list(iris), pack__status=Pack.Status.ACTIVE)
    return {c.iri: c for c in rows}


def graph_batches(
    collection: Collection,
    documents: Iterable[Document],
    *,
    secret: str | None,
    batch: int = 200,
) -> Iterator[Graph]:
    """One graph per batch of documents, named after the collection."""

    described: set[str] = set()
    coll = URIRef(ids.collection_iri(collection.slug))
    chunk: list[Document] = []

    def flush(first: bool) -> Graph:
        g = new_graph(ids.collection_iri(collection.slug))
        if first:
            g.add((coll, RDF.type, BASE.Collection))
            g.add((coll, RDFS.label, Literal(collection.name or collection.slug)))
        tagged = [
            (doc, curated_tags(doc.tags or [], (doc.fields or {}).get("curated") or {}))
            for doc in chunk
        ]
        concepts = _concepts({t.iri for _, tags in tagged for t in tags if t.iri not in described})
        for doc, tags in tagged:
            d = URIRef(ids.doc_iri(doc.id))
            g.add((d, RDF.type, BASE.Document))
            g.add((d, DCTERMS.identifier, Literal(doc.id)))
            if doc.title:
                g.add((d, DCTERMS.title, Literal(doc.title)))
            g.add((d, BASE.inCollection, coll))
            for tag in tags:
                target, hidden = ids.target_iri(tag.iri, secret)
                node = URIRef(target)
                g.add((d, DCTERMS.subject, node))
                if target in described or len(described) >= MAX_DESCRIBED:
                    continue
                described.add(target)
                concept = concepts.get(tag.iri)
                if tag.kind == "concept":
                    g.add((node, RDF.type, SKOS.Concept))
                    if tag.label:
                        g.add((node, SKOS.prefLabel, Literal(tag.label)))
                    if concept is not None:
                        if concept.scheme:
                            g.add((node, SKOS.inScheme, URIRef(ids.safe_iri(concept.scheme))))
                        for broader in concept.broader or []:
                            g.add((node, SKOS.broader, URIRef(ids.safe_iri(str(broader)))))
                    continue
                if ids.is_ner_entity(tag.iri):
                    g.add((node, RDF.type, BASE[ids.entity_class(tag.iri)]))
                elif concept is not None and concept.type_iri:
                    g.add((node, RDF.type, URIRef(ids.safe_iri(str(concept.type_iri)))))
                if tag.label and not hidden:
                    g.add((node, RDFS.label, Literal(tag.label)))
        return g

    first = True
    for doc in documents:
        chunk.append(doc)
        if len(chunk) >= batch:
            yield flush(first)
            first = False
            chunk = []
    if chunk or first:
        yield flush(first)
