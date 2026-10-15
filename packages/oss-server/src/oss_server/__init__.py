"""Semascope server: the Django project behind the API, admin and UI."""

from oss_server.celery import app as celery_app

__all__ = ["celery_app"]
