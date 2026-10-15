# Solr 10 configsets

Phase 1 adds three configsets here, written from scratch for Solr 10:

| Collection | Unit | Purpose |
|---|---|---|
| `documents` | one document | metadata, full text, facets, pack tags with `pack_version` |
| `chunks` | one chunk | `DenseVectorField` (cosine, quantised), chunk text, document id |
| `entities` | one label of one concept or individual, per pack version | the Tagger handler dictionary |

Rules: managed schema with schemaless off; nothing that Solr 10 removed
(`<lib>`, `ManagedSynonymFilterFactory`, language response writers,
`ExternalFileField`); basic authentication from `security.json.tmpl`, which
the server renders with the generated password at first start.
