# Product decisions

Status: **draft**, 2026-10-17. Describes 1.0.0-rc.3 (unreleased).

The independent review of 1.0.0-rc.2 listed product decisions that a
replacement for Open Semantic Search has to make (D-1 to D-18, the questions
Open Semantic Search itself left open) and decisions the review raised about
Semascope (N-1 to N-14). This page says, for each one, what Semascope chose
and where that is recorded, or that it is still **open: the owner decides**,
with the option the review recommended.

"Recorded in" names an ADR where there is one. Many choices are recorded only
in the documentation and the code; those are marked *no ADR*, and an ADR for
them is a candidate for the owner's list. Proposed ADRs are not yet accepted.

## Questions carried over from Open Semantic Search

| # | Question | Semascope's choice | Recorded in |
|---|---|---|---|
| D-1 | Pages and sentences: separate hits, excluded, or grouped under the document? | **Grouped.** A hit is always a document. Pages are kept on the document (`structure`), and chunks of about 350 words are passages in the `chunks` collection, returned with semantic and hybrid hits. The document page counts query matches per page and jumps to the first matching page (`/api/documents/{id}/matches`). No sentence segmentation; keyword hits do not name a page. | [Concepts](../concepts.md#views-of-a-search), [Searching](../search.md#modes); *no ADR* |
| D-2 | Serving original files | **An authorised download from the API**, checked per collection like the text, with safe types inline and everything else as an attachment, and byte ranges. | [ADR-0016](../adr/0016-original-file-download.md) (proposed) |
| D-3 | Stale values: atomic updates, or full replace? | **Full replace.** Every run recomputes a document's fields and tags; curators' decisions are kept beside the tags and re-applied through one curated view, so a reindex never undoes a decision; a rejected tag stays on the document, flagged, and is left out of Solr and the graph. Hierarchical facets follow the decisions. | [ADR-0009](../adr/0009-explainability-record.md) (proposed), [Explainability model](explainability.md) |
| D-4 | Two-tier OCR (OCR later) or one pass? | **Per collection: `now`, `later` or `off`.** `later` indexes the text layer at once and OCRs on a low-priority task. A paths-first pass before extraction is not part of it. | [ADR-0014](../adr/0014-ocr-later.md) (proposed) |
| D-5 | Which legacy features to drop | **Deliver capabilities, not technologies.** Features Open Semantic Search documented but never delivered get a modern equivalent or are recorded as superseded (Stanford NER, the legacy entities app); X/Twitter has no connector (paid API); there is no compatibility with its index, URLs, configuration or plugins. Not recorded either way: the metadata-server (Drupal) annotations and RDF enrichment. **Open for those two: the owner decides**; the review recommended recording them as dropped. | [ADR-0012](../adr/0012-never-delivered-capabilities.md) (proposed), [Coming from OSS](migration-from-oss.md), [Concepts](../concepts.md#source) |
| D-6 | UI approach; URL contract | **A single-page app over a JSON API.** Search state is in the URL: `/?q=&collection=&page=&f=&x=&view=&mode=&sort=&op=&stem=&from=&to=` (`f` chosen and `x` excluded facet values, `view` list, media or terms, `mode` semantic or hybrid, `op` all, any or phrase, `stem=false` for exact words, `from`/`to` a date range); `/table`, `/entities` and `/graph/results` take the same search; `/documents/<id>?q=` carries the query to the document. Open Semantic Search's URLs are not supported. | [ADR-0006](../adr/0006-stack.md) (proposed), `packages/oss-web/src/searchState.ts`; *no ADR for the URL contract* |
| D-7 | Auth model | **Roles per collection, reading denied by default** (`SEMASCOPE_DEFAULT_ROLE=none`); local accounts made by an administrator, optional OpenID Connect; workers run in-process and need no credentials of their own. Document-level access control is not offered: a collection is the unit. **Open: recording document-level ACLs as out of scope for 1.0** (the review's recommendation). | [Concepts: Roles](../concepts.md#roles), [Security](../security.md); *no ADR* |
| D-8 | Configuration format | **Environment variables and `.env`, each readable from a `<NAME>_FILE`** (Docker secrets); per-collection tuning in Postgres. No TOML file, no generated code, no importer of Open Semantic Search configuration. | [ADR-0006](../adr/0006-stack.md) as amended 2026-10-17 (proposed) |
| D-9 | Solr version and schema style | **Solr 10, managed schema with schemaless off**, SolrCloud mode with embedded ZooKeeper, basic authentication; the full text is stored; a vector field per dimension in `chunks`. | [ADR-0003](../adr/0003-solr-10.md) (accepted), [deploy/solr](https://github.com/rdb420/semascope/blob/main/deploy/solr/README.md); *no ADR for cloud mode* |
| D-10 | Fix Open Semantic Search's data bugs at a reindex's cost? | **Moot**: a new schema, nothing inherited. | [ADR-0001](../adr/0001-clean-room-apache-2.md) |
| D-11 | Should extracted entities be full-text searchable? | Entities are facets and fields (`persons_ss`, `concept_labels_ss`, ...), not part of the text a keyword query searches; pack synonyms make a concept's other names find each other. **Open: the owner decides**; the review recommended copying entity and concept labels into a searchable `entities_txt` field. | — |
| D-12 | Stemmed tagging per language | Not built: dictionary tagging matches labels as written (lower-cased, accents folded), so a pack needs its labels in each language. **Open: the owner decides**; the review recommended folding it into the language decision (N-5). | — |
| D-13 | Documented but never built: implement or descope? | **Published, with states.** The capability matrix marks every capability implemented, experimental or planned; ADR-0012 tracks each never-delivered capability to delivery or supersession; the plan lists what comes after 1.0. | [Capability matrix](capabilities.md), [ADR-0012](../adr/0012-never-delivered-capabilities.md), [Plan](../plan.md) |
| D-14 | Packaging targets | **Docker Compose on one Linux host** (amd64 and arm64); Helm and multi-host after 1.0; no `.deb`, no desktop VM. | [Deployment guide](deployment.md#supported-platforms), [Plan](../plan.md); *no ADR* |
| D-15 | Are unmodified files skipped on re-index? | **Yes, by content hash** (and, for folders, size and modification time before hashing); a run deletes only after a complete run that saw enough of the source; a deleted document comes back. | [ADR-0013](../adr/0013-deletion-and-retention.md) (proposed), [Concepts](../concepts.md#source) |
| D-16 | View flags; caps on rows, offsets and facets | **Caps decided and documented** (page size 0–100, page 1–500 and no further than the 10,000th hit, facet values 1–100, 1,000 fused candidates, exports 10,000, list search 500 names), with per-token rate limits. **Open: per-installation switches to hide views** (none exist); the review left this to the owner. | [Searching: Limits](../search.md#limits), [Security](../security.md) |
| D-17 | Retro-tagging and ontology rebuilds in the background? | **Yes.** Pack imports and activations are jobs that queue the reindex; a curator's new label queues the tag stage for the documents that contain it. | [Concepts: Pack](../concepts.md#pack); *no ADR* |
| D-18 | Social-media connector | **Mastodon and compatible servers** (public timelines, hashtags, accounts); X/Twitter out of scope. | [Connector settings](../connectors.md#mastodon); recorded as an ADR-0012 row (proposed) |

## Decisions the review raised

| # | Decision | Semascope's choice | Recorded in |
|---|---|---|---|
| N-1 | Licence policy: psycopg (LGPL), the CUDA EULA, Neo4j (GPL) | psycopg is allowed as an unmodified, dynamically linked run-time library; a licence gate in CI enforces the allow-list; the CUDA libraries are limited to the optional `-gpu` image. Neo4j Community runs as a separate server process, but that reasoning is not written down. **Open: the owner accepts ADR-0011** (until then ADR-0001's accepted text says "LGPL only as an optional extra") **and decides whether to record Neo4j as aggregation**, as the review recommended. | [ADR-0011](../adr/0011-psycopg-lgpl.md) (proposed), [ADR-0008](../adr/0008-runtimes-and-providers.md), [CONTRIBUTING](https://github.com/rdb420/semascope/blob/main/CONTRIBUTING.md), `.github/licence-policy.toml` |
| N-2 | Default-deny access | **Default `none`** for new and upgraded installs (`reader` must be set to keep open reading). An unknown value counts as `none` with a warning, rather than stopping the server as the review recommended. | [Concepts: Roles](../concepts.md#roles), changelog upgrade notes; *no ADR* |
| N-3 | Sign-up policy | **Closed**: no local sign-up; single sign-on creates accounts only while `SEMASCOPE_OIDC_AUTO_SIGNUP` is true, with the role `SEMASCOPE_OIDC_DEFAULT_ROLE` (default `none`). A sign-on whose e-mail matches a local account signs into it without the e-mail having been verified by Semascope (allauth's e-mail authentication). **Open: the owner decides whether to require a verified e-mail before matching**, as the review recommended. | [Security](../security.md), [Deployment guide](deployment.md#authentication-and-access); *no ADR* |
| N-4 | Embedding dimension | **A vector field per dimension** (256 to 4096) in `chunks`; `oss doctor` checks the model fits; a change of model needs `oss reindex --stage embed`. | [Operations: Runtimes](../operations.md#runtimes); *no ADR* |
| N-5 | Language coverage | **28 languages** analysed for search (stop words and stemmers, CJK bigrams), routed on the detected language, with the text also searchable as written; OCR languages per collection. | [Searching: Languages](../search.md#languages), [Languages and OCR](../languages.md); *no ADR* |
| N-6 | Deletion by absence and retention | **Per-connector policy** (feeds and Mastodon never delete by absence), deletion only after a complete run that saw at least half of the documents, revival of deleted documents, one run per source, deletion that reaches every store. Retention by age is not part of it. | [ADR-0013](../adr/0013-deletion-and-retention.md) (proposed) |
| N-7 | Which pipeline settings a collection may override | **An allow-list of tuning keys**; service addresses and credentials come only from the deployment. | [Connector settings](../connectors.md#collection-settings), [Security](../security.md), `pipeline/overrides.py`; *no ADR* |
| N-8 | Pack manifest strictness | **A mismatch is an error unless forced; a missing manifest is an error for a `released` pack** and a warning for others. | [ADR-0007](../adr/0007-pack-contract.md) (proposed), [Pack specification](pack-spec.md) |
| N-9 | Release and versioning | Semantic versions; images built only from `v*` tags after `ci` and `integration` pass on the tagged commit, with SBOM, provenance and cosign signatures; the compose default image tag is the checkout's version, `:main` is no longer published; actions pinned by SHA. Not yet exercised: no tag has been cut. | [Operations: CI and releases](../operations.md#continuous-integration-and-releases); *no ADR* |
| N-10 | Solr mode and authentication | **SolrCloud with embedded ZooKeeper**, one node, basic authentication (`blockUnknown`) set up from `SOLR_PASSWORD`; the health check fails while Solr answers anonymous requests. | [deploy/solr](https://github.com/rdb420/semascope/blob/main/deploy/solr/README.md); *no ADR* |
| N-11 | External map tiles | **Configurable** (`SEMASCOPE_MAP_TILE_URL`; empty for no base map), OpenStreetMap by default, disclosed. | [Deployment guide: Offline](deployment.md#offline-and-air-gapped-operation) |
| N-12 | Request caps and rate limits | **Documented maxima and per-token rate limits** on the expensive routes, sign-in lockout per account and address, the Reconciliation API signed in unless the operator opens it. | [Security](../security.md), [Operations: Limits](../operations.md#limits-and-the-audit-log); *no ADR* |
| N-13 | Container expansion in every connector | **Partly**: archives, mail and PST expand into child documents; every other file has the text of its embedded items (attachments, archive members, images in Office files) indexed with it, within limits, but not as documents of their own. **Open: the owner decides whether embedded items should become documents everywhere**, as the review recommended (one bounded recursion). | [Concepts: Stage](../concepts.md#stage), [Capability matrix](capabilities.md#enrichment) |
| N-14 | ADR governance | The ADR index lists every record with its status, says that accepted records are superseded rather than edited, and lists the known departures (ADR-0008's late paragraph; statements in accepted ADRs the code no longer matches). **Open: the owner accepts, amends or supersedes the proposed ADRs (0006, 0007, 0009–0016)**. | [ADR index](../adr/index.md) |

## Still open, in one list

1. D-5: record the metadata-server annotations and RDF enrichment as dropped.
2. D-7: record document-level access control as out of scope for 1.0.
3. D-11: entity and concept labels in a searchable text field.
4. D-12: stemmed dictionary tagging per language.
5. D-16: switches to hide views per installation.
6. N-1: accept ADR-0011; record Neo4j as a separate GPL process.
7. N-3: require a verified e-mail before a sign-on matches a local account.
8. N-13: embedded items as documents of their own in every connector.
9. N-14: accept, amend or supersede each proposed ADR.

Decided but recorded only in the documentation (candidates for an ADR):
D-1, D-6 (URL contract), D-7, D-9 (cloud mode), D-14, D-17, N-2, N-3, N-4,
N-5, N-7, N-9, N-10, N-12.
