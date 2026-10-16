"""A plain list of names as a pack: one SKOS scheme, one concept per line (LIST-05).

The list becomes a minimal but complete bundle on the data volume, which then
imports like any other pack (validation, release manifest, competency question,
diff and confirmation, a job), so a list needs no second import path:

    Label | another name | another name
    # a comment; blank lines are skipped

The first name on a line is the preferred label, the others alternative labels.
The pack is a `draft`, its terms `external` (the operator vouches for the list),
and it tags text with every concept, like a SKOS pack that declares no
`tag_schemes`.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from rdflib import OWL, RDF, RDFS, SKOS, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS

from oss_core.pack import load_pack, write_manifest
from oss_server.knowledge.importer import slugify

MAX_TERMS = 20_000
MAX_NAMES_PER_LINE = 50
_LANG = re.compile(r"^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{1,8})*$")
_PACK_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}$")


class ListError(ValueError):
    pass


def parse_list(text: str) -> list[list[str]]:
    """Lines of names: [preferred, alternative…]; blank and `#` lines skipped, and a line
    whose preferred name came before adds its other names to that line."""

    out: list[list[str]] = []
    first: dict[str, list[str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names: list[str] = []
        for raw in line.split("|")[:MAX_NAMES_PER_LINE]:
            name = " ".join(raw.split())[:500]
            if len(name) >= 2 and name not in names:
                names.append(name)
        if not names:
            continue
        same = first.get(names[0].casefold())
        if same is not None:
            same.extend(n for n in names[1:] if n not in same)
            continue
        first[names[0].casefold()] = names
        out.append(names)
    if not out:
        raise ListError("the list has no names (two characters or more)")
    if len(out) > MAX_TERMS:
        raise ListError(f"the list has {len(out)} lines; at most {MAX_TERMS}")
    return out


def list_root() -> Path:
    from oss_core.config import get_settings

    return get_settings().semascope_data_dir / "packs" / "lists"


def write_list_bundle(
    *,
    pack_id: str,
    version: str,
    title: str,
    scheme: str,
    text: str,
    lang: str = "en",
    licence: str = "",
    source: str = "",
    root: Path | None = None,
) -> Path:
    """Write the bundle for a list and return its directory (under the data volume)."""

    if not _PACK_ID.fullmatch(pack_id):
        raise ListError("pack_id: lower-case letters, digits, hyphens and underscores, 1-64")
    if not _VERSION.fullmatch(version):
        raise ListError("version: 1-32 characters from letters, digits and . + _ -")
    if not _LANG.fullmatch(lang):
        raise ListError("lang: a language tag such as en or pt-BR")
    title = " ".join(title.split())[:200] or pack_id
    scheme = " ".join(scheme.split())[:200] or title
    lines = parse_list(text)
    out = (root or list_root()) / pack_id / version
    out.mkdir(parents=True, exist_ok=True)
    (out / "queries").mkdir(exist_ok=True)

    base = f"https://lists.semascope.invalid/{pack_id}#"
    ns = Namespace(base)
    scheme_iri = ns["scheme"]
    today = datetime.now(UTC).date().isoformat()

    onto = Graph()
    onto.bind("owl", OWL)
    onto.bind("dcterms", DCTERMS)
    onto_iri = URIRef(base.rstrip("#"))
    onto.add((onto_iri, RDF.type, OWL.Ontology))
    onto.add((onto_iri, RDFS.label, Literal(title, lang=lang)))
    onto.add((onto_iri, DCTERMS.title, Literal(title, lang=lang)))
    onto.serialize(out / "ontology.ttl", format="turtle")

    tax = Graph()
    tax.bind("skos", SKOS)
    tax.bind("list", ns)
    tax.add((scheme_iri, RDF.type, SKOS.ConceptScheme))
    tax.add((scheme_iri, SKOS.prefLabel, Literal(scheme, lang=lang)))
    terms: list[dict[str, Any]] = []
    used: set[str] = set()
    for i, names in enumerate(lines, 1):
        slug = slugify(names[0])
        unique, n = slug, 1
        while unique in used or unique == "scheme":
            n += 1
            unique = f"{slug}-{n}"
        used.add(unique)
        concept = ns[unique]
        tax.add((concept, RDF.type, SKOS.Concept))
        tax.add((concept, SKOS.inScheme, scheme_iri))
        tax.add((concept, SKOS.topConceptOf, scheme_iri))
        tax.add((scheme_iri, SKOS.hasTopConcept, concept))
        tax.add((concept, SKOS.prefLabel, Literal(names[0], lang=lang)))
        for alt in names[1:]:
            tax.add((concept, SKOS.altLabel, Literal(alt, lang=lang)))
        terms.append(
            {
                "id": f"TERM-{i:05d}",
                "iri": f"<{concept}>",
                "kind": "skos_concept",
                "label": names[0],
                "definition": "",
                "status": "accepted",
                "support": "external",
                "evidence": ["EV-001"],
                "rationale": "from a list the operator imported",
            }
        )
    tax.serialize(out / "taxonomy.ttl", format="turtle")
    (out / "shapes.ttl").write_text(
        "@prefix sh: <http://www.w3.org/ns/shacl#> .\n", encoding="utf-8"
    )
    (out / "instances.ttl").write_text(
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n", encoding="utf-8"
    )

    requirements = {
        "ontology": {
            "id": pack_id,
            "title": title,
            "base_iri": base,
            "version": version,
            "status": "draft",
            "created": today,
            "modified": today,
            "creator": "Semascope list import",
            "licence": licence or None,
        },
        "scope": {"inclusions": [f"the names of the list {title!r}"], "exclusions": []},
        "competency_questions": [
            {
                "id": "CQ-001",
                "question": "Which names does the list hold?",
                "priority": "core",
                "purpose": "retrieval",
                "status": "active",
            }
        ],
    }
    _yaml(out / "requirements.yaml", requirements)
    _yaml(
        out / "sources.yaml",
        {
            "sources": [
                {
                    "id": "SRC-001",
                    "title": source or f"list {title}",
                    "source_type": "list",
                    "authority": "operator",
                    "status": "current",
                    "date": today,
                }
            ]
        },
    )
    evidence = {
        "id": "EV-001",
        "source_id": "SRC-001",
        "locator": {},
        "claim": f"The list names {len(lines)} terms.",
        "support": "explicit",
        "supports": [t["id"] for t in terms[:50]],
        "confidence": 1.0,
        "notes": None,
    }
    (out / "evidence.jsonl").write_text(json.dumps(evidence) + "\n", encoding="utf-8")
    _yaml(out / "decisions.yaml", {"decisions": []})
    _yaml(out / "model.yaml", {"terms": terms, "axioms": []})
    (out / "queries" / "cq-001.rq").write_text(
        "PREFIX skos: <http://www.w3.org/2004/02/skos/core#>\n"
        "SELECT ?concept WHERE { ?concept a skos:Concept }\n",
        encoding="utf-8",
    )
    _yaml(
        out / "queries" / "manifest.yaml",
        {
            "queries": [
                {
                    "cq_id": "CQ-001",
                    "file": "cq-001.rq",
                    "fixture": "taxonomy.ttl",
                    "include_ontology": False,
                    "include_taxonomy": True,
                    "entailment": "none",
                    "expected": {"type": "row_count", "value": len(lines)},
                }
            ]
        },
    )
    (out / "README.md").write_text(
        f"# {title}\n\nA pack generated by Semascope from a plain list of {len(lines)} "
        f"names on {today}. Each line is a SKOS concept in the scheme {scheme!r}; the "
        "first name is its preferred label, the others alternative labels.\n",
        encoding="utf-8",
    )
    (out / "release-manifest.json").unlink(missing_ok=True)
    write_manifest(load_pack(out, force=True))
    return out


def _yaml(path: Path, data: Any) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
