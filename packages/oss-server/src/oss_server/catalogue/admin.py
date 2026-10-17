from typing import Any

from django import forms
from django.contrib import admin

from oss_server.catalogue.models import ApiToken, Collection, CollectionRole, Document, Source
from oss_server.redact import MASK, connector_credentials, mask_text, redact, restore_masked


class MaskedSettingsForm(forms.ModelForm):  # type: ignore[type-arg]
    """Shows `settings` with credentials masked, and keeps the stored value of every field
    that comes back still masked, so the admin never displays a secret and saving the
    form never overwrites one. Type a new value over the mask to change a credential."""

    def _credentials(self) -> tuple[str, ...]:
        return connector_credentials(str(getattr(self.instance, "connector", "") or ""))

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        field = self.fields.get("settings")
        if field is not None:
            field.help_text = (
                f"Credentials show as {MASK}; leave the mask to keep the stored value."
            )
        if self.instance.pk and "settings" in self.initial:
            self.initial["settings"] = redact(
                self.instance.settings, identifiers=True, credentials=self._credentials()
            )

    def clean_settings(self) -> Any:
        value = self.cleaned_data.get("settings")
        if self.instance.pk:
            stored = type(self.instance).objects.filter(pk=self.instance.pk).first()
            if stored is not None:
                return restore_masked(value, stored.settings)
        return value


@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin[Collection]):
    list_display = ("slug", "name", "profile", "created_at")
    form = MaskedSettingsForm


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin[Source]):
    list_display = ("name", "collection", "connector", "enabled", "status", "last_run_at")
    list_filter = ("collection", "connector", "status")
    form = MaskedSettingsForm
    readonly_fields = ("masked_last_error",)
    exclude = ("last_error",)

    @admin.display(description="last error")
    def masked_last_error(self, obj: Source) -> str:
        return mask_text(obj.last_error or "")


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin[Document]):
    list_display = ("title", "collection", "source", "status", "media_type", "indexed_at")
    list_filter = ("collection", "status", "media_type")
    search_fields = ("title", "uri")
    readonly_fields = ("id", "content_hash", "stage_runs", "metadata", "fields", "tags")


@admin.register(ApiToken)
class ApiTokenAdmin(admin.ModelAdmin[ApiToken]):
    list_display = ("name", "prefix", "user", "created_at", "last_used_at")
    readonly_fields = ("key_hash", "prefix")


@admin.register(CollectionRole)
class CollectionRoleAdmin(admin.ModelAdmin[CollectionRole]):
    list_display = ("user", "collection", "role")
    list_filter = ("role", "collection")
