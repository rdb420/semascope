from pathlib import Path

from django.conf import settings
from django.contrib import admin
from django.http import FileResponse, Http404, HttpRequest, JsonResponse
from django.urls import include, path, re_path
from django.views.decorators.cache import never_cache

from oss_core import __version__
from oss_server.api.root import api

WEB_DIR = Path(__file__).resolve().parent / "static" / "web"


def healthz(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok", "version": __version__})


@never_cache
def spa(request: HttpRequest, path: str = "") -> FileResponse:
    """Serve the built browser UI for every non-API path (client-side routing)."""

    index = WEB_DIR / "index.html"
    if not index.exists():
        raise Http404("the browser UI has not been built; see packages/oss-web")
    return FileResponse(index.open("rb"), content_type="text/html")


urlpatterns = [
    path("healthz", healthz),
    path("admin/", admin.site.urls),
    path("api/", api.urls),
    *([path("accounts/", include("allauth.urls"))] if settings.OIDC_ENABLED else []),
    re_path(r"^(?!api/|admin/|accounts/|static/|assets/|healthz)(?P<path>.*)$", spa),
]
