"""Export pack deltas as a bundle fragment the ontology-skill workflow can ingest.

The fragment is a directory with `taxonomy.delta.ttl` (new concepts and
labels as SKOS triples) and `model.delta.yaml` (terms with status `draft`
and support `proposed`, plus the curators' reasons as evidence notes).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from rdflib import RDF, RDFS, SKOS, Graph, Literal, URIRef

from oss_server.knowledge.models import Concept, Pack, PackDelta

_SLUG = re.compile(r"[^a-z0-9]+")
# a language tag (BCP 47 shape), and an absolute IRI with nothing Turtle could misread
_LANG = re.compile(r"^[a-zA-Z]{1,8}(-[a-zA-Z0-9]{1,8})*$")
_IRI = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:[^\s<>\"{}|\\^`]+$")


def _slug(value: str) -> str:
    return _SLUG.sub("-", value.lower()).strip("-")[:64] or "x"


def valid_iri(value: object) -> bool:
    """An absolute IRI that serialises as itself (no spaces, angle brackets, quotes...)."""

    return isinstance(value, str) and bool(_IRI.fullmatch(value))


def valid_lang(value: object) -> bool:
    return isinstance(value, str) and bool(_LANG.fullmatch(value))


def _lang(params: dict[str, Any]) -> str:
    lang = params.get("lang") or "en"
    return str(lang) if valid_lang(lang) else "en"


def _literal(value: object, lang: str) -> Literal:
    return Literal(str(value), lang=lang)


def build_delta(pack: Pack, deltas: list[PackDelta]) -> dict[str, str]:
    """Return {filename: content} for the fragment.

    The Turtle is written by rdflib from terms built here, so a curator's text
    (labels, definitions, IRIs) can never add triples: literals are escaped by the
    serialiser, an IRI that is not a plain absolute IRI is left out (and reported
    in the notes), and a new concept's IRI is made from slugs.
    """

    g = Graph()
    g.bind("skos", SKOS)
    g.bind("rdfs", RDFS)
    terms: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    base = pack.base_iri.rstrip("#/")
    n = 0
    for d in deltas:
        p = d.params
        lang = _lang(p)
        if d.kind == PackDelta.Kind.ADD_LABEL:
            if not valid_iri(p.get("iri")):
                notes.append({"skipped": d.pk, "reason": "iri is not a valid IRI"})
                continue
            pred = SKOS.hiddenLabel if p.get("label_type") == "hidden" else SKOS.altLabel
            g.add((URIRef(str(p["iri"])), pred, _literal(p["label"], lang)))
        elif d.kind == PackDelta.Kind.NEW_CONCEPT:
            n += 1
            scheme_slug = _slug(str(p.get("scheme_slug") or "concepts"))
            scheme = Concept.objects.filter(
                pack=pack, kind=Concept.Kind.SCHEME, scheme_slug=p.get("scheme_slug")
            ).first()
            iri = URIRef(f"{base}/{scheme_slug}#{_slug(str(p['label']))}")
            g.add((iri, RDF.type, SKOS.Concept))
            g.add((iri, SKOS.prefLabel, _literal(p["label"], lang)))
            if scheme and valid_iri(scheme.iri):
                g.add((iri, SKOS.inScheme, URIRef(scheme.iri)))
            if p.get("broader"):
                if valid_iri(p["broader"]):
                    g.add((iri, SKOS.broader, URIRef(str(p["broader"]))))
                else:
                    notes.append({"iri": str(iri), "skipped_broader": str(p["broader"])[:500]})
            if p.get("definition"):
                g.add((iri, SKOS.definition, _literal(p["definition"], lang)))
            terms.append(
                {
                    "id": f"TERM-DRAFT-{n:03d}",
                    "iri": f"<{iri}>",
                    "kind": "skos_concept",
                    "label": p["label"],
                    "definition": p.get("definition") or "",
                    "status": "draft",
                    "support": "proposed",
                    "evidence": [],
                    "rationale": d.reason or "proposed by a curator in Semascope",
                }
            )
        elif d.kind == PackDelta.Kind.RETIRE_LABEL:
            notes.append({"iri": p.get("iri"), "retire_label": p.get("label"), "reason": d.reason})
        elif d.kind == PackDelta.Kind.NOTE:
            notes.append({"iri": p.get("iri"), "note": p.get("text"), "reason": d.reason})
    model = {
        "delta_for": {
            "pack_id": pack.pack_id,
            "version": pack.version,
            "generated_at": datetime.now(UTC).isoformat(),
        },
        "terms": terms,
        "notes": notes,
    }
    return {
        "taxonomy.delta.ttl": g.serialize(format="turtle"),
        "model.delta.yaml": yaml.safe_dump(model, sort_keys=False, allow_unicode=True),
    }


def write_delta(pack: Pack, deltas: list[PackDelta], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, content in build_delta(pack, deltas).items():
        (out_dir / name).write_text(content, encoding="utf-8")
    return out_dir
