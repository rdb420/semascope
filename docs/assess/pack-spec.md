# Pack specification

Status: **draft**, 2026-10-16. Describes what 1.0.0-rc.2 (commit `8e4cb2b`)
does with a pack, as read from the code. Where the code does less than
[ADR-0007](../adr/0007-pack-contract.md) promises, the gap is listed at the
end rather than written over.

A pack is a knowledge model packaged as an `ontology` skill project bundle,
contract version **2.2.0** (`oss version` prints it). Semascope implements the
contract itself in `oss_core.pack` and the server's `knowledge` app; it does
not run the skill's code.

## File layout

A pack is one directory. These eleven files must exist, or the pack fails
validation with `missing_file`:

| File | Read by Semascope | Used for |
|---|---|---|
| `requirements.yaml` | yes | identity, version, tagging policy (below) |
| `model.yaml` | yes | term kind and support, which decide what may tag |
| `ontology.ttl` | yes | OWL classes and properties |
| `taxonomy.ttl` | yes | SKOS concept schemes and concepts |
| `instances.ttl` | yes | named individuals |
| `shapes.ttl` | yes | SHACL shapes, applied to document metadata at run time |
| `queries/manifest.yaml` | yes | competency questions run at import |
| `sources.yaml` | stored verbatim | shown on the pack page |
| `decisions.yaml` | stored verbatim | shown on the pack page |
| `evidence.jsonl` | existence and hash only | not parsed |
| `README.md` | existence and hash only | not parsed |

Also recognised:

- `queries/*.rq`: the SPARQL files the query manifest names.
- `release-manifest.json`: written by `oss pack validate --manifest` with
  `ontology_id`, `ontology_version`, `project_status`, `contract_version` and
  `files[{path, sha256}]`. It is excluded from the pack hash, as is
  `validation/`.

The **pack hash** (`manifest_hash`) is the SHA-256 of the sorted list of
`(path, sha256)` pairs over every other file. An import whose hash matches
the stored version is skipped unless `--force` is given.

## Serialisations

Turtle only. The four RDF files are read by their fixed names with
`format="turtle"`; other RDF files in the directory are ignored. A pack in
RDF/XML, N-Triples or JSON-LD must be converted to Turtle first.

All four files are loaded into the embedded Oxigraph store as one named
graph per pack version, `urn:semascope:pack:{id}:{version}`. Staff can
query it with `POST /api/knowledge/packs/{id}/sparql` (SELECT and ASK,
row-capped).

## `requirements.yaml`

Only two blocks are interpreted. Everything else (scope, standards,
audience, release policy, …) is stored with the pack and shown, not acted on.

```yaml
ontology:
  id: semascope-demo          # required; the pack's stable identity
  version: 0.1.0              # required; free-form string, max 32 characters
  base_iri: https://semascope.dev/ontology/demo#   # required
  title: Sacred texts and mythology demo model     # optional; defaults to the directory name
  status: draft               # optional: draft | candidate | release_candidate | released
  licence: https://www.apache.org/licenses/LICENSE-2.0   # optional

semascope:                    # optional; ignored by the skill's validator
  tag_schemes: [traditions, themes, text-types]   # omit for every scheme; [] for none
  tag_individuals: true       # default false: instances are fixtures
  entity_classes: [Deity, Hero]   # optional: limit the classes offered to zero-shot NER
```

`tag_schemes` lists scheme slugs, which are the slugified `skos:prefLabel` of
each `skos:ConceptScheme`. `entity_classes` takes class local names or
labels; without it, zero-shot NER providers (`gliner`, `remote`) are asked
for every class of every active pack.

## `model.yaml`

Each entry in `terms[]` is read for `iri`, `kind`, `support`, `status`,
`evidence` and `rationale`. Labels and definitions come from the RDF, not
from here. An `iri` of the form `ex:local` is expanded against `base_iri`.

| Field | Allowed values |
|---|---|
| `kind` | `class`, `object_property`, `data_property`, `annotation_property`, `skos_concept`, `individual` |
| `support` | `source_supported`, `derived`, `external`, `modelling_construct`, `proposed`, `disputed` |

A term whose support is `proposed` or `disputed` never tags text.

## How the RDF is interpreted

| RDF | Becomes | Tags text? |
|---|---|---|
| `skos:ConceptScheme` | a scheme and one hierarchical facet family, slug from its `skos:prefLabel` | no |
| `skos:Concept` | a concept in the scheme named by `skos:inScheme` (else `skos:topConceptOf`); the facet path follows the first `skos:broader` parent | yes, if its scheme is in `tag_schemes` |
| `owl:Class` | an entity type, facet family and graph label; hierarchy from `rdfs:subClassOf` | never |
| `owl:NamedIndividual` (not also a concept or scheme) | an individual typed by its first known `rdf:type`; latitude and longitude read from any predicate named `lat`/`latitude` and `lon`/`long`/`lng`/`longitude` | yes, if `tag_individuals: true` |
| `owl:ObjectProperty`, `owl:DatatypeProperty` | counted and checked for labels; otherwise only reachable by SPARQL | no |
| `sh:NodeShape` | counted at import; at run time every active pack's `shapes.ttl` validates each document's metadata graph (`full` profile) | no |

Labels:

- **Preferred label:** `skos:prefLabel`, else `rdfs:label`, else
  `dcterms:title` (individuals only), else the IRI's local name. Only the
  first preferred label is kept, with its language tag.
- **Alternative and hidden labels:** `skos:altLabel` and `skos:hiddenLabel`.
- **Definition:** `skos:definition`, else `rdfs:comment`, else
  `dcterms:description`, cut at 4,000 characters.

Every taggable preferred, alternative and hidden label of two or more
characters becomes one entry in the Solr `entities` dictionary that the
`tag` stage matches against. Labels that are all upper case and six
characters or shorter match case-sensitively.

Not used in 1.0: `skos:notation`, `skos:narrower` (only `broader` is read),
`skos:related`, the SKOS mapping properties, `skos:scopeNote`, `owl:sameAs`,
`owl:equivalentClass`, and preferred labels in a second language.

## Named individuals

An individual tags text only when the pack sets `tag_individuals: true`,
because the skill's contract defines `instances.ttl` as fixtures. When it
does, its labels join the dictionary, and the `link` stage also attaches
named entities found by NER to it when their normalised text matches one of
its labels. Its first `rdf:type` gives its facet (`x_<class>_entities_ss`)
and graph class. Individuals with coordinates appear on the map.

## Several packs, imports and conflicts

- Each pack version is its own named graph. `owl:imports` is not followed;
  a pack that reuses another refers to its IRIs without importing it.
- Several packs can be active at once, one active version per pack `id`.
- A collection uses the packs bound to it (`PackBinding`, with a
  precedence), or every active pack when it has no bindings.
- Precedence orders bindings but does not yet resolve conflicts: when two
  packs share a label, both tags are kept by IRI in the `tag` stage, and the
  `link` stage takes the first dictionary hit.

## Versioning

- `version` is a string; Semascope stores it and does not compare it. Any
  scheme works; semantic versioning is recommended so that people can read
  it.
- A **new version** is a new import. Activating it retires the previous
  version, re-syncs the dictionary and queues a reindex of the collections
  that use the pack. `POST /api/knowledge/packs/{id}/activate?version=`
  switches back.
- **Re-importing the same version** with changed content replaces it in
  place (`--force` if the hash is unchanged).
- Retired versions keep their named graph and concepts, so older tags still
  resolve.
- Every dictionary tag records `pack_id` and `pack_version`. Solr carries
  `pack_versions_ss` (`id@version`) per document, and the graph carries
  `TAGGED.pack_version` and a `(:Pack {key: "id@version"})` node per pack
  version.

## Validation

`oss pack validate PATH [--manifest]` runs locally without a server and
exits 1 on any error. Import runs the same checks and refuses a pack with
errors (HTTP 422).

| Level | Code | Condition |
|---|---|---|
| error | `missing_file` | one of the eleven files is absent |
| error | `yaml` | a YAML file does not parse |
| error | `missing_metadata` | `ontology.id`, `version` or `base_iri` is missing |
| error | `rdf_parse` | a Turtle file does not parse |
| error | `no_label`, `no_definition` | an OWL class or property inside `base_iri` lacks `rdfs:label`, or lacks `rdfs:comment`/`skos:definition` |
| error | `term_kind`, `term_support` | unknown value in `model.yaml` |
| error | `no_rationale` | an accepted `modelling_construct` term has no rationale |
| error | `no_evidence` | an accepted `source_supported` or `derived` term has no evidence |
| error | `accepted_unsupported` | an accepted term has support `proposed` or `disputed` |
| warning | `status` | `ontology.status` is not one of the four known values |

**Competency questions** run at import against the pack's named graph.
Supported expectations: `row_count`, `min_rows`, `max_rows`, `ask_true`,
`ask_false` and `contains_bindings`. A query that declares an entailment
regime other than `none` is recorded as `skipped-reasoner`. Results are
stored and shown on the pack page; a failed question does not block the
import.

## Commands and API

| Action | CLI | API (editor role) |
|---|---|---|
| validate | `oss pack validate PATH [--manifest]` | — |
| import | `oss pack import PATH [--activate] [--reindex] [--force]` | `POST /api/knowledge/packs/import` |
| list | `oss pack list` | `GET /api/knowledge/packs` |
| activate or roll back | — | `POST /api/knowledge/packs/{id}/activate?version=` |
| curator proposals | — | `GET`/`POST /api/knowledge/packs/{id}/deltas`, `GET …/deltas/export` |

The import path must lie under the import directory or the bundled packs
folder.

## Curator deltas

Curators propose changes without editing the pack. The kinds are
`add_label`, `new_concept`, `retire_label` and `note`. The export
produces `taxonomy.delta.ttl` and `model.delta.yaml`, with each term marked
`status: draft` and `support: proposed`. These go back to the ontology-skill
workflow, which accepts them into the next pack version.

## Gaps against ADR-0007 (to close or to re-scope before 1.0)

1. CI validates `packs/base` with `oss pack validate`, not with the skill's
   own scripts; the demo pack is validated only by `just packs`.
2. A pack upgrade is not yet "a diff and a reindex plan": activation queues
   the reindex at once, and no diff is produced.
3. `release-manifest.json` is written but never verified on import.
4. SHACL shapes are not run against the pack's own instances at import.
5. There is no command to remove a pack version (retire is the only
   off-switch).
6. Precedence does not resolve label conflicts between packs, and a concept
   IRI present in two active packs breaks `GET /knowledge/concepts?iri=`.
7. `semascope.entity_classes` is implemented but not in the ADR.
8. A draft label added by a curator tags every collection that uses the
   pack, not only the proposing one, and the next dictionary sync removes it.
