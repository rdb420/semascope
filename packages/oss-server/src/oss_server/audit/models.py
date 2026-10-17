"""The audit log: one append-only row per security-relevant action.

Who (`actor_id`, `actor_name`, `via`), when (`at`), what (`action`, `outcome`, the
target and `detail`) and from where (`ip`, `user_agent`). Rows are never changed:
`save` refuses an existing row, `delete` and queryset updates and deletes refuse, and
on PostgreSQL a trigger refuses UPDATE as well (migration 0001). Only the retention
sweep (`purge_before`) removes rows older than `SEMASCOPE_AUDIT_RETENTION_DAYS`.

The actor is kept as an id and a name, not a foreign key, so deleting a user keeps
(and never rewrites) the entries about them. No secret is ever stored: details pass
through `oss_server.redact` and hold field names, never credential values.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import models
from django.utils import timezone


class AppendOnlyError(RuntimeError):
    """An attempt to change or delete an audit entry."""


class AuditQuerySet(models.QuerySet["AuditEntry"]):
    def update(self, **kwargs: Any) -> int:
        raise AppendOnlyError("audit entries are never changed")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise AppendOnlyError("audit entries are removed only by the retention sweep")

    def purge_before(self, cutoff: datetime) -> int:
        """Remove the entries older than `cutoff` (the retention sweep, nothing else)."""

        qs = AuditEntry.objects.filter(at__lt=cutoff)
        return int(qs._raw_delete(qs.db))  # noqa: SLF001 - bypasses the refusal above


class AuditEntry(models.Model):
    class Outcome(models.TextChoices):
        OK = "ok"
        FAILED = "failed"
        REFUSED = "refused"

    at = models.DateTimeField(default=timezone.now, db_index=True)
    action = models.CharField(max_length=64, db_index=True)
    outcome = models.CharField(max_length=16, choices=Outcome.choices, default=Outcome.OK)
    actor_id = models.IntegerField(null=True, blank=True, db_index=True)
    actor_name = models.CharField(max_length=150, blank=True)
    via = models.CharField(
        max_length=16, blank=True, help_text="session, token, admin, anonymous or system"
    )
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=200, blank=True)
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=200, blank=True)
    target_name = models.CharField(max_length=200, blank=True)
    collection = models.CharField(max_length=100, blank=True, db_index=True)
    detail = models.JSONField(default=dict, blank=True)

    objects = AuditQuerySet.as_manager()

    class Meta:
        ordering = ["-at", "-id"]
        verbose_name = "audit entry"
        verbose_name_plural = "audit log"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise AppendOnlyError("audit entries are never changed")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise AppendOnlyError("audit entries are removed only by the retention sweep")

    def __str__(self) -> str:
        who = self.actor_name or self.via or "?"
        return f"{self.at:%Y-%m-%d %H:%M:%S} {who} {self.action} {self.outcome}"
