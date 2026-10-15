from django.contrib import admin

from oss_server.curation.models import Annotation, Event, Proposal, Review, Webhook


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin[Review]):
    list_display = ("kind", "label", "collection", "status", "confidence", "created_at")
    list_filter = ("kind", "status", "collection")


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


@admin.register(Webhook)
class WebhookAdmin(admin.ModelAdmin[Webhook]):
    list_display = ("url", "enabled", "last_event_id", "created_at")
