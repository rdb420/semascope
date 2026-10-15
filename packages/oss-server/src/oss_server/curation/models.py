"""Curation models.

Review: something a machine proposed that a person should look at (a
low-confidence tag, an unlinked entity that resembles a pack individual, a
SHACL violation). Decision -> Proposal.

Proposal: the record of a curation decision (rebuild specification 10.4):
applied to Semascope's stores at once and exported through the outbox so an
external system of record can mirror, reject or audit it. `external_ref`
is a stable hash so a re-export is idempotent.

Annotation: a curator's note or tag on a document.

Event: the outbox. Webhook: a subscriber to it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from django.db import models


class Review(models.Model):
    class Kind(models.TextChoices):
        TAG = "tag"  # a machine tag with confidence below the threshold
        LINK = "link"  # an NER entity that resembles a pack individual
        VIOLATION = "violation"  # a SHACL violation on the document graph
        DELTA = "delta"  # a curator's pack delta awaiting a second pair of eyes

    class Status(models.TextChoices):
        OPEN = "open"
        ACCEPTED = "accepted"
        REJECTED = "rejected"
        DEFERRED = "deferred"

    collection = models.ForeignKey(
        "catalogue.Collection", on_delete=models.CASCADE, related_name="reviews"
    )
    document = models.ForeignKey(
        "catalogue.Document",
        on_delete=models.CASCADE,
        related_name="reviews",
        null=True,
        blank=True,
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    target = models.CharField(max_length=500, help_text="IRI, entity key or shape path")
    label = models.CharField(max_length=500, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    decided_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["collection", "status", "kind"])]
        constraints = [
            models.UniqueConstraint(fields=["document", "kind", "target"], name="uniq_review_item")
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.label or self.target} ({self.status})"


class Proposal(models.Model):
    class Kind(models.TextChoices):
        ACCEPT_TAG = "accept_tag"
        REJECT_TAG = "reject_tag"
        LINK_ENTITY = "link_entity"
        ADD_TAG = "add_tag"
        REMOVE_TAG = "remove_tag"
        ADD_NOTE = "add_note"
        ADD_LABEL = "add_label"
        NEW_CONCEPT = "new_concept"
        MERGE_ENTITIES = "merge_entities"
        DISMISS_VIOLATION = "dismiss_violation"

    collection = models.ForeignKey(
        "catalogue.Collection", on_delete=models.CASCADE, related_name="proposals"
    )
    document = models.ForeignKey(
        "catalogue.Document",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="proposals",
    )
    kind = models.CharField(max_length=24, choices=Kind.choices)
    target = models.CharField(max_length=500)
    params = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    pack_id = models.CharField(max_length=64, blank=True)
    pack_version = models.CharField(max_length=32, blank=True)
    decided_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True)
    decided_at = models.DateTimeField(auto_now_add=True)
    external_ref = models.CharField(max_length=64, unique=True, editable=False)
    applied = models.BooleanField(default=True)

    class Meta:
        ordering = ["-decided_at"]

    @staticmethod
    def make_ref(
        collection: str, kind: str, target: str, params: dict[str, Any], document_id: str | None
    ) -> str:
        payload = json.dumps(
            [collection, kind, target, params, document_id], sort_keys=True, default=str
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.external_ref:
            self.external_ref = self.make_ref(
                self.collection.slug, self.kind, self.target, self.params, self.document_id
            )
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.kind} {self.target}"


class Annotation(models.Model):
    document = models.ForeignKey(
        "catalogue.Document", on_delete=models.CASCADE, related_name="annotations"
    )
    author = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True)
    text = models.TextField(blank=True)
    tags = models.JSONField(default=list, blank=True, help_text="free tags (strings)")
    span = models.JSONField(
        null=True, blank=True, help_text="{start, end, page} when the note is about a passage"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"note on {self.document_id} by {self.author_id}"


class Event(models.Model):
    """The outbox: one row per thing that happened, read by cursor."""

    kind = models.CharField(max_length=48, db_index=True)
    collection = models.CharField(max_length=64, blank=True, db_index=True)
    subject = models.CharField(max_length=500, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.id} {self.kind} {self.subject}"


class Webhook(models.Model):
    url = models.URLField()
    kinds = models.JSONField(default=list, blank=True, help_text="event kinds to send; empty = all")
    secret = models.CharField(
        max_length=128, blank=True, help_text="HMAC-SHA256 of the body in X-Semascope-Signature"
    )
    enabled = models.BooleanField(default=True)
    last_event_id = models.BigIntegerField(default=0)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.url
