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
