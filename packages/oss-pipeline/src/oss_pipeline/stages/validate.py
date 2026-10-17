"""validate: SHACL-check the document's metadata graph against the active packs' shapes.

The document is expressed as RDF with the base pack's terms (a Document with
its content hash, collection, source, language and tags), then validated with
pyshacl against every active pack's `shapes.ttl`. Violations never reject a
document: they are recorded on it as `shacl_violations` for the review queue.
Shapes come from the StageContext (`shapes_ttl`, concatenated by the server).
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from oss_core.contracts import StageContext, WorkingDocument
from oss_pipeline.stages.base import SkipStageError

if TYPE_CHECKING:
    from rdflib import Graph, URIRef

BASE = "https://semascope.dev/ontology/base#"
# what an IRI may hold as it is: RFC 3987's reserved characters, `%` (already escaped)
# and everything outside ASCII; anything else (space, `<`, `>`, `"`, `{`, `}`, `|`, `\`,
# `^`, a backtick, control characters) is percent-encoded so the IRI stays one valid term
IRI_SAFE = ":/?#[]@!$&'()*+,;=%-._~"


def _iri(value: str) -> URIRef:
    from rdflib import URIRef

    return URIRef(quote(value, safe=IRI_SAFE + "".join(c for c in value if ord(c) > 127)))


def document_rdf(doc: WorkingDocument) -> Graph:
    """The document as an RDF graph in the base pack's vocabulary.

    Built from rdflib terms, never by pasting text into Turtle: a title with a carriage
    return or quotes stays one literal, and an IRI holding `>` or a space (NER IRIs carry
    the entity's name) is percent-encoded instead of ending the term.
    """

    from rdflib import RDF, SKOS, Graph, Literal, Namespace
    from rdflib.namespace import DCTERMS

    base = Namespace(BASE)
    g = Graph()
    g.bind("base", base)
    g.bind("dcterms", DCTERMS)
    g.bind("skos", SKOS)
    node = _iri(f"urn:semascope:doc:{doc.id}")
    collection = _iri(f"urn:semascope:collection:{doc.collection}")
    source = _iri(f"urn:semascope:source:{doc.source_id}")
    g.add((node, RDF.type, base.Document))
    g.add((node, base.contentHash, Literal(doc.content_hash)))
    g.add((node, base.inCollection, collection))
    g.add((node, base.fromSource, source))
    if doc.title:
        g.add((node, DCTERMS.title, Literal(doc.title[:500])))
    if doc.language:
        g.add((node, DCTERMS.language, Literal(doc.language)))
    g.add((collection, RDF.type, base.Collection))
    g.add((source, RDF.type, base.Source))
    for i, tag in enumerate(doc.tags):
        tag_node = _iri(f"urn:semascope:tag:{doc.id}:{i}")
        stage = _iri(f"urn:semascope:stage:{tag.stage}")
        target = _iri(tag.iri)
        g.add((node, base.hasTag, tag_node))
        g.add((tag_node, RDF.type, base.Tag))
        g.add((tag_node, base.tagTarget, target))
        g.add((tag_node, base.confidence, Literal(Decimal(f"{tag.confidence:.2f}"))))
        g.add((tag_node, base.producedByStage, stage))
        g.add((stage, RDF.type, base.StageRun))
        if tag.kind == "concept":
            g.add((target, RDF.type, SKOS.Concept))
    return g


@lru_cache(maxsize=4)
def _shapes(turtle: str) -> Graph:
    """The packs' shapes, parsed once per process for each set of them."""

    from rdflib import Graph

    return Graph().parse(data=turtle, format="turtle")


def document_graph(doc: WorkingDocument) -> str:
    """The document as Turtle, using the base pack's vocabulary."""

    return str(document_rdf(doc).serialize(format="turtle"))


class ValidateStage:
    name = "validate"
    version = "2"
    requires = ("tags",)
    provides = ("shacl_violations",)

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        shapes = str(ctx.settings.get("shapes_ttl") or "")
        if not shapes.strip():
            raise SkipStageError("no shapes from active packs")
        try:
            from pyshacl import validate
        except ImportError as exc:
            raise SkipStageError("pyshacl is not installed") from exc
        data = document_rdf(doc)
        shapes_graph = _shapes(shapes)
        conforms, _report_graph, _text = validate(
            data, shacl_graph=shapes_graph, inference="none", abort_on_first=False
        )
        violations: list[dict[str, Any]] = []
        if not conforms:
            from rdflib import Namespace

            sh = Namespace("http://www.w3.org/ns/shacl#")
            for result in _report_graph.subjects(sh.resultSeverity, None):
                violations.append(
                    {
                        "focus": str(_report_graph.value(result, sh.focusNode) or ""),
                        "path": str(_report_graph.value(result, sh.resultPath) or ""),
                        "message": str(_report_graph.value(result, sh.resultMessage) or ""),
                        "severity": str(
                            _report_graph.value(result, sh.resultSeverity) or ""
                        ).rsplit("#", 1)[-1],
                    }
                )
        doc.fields["shacl_conforms"] = conforms
        doc.fields["shacl_violations"] = violations[:100]
        return doc
