"""Analysis views over a search's results (ADR-0012: ANL-12 to ANL-15).

Pure functions over document texts, kept apart from the API so they can be
tested without Solr: `clusters` (k-means over TF-IDF term vectors or passage
embeddings, labelled by their significant terms), `compare` (significant terms
of one set of documents against another or the index), `wordtree` (a
concordance tree around a word or phrase) and `diff` (line and word diff of two
texts). `hits` finds the documents of a search, read-scoped twice: in Solr and
again in Postgres.
"""
