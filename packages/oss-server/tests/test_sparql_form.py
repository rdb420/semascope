"""The pack SPARQL console accepts SELECT and ASK after any prologue, and nothing else."""

from __future__ import annotations

import pytest

from oss_server.api.knowledge import _query_form


@pytest.mark.parametrize(
    ("query", "form"),
    [
        ("SELECT ?s WHERE { ?s ?p ?o }", "SELECT"),
        (
            "PREFIX skos: <http://www.w3.org/2004/02/skos/core#>\n"
            "PREFIX ex: <https://example.org/p#>\n"
            "SELECT ?c WHERE { ?c a skos:Concept }",
            "SELECT",
        ),
        ("# a comment\nprefix : <https://example.org/p#>\nask { ?s ?p ?o }", "ASK"),
        ("BASE <http://example.org/> PREFIX a: <y> CONSTRUCT { } WHERE { }", "CONSTRUCT"),
        ("PREFIX select: <https://example.org/s#> DESCRIBE <a>", "DESCRIBE"),
        ("", ""),
    ],
)
def test_query_form_skips_the_prologue(query: str, form: str) -> None:
    assert _query_form(query) == form
