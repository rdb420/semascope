# Languages and OCR

Semascope makes no assumption about the language of a corpus, but three
steps depend on language, and each is set separately: how text is searched,
which languages OCR reads, and which entity model reads the text.

| Step | Default | Set by |
|---|---|---|
| Language detection | Tika, per document | `detect_language` per collection |
| Search analysis | the document's language, if it is one of 28 | nothing to set; [Searching](search.md#languages) |
| Synonyms | the active packs' labels, every language | the packs |
| OCR | English (`eng`), in the document's own run | `ocr` and `ocr_languages` per collection |
| Named entities | spaCy English for English, spaCy multilingual for everything else | the runtime (`SEMASCOPE_NER_PROVIDER`), deployment-wide |

## Language detection

The `extract` stage takes the language from the file's own metadata, else
from the connector, else asks Tika to detect it from the first 20,000
characters (unless the collection sets `detect_language: false`). Regional
codes are reduced to the language: `zh-CN` and `zh-TW` are `zh`, `pt_BR` is
`pt`, `nb` and `nn` are `no`. The result is the document's `language_s`
facet; `und` means none was found.

## Search

Every document is indexed as written and, for 28 languages (ar bg ca cs da
de el en es fa fi fr hi hu id it ja ko lv nl no pl pt ro ru sv tr zh), once
more with that language's stop words and stemmer, Chinese, Japanese and
Korean as character pairs. Queries search both, and `exact:` or switching
off "other word forms" searches only the written form. Any other language
is searched as written. The details, and the reindex a collection indexed
before 1.0.0-rc.3 needs, are in [Searching](search.md#languages).

Synonyms come from the active packs' preferred, alternative and hidden
labels in every language, at query time ([Synonyms](search.md#synonyms)).

## OCR

OCR runs in Tika (Tesseract) for images and for PDF pages without a text
layer, or in MinerU on the `remote` runtime for the formats it takes
([Runtimes](operations.md#runtimes)).

### When: `ocr`

A collection's `settings.pipeline.ocr`
([ADR-0014](adr/0014-ocr-later.md)):

| Value | Effect |
|---|---|
| `now` (default; also `true`) | OCR in the document's own run |
| `later` | index the text layer and metadata at once; a document that needs OCR (an image, a PDF with fewer than `ocr_min_chars_per_page`, 40, characters per page, or a file holding images) is marked `ocr_pending` and OCRed by a low-priority task |
| `off` (also `false`) | never OCR |

Pending documents carry `ocr_pending_b: true` in the index (a filter) and
"OCR pending" on their page, and are counted by `/api/stats`, `oss stats`
and `oss doctor`. See [OCR later](operations.md#ocr-later).

### Which languages: `ocr_languages`

```json
{"settings": {"pipeline": {"ocr_languages": "eng+deu"}}}
```

A string of Tesseract language codes joined by `+` (`eng`, `deu`,
`chi_sim`), or a list; at most eight. The collection's OCR reads all of
them, which is slower than one. Without the setting, Tika's configuration
applies (`deploy/tika/tika-config.xml`: `eng`). The API refuses a value that
is not a list of codes (422).

The Tika image in `compose.yaml` (`apache/tika:3.3.0.0-full`) has the
Tesseract data for `deu`, `eng`, `fra`, `ita`, `jpn` and `spa`. A language
the image does not have gives no OCR text at all, so for another language
build a Tika image with that Tesseract language pack and point the `tika`
service at it. MinerU chooses its own OCR languages; `ocr_languages` does
not reach it.

## Named entities

The `ner` stage's provider is chosen for the whole deployment
([Runtimes](operations.md#runtimes)):

- `spacy` (the `cpu` runtime): `en_core_web_sm` for documents detected as
  English and `xx_ent_wiki_sm` (multilingual) for every other language and for
  documents without one. A collection cannot choose another spaCy model.
- `gliner` (the `gpu` runtime) and `remote`: zero-shot models asked for the
  packs' classes, whatever the language; how well they read a language
  depends on the model.

Dictionary tagging against packs uses the labels as written in the packs
(lower-cased, accents folded, an English `'s` dropped), so a pack needs labels in each language its
documents use; stemmed tagging is not available.
