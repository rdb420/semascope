"""validate: SHACL-check the document's metadata graph against the active packs' shapes.

The document is expressed as RDF with the base pack's terms (a Document with
its content hash, collection, source, language and tags), then validated with
pyshacl against every active pack's `shapes.ttl`. Violations never reject a
document: they are recorded on it as `shacl_violations` for the review queue.
Shapes come from the StageContext (`shapes_ttl`, concatenated by the server).
"""

from __future__ import annotations

from typing import Any

from oss_core.contracts import StageContext, WorkingDocument
from oss_pipeline.stages.base import SkipStageError

BASE = "https://semascope.dev/ontology/base#"


def document_graph(doc: WorkingDocument) -> str:
    """The document as Turtle, using the base pack's vocabulary."""

    def lit(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'

    lines = [
        "@prefix base: <https://semascope.dev/ontology/base#> .",
        "@prefix dcterms: <http://purl.org/dc/terms/> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        f"<urn:semascope:doc:{doc.id}> a base:Document ;",
        f"    base:contentHash {lit(doc.content_hash)} ;",
        f"    base:inCollection <urn:semascope:collection:{doc.collection}> ;",
        f"    base:fromSource <urn:semascope:source:{doc.source_id}> ;",
    ]
    if doc.title:
        lines.append(f"    dcterms:title {lit(doc.title[:500])} ;")
    if doc.language:
        lines.append(f"    dcterms:language {lit(doc.language)} ;")
    for i in range(len(doc.tags)):
        lines.append(f"    base:hasTag <urn:semascope:tag:{doc.id}:{i}> ;")
    lines[-1] = lines[-1].rstrip(" ;") + " ."
    lines.append(f"<urn:semascope:collection:{doc.collection}> a base:Collection .")
    lines.append(f"<urn:semascope:source:{doc.source_id}> a base:Source .")
    for i, tag in enumerate(doc.tags):
        lines.append(
            f"<urn:semascope:tag:{doc.id}:{i}> a base:Tag ; base:tagTarget <{tag.iri}> ; "
            f"base:confidence {tag.confidence:.2f} ; "
            f"base:producedByStage <urn:semascope:stage:{tag.stage}> ."
        )
        if tag.kind == "concept":
            lines.append(f"<{tag.iri}> a skos:Concept .")
    for stage in {t.stage for t in doc.tags}:
        lines.append(f"<urn:semascope:stage:{stage}> a base:StageRun .")
    return "\n".join(lines) + "\n"


class ValidateStage:
    name = "validate"
    version = "1"
    queue = "cpu"
    requires = ("tags",)
    provides = ("shacl_violations",)

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        shapes = str(ctx.settings.get("shapes_ttl") or "")
        if not shapes.strip():
            raise SkipStageError("no shapes from active packs")
        try:
            from pyshacl import validate
            from rdflib import Graph
        except ImportError as exc:
            raise SkipStageError("pyshacl is not installed") from exc
        data = Graph().parse(data=document_graph(doc), format="turtle")
        shapes_graph = Graph().parse(data=shapes, format="turtle")
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
