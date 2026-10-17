# Searching

How to write a query, how languages and pack synonyms affect what it finds,
and the filters, sorting and views around it. The search page, `POST
/api/search`, saved searches, alerts, feeds, exports and list search all read
queries the same way.

## Modes

| mode | finds | ranks by |
|---|---|---|
| keyword (default) | documents containing the words, in the syntax below | relevance (BM25), phrase matches first |
| semantic | passages close in meaning to the query, whatever their words | similarity of the best passages (embeddings) |
| hybrid | both lists, fused by reciprocal rank | both |

Semantic and hybrid read the query as plain text: the syntax below applies to
keyword search, and to the keyword half of hybrid. They rank at most 1,000
documents.

## Query syntax

| write | finds |
|---|---|
| `flood ark` | documents with most of the words (see [How many words must match](#how-many-words-must-match)) |
| `"golden bough"` | the phrase, words in this order |
| `"golden bough"~3` | the words within three positions of each other |
| `odysseus OR arjuna` | either word |
| `odysseus ithaca OR penelope` | Odysseus, and Ithaca or Penelope: OR joins the words on either side of it into one choice |
| `odysseus AND ithaca`, `+odysseus +ithaca` | both words |
| `troy -achilles`, `troy NOT achilles` | Troy, without Achilles |
| `(zeus OR jupiter) AND (thunder OR lightning)` | groups |
| `odys*`, `wom?n` | wildcards: `*` any characters, `?` one |
| `odyseus~1` | words one edit (a letter added, dropped, changed or swapped) away; `~2` allows two |
| `exact:running` | the word as written: no other word forms (pack synonyms still apply) |
| `title:odyssey` | the title only (with other word forms); `text:` the body only |
| `language_s:de`, `persons_ss:"Zeus"` | any index field by name: the facet fields, `uri_s`, `media_type_s` and so on |
| `modified_dt:[2020-01-01T00:00:00Z TO *]` | a range; `[ ]` includes the ends, `{ }` excludes them |
| `zeus^3 hera` | a boost: Zeus counts three times as much |

Operators are upper case: `or` and `and` are words. Letters are compared without
case or accents: `vainamoinen` finds "Väinämöinen", `grossmutter` finds
"Großmutter". A query that is a number (`1984`) is a word like any other.

### How many words must match

By default a document needs all the words of a query of one or two words, all
but one of three to five words, and 80% of longer ones (Solr's `mm`, set in
`deploy/solr/configsets/documents/conf/solrconfig.xml`). Words marked `+` or
joined by AND are always required; words marked `-` or NOT never match. The
**Match** option on the search page (the API's `operator`) changes the default:

| Match | `operator` | a document needs |
|---|---|---|
| Most words | (none) | the rule above |
| All words | `all` | every word |
| Any word | `any` | one word |
| Exact phrase | `phrase` | the whole query as one phrase |

### Other word forms

**Other word forms** (on by default; the API's `stemming`) also matches the
stems of the words in each document's language: `run` finds "running" and
"runners" in English texts, `Kind` finds "Kinder" in German ones, `canzone`
finds "canzoni" in Italian ones. A match of the very words ranks above a
match of another form. Switch it off, or write `exact:word`, for the written
form only.

## Languages

Extraction detects each document's language (Tika). Every document is indexed
twice: once as written (lowercased, accents folded, no word removed, in
`title_txt` and `text_txt`), and once analysed for its language, with that
language's stop words and stemmer (`title_txt_<code>`, `text_txt_<code>`). A
query searches both, so stems never cross languages: a German stem matches
German documents only.

| code | language | analysis |
|---|---|---|
| ar | Arabic | normalisation, light stemmer, stop words |
| bg | Bulgarian | stemmer, stop words |
| ca | Catalan | elision, Snowball stemmer, stop words |
| cs | Czech | stemmer, stop words |
| da | Danish | Snowball stemmer, stop words |
| de | German | normalisation (ä, ö, ü, ß), light stemmer, stop words |
| el | Greek | Greek lower case, stemmer, stop words |
| en | English | possessives, Porter stemmer, stop words (`protwords.txt` keeps words unstemmed) |
| es | Spanish | light stemmer, stop words |
| fa | Persian | normalisation, stemmer, stop words |
| fi | Finnish | light stemmer, stop words |
| fr | French | elision (l', d'), light stemmer, stop words |
| hi | Hindi | normalisation, stemmer, stop words |
| hu | Hungarian | light stemmer, stop words |
| id | Indonesian | stemmer, stop words |
| it | Italian | elision, Italian light stemmer, stop words |
| ja, ko, zh | Japanese, Korean, Chinese | overlapping pairs of characters (CJK bigrams), full-width forms folded |
| lv | Latvian | stemmer, stop words |
| nl | Dutch | Snowball stemmer with exceptions (`stemdict_nl.txt`), stop words |
| no | Norwegian (also `nb`, `nn`) | light stemmer, stop words |
| pl | Polish | Stempel stemmer |
| pt | Portuguese | light stemmer, stop words |
| ro | Romanian | Snowball stemmer, stop words |
| ru | Russian | light stemmer, stop words |
| sv | Swedish | light stemmer, stop words |
| tr | Turkish | apostrophes, Turkish lower case, Snowball stemmer, stop words |

A document in any other language, or whose language was not detected, is
searched as written only. The list lives in
`packages/oss-pipeline/src/oss_pipeline/languages.py` and the documents schema;
a new language needs both and a reindex.

Notes:

- The analysers, stemmers and word lists are Lucene's, through Solr 10. Polish
  stemming comes from Solr's `analysis-extras` module, which `compose.yaml`
  loads (`SOLR_MODULES`); an external Solr without it cannot load the documents
  schema.
- Stop words are removed only in a language's own fields, never in the fields
  as written, so "To be or not to be" still finds the phrase.
- Tika reports Chinese as `zh-cn` or `zh-tw`; the extraction stage keeps the
  language (`zh`), so such documents use the CJK fields.
- OCR reads English unless a collection sets `ocr_languages` (Tesseract codes;
  the shipped Tika image has deu, eng, fra, ita, jpn and spa; see
  [operations](operations.md)).

## Synonyms

The labels of the active packs widen a query. For each taggable concept and
individual of an active pack, `synonyms_packs.txt` in the documents configset
holds:

- an individual's names (preferred labels in every language, alternative and
  hidden labels) as equivalents: `Odysseus, Ulysses` makes either find both;
- a concept's other labels at half weight: `flood => flood, deluge|0.5,
  great flood|0.5`, so "flood" also finds "deluge", but texts that say "flood"
  rank first.

Hidden labels (known misspellings and OCR errors) are found when the correct
form is searched. Accent-free forms are added, so a query typed without
accents expands too. A one-word label that is a stop word of English or of the
label's language, labels shorter than three characters and short acronyms
(`US`, `NATO`: a query is lowercased before synonyms apply) do not expand.

Synonyms apply to the fields as written, at query time only: they change no
index and need no reindex. The file is rewritten and the documents collection
reloaded when a pack is imported or activated and when the server starts. It
is shared by every collection, and kept under 900 kB (ZooKeeper's limit on a
configset file is 1 MB); a larger vocabulary leaves terms out and says so in
the server log.

### Testing and growing synonyms

The **Synonyms** page (`GET /api/synonyms/expand?q=`) shows what a query
expands to: each run of its words that is a label of an active pack, the
concept or individual it names, and the labels it brings in with their
weight; the labels curators have proposed that wait for a new pack version
(in the collections you may read); and the tokens Solr's query analysis
makes of it, synonyms marked.

For a concept or individual, **Suggest labels** (`GET /api/synonyms/suggest?iri=`)
lists candidates from the documents you may read: spelling variants of its
labels among the names the index holds (an edit distance of one per four
letters, at most three), names that occur with it at least twice as often
as without it, and the significant words of the documents tagged with it.
A curator proposes a candidate as an alternative or hidden label (a pack
delta, as on the concept page): it tags the proposing collection's
documents at once, and becomes a search synonym when a pack version that
accepts it is activated.

## Filters, facets and sorting

- **Facets** list the values of the documents found, with counts. Click a value
  to keep only documents with it (several values of one facet: any of them);
  click **−** beside it to leave documents with it out (the API's `exclude`).
  **show more** lists up to 100 values (`facet_more`).
- **Filters in force** shows every chosen and excluded value and the date range,
  each with a remove button, also when its facet no longer lists it.
- **Trees**: a pack's scheme facets and any folder-path field (a field named
  `*_paths` written by a connector) show as a tree; choosing a folder includes
  its subfolders.
- **Dates**: the timeline counts documents by year of modification. Click a bar
  to keep that year; the bars become months, then days. The API takes
  `dates: {field, start, end}` (ISO dates; `start` included, `end` excluded) on
  any `*_dt` field.
- **Sort**: relevance (default), newest or oldest first (by modification date),
  title, size. A search without words lists the newest documents first.
- **Suggestions**: the search box suggests document titles while you type
  (`GET /api/suggest`); choosing one searches for that title as a phrase.

## List search

**Lists** searches a batch of names (up to 500), each on its own: first as a
phrase, then, when the phrase finds nothing, all its words in any order with
other word forms, then each word with one typing error allowed. The table says
which of the three found each name and the best score, and sorts on any
column. It takes the collection and a language; the API also takes `filters`,
`exclude`, `dates` and `methods` (for example `["phrase"]` to stay with the
phrase).

## Terms

The **terms** view lists the words that stand out in the top 30 hits: a word
scores by how much more often the hits contain it than the documents of the
collections searched do. Stop words of the hits' languages are left out.

## Limits

| parameter | range |
|---|---|
| `page_size` | 0 to 100 (0 counts only) |
| `page` | 1 to 500, and a page may not start beyond the 10,000th hit: export instead |
| `facet_limit` | 1 to 100 values per facet |
| semantic and hybrid | the best 1,000 documents |
| CSV and RDF export | the first 10,000 hits |
| list search | 500 names, 20 hits each |

## After an upgrade

The language fields are written at indexing time. A collection indexed before
them is still searched as written (and, for every language, with English
stemming, as before) until it is reindexed:

```bash
./oss reindex --collection <slug> --stage index
```

The synonyms need no reindex: they are in place once the server has started.
