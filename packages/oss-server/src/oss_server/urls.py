from django.contrib import admin
from django.http import HttpRequest, JsonResponse
from django.urls import path

from oss_core import __version__
from oss_server.api.root import api


def healthz(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok", "version": __version__, "phase": 1})


urlpatterns = [
    path("healthz", healthz),
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]
