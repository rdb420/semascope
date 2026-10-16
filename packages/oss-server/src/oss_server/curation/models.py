"""Curation models.

Review: something a machine proposed that a person should look at (a
low-confidence tag, an unlinked entity that resembles a pack individual, a
SHACL violation). Its status is the decision in force; every decision on it
(defer, then accept, say) is kept as a ReviewDecision. Decision -> Proposal.

Proposal: the record of a curation decision (rebuild specification 10.4):
applied to Semascope's stores at once and exported through the outbox so an
external system of record can mirror, reject or audit it. The log is
append-only: a new decision on the same target is a new row, and the newest
one is in force. `external_ref` is a stable hash per decision (chained to the
decision it supersedes), so a re-export is idempotent. `params["tag"]` keeps
the tag as it was when it was decided (stage, confidence, spans, provenance),
so a rejected tag's evidence outlives the tag.

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


class ReviewDecision(models.Model):
    """One decision on a review item: the history behind `Review.status` (append-only).

    Deferring and later deciding leaves two rows; the review keeps the decision in
    force. An accept or reject that became a proposal points at it.
    """

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="decisions")
    decision = models.CharField(max_length=16)  # accept | reject | defer
    note = models.TextField(blank=True)
    decided_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True)
    decided_at = models.DateTimeField(auto_now_add=True)
    proposal = models.ForeignKey(
        "Proposal", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["decided_at", "pk"]

    def __str__(self) -> str:
        return f"{self.decision} on review {self.review_id}"


class Proposal(models.Model):
    class Kind(models.TextChoices):
        ACCEPT_TAG = "accept_tag"
        REJECT_TAG = "reject_tag"
        LINK_ENTITY = "link_entity"
        ADD_TAG = "add_tag"
        REMOVE_TAG = "remove_tag"
        ADD_NOTE = "add_note"
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
        indexes = [
            models.Index(fields=["collection", "document", "target"], name="proposal_target_idx")
        ]

    @staticmethod
    def make_ref(
        collection: str,
        kind: str,
        target: str,
        params: dict[str, Any],
        document_id: str | None,
        *,
        previous: str = "",
    ) -> str:
        """The decision's stable reference; `previous` is the ref of the decision it supersedes.

        The snapshot of the decided tag (`params["tag"]`) is evidence, not the decision,
        and is left out.
        """

        decided = {k: v for k, v in params.items() if k != "tag"}
        parts: list[Any] = [collection, kind, target, decided, document_id]
        if previous:  # the first decision on a target keeps the rc.2 formula
            parts.append(previous)
        payload = json.dumps(parts, sort_keys=True, default=str)
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


class BulkTagJob(models.Model):
    """One tag decision applied to every document a search finds, run by a worker.

    Each document gets its own proposal (accept or add, reject or remove), with the
    job named in its reason, so the log, the outbox and the per-document history
    read as if a curator had decided each document; review items on the tag are
    settled as a direct decision settles them.
    """

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        DONE = "done"
        FAILED = "failed"

    collection = models.ForeignKey(
        "catalogue.Collection", on_delete=models.CASCADE, related_name="bulk_tag_jobs"
    )
    query = models.JSONField(default=dict, help_text="the search: q and filters")
    iri = models.CharField(max_length=500)
    label = models.CharField(max_length=500, blank=True)
    kind = models.CharField(max_length=16, default="concept")
    decision = models.CharField(max_length=8)  # accept | reject
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    matched = models.PositiveIntegerField(default=0)
    decided = models.PositiveIntegerField(default=0, help_text="new proposals recorded")
    unchanged = models.PositiveIntegerField(default=0, help_text="the decision was in force")
    unapplied = models.PositiveIntegerField(default=0, help_text="recorded, a store missed it")
    error = models.TextField(blank=True)
    created_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self) -> str:
        return f"{self.decision} {self.iri} on {self.collection_id} ({self.status})"
