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
`embed_max_chunks`), and the GPU profile moves embeddings to CUDA.
