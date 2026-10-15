# Quality numbers

Recorded on the demo corpus (sixteen Project Gutenberg texts, see
`packs/demo/sources.yaml`) with the demo pack v0.1.0, by
`scripts/tag_quality.py` against `packs/demo/golden.yaml`. Document level:
for each of eight texts, which individuals, traditions, text types and
themes should be found, and which must not.

| Date | Pack | Precision (vs absent lists) | Recall | Notes |
|---|---|---|---|---|
| 2026-10-15 | semascope-demo 0.1.0 | 1.00 | 0.98 | 56 of 57 expected tags found; the miss is "epic" in the Iliad, whose text never uses the word. |

How to reproduce on a running stack with the demo loaded (`./oss demo`):

```bash
docker compose run --rm -v "$PWD/scripts:/scripts:ro" -v "$PWD/packs/demo/golden.yaml:/golden.yaml:ro" \
  cli python /scripts/tag_quality.py --collection demo --golden /golden.yaml
```

Span-level precision and recall, and NER quality, arrive with the review
queue in Phase 3, when curators' decisions give a labelled set.

## Performance baseline

Recorded on 2026-10-15 on the development host (AMD Ryzen AI MAX+ 395, CPU
only, `--size medium`, worker concurrency 2) against the demo collection of
22 texts (27 MB; most are whole books of 0.5 to 4.5 MB), with
`scripts/bench.py --runs 10`. Latencies are what the API measured (Solr time)
and end to end from inside the compose network.

| mode | p50 ms (api) | p95 ms (api) | p95 ms (end to end) |
|---|---|---|---|
| keyword | 41 to 150 | 55 to 185 | 70 to 204 |
| semantic | 17 to 22 | 23 to 46 | 82 to 541 (first query loads the model) |
| hybrid | 42 to 85 | 67 to 133 | 134 to 186 |

Stage time per document (median / p95): structure 54 / 250 ms, patterns
122 / 664 ms, tag 120 / 608 ms, link 9 / 177 ms, preview 2 / 79 ms, index
221 / 632 ms, graph 142 / 430 ms, **ner 28 / 59 s**, **embed 117 / 151 s**.
Named-entity recognition and embedding dominate on book-length texts
(spaCy's small English model over up to 300,000 characters; FastEmbed on up
to 800 chunks on CPU). Ordinary documents of a few pages take well under a
second per stage. Both caps are per-collection settings (`ner_max_chars`,
`embed_max_chunks`).

rc.2 (2026-10-16) cut the text into paragraph windows and dropped the
tagger and parser from the spaCy pipeline. Replaying only the NER stage over
the demo collection (22 documents, 16 of them capped at 300,000 characters)
on the same host took **5.4 s median, 6.5 s p95, 8.9 s at most** per
document. Part of the difference from rc.1's 28 s is that the rc.1 figure
was measured during full indexing, with embedding running alongside on the
same cores; the windowed pipeline alone measured about a quarter faster
than the whole-text call in isolation.

The `gpu` runtime, measured on an RTX 3090 with two worker processes
replaying the same 22 documents (`reindex --stage ner,embed`): **embed 0.6 s
median, 1.7 s at most** per document (against 117 s median on CPU), and
GLiNER2-large zero-shot entities **15.8 s median, 32 s at most** per
document, slower than spaCy's small model on CPU but asked for the packs'
classes as well as the base four (the Odyssey gained hero, deity and
teacher facets without training). The two processes held 22 GB of the card
at peak. `remote` moves the same stages to services; see
[operations](operations.md#runtimes).
