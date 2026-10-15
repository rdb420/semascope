"""Django settings, built from the one Settings object every package shares."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import django_stubs_ext

from oss_core.config import get_settings

django_stubs_ext.monkeypatch()
_settings = get_settings()
BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = _settings.semascope_secret_key.get_secret_value()
DEBUG = _settings.semascope_debug
ALLOWED_HOSTS = [_settings.semascope_host, "server", "localhost", "127.0.0.1"]
CSRF_TRUSTED_ORIGINS = [
    f"https://{_settings.semascope_host}",
    f"https://{_settings.semascope_host}:8443",
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "oss_server.catalogue",
    "oss_server.knowledge",
    "oss_server.curation",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "oss_server.urls"
WSGI_APPLICATION = "oss_server.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]


def _database() -> dict[str, object]:
    url = urlparse(_settings.database_url)
    if url.scheme.startswith("sqlite"):
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": url.path or ":memory:"}
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": url.path.lstrip("/"),
        "USER": url.username,
        "PASSWORD": url.password,
        "HOST": url.hostname,
        "PORT": url.port or 5432,
        "CONN_MAX_AGE": 60,
    }


DATABASES = {"default": _database()}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "en-au"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = _settings.semascope_data_dir / "static"
STATIC_ROOT.mkdir(parents=True, exist_ok=True)
STATICFILES_DIRS = [p for p in [BASE_DIR / "static"] if p.exists()]
WHITENOISE_ROOT = BASE_DIR / "static" / "web"  # built UI: /assets/*, /favicon.svg
WHITENOISE_INDEX_FILE = False
STORAGES = {
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
X_FRAME_OPTIONS = "DENY"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

SEMASCOPE = _settings
