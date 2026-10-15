# Sacred texts and mythology demo model

Status: **draft** (v0.1.0). Contract: `ontology` skill project bundle 2.2.0.

**Models:** the traditions (as a hierarchy: Abrahamic, Indian, East Asian,
European, Ancient Near East, comparative religion), text types and recurring
themes of the Semascope demo corpus; the figures (deities, heroes, teachers)
and places those texts name, with the alternative names the translations use
(Ulysses for Odysseus, Jove for Zeus, Wainamoinen for Väinämöinen); and which
motifs each tradition carries.

**Does not model:** theological claims, textual scholarship beyond the corpus,
modern organisations or living people.

**Intended use:** retrieval and cataloguing inside Semascope: hierarchical
facets from the three schemes, dictionary tagging and entity linking from the
individuals' labels, a map from the places' coordinates, and a graph of
figures shared across traditions. Tagging policy: all three schemes and the
individuals tag text (`semascope:` in `requirements.yaml`).

**Sources:** the nine Project Gutenberg texts listed in `sources.yaml`.

**Core competency questions:** figures in more than one tradition (CQ-001),
places in both Greek epics (CQ-002), traditions with a flood narrative
(CQ-003). All run as SPARQL at import.

**Unresolved:** ISS-001, whether the Buddha is a Teacher or a Deity.
