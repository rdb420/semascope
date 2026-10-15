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

from oss_server.knowledge.models import Concept, Pack, PackDelta

_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(value: str) -> str:
    return _SLUG.sub("-", value.lower()).strip("-")[:64] or "x"


def _ttl_literal(value: str, lang: str) -> str:
    text = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"@{lang}' if lang else f'"{text}"'


def build_delta(pack: Pack, deltas: list[PackDelta]) -> dict[str, str]:
    """Return {filename: content} for the fragment."""

    ttl: list[str] = [
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "",
    ]
    terms: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    n = 0
    for d in deltas:
        p = d.params
        if d.kind == PackDelta.Kind.ADD_LABEL:
            pred = "skos:hiddenLabel" if p.get("label_type") == "hidden" else "skos:altLabel"
            ttl.append(
                f"<{p['iri']}> {pred} {_ttl_literal(str(p['label']), str(p.get('lang') or 'en'))} ."
            )
        elif d.kind == PackDelta.Kind.NEW_CONCEPT:
            n += 1
            scheme = Concept.objects.filter(
                pack=pack, kind=Concept.Kind.SCHEME, scheme_slug=p.get("scheme_slug")
            ).first()
            scheme_part = p.get("scheme_slug") or "concepts"
            iri = f"{pack.base_iri.rstrip('#/')}/{scheme_part}#{_slug(str(p['label']))}"
            lang = str(p.get("lang") or "en")
            lines = [
                f"<{iri}> a skos:Concept ;",
                f"    skos:prefLabel {_ttl_literal(str(p['label']), lang)} ;",
            ]
            if scheme:
                lines.append(f"    skos:inScheme <{scheme.iri}> ;")
            if p.get("broader"):
                lines.append(f"    skos:broader <{p['broader']}> ;")
            if p.get("definition"):
                lines.append(f"    skos:definition {_ttl_literal(str(p['definition']), lang)} ;")
            lines[-1] = lines[-1].rstrip(" ;") + " ."
            ttl.extend(lines)
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
        "taxonomy.delta.ttl": "\n".join(ttl) + "\n",
        "model.delta.yaml": yaml.safe_dump(model, sort_keys=False, allow_unicode=True),
    }


def write_delta(pack: Pack, deltas: list[PackDelta], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, content in build_delta(pack, deltas).items():
        (out_dir / name).write_text(content, encoding="utf-8")
    return out_dir
