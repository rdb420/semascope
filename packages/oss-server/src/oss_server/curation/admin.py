from typing import Any

from django import forms
from django.contrib import admin

from oss_server.curation.models import (
    Annotation,
    Event,
    Proposal,
    Review,
    ReviewDecision,
    Webhook,
)


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin[Review]):
    list_display = ("kind", "label", "collection", "status", "confidence", "created_at")
    list_filter = ("kind", "status", "collection")


@admin.register(ReviewDecision)
class ReviewDecisionAdmin(admin.ModelAdmin[ReviewDecision]):
    list_display = ("review", "decision", "decided_by", "decided_at")
    list_filter = ("decision",)


@admin.register(Proposal)
class ProposalAdmin(admin.ModelAdmin[Proposal]):
    list_display = ("kind", "target", "collection", "decided_by", "decided_at", "applied")
    list_filter = ("kind", "collection", "applied")
    readonly_fields = ("external_ref",)


@admin.register(Annotation)
class AnnotationAdmin(admin.ModelAdmin[Annotation]):
    list_display = ("document", "author", "created_at")


@admin.register(Event)
class EventAdmin(admin.ModelAdmin[Event]):
    list_display = ("id", "kind", "collection", "subject", "created_at")
    list_filter = ("kind",)


class WebhookForm(forms.ModelForm):  # type: ignore[type-arg]
    """The signing secret is written, never shown: an empty field keeps the stored one."""

    secret = forms.CharField(
        required=False,
        max_length=128,
        widget=forms.PasswordInput(render_value=False),
        help_text="HMAC-SHA256 key for X-Semascope-Signature; leave empty to keep the current one",
    )

    class Meta:
        model = Webhook
        fields = ("url", "kinds", "secret", "enabled")

    def clean_secret(self) -> Any:
        value = self.cleaned_data.get("secret") or ""
        if not value and self.instance.pk:
            stored = Webhook.objects.filter(pk=self.instance.pk).values_list("secret", flat=True)
            return stored.first() or ""
        return value


@admin.register(Webhook)
class WebhookAdmin(admin.ModelAdmin[Webhook]):
    list_display = ("host", "enabled", "last_event_id", "created_at")
    form = WebhookForm

    @admin.display(description="address")
    def host(self, obj: Webhook) -> str:
        from oss_server.redact import mask_url

        return mask_url(obj.url)
