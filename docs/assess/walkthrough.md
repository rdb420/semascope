# Walkthrough: one piece of evidence, end to end

Status: **draft**, 2026-10-16. The steps and commands are final; the
screenshots marked *to capture* are still to be taken on a stack running
1.0.0-rc.2 with the demo loaded. `tests/e2e/ui_smoke.py` already captures
the pages marked *smoke* (its file name in brackets).

The thread: the demo corpus's *Odyssey* (Samuel Butler's translation)
names its hero **Ulysses**. The demo pack defines the individual
`demo:odysseus` with the preferred label "Odysseus" and the alternative
label "Ulysses". This walkthrough follows that one link from the file on
disk to the decision that leaves the system.

## 1. Ingest

```bash
./oss up
./oss demo                      # sixteen texts and the demo pack, collection "demo"
./oss stats                     # documents per state, chunks per model
```

`oss demo` imports and activates the pack, then indexes the corpus. Each
document runs the `default` profile: fetch, extract, structure, patterns,
tag, ner, link, preview, embed, index, graph.

*To capture:* `oss stats` output after indexing.

## 2. Search and facets

Search for `Ulysses` in the demo collection. The *Odyssey* is found, and
the query chip shows that "Ulysses" was recognised as Odysseus. The
traditions facet offers *Greek*, and the hero facet lists Odysseus.

*Smoke:* search page [01-search.png], query chips [02-query.png],
hierarchical facet [03-facet.png].

## 3. The document and why it is tagged

Open the *Odyssey*. The tag list shows **Odysseus** from the `tag` stage,
its confidence and the number of occurrences: the translation uses
"Ulysses" over six hundred times and "Odysseus" a handful. The confidence is
1.0 for a preferred-label match and 0.9 for an alternative label; in rc.2
it is set by whichever label occurs first. The stage-run panel shows which stage versions
produced the tags and how long each took.

The same facts through the API:

```bash
curl -sk -H "Authorization: Bearer $SEMASCOPE_API_TOKEN" \
  https://localhost:8443/api/curation/documents/<id>/tags | jq '.[] | select(.label=="Odysseus")'
```

which returns the IRI, stage, confidence, `pack_id`, `pack_version` and
the occurrence count ([Explainability](explainability.md) lists every
field).

*Smoke:* document page [04-document.png]. *To capture:* the tag tooltip,
and the API answer.

## 4. The concept page

Follow Odysseus to his page: definition, alternative names, class (Hero),
traditions, and every document that mentions him, across the *Odyssey*,
the *Iliad* and Bulfinch.

*Smoke:* packs [05-packs.png], pack tree [06-pack.png], concept page
[07-concept.png].

## 5. The graph

Open the graph from the document. Odysseus is an `Entity` linked to the
*Odyssey* by `MENTIONS` and to his pack version by `DEFINED_IN`. Expand
him to see the other texts that mention him; ask for the shortest path
from Odysseus to Hector, which runs through the *Iliad*.

*Smoke:* graph hubs [08-graph.png]. *To capture:* the neighbourhood of
Odysseus, and a shortest path.

## 6. Review

Open the review queue for the demo collection. It holds the links the
machine was less sure of: tags matched on hidden labels, named entities that
resemble a pack individual without matching it, and SHACL violations when
the `full` profile ran. Accept one link item that proposes Odysseus for an
unlinked name, and reject one tag.

*Smoke:* review queue [09-review.png]. *To capture:* the item before and
after the decision, and the document page showing the rejected tag struck
through.

## 7. The decision leaves the system

Each decision is a proposal with a stable reference, applied at once to
PostgreSQL, Solr and Neo4j, and written to the outbox:

```bash
curl -sk -H "Authorization: Bearer $SEMASCOPE_API_TOKEN" \
  "https://localhost:8443/api/curation/events?collection=demo&kind=proposal" | jq '.[-2:]'
```

Each event carries the proposal's kind, target, parameters, reason, who
decided and when, and the `external_ref` a downstream system uses to
mirror or reject it. A webhook, if configured, receives the same event
with an HMAC signature. The graph now has a `CURATED` edge beside the
machine's edge, and `./oss reindex --collection demo` leaves the decision
in place.

*To capture:* the two events, the `CURATED` edge in the graph view, and
the document after a reindex.

## 8. Map and terms

The places of the pack with coordinates appear on the map (Ithaca, Troy,
Mount Olympus); the terms view shows what is significant in a result set,
for example the results for `flood`.

*Smoke:* map [10-map.png], terms [11-terms.png].

## Capturing the screenshots

```bash
SEMASCOPE_UI_PASSWORD=... uv run tests/e2e/ui_smoke.py   # 01 to 11
```

The *to capture* shots need the smoke test extended with the review
decision, the graph path and the tag tooltip, so that every image on this
page is reproducible from a script rather than taken by hand.
