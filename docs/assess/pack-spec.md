# Pack specification

Status: **draft**, 2026-10-16. Describes what 1.0.0-rc.2 (commit `8e4cb2b`)
does with a pack, as read from the code, updated for the pack hardening
after rc.2 (branch `hardening/packs`). Where the code does less than
[ADR-0007](../adr/0007-pack-contract.md) promises, the gap is listed at the
end rather than written over.

A pack is a knowledge model packaged as an `ontology` skill project bundle,
contract version **2.2.0** (`oss version` prints it). Semascope implements the
contract itself in `oss_core.pack` and the server's `knowledge` app; it does
not run the skill's code.

## File layout

A pack is one directory. These eleven files must exist, or the pack fails
validation with `missing_file`. The four RDF files may use another
serialisation (see below):

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
| `evidence.jsonl` | ids only | checked for duplicates and for the ids `model.yaml` cites |
| `README.md` | existence and hash only | not parsed |

Also recognised:

- `queries/*.rq`: the SPARQL files the query manifest names.
- `release-manifest.json`: written by `oss pack validate --manifest` (or the
  skill's `release_manifest.py`) with `ontology_id`, `ontology_version`,
  `project_status`, `contract_version` and `files[{path, sha256}]`.
  Validation and import **verify** it: a file whose hash differs, a listed
  file that is missing, an unlisted file, or an id or version that differs
  from `requirements.yaml` is a `manifest_mismatch` error, or a warning with
  `--force`. A pack without a manifest gets a `no_manifest` warning.
  `oss pack validate --manifest` rewrites it, so a stale one is then not an
  error.

The manifest, and any path with a `validation`, `.git` or `__pycache__`
component, are not pack content: they are left out of the manifest and of
the **pack hash** (`manifest_hash`), the SHA-256 of the sorted list of
`(path, sha256)` pairs over every other file. An import whose hash matches
the stored version is skipped unless `--force` is given.

## Serialisations

Each of the four RDF files is found by its stem and read by its
extension:

| Extension | Serialisation |
|---|---|
| `.ttl` | Turtle |
| `.nt` | N-Triples |
| `.rdf`, `.owl`, `.xml` | RDF/XML |
| `.jsonld` | JSON-LD |

Two files for one stem (`taxonomy.ttl` and `taxonomy.rdf`) are an
`ambiguous_rdf` error. `shapes` must be Turtle (`rdf_format` otherwise),
because the `validate` stage concatenates every active pack's shapes as
Turtle at run time. JSON-LD must carry its `@context` inline: a remote
context would make validation and import fetch URLs, and is an
`rdf_parse` error. A query manifest that names `instances.ttl` also finds
`instances.jsonld` (or any other serialisation of the four files), so a
converted pack keeps its manifest. Other RDF files in the directory are read
only when the query manifest names them.

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
  version: 0.1.0              # required; semantic versioning recommended (below)
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
  `dcterms:title` (individuals only), else the IRI's local name. When there
  are several (SKOS allows one per language), the English one is the main
  label (then an untagged one, then by language tag). The others are kept
  as further preferred labels with their language: they are shown, compared
  by the upgrade diff and, for taggable terms, become dictionary entries
  (`label_type: pref`) carrying their own `lang`.
- **Notation:** `skos:notation` is kept (in `extra.notation`) and shown;
  it does not tag text.
- **Alternative and hidden labels:** `skos:altLabel` and `skos:hiddenLabel`.
- **Definition:** `skos:definition`, else `rdfs:comment`, else
  `dcterms:description`, cut at 4,000 characters.

Every taggable preferred, alternative and hidden label of two or more
characters becomes one entry in the Solr `entities` dictionary that the
`tag` stage matches against. Labels that are all upper case and six
characters or shorter match case-sensitively.

Not used in 1.0: `skos:narrower` (only `broader` is read), `skos:related`,
the SKOS mapping properties, `skos:scopeNote`, `owl:sameAs`,
`owl:equivalentClass`, and the language of alternative and hidden labels
(they take the main label's).

## Named individuals

An individual tags text only when the pack sets `tag_individuals: true`,
because the skill's contract defines `instances.ttl` as fixtures. When it
does, its labels join the dictionary, and the `link` stage also attaches
named entities found by NER to it when their normalised text matches one of
its labels. Its first `rdf:type` gives its facet (`x_<class>_entities_ss`)
and graph class. Individuals with coordinates appear on the map.

## Several packs, imports and conflicts

- Each pack version is its own named graph. `owl:imports` is not followed
  (validation warns, `owl_imports`); a pack that reuses another refers to
  its IRIs without importing it.
- Several packs can be active at once, one active version per pack `id`.
- A collection uses the packs bound to it (`PackBinding`, with a
  precedence), or every active pack when it has no bindings.
- Precedence resolves conflicts (since the fix after rc.2): a collection's
  bindings by `precedence` then pack id, otherwise the lowest precedence
  any binding gives the pack (100 when unbound) then pack id. An IRI in two
  packs is tagged once and attributed to the first pack; a label two packs
  give to different individuals links to the first pack's individual; the
  concept endpoint and the reconciliation preview answer with the first
  pack's concept.

## Versioning

- `version` is 1 to 32 characters from letters, digits and `. + _ -`
  (`bad_version` otherwise, since it is part of the graph IRI and of
  `id@version` keys). Semantic versioning (`MAJOR.MINOR.PATCH`, with an
  optional pre-release or build suffix) is recommended; any other form is a
  `version_format` warning. Semascope stores versions and does not compare
  them.
- A **new version** is a new import. Activating it retires the previous
  version, re-syncs the dictionary and queues a reindex of the collections
  that use the pack. `POST /api/knowledge/packs/{id}/activate?version=`
  switches back.
- **An upgrade is a diff and a reindex plan.** Whenever activation would
  replace an active version (a new version, or changed content under the
  same version), the import and the activation answer **409** with the plan
  unless `confirm=true`; the CLI prints it and needs `--yes`. A first import
  of a pack id, or re-activating the active version, needs no confirmation.
  The plan is also available on its own (`oss pack diff`, below):
  - the **diff**, term by term against the active version: terms added,
    removed and changed by kind (concept, individual, class, scheme), the
    fields that changed (labels, taggability, scheme, facet path, type,
    broader, definition, notation, support, status, coordinates), dictionary
    labels added and removed, and terms that became taggable or stopped
    being taggable. Lists are capped at 200 entries each;
  - the **reindex plan**, per collection: whether activation reindexes it
    (bound to the pack, or bound to nothing), its documents, those tagged by
    the active version (`pack_versions_ss`), and those carrying a removed or
    tag-relevant changed term (`concept_iris_ss`, `entity_iris_ss`). Whole
    collections are reindexed, because an added label can match any
    document.
- **Re-importing the same version** with changed content replaces it in
  place (`--force` if the hash is unchanged), and needs confirmation when
  that version is active.
- Retired versions keep their named graph and concepts, so older tags still
  resolve, until they are **removed**: `oss pack remove ID --version V`
  (`DELETE /api/knowledge/packs/{id}/versions/{version}`, admin role) drops
  the version's named graph, its concepts and its dictionary entries. The
  active version cannot be removed (409). Documents tagged with a removed
  version keep those tags, and its `id@version` in `pack_versions_ss`,
  until they are reindexed; the answer says how many there are. Curator
  deltas made against it are kept.
- Every dictionary tag records `pack_id` and `pack_version`. Solr carries
  `pack_versions_ss` (`id@version`) per document, and the graph carries
  `TAGGED.pack_version` and a `(:Pack {key: "id@version"})` node per pack
  version.

## Validation

`oss pack validate PATH [--manifest] [--force]` runs locally without a
server, then runs the competency questions, and exits 1 on any error or
failed question. Import runs the same checks (not the questions' verdict)
and refuses a pack with errors (HTTP 422). CI runs it on `packs/base` and
`packs/demo/pack`, without `--manifest`, so a stale manifest fails the
build.

| Level | Code | Condition |
|---|---|---|
| error | `missing_file` | one of the eleven files is absent |
| error | `ambiguous_rdf`, `rdf_format` | two serialisations of one RDF file; shapes not in Turtle |
| error | `yaml` | a YAML file does not parse |
| error | `missing_metadata` | `ontology.id`, `version` or `base_iri` is missing |
| error | `bad_id`, `bad_version` | the id is not 1-64 letters, digits, `-` or `_`; the version is not 1-32 of letters, digits and `. + _ -` |
| warning | `version_format` | the version is not semantic versioning |
| error | `manifest_mismatch` | `release-manifest.json` differs from the files (warning with `--force`) |
| warning | `no_manifest` | there is no `release-manifest.json` |
| error | `rdf_parse` | an RDF file does not parse, or JSON-LD names a remote context |
| error | `no_label`, `no_definition` | an OWL class or property inside `base_iri` lacks `rdfs:label`, or lacks `rdfs:comment`/`skos:definition` |
| error | `no_preflabel` | a SKOS concept has no `skos:prefLabel` |
| warning | `owl_imports` | the pack declares `owl:imports`, which is not followed |
| error | `term_kind`, `term_support` | unknown value in `model.yaml` |
| error | `duplicate_id` | two terms in `model.yaml`, or two lines of `evidence.jsonl`, share an id |
| error | `evidence_json` | a line of `evidence.jsonl` is not a JSON object with an id |
| error | `unknown_evidence` | a term or axiom cites an evidence id that `evidence.jsonl` lacks |
| error | `no_rationale` | an accepted `modelling_construct` term has no rationale |
| error | `no_evidence` | an accepted `source_supported` or `derived` term has no evidence |
| error | `accepted_unsupported` | an accepted term has support `proposed` or `disputed` |
| error | `cq_manifest`, `cq_file`, `cq_fixture` | a query entry is malformed, its query file is missing, or a fixture is missing, outside the pack or not RDF |
| error | `cq_expected` | a query entry has no expectation, or an unknown expectation type |
| error | `cq_coverage` | a `core` competency question in `requirements.yaml` has no query (unless `queryable: false` with a `nonqueryable_rationale`) |
| error | `shacl_violation` | SHACL over the fixtures reports a violation |
| warning, info | `shacl_warning`, `shacl_info` | SHACL results with severity `sh:Warning` or `sh:Info` |
| error | `shacl_engine` | the shapes cannot be run |
| warning | `status` | `ontology.status` is not one of the four known values |

**SHACL over the pack's own data.** `shapes.ttl` is run with pyshacl over
`instances` as the data graph, with `ontology` and `taxonomy` mixed in and
no inference, as the skill's `run_shacl.py` does. At most 50 results per
level are listed. At run time the same shapes validate each document's
metadata graph (see above).

**Competency questions** run at import and in `oss pack validate`. Each
manifest entry is run over the dataset it names, as the skill builds it:
its `fixture` (default `instances`), plus `ontology` unless
`include_ontology: false`, plus `taxonomy` unless `include_taxonomy: false`,
plus any `extra_graphs`; shapes are not part of it. Queries run with rdflib
over an in-memory graph, not over the stored pack graph. Supported
expectations: `row_count`, `min_rows`, `max_rows`, `ask_true`, `ask_false`
and `contains_bindings`; any other type is a validation error and the
question's status is `fail`. A query that declares an entailment regime
other than `none` is recorded as `skipped-reasoner`. Results are stored and
shown on the pack page; at import a failed question does not block the
import, in `oss pack validate` it fails the run.

## Commands and API

| Action | CLI | API |
|---|---|---|
| validate | `oss pack validate PATH [--manifest] [--force]` | — |
| diff and reindex plan | `oss pack diff PATH [--force]` | `POST /api/knowledge/packs/diff` (editor); `GET …/packs/{id}/diff?version=` for an imported version (editor) |
| import | `oss pack import PATH [--activate] [--reindex] [--force] [--yes]` | `POST /api/knowledge/packs/import` (editor; `confirm`) |
| list | `oss pack list` | `GET /api/knowledge/packs` |
| activate or roll back | `oss pack activate ID --version V [--yes]` | `POST /api/knowledge/packs/{id}/activate?version=&confirm=` (editor) |
| remove a version | `oss pack remove ID --version V [--yes]` | `DELETE /api/knowledge/packs/{id}/versions/{version}` (admin) |
| curator proposals | — | `GET`/`POST /api/knowledge/packs/{id}/deltas`, `GET …/deltas/export` |

The import and diff paths must lie under the import directory or the
bundled packs folder. Removing a version needs the admin role, not editor:
it cannot be undone on the server (the bundle can be imported again), and
it drops the graph that older tags resolve against.

## Curator deltas

Curators propose changes without editing the pack. The kinds are
`add_label`, `new_concept`, `retire_label` and `note`. The export
produces `taxonomy.delta.ttl` and `model.delta.yaml`, with each term marked
`status: draft` and `support: proposed`. These go back to the ontology-skill
workflow, which accepts them into the next pack version.

## Gaps against ADR-0007 (to close or to re-scope before 1.0)

1. Re-scoped in the hardening: the skill's own scripts are not public and
   have no licence, so CI cannot run them. CI runs `oss pack validate` on
   `packs/base` and `packs/demo/pack`, which implements their gates (see
   ADR-0007 for what is and is not covered).
2. Fixed in the hardening: a pack upgrade is a diff and a reindex plan, and
   replacing an active version needs confirmation.
3. Fixed in the hardening: `release-manifest.json` is verified on import
   and in `oss pack validate`.
4. Fixed in the hardening: SHACL shapes run against the pack's own
   instances at validation and import.
5. Fixed in the hardening: `oss pack remove` removes a version that is not
   active.
6. Fixed after rc.2: precedence did not resolve label conflicts between
   packs, and a concept IRI present in two active packs broke
   `GET /knowledge/concepts?iri=`. Both now resolve by binding precedence.
7. Fixed in the hardening: `semascope.entity_classes` is in the ADR.
8. Fixed after rc.2: a draft label added by a curator tagged every
   collection that used the pack, and the next dictionary sync removed it.
   Draft entries now carry `collection_scope`, tag only the proposing
   collection, and are rebuilt from the pending deltas on every sync.
9. Open: the skill validator's declaration-hygiene checks (undeclared
   custom predicates, SKOS/OWL hierarchy mixing, model/RDF mismatches) and
   its axiom, mapping and release-policy gates are not implemented.
10. Open: the language of alternative and hidden labels is not kept.
