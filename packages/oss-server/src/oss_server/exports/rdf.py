"""Documents with their tags and curator decisions as RDF (Turtle or JSON-LD).

The vocabulary is the base pack's (`packs/base/ontology.ttl`), so the export
answers the base pack's competency questions:

    <urn:semascope:doc:ID> a base:Document ;
        dcterms:title, dcterms:identifier, dcterms:source <original URI>,
        dcterms:format, dcterms:language, dcterms:modified, prov:generatedAtTime ;
        base:contentHash ; base:inCollection <urn:semascope:collection:SLUG> ;
        base:fromSource <urn:semascope:source:N> ; base:hasPage ... ;
        base:hasTag <urn:semascope:tag:ID:H> ; rdfs:seeAlso <its page in the UI> .
    <urn:semascope:tag:ID:H> a base:Tag ; base:tagTarget <concept or entity> ;
        base:confidence ; base:producedByPack <urn:semascope:pack:P@V> ;
        base:producedByStage <urn:semascope:stage:S:V> ; prov:wasDerivedFrom <NER entity> .

Tags are the curated view (rejected tags left out, accepted additions and
links applied), as in search and the graph. A stage is a `base:StageRun` (a
`prov:Activity`) titled with its name. Notes and decisions are `oa:Annotation`s
in the W3C Web Annotation vocabulary (see `w3c.py`) whose target is the
document's node. NER entities are named as `ids.target_iri` says, so a
pseudonymising collection exports keys, not names.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from rdflib import RDF, RDFS, SKOS, XSD, BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, OWL, PROV

from oss_pipeline.provenance import curated_tags
from oss_server.catalogue.models import Document
from oss_server.exports import ids

BASE = Namespace(ids.BASE)
OA = Namespace("http://www.w3.org/ns/oa#")
AS = Namespace("http://www.w3.org/ns/activitystreams#")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")
DC = Namespace("http://purl.org/dc/elements/1.1/")

PREFIXES: dict[str, str] = {
    "rdf": str(RDF),
    "base": ids.BASE,
    "dcterms": str(DCTERMS),
    "prov": str(PROV),
    "oa": str(OA),
    "as": str(AS),
    "foaf": str(FOAF),
    "dc": str(DC),
    "rdfs": str(RDFS),
    "skos": str(SKOS),
    "owl": str(OWL),
    "xsd": str(XSD),
}
JSONLD_CONTEXT: dict[str, str] = dict(PREFIXES)
MAX_PAGES = 2000  # page nodes per document; longer documents keep their page count only

TURTLE = "text/turtle; charset=utf-8"
JSONLD = "application/ld+json"
NQUADS = "application/n-quads"
FORMATS = {"turtle": (TURTLE, "ttl"), "jsonld": (JSONLD, "jsonld"), "nquads": (NQUADS, "nq")}


def new_graph(identifier: str | None = None) -> Graph:
    """An empty graph with the export's prefixes; `identifier` names it (the graph of an
    N-Quads line)."""

    g = Graph(identifier=URIRef(identifier)) if identifier else Graph()
    for prefix, ns in PREFIXES.items():
        g.bind(prefix, ns, override=True, replace=True)
    return g


def _ref(value: str) -> URIRef:
    return URIRef(ids.safe_iri(value))


def add_document(
    g: Graph,
    doc: Document,
    *,
    site: str,
    secret: str | None,
    annotations: Iterable[dict[str, Any]] = (),
) -> URIRef:
    """Add one document, its tags in force and its annotations to the graph."""

    d = URIRef(ids.doc_iri(doc.id))
    g.add((d, RDF.type, BASE.Document))
    g.add((d, DCTERMS.identifier, Literal(doc.id)))
    if doc.title:
        g.add((d, DCTERMS.title, Literal(doc.title)))
    if doc.uri:
        g.add((d, DCTERMS.source, _ref(doc.uri)))
    if doc.media_type:
        g.add((d, DCTERMS["format"], Literal(doc.media_type)))
    if doc.language:
        g.add((d, DCTERMS.language, Literal(doc.language)))
    if doc.modified_at:
        g.add((d, DCTERMS.modified, Literal(doc.modified_at.isoformat(), datatype=XSD.dateTime)))
    if doc.indexed_at:
        g.add((d, PROV.generatedAtTime, Literal(doc.indexed_at.isoformat(), datatype=XSD.dateTime)))
    if doc.content_hash:
        g.add((d, BASE.contentHash, Literal(doc.content_hash)))
    g.add((d, RDFS.seeAlso, URIRef(ids.page_url(site, doc.id))))

    coll = URIRef(ids.collection_iri(doc.collection.slug))
    g.add((d, BASE.inCollection, coll))
    g.add((coll, RDF.type, BASE.Collection))
    g.add((coll, RDFS.label, Literal(doc.collection.name or doc.collection.slug)))
    src = URIRef(ids.source_iri(doc.source_id))
    g.add((d, BASE.fromSource, src))
    g.add((src, RDF.type, BASE.Source))

    pages = len(doc.pages or []) or int((doc.metadata or {}).get("page_count") or 0)
    for n in range(1, min(pages, MAX_PAGES) + 1):
        p = URIRef(ids.page_iri(doc.id, n))
        g.add((d, BASE.hasPage, p))
        g.add((p, RDF.type, BASE.Page))
        g.add((p, BASE.pageNumber, Literal(n, datatype=XSD.positiveInteger)))

    for tag in curated_tags(doc.tags or [], (doc.fields or {}).get("curated") or {}):
        target, hidden = ids.target_iri(tag.iri, secret)
        t = URIRef(ids.tag_iri(doc.id, target))
        tnode = URIRef(target)
        g.add((d, BASE.hasTag, t))
        g.add((t, RDF.type, BASE.Tag))
        g.add((t, BASE.tagTarget, tnode))
        g.add((t, BASE.confidence, Literal(f"{tag.confidence:.4f}", datatype=XSD.decimal)))
        if tag.kind == "concept":
            g.add((tnode, RDF.type, SKOS.Concept))
            if tag.label:
                g.add((tnode, SKOS.prefLabel, Literal(tag.label)))
        else:
            if ids.is_ner_entity(tag.iri):
                g.add((tnode, RDF.type, BASE[ids.entity_class(tag.iri)]))
            if tag.label and not hidden:
                g.add((tnode, RDFS.label, Literal(tag.label)))
        if tag.pack_id:
            pk = URIRef(ids.pack_iri(tag.pack_id, tag.pack_version))
            g.add((t, BASE.producedByPack, pk))
            g.add((pk, RDF.type, BASE.Pack))
            g.add((pk, DCTERMS.identifier, Literal(tag.pack_id)))
            if tag.pack_version:
                g.add((pk, BASE.packVersion, Literal(tag.pack_version)))
        st = URIRef(ids.stage_iri(tag.stage, tag.stage_version))
        g.add((t, BASE.producedByStage, st))
        g.add((st, RDF.type, BASE.StageRun))
        g.add((st, DCTERMS.title, Literal(tag.stage)))
        if tag.stage_version:
            g.add((st, OWL.versionInfo, Literal(tag.stage_version)))
        if tag.linked_from is not None:
            origin, _ = ids.target_iri(tag.linked_from.iri, secret)
            g.add((t, PROV.wasDerivedFrom, URIRef(origin)))

    for anno in annotations:
        add_annotation(g, anno, target=d)
    return d


_MOTIVATIONS = {
    "assessing",
    "bookmarking",
    "classifying",
    "commenting",
    "describing",
    "editing",
    "highlighting",
    "identifying",
    "linking",
    "moderating",
    "questioning",
    "replying",
    "tagging",
}


def _agent(g: Graph, value: dict[str, Any]) -> BNode | URIRef:
    node: BNode | URIRef = URIRef(ids.safe_iri(value["id"])) if value.get("id") else BNode()
    kind = value.get("type")
    if kind == "Person":
        g.add((node, RDF.type, FOAF.Person))
    elif kind == "Software":
        g.add((node, RDF.type, AS.Application))
    if value.get("name"):
        g.add((node, FOAF.name, Literal(value["name"])))
    if value.get("nickname"):
        g.add((node, FOAF.nick, Literal(value["nickname"])))
    return node


def add_annotation(g: Graph, anno: dict[str, Any], *, target: URIRef | None = None) -> URIRef:
    """A Web Annotation dict (as `w3c.py` writes them) as oa: triples.

    `target`, when given, replaces the target's source (the RDF export points
    annotations at the document's node rather than at its original URI).
    """

    a = URIRef(anno["id"])
    g.add((a, RDF.type, OA.Annotation))
    motivation = anno.get("motivation")
    if motivation in _MOTIVATIONS:
        g.add((a, OA.motivatedBy, OA[motivation]))
    for key, pred in (("created", DCTERMS.created), ("modified", DCTERMS.modified)):
        if anno.get(key):
            g.add((a, pred, Literal(str(anno[key]), datatype=XSD.dateTime)))
    if anno.get("creator"):
        g.add((a, DCTERMS.creator, _agent(g, anno["creator"])))
    if anno.get("generator"):
        g.add((a, AS.generator, _agent(g, anno["generator"])))
    if anno.get("via"):
        g.add((a, OA.via, Literal(str(anno["via"]))))
    for body in anno.get("body") or []:
        b = BNode()
        g.add((a, OA.hasBody, b))
        if body.get("type") == "TextualBody":
            g.add((b, RDF.type, OA.TextualBody))
            g.add((b, RDF.value, Literal(str(body.get("value", "")))))
            if body.get("format"):
                g.add((b, DC["format"], Literal(body["format"])))
        else:
            g.add((b, RDF.type, OA.SpecificResource))
            g.add((b, OA.hasSource, URIRef(str(body["source"]))))
        if body.get("purpose") in _MOTIVATIONS:
            g.add((b, OA.hasPurpose, OA[body["purpose"]]))
    tgt = anno.get("target") or {}
    t = BNode()
    g.add((a, OA.hasTarget, t))
    g.add((t, RDF.type, OA.SpecificResource))
    g.add((t, OA.hasSource, target if target is not None else _ref(str(tgt.get("source", "")))))
    if tgt.get("scope"):
        g.add((t, OA.hasScope, URIRef(str(tgt["scope"]))))
    for sel in tgt.get("selector") or []:
        s = BNode()
        g.add((t, OA.hasSelector, s))
        if sel.get("type") == "TextPositionSelector":
            g.add((s, RDF.type, OA.TextPositionSelector))
            g.add((s, OA.start, Literal(int(sel["start"]), datatype=XSD.nonNegativeInteger)))
            g.add((s, OA.end, Literal(int(sel["end"]), datatype=XSD.nonNegativeInteger)))
        elif sel.get("type") == "TextQuoteSelector":
            g.add((s, RDF.type, OA.TextQuoteSelector))
            for key in ("exact", "prefix", "suffix"):
                if sel.get(key):
                    g.add((s, OA[key], Literal(str(sel[key]))))
    return a


# --- streaming serialisation -------------------------------------------------------------------


def turtle_header() -> str:
    return "".join(f"@prefix {p}: <{ns}> .\n" for p, ns in PREFIXES.items()) + "\n"


def turtle_body(g: Graph) -> str:
    """The graph as Turtle without the prefix lines the header already declared."""

    known = turtle_header().splitlines()
    lines = g.serialize(format="turtle").splitlines()
    return "\n".join(line for line in lines if line not in known).strip() + "\n\n"


def jsonld_nodes(g: Graph) -> list[dict[str, Any]]:
    """The graph's nodes, compacted with the export's context, sorted by id."""

    out = json.loads(g.serialize(format="json-ld", context=JSONLD_CONTEXT, auto_compact=True))
    if isinstance(out, list):
        nodes = out
    elif "@graph" in out:
        nodes = list(out["@graph"])
    else:
        nodes = [{k: v for k, v in out.items() if k != "@context"}]
    return sorted(nodes, key=lambda n: str(n.get("@id", "")))


def nquads_body(g: Graph) -> str:
    """The graph's triples as N-Quads lines in the graph its identifier names (a graph
    without an IRI for a name writes N-Triples lines, which are N-Quads of the default
    graph)."""

    name = g.identifier
    lines = g.serialize(format="nt").splitlines()
    if not isinstance(name, URIRef):
        return "".join(line + "\n" for line in lines if line.strip())
    suffix = f" {name.n3()} ."
    return "".join(line[:-2] + suffix + "\n" for line in lines if line.endswith(" ."))


def stream(graphs: Iterable[Graph], fmt: str) -> Iterator[str]:
    """One document's graph after another as one Turtle, JSON-LD or N-Quads document (in
    N-Quads, each document's triples are in the document's own named graph)."""

    if fmt == "nquads":
        for g in graphs:
            yield nquads_body(g)
        return
    if fmt == "turtle":
        yield turtle_header()
        for g in graphs:
            yield turtle_body(g)
        return
    yield '{"@context": ' + json.dumps(JSONLD_CONTEXT) + ', "@graph": ['
    first = True
    for g in graphs:
        for node in jsonld_nodes(g):
            yield ("\n" if first else ",\n") + json.dumps(node, ensure_ascii=False)
            first = False
    yield "\n]}\n"


def document_graphs(
    docs: Iterable[Document],
    *,
    site: str,
    secret: Callable[[Document], str | None],
    annotations: Callable[[list[Document]], dict[str, list[dict[str, Any]]]],
    batch: int = 100,
) -> Iterator[Graph]:
    """One graph per document, loading annotations for `batch` documents at a time."""

    chunk: list[Document] = []

    def flush() -> Iterator[Graph]:
        found = annotations(chunk)
        for doc in chunk:
            g = new_graph(ids.doc_iri(doc.id))
            add_document(g, doc, site=site, secret=secret(doc), annotations=found.get(doc.id, []))
            yield g

    for doc in docs:
        chunk.append(doc)
        if len(chunk) >= batch:
            yield from flush()
            chunk = []
    if chunk:
        yield from flush()
