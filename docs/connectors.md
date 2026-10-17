# Connector settings

A **source** is one connector with its settings inside a collection
([Concepts](concepts.md#source)). This page lists every connector and
every setting it reads, as the code reads them
(`oss_pipeline.connectors`), and the per-collection pipeline settings.

## Creating and running a source

`POST /api/collections/{slug}/sources` (editor role):

```json
{
  "name": "company-site",
  "connector": "web",
  "settings": {
    "urls": ["https://example.org/sitemap.xml"],
    "mode": "sitemap",
    "max_pages": 500,
    "exclude": ["/tag/"],
    "scrape": {"content": "main article", "title": "h1"}
  },
  "enabled": true,
  "schedule": "every 6h"
}
```

| Field | Default | Meaning |
|---|---|---|
| `name` | required | unique within the collection (409 otherwise) |
| `connector` | required | one of the names below, or a [plugin](plugins.md)'s (422 for an unknown one, listing the known ones) |
| `settings` | `{}` | the connector's settings, below |
| `enabled` | `true` | whether the scheduler runs it |
| `schedule` | `""` | `every <n> m\|h\|d` (`every 15m`, `every 2 hours`, `every 1d`), a cron expression, or empty for manual runs only |

The answer is 201 with the source (`id`, `status` `idle|running|failed`,
`last_run_at`, `last_error`, `document_count`). The settings are checked
when the source is created and again when it runs: paths must lie under
`SEMASCOPE_SOURCE_ROOTS`, URLs must be `http` or `https`, and `max_bytes`
must be a positive integer of at most 1 GiB (422 otherwise). Some settings
are only checked when the source runs, which then fails with the reason:
the schedule's syntax (an invalid schedule runs once and then never again),
`scrape` rules, the paths of `warc` and `pst` sources, and the addresses of
`hypothesis` and `mastodon` services.

Other routes: `GET /api/collections/{slug}/sources` (reader; settings are
shown to editors only, with credentials masked), `POST
/api/sources/{id}/run` (editor; 202, or 409 while the source is running),
`DELETE /api/sources/{id}` (editor; removes its documents everywhere). The
API has no route to change a source: edit it in the admin
(`/admin/`, *Sources*), or delete and create it again. `oss index <path>`
creates and runs a `filesystem` source for a folder under the import
directory; `oss upload` uses the collection's `upload` source.

## Settings every source understands

| Setting | Default | Meaning |
|---|---|---|
| `delete_missing` | the connector's policy | `true` or `false`: whether a complete run deletes the documents it no longer saw ([ADR-0013](adr/0013-deletion-and-retention.md)) |
| `min_yield` | 0.5 | a run of a source with ten or more documents that sees less than this share of them deletes nothing; 0 turns the rule off |
| `max_bytes` | per connector | the largest response or file read; at most 1 GiB |

Connectors that download or unpack write to a work area on the data volume
(`<data>/work/<collection>/<source id>/`). Items from folders, archives,
buckets and web sources get `folder_paths` (a tree facet: the source
folder, bucket or host, then the folders below it) and `extension_s`.

A connector's custom fields are indexed under their own names (`author_s`,
`keywords_ss`); a name that would replace one of the index's fields is
dropped.

## File connectors

Paths are inside the stack: `/import/...` is the host's
`SEMASCOPE_IMPORT_DIR` ([Files on this machine](operations.md#files-on-this-machine)).

### `filesystem`

One document per file, identified by its `file://` URI.

| Setting | Default | Meaning |
|---|---|---|
| `path` | required | a folder under `SEMASCOPE_SOURCE_ROOTS` |
| `include` | `["*"]` | glob patterns matched against the file name |
| `exclude` | `[]` | glob patterns matched against file and folder names; `.*`, `*~`, `*.tmp`, `*.part`, `Thumbs.db`, `node_modules`, `.git` and `.venv` are always excluded |
| `follow_symlinks` | `false` | follow symbolic links (their targets must still lie under the roots) |
| `max_bytes` | 512 MiB | larger files, and empty ones, are skipped |
| `sidecars` | `true` | read sidecar metadata files (below) |

A file with the same size and modification time as at the last run is not
read again. A folder or file that cannot be read is reported, so that run
deletes nothing.

**Sidecars.** For `report.pdf`, Semascope reads `report.pdf.meta.json`,
`report.pdf.xmp` or `report.xmp`; sidecars are not indexed as documents.
A `.meta.json` file is a JSON object: `title` sets the title, and every
other key becomes a custom field named after the key (lower-cased, other
characters as `_`) with a suffix by value type (`_s` text, `_ss` list,
`_l` integer, `_d` number, `_b` true or false; a key that already ends in
such a suffix keeps it). One level of nesting is flattened
(`{"case": {"id": 7}}` gives `case_id_l`). From XMP: `dc:title` (the
title), `dc:subject` (`keywords_ss`), `dc:creator` (`creator_ss`),
`dc:description`, `dc:rights`, `xmp:Rating` (`rating_l`) and `xmp:Label`.
JSON wins over XMP. At most 100 fields, 1,000 characters per value, 100
list values; sidecars over 1 MiB are skipped. A change to a sidecar alone
makes the next run process its file again.

### `upload`

Created by the first `POST /api/collections/{slug}/upload` (or
`oss upload`) as the source `uploads` of the collection; you do not create
it by hand. Files are stored under `<data>/uploads/<collection>/` and
identified as `upload://<collection>/<sha256>/<name>`. One file is at most
`SEMASCOPE_UPLOAD_MAX_MB` (512).

### `mail`

One document per message, its attachments as child documents.

| Setting | Default | Meaning |
|---|---|---|
| `path` | required | a folder, an mbox file or an `.eml` file |
| `attachments` | `true` | index attachments as child documents |

`.eml` files are single messages; files named `*.mbox` or `*mbox`, or
without a suffix and starting with `From `, are mboxes. A message is
`mail:<Message-ID>#<hash>` (the hash of the message without its client
status headers), so two messages with one Message-ID stay two documents;
an attachment is `<message>/<file name>`, numbered when names repeat.
Outlook `.msg` files are parsed by Tika from any file source.

### `pst`

Outlook `.pst` and `.ost` mailboxes. Needs the optional `pst` extra, which
the published image does not include ([ADR-0015](adr/0015-pst-libpff-extra.md));
without it, the run fails and says so.

| Setting | Default | Meaning |
|---|---|---|
| `path` | required | a folder or a `.pst`/`.ost` file |
| `attachments` | `true` | attachments as child documents |
| `max_messages` | 100,000 | per run; reaching it stops the run (and it deletes nothing) |
| `max_attachment_bytes` | 50 MiB | larger attachments are skipped |

Each message is an RFC 822 document identified as
`<file URI>!/<folder>/.../<message id>`.

### `archive`

Members of zip and tar archives (`.zip`, `.tar`, `.tar.gz`, `.tgz`,
`.tar.bz2`, `.tar.xz`) as documents, identified as
`file:///.../a.zip!/<member>` (`...!/inner.zip!/<member>` when nested).
Members are streamed to disk, never held in memory, and the limits count
the bytes actually decompressed:

| Setting | Default | Meaning |
|---|---|---|
| `path` | required | an archive, or a folder searched for archives |
| `max_members` | 10,000 | members per top-level archive, across all nesting levels |
| `max_member_bytes` | 256 MiB | a larger member is skipped |
| `max_ratio` | 100 | the byte budget is this many times the archive's size; a zip member whose header claims a higher ratio is skipped |
| `max_total_bytes` | 4 GiB | the budget's ceiling, across all nesting levels |
| `max_depth` | 2 | how deep nested archives are expanded |

Hidden members, links and devices are skipped; a corrupt archive or
member is reported. 7z and rar are recognised by Tika, not unpacked.

### `table`

One document per non-empty row of a CSV, TSV or XLSX file.

| Setting | Default | Meaning |
|---|---|---|
| `path` | required | a file, or a folder of `.csv`, `.tsv`, `.xlsx`, `.xlsm` |
| `title_column` | — | the column that gives the title |
| `text_columns` | all | columns that make the text (`Column: value` lines) |
| `id_column` | row number | the column that identifies a row (`file:///...#row=<id>`) |
| `field_columns` | `{}` | `{"column": "field_s"}`: columns as custom fields (give the suffix) |
| `sheet` | the first | the XLSX sheet |
| `encoding` | `utf-8` | of CSV and TSV files |

### `warc`

Web archives (`.warc`, `.warc.gz`): one document per archived page (a 2xx
`response` record, or a `resource` record), under its original URL, with
the capture date as its modified time; when a URL was captured several
times, the latest capture wins.

| Setting | Default | Meaning |
|---|---|---|
| `path` | required | a folder or a WARC file |
| `max_record_bytes` | 50 MiB | larger payloads are skipped |
| `max_records` | 100,000 | per run; reaching it stops the run (and it deletes nothing) |

### `rdf`

One document per subject of RDF files or of a SPARQL endpoint; the title
is the subject's first `rdfs:label`, `skos:prefLabel`, `dcterms:title`,
`dc:title`, `schema:name` or `foaf:name`, else its local name.

| Setting | Default | Meaning |
|---|---|---|
| `path` | required without `endpoint` | a file or folder of `.ttl`, `.nt`, `.n3`, `.rdf`, `.owl`, `.jsonld`, `.trig` |
| `endpoint` | — | a SPARQL endpoint URL instead of files |
| `graph` | — | read only this named graph of the endpoint |
| `limit` | 100,000 | triples read from the endpoint (in a stable order); reaching it stops the run |
| `types` | all | class IRIs: only subjects of these types |
| `max_bytes` | 200 MiB | the endpoint's answer |

JSON-LD files must carry their context inline; a remote `@context` is
refused, never fetched.

## Network connectors

Every request goes through the outbound guard: public addresses only
unless `SEMASCOPE_ALLOWED_PRIVATE_HOSTS` names the host, each redirect
checked, bodies capped while they stream ([Security](security.md)).

### `web`

| Setting | Default | Meaning |
|---|---|---|
| `urls` | required | seed URLs |
| `mode` | `pages` | `pages` (the URLs themselves), `sitemap` (each URL is a sitemap; nested sitemaps up to three levels) or `crawl` (follow links on the same host) |
| `max_pages` | 200 | pages per run; a run that stops here deletes nothing |
| `max_depth` | 2 | link depth of a crawl |
| `include`, `exclude` | — | regular expressions searched in the full URL |
| `respect_robots` | `true` | obey robots.txt |
| `delay_seconds` | 0.5 | pause after each page |
| `user_agent` | `semascope/0.1 (+https://github.com/rdb420/semascope)` | sent with every request |
| `max_bytes` | 50 MiB | per response |
| `scrape` | — | extraction rules, below |

A sitemap run collects at most ten times `max_pages` URLs (at least
1,000). Images, stylesheets, scripts, fonts, media and zip files are
skipped by their extension. A page that answers 404 or 410 is gone; a 5xx,
401, 403, 408, 425 or 429 answer, a timeout or a name that does not
resolve is reported, and that run deletes nothing; a run that can read
nothing fails. A document is identified by the URL it was requested under,
without its fragment.

**Scrape rules** (`scrape`), CSS selectors only:

```json
{
  "remove": ["nav", "footer", ".advert"],
  "content": "article.post",
  "title": "h1.headline",
  "fields": {"author": "meta[name=author] @content", "tags": "ul.tags li"}
}
```

`remove` drops elements first; `content` keeps only the matching elements
as the document (the whole page when nothing matches); `title` takes the
first match's text (else `<title>`); each of `fields` (names of lower-case
letters, digits and `_`) fills `<name>_ss`, and ` @attribute` takes an
attribute instead of the text. At most 50 fields, 100 values each, 1,000
characters a value. A selector that does not parse fails the run. A crawl
still follows the links of the whole page. Changing the rules makes the
next run process every page again.

### `feed`

RSS and Atom: one document per entry, identified by its link (or id).

| Setting | Default | Meaning |
|---|---|---|
| `urls` | required | feed URLs |
| `max_entries` | 200 | per feed |
| `fetch_links` | `false` | index the linked page instead of the entry's own content |
| `max_bytes` | 50 MiB | per response |

Feeds never delete by absence: articles roll off a feed while they still
exist. A feed that cannot be read fails the run once the others are read.

### `s3`

Objects in an S3-compatible bucket (AWS, MinIO, Ceph): one document per
object, `s3://<bucket>/<key>`, with the ETag for change detection.

| Setting | Default | Meaning |
|---|---|---|
| `bucket` | required | |
| `prefix` | — | only keys under it |
| `endpoint_url` | AWS | for MinIO and other services |
| `region` | — | |
| `access_key`, `secret_key` | the environment's AWS credentials | both or neither; the secret is masked in every answer, the access key for anyone below editor |
| `include`, `exclude` | `["*"]`, `[]` | glob patterns matched against the full key |
| `max_bytes` | 512 MiB | larger objects are skipped |

Credentials are read from the source when an object is fetched; they
never travel in queued tasks.

### `hypothesis`

Annotations from a Hypothesis service: one document per annotation, with
its quote, note and tags, and the annotated page as `annotates_s`.

| Setting | Default | Meaning |
|---|---|---|
| `group`, `uri`, `user` | — | at least one of them (`user` as `acct:name@hypothes.is`) |
| `tag` | — | only annotations with this tag |
| `token` | — | for private groups; masked |
| `api_url` | `https://api.hypothes.is/api` | another Hypothesis service |
| `max_annotations` | 5,000 | per run; reaching it stops the run |

To attach annotations to documents you already have, as notes, use
`POST /api/annotations/w3c/import` instead ([Interoperability](interop.md)).

### `mastodon`

Public posts of a Mastodon-compatible server: one document per post, a
boost as the post it boosts, the HTML reduced to text, with `author_s` and
`mastodon_tags_ss`.

| Setting | Default | Meaning |
|---|---|---|
| `instance` | required | the server (`https://` is added when missing) |
| `hashtags` | — | hashtags to follow |
| `accounts` | — | accounts to follow |
| `timeline` | — | `local` or `federated` |
| `token` | — | an access token; masked |
| `max_statuses` | 200 | per hashtag, account or timeline |

At least one of `hashtags`, `accounts` or `timeline`. Only public and
unlisted posts are kept. Like feeds, posts that roll off are not deleted.
X (Twitter) has no connector: its API cannot be read without a paid
agreement.

## Collection settings

A collection is created with `POST /api/collections`
(`{"slug", "name", "description", "profile", "settings"}`, editor role):
the slug is letters, digits, `-` and `_` (at most 64), and `profile` is
`default`, `fast` or `full` ([Plugins](plugins.md#profiles)).
`settings.pipeline` tunes its stages, and only the keys below are
accepted (422 for any other); service addresses and credentials come from
the deployment.

| Key | Default | Meaning |
|---|---|---|
| `ocr` | `now` | `now`, `later` or `off` ([Languages and OCR](languages.md)) |
| `ocr_min_chars_per_page` | 40 | with `later`, a PDF with fewer characters per page needs OCR |
| `ocr_languages` | Tika's `eng` | Tesseract codes, `"eng+deu"` or a list, at most 8 |
| `detect_language` | `true` | ask Tika for the language when the file says none |
| `embedded_max_items` | 100 | embedded items (attachments, archive members, images in Office files) whose text joins the document's |
| `embedded_max_chars` | 1,000,000 | characters of embedded text |
| `chunk_words`, `chunk_overlap` | 350, 40 | passage size and overlap in words |
| `patterns` | all | built-in extractors that run: `emails`, `urls`, `phones`, `money`, `hashtags`, `iban`, `law`, `currency` |
| `regex_lists`, `law_patterns`, `law_defaults`, `dollar_currency` | — | see [Regex lists](concepts.md#regex-lists-and-the-pattern-extractors) |
| `patterns_max_chars` | 2,000,000 | characters the extractors read |
| `preview_pages`, `preview_width` | 20, 1200 | PDF pages rendered as previews, and their width in pixels |
| `ner_max_chars` | 300,000 | characters sent to entity recognition |
| `ner_min_length` | 3 | shortest entity kept |
| `ner_window_chars` | 20,000 (spaCy), 1,800 (others) | text per entity-recognition request |
| `embed` | `true` | `false` skips embedding and removes the document's passages |
| `embed_max_chunks` | 800 | passages embedded per document |
| `pseudonymise` | `false` | pseudonymise named-entity keys in the graph |
| `stage_retries`, `stage_retry_backoff_s` | 2, 2.0 | in-place retries of a stage after a transient failure |
| `task_retries`, `task_retry_backoff_s` | 3, 60.0 | retries of the document task |

`ocr`, `ocr_languages`, `regex_lists`, `law_patterns`, `law_defaults` and
`dollar_currency` are checked when the collection is saved; the numbers are
read as numbers when a document runs. The review threshold (0.8) and the
entity models are deployment-wide, not per collection. The API has no route
to change a collection's settings: use the admin.
