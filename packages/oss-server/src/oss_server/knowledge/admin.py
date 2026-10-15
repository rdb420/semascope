from django.contrib import admin

from oss_server.knowledge.models import Concept, Pack, PackBinding, PackDelta


@admin.register(Pack)
class PackAdmin(admin.ModelAdmin[Pack]):
    list_display = (
        "pack_id",
        "version",
        "title",
        "status",
        "bundle_status",
        "imported_at",
        "activated_at",
    )
    list_filter = ("status",)
    readonly_fields = ("manifest_hash", "graph_iri", "counts", "findings", "cq_results")


@admin.register(Concept)
class ConceptAdmin(admin.ModelAdmin[Concept]):
    list_display = ("pref_label", "kind", "scheme_slug", "path", "pack", "taggable")
    list_filter = ("kind", "scheme_slug", "pack", "taggable")
    search_fields = ("pref_label", "iri", "alt_labels")


@admin.register(PackBinding)
class PackBindingAdmin(admin.ModelAdmin[PackBinding]):
    list_display = ("collection", "pack_id", "precedence", "enabled")


@admin.register(PackDelta)
class PackDeltaAdmin(admin.ModelAdmin[PackDelta]):
    list_display = ("kind", "pack_id", "collection", "status", "proposed_by", "created_at")
    list_filter = ("kind", "status", "pack_id")
