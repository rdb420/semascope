"""Catalogue models.

Document ids are deterministic: sha256(collection + uri)[:32], so a re-run
of the same source updates rather than duplicates, in Postgres, Solr and the
graph alike.
"""

from __future__ import annotations

import hashlib

from django.db import models


def document_id(collection_slug: str, uri: str) -> str:
    return hashlib.sha256(f"{collection_slug}\n{uri}".encode()).hexdigest()[:32]


class Collection(models.Model):
    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    profile = models.CharField(max_length=32, default="default")
    settings = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["slug"]

    def __str__(self) -> str:
        return self.slug


class Source(models.Model):
    class Status(models.TextChoices):
        IDLE = "idle"
        RUNNING = "running"
        FAILED = "failed"

    collection = models.ForeignKey(Collection, on_delete=models.CASCADE, related_name="sources")
    name = models.CharField(max_length=200)
    connector = models.CharField(max_length=64)
    settings = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=True)
    schedule = models.CharField(
        max_length=64, blank=True, help_text="cron expression, empty = manual"
    )
    cursor = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.IDLE)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["collection", "name"]
        constraints = [
            models.UniqueConstraint(fields=["collection", "name"], name="uniq_source_name")
        ]

    def __str__(self) -> str:
        return f"{self.collection.slug}/{self.name}"


class Document(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        PROCESSING = "processing"
        INDEXED = "indexed"
        FAILED = "failed"
        DELETED = "deleted"

    id = models.CharField(primary_key=True, max_length=32)
    collection = models.ForeignKey(Collection, on_delete=models.CASCADE, related_name="documents")
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="documents")
    uri = models.TextField()
    content_hash = models.CharField(max_length=64)
    media_type = models.CharField(max_length=128, blank=True)
    language = models.CharField(max_length=8, blank=True)
    title = models.TextField(blank=True)
    size = models.BigIntegerField(null=True, blank=True)
    modified_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    error = models.TextField(blank=True)
    # Stored extraction, so enrichment and indexing can be replayed without Tika.
    text = models.TextField(blank=True)
    pages = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    fields = models.JSONField(default=dict, blank=True)
    tags = models.JSONField(default=list, blank=True)
    stage_runs = models.JSONField(default=list, blank=True)
    blob_path = models.TextField(blank=True)
    indexed_at = models.DateTimeField(null=True, blank=True)
    seen_at = models.DateTimeField(
        null=True, blank=True, help_text="last discovery pass that saw it"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["collection", "status"]),
            models.Index(fields=["source", "seen_at"]),
            models.Index(fields=["content_hash"]),
        ]

    def __str__(self) -> str:
        return f"{self.collection.slug}:{self.uri}"


class ApiToken(models.Model):
    """A bearer token for scripts and the CLI. The secret is stored hashed."""

    name = models.CharField(max_length=100)
    key_hash = models.CharField(max_length=64, unique=True)
    prefix = models.CharField(max_length=8)
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="api_tokens")
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    @staticmethod
    def hash_key(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    def __str__(self) -> str:
        return f"{self.name} ({self.prefix}…)"
