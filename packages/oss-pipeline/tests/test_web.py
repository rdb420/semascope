import re

import pytest

from oss_pipeline.connectors.web import _allowed, _links, _refuse_private


def test_links_are_resolved_and_deduplicated() -> None:
    html = (
        '<a href="/a">A</a> <a href="b#frag">B</a> '
        '<a href="http://other.example/x">X</a> <a href="/a">again</a>'
    )
    assert _links("https://site.example/dir/page", html) == [
        "https://site.example/a",
        "https://site.example/dir/b",
        "http://other.example/x",
    ]


def test_allowed_filters() -> None:
    inc = [re.compile(r"/docs/")]
    assert _allowed("https://x.example/docs/a", inc, [])
    assert not _allowed("https://x.example/blog/a", inc, [])
    assert not _allowed("https://x.example/docs/a.png", inc, [])
    assert not _allowed("ftp://x.example/docs/a", [], [])
    assert not _allowed("https://x.example/docs/private", [], [re.compile("private")])


def test_private_addresses_are_refused() -> None:
    with pytest.raises(ValueError):
        _refuse_private("http://127.0.0.1/")
    with pytest.raises(ValueError):
        _refuse_private("http://localhost/")
