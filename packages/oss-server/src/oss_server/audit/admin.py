from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from oss_server.audit.models import AuditEntry


@admin.register(AuditEntry)
class AuditEntryAdmin(admin.ModelAdmin[AuditEntry]):
    """Read-only: staff read the log here; nobody adds, changes or deletes an entry."""

    list_display = ("at", "actor_name", "via", "action", "outcome", "target_type", "target_name")
    list_filter = ("action", "outcome", "via", "target_type")
    search_fields = ("actor_name", "action", "target_name", "target_id", "collection", "ip")
    date_hierarchy = "at"
    readonly_fields = [f.name for f in AuditEntry._meta.fields]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_view_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return bool(request.user.is_active and request.user.is_staff)
