# Writing a connector or stage plugin

Semascope's pipeline has two extension points, both plain Python classes
registered through
[entry points](https://packaging.python.org/en/latest/specifications/entry-points/):

| Entry point group | What it adds | Built-ins |
|---|---|---|
| `oss.connectors` | a connector: where documents come from | `filesystem`, `web`, `upload`, `feed`, `mail`, `archive`, `table`, `s3`, `rdf`, `warc`, `hypothesis`, `pst`, `mastodon` |
| `oss.stages` | a stage: one step of the per-document pipeline | `fetch`, `extract`, `structure`, `patterns`, `tag`, `ner`, `link`, `validate`, `preview`, `embed`, `index`, `graph` |

The protocols are in `oss_core.contracts` (package `oss-core`); a change to
them needs an ADR ([CONTRIBUTING](https://github.com/rdb420/semascope/blob/main/CONTRIBUTING.md)).
They are `typing.Protocol` classes: nothing checks them at run time, so a
plugin only has to have the attributes and methods.

What is **not** pluggable today: embedding models (`fastembed`, `openai`,
`sidecar`), entity providers (`spacy`, `gliner`, `remote`) and the pattern
extractors are chosen from fixed lists in `oss_pipeline.embedders`,
`oss_pipeline.stages.ner` and `oss_pipeline.extractors`; a new one is a code
change. The remote providers are the way to bring your own model without
one ([Runtimes](operations.md#runtimes)). The index layer has an
`IndexBackend` protocol, but nothing implements or calls it: the `index`
stage writes to Solr directly.

## Registering a plugin

In the plugin package's `pyproject.toml`:

```toml
[project.entry-points."oss.connectors"]
echo = "my_plugin.echo:EchoConnector"

[project.entry-points."oss.stages"]
ner = "my_plugin.ner:MyNerStage"
```

Install the package into the image the worker, server and cli run from
(a derived image: `FROM ghcr.io/rdb420/semascope:<tag>` and
`uv pip install my-plugin`, or a build of your own). On start, each
process loads the built-ins, then every entry point of the two groups
(`oss_pipeline.registry`):

- the entry point's name is the connector's or stage's name; a plugin with
  the name of a built-in **replaces** it;
- the class is instantiated with no arguments;
- a plugin that fails to import is skipped with a warning in the log
  (`plugin … failed to load`), so a broken plugin never stops the worker.

## Connectors

```python
class Connector(Protocol):
    name: str

    def discover(self, source: SourceConfig, since: Cursor | None) -> Iterable[SourceItem]: ...
    def fetch(self, item: SourceItem) -> FetchedItem: ...
    def deleted(self, source: SourceConfig, since: Cursor) -> Iterable[str]: ...
```

- `discover` yields one `SourceItem` per document the source has now:
  `uri` (the document's identity within the collection), `content_hash`
  (what change detection compares; an item with the same hash as last time
  is not processed again), and optionally `modified_at`, `size` and a
  `metadata` dict. `source.settings` is the source's settings object as the
  API stored it ([Connector settings](connectors.md)).
- `fetch` returns a `FetchedItem` with either a `path` to a local file or
  the `content` bytes, and optionally the `media_type`. The `fetch` stage
  stores it in the blob store under the SHA-256 of the bytes. A connector
  that downloads or unpacks during `discover` writes to the source's work
  area (`oss_pipeline.connectors.base.work_dir(source, kind)`), which the
  document task and the daily `gc` clean up.
- `deleted` is part of the protocol but **not called**: deletion works by
  absence ([ADR-0013](adr/0013-deletion-and-retention.md)). Return an
  empty list.

Optional members the server looks for (see `oss_pipeline.connectors.base`
and the built-ins for examples):

| Member | Effect |
|---|---|
| `fetch(self, item, source=...)` | if `fetch` takes a `source` keyword, it gets the `SourceConfig`, so credentials can be read from the source settings at fetch time instead of travelling in the queued task |
| `discover(..., known=...)` | if `discover` takes `known`, it gets what the catalogue already holds for the source, to skip unchanged items cheaply |
| `report: RunReport` | the connector records what it could not read (`report.skip(what, reason)`) and whether it stopped at a limit (`report.truncated = True`); an incomplete run deletes nothing. Without a report, every run counts as complete |
| `deletes_missing: bool` | whether a complete run deletes what it no longer yields (default `True` for plugins; feeds and Mastodon say `False`); a source's `delete_missing` setting overrides it |
| `credential_settings: tuple[str, ...]` | setting names that hold credentials, masked wherever source settings are shown |

A connector that fetches URLs a source or user supplies should use the
guarded HTTP client (`oss_pipeline.connectors.guard.guarded_client`), and
one that reads local paths should check them with the guard's path
functions, as the built-ins do ([Security](security.md)).

Custom fields: put `x_<name>_<suffix>` keys in an item's
`metadata["fields"]` (lower-case letters, digits, `_`, `-`; a dynamic-field
suffix such as `_s`, `_ss`, `_l`, `_d`, `_b`, `_dt`). They are indexed as
`<name>_<suffix>`; a key that would replace one of the index's own fields
is dropped.

## Stages

```python
class Stage(Protocol):
    name: str
    version: str
    requires: tuple[str, ...]
    provides: tuple[str, ...]

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument: ...
```

- `run` takes the working document and returns it, changed. The
  `WorkingDocument` holds `id`, `collection`, `source_id`, `uri`,
  `content_hash`, `media_type`, `language`, `title`, `text`, `pages`,
  `chunks`, `metadata`, `fields` (anything else, including custom `x_`
  fields), `tags` (a list of `Tag`), `embeddings` and `stage_runs`.
  `StageContext` gives the collection, the profile, the collection's
  pipeline settings and the active pack versions.
- `requires` and `provides` name what the stage reads and adds (model
  attributes or `fields` keys). A profile is checked when it is built: every
  stage's `requires` must be provided by an earlier stage, or the documents
  fail with that message.
- `version` is recorded with every run and on the tags the stage makes.
  Changing it does not by itself reprocess anything; run
  `oss reindex --collection <slug>` (or `--stage <name>`) after deploying a
  new version.
- A stage instance lives as long as the worker process and serves every
  document it runs, so keep clients (HTTP, database) on the instance and
  give it a `close()` method; it is called when the process exits.
- Tags: a `Tag` has `iri`, `label`, `kind` (`concept` or `entity`),
  `stage`, `confidence` and `spans` (with `page` and `matched`), and the
  provenance fields of [ADR-0009](adr/0009-explainability-record.md)
  (`stage_version`, `matched_label`, `label_type`, `lang`, `model`,
  `linked_from`, `pack_id`, `pack_version`). Fill what applies.

Failures:

- Raise `oss_pipeline.stages.base.SkipStageError` to record the stage as
  skipped (for example, nothing to do for this media type).
- Any other exception fails the stage. It is **transient** when it, or an
  exception in its `__cause__` chain, is a connection error or timeout, an
  `httpx` transport error or an HTTP 408, 425, 429, 500, 502, 503 or 504
  answer, or a Neo4j availability error, or carries an attribute
  `transient = True` (set `transient = False` to force permanent). A
  transient failure is retried in place twice (about 2 s and 4 s), then the
  whole document task up to three times (60, 120, 240 s); a permanent one
  is never retried (`oss_pipeline.retry`).
- A failure of `fetch` or `extract` stops the profile; a failure of a later
  stage is recorded and the rest of the profile runs.

### Profiles

A collection runs one of three fixed profiles (`oss_pipeline.registry`):

| Profile | Stages |
|---|---|
| `fast` | fetch, extract, structure, tag, index |
| `default` | fetch, extract, structure, patterns, tag, ner, link, preview, embed, index, graph |
| `full` | `default` with validate after link |

A plugin cannot add a profile or insert a new stage name into one: a stage
plugin takes effect by **replacing a built-in of the same name** (for
example a different `ner` or `preview`). A new stage in its own position
needs a change to `PROFILES`. A collection naming another profile fails its
documents ("unknown profile").

## Testing a plugin

`packages/oss-pipeline/tests/test_plugins.py` shows the pattern: build an
`importlib.metadata.EntryPoint("echo", "my_plugin.echo:EchoConnector",
"oss.connectors")`, monkeypatch `oss_pipeline.registry.entry_points` to
return it, then call `registry.connectors()` (or `registry.stages()`) and
drive `discover` and `fetch` with a `SourceConfig`. The suite covers a
connector plugin being found and run, a plugin replacing a built-in, and a
broken plugin being skipped; it has no example of a working stage plugin
yet.
