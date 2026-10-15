from django.contrib import admin

from oss_server.catalogue.models import ApiToken, Collection, CollectionRole, Document, Source


@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin[Collection]):
    list_display = ("slug", "name", "profile", "created_at")


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin[Source]):
    list_display = ("name", "collection", "connector", "enabled", "status", "last_run_at")
    list_filter = ("collection", "connector", "status")


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
