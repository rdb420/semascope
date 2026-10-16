"""Browser search integration (parity row SRCH-15): an OpenSearch 1.1 description.

The description names the search page and a suggestions URL with absolute
URLs, so it is built per request from the host the browser used. It holds no
data (the product name and two URL templates), so it is served to anyone: a
browser fetches it when it offers to add the search engine, not necessarily
with the user's cookies. Searching and suggestions need a signed-in session,
as everywhere else.
"""

from __future__ import annotations

from typing import Any
from xml.sax.saxutils import escape, quoteattr

from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import Router

from oss_server.exports.load import site_of

CONTENT_TYPE = "application/opensearchdescription+xml"


def description(site: str) -> str:
    search = f"{site}/?q={{searchTerms}}"
    suggest = f"{site}/api/opensearch/suggest?q={{searchTerms}}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/"'
        ' xmlns:moz="http://www.mozilla.org/2006/browser/search/">\n'
        "  <ShortName>Semascope</ShortName>\n"
        "  <Description>Search this Semascope</Description>\n"
        "  <InputEncoding>UTF-8</InputEncoding>\n"
        f'  <Image width="16" height="16" type="image/svg+xml">{escape(site)}/favicon.svg</Image>\n'
        f'  <Url type="text/html" method="get" template={quoteattr(search)}/>\n'
        '  <Url type="application/x-suggestions+json" method="get"'
        f" template={quoteattr(suggest)}/>\n"
        f'  <Url type="application/opensearchdescription+xml" rel="self"'
        f" template={quoteattr(site + '/api/opensearch.xml')}/>\n"
        f"  <moz:SearchForm>{escape(site)}/</moz:SearchForm>\n"
        "</OpenSearchDescription>\n"
    )


def opensearch_xml(request: HttpRequest) -> HttpResponse:
    """The OpenSearch description the UI links with `<link rel="search">`."""

    return HttpResponse(description(site_of(request)), content_type=CONTENT_TYPE)


def opensearch_suggest(request: HttpRequest, q: str = "") -> JsonResponse:
    """Title suggestions in the OpenSearch suggestions format: `[query, [titles]]`."""

    from oss_server.api import root

    titles: list[Any] = root.suggest(request, q).suggestions if q.strip() else []
    return JsonResponse([q, titles], safe=False, content_type="application/x-suggestions+json")


def mount(router: Router) -> None:
    router.get("/opensearch.xml", auth=None, tags=["search"])(opensearch_xml)
    router.get("/opensearch/suggest", tags=["search"])(opensearch_suggest)
