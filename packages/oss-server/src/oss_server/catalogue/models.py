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

    class GraphStatus(models.TextChoices):
        """The Neo4j projection of an indexed document; search does not wait for it."""

        NONE = "", "not projected"  # no graph stage in the profile, or never run
        OK = "ok"
        PENDING = "pending"  # Neo4j was unavailable; retried, then re-projected by `recover`
        FAILED = "failed"  # a permanent graph error; `oss reindex` projects it again

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
    graph_status = models.CharField(
        max_length=16, choices=GraphStatus.choices, default=GraphStatus.NONE, blank=True
    )
    # Worker reliability (docs/operations.md#crashes-and-recovery)
    item = models.JSONField(
        default=dict, blank=True, help_text="the connector item the last processing run was given"
    )
    processing_since = models.DateTimeField(
        null=True, blank=True, help_text="when the current processing run started"
    )
    attempts = models.PositiveSmallIntegerField(
        default=0, help_text="runs started and not finished: a worker died or was killed"
    )
    fingerprint = models.CharField(
        max_length=64,
        blank=True,
        help_text="content, profile, stages, packs and settings it was indexed with",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["collection", "status"]),
            models.Index(fields=["source", "seen_at"]),
            models.Index(fields=["content_hash"]),
            models.Index(fields=["status", "processing_since"]),
            models.Index(fields=["graph_status"]),
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


class SavedSearch(models.Model):
    """A named search a user keeps; with `alert` it emits an event when new documents match."""

    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="saved_searches")
    name = models.CharField(max_length=200)
    query = models.JSONField(default=dict, help_text="the SearchIn payload")
    alert = models.BooleanField(default=False)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_total = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["user", "name"], name="uniq_saved_search")]

    def __str__(self) -> str:
        return self.name


class CollectionRole(models.Model):
    """A user's role on one collection, or on every collection when `collection` is empty."""

    ROLE_CHOICES = [(r, r) for r in ("reader", "curator", "editor", "admin")]

    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="collection_roles")
    collection = models.ForeignKey(
        Collection, on_delete=models.CASCADE, null=True, blank=True, related_name="roles"
    )
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default="reader")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "collection"], name="uniq_collection_role")
        ]

    def __str__(self) -> str:
        return f"{self.user} is {self.role} on {self.collection or 'every collection'}"
