"""Pack registry and the derived vocabulary.

A Pack row is one imported version of a bundle. Concept rows are the derived
vocabulary (SKOS concepts, named individuals and OWL classes) that the UI,
the facets and the Reconciliation API read; the full triples live in the
Oxigraph store under a named graph per pack version.
"""

from __future__ import annotations

from django.db import models


class Pack(models.Model):
    class Status(models.TextChoices):
        IMPORTED = "imported"
        ACTIVE = "active"
        RETIRED = "retired"
        FAILED = "failed"

    pack_id = models.SlugField(max_length=64)
    version = models.CharField(max_length=32)
    title = models.CharField(max_length=200)
    base_iri = models.CharField(max_length=500)
    bundle_status = models.CharField(
        max_length=32, blank=True, help_text="status from requirements.yaml"
    )
    licence = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.IMPORTED)
    manifest_hash = models.CharField(max_length=64)
    graph_iri = models.CharField(max_length=500, help_text="named graph in the pack store")
    source_path = models.TextField(blank=True, help_text="where the bundle was read from")
    counts = models.JSONField(default=dict, blank=True)
    findings = models.JSONField(default=list, blank=True)
    cq_results = models.JSONField(default=dict, blank=True)
    requirements = models.JSONField(default=dict, blank=True)
    decisions = models.JSONField(default=list, blank=True)
    sources = models.JSONField(default=list, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["pack_id", "-imported_at"]
        constraints = [
            models.UniqueConstraint(fields=["pack_id", "version"], name="uniq_pack_version")
        ]

    def __str__(self) -> str:
        return f"{self.pack_id}@{self.version}"


class Concept(models.Model):
    """A taggable or navigable term derived from a pack: concept, individual or class."""

    class Kind(models.TextChoices):
        CONCEPT = "concept"
        INDIVIDUAL = "individual"
        CLASS = "class"
        SCHEME = "scheme"

    pack = models.ForeignKey(Pack, on_delete=models.CASCADE, related_name="concepts")
    iri = models.CharField(max_length=500)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    pref_label = models.CharField(max_length=500)
    lang = models.CharField(max_length=8, blank=True)
    alt_labels = models.JSONField(default=list, blank=True)
    hidden_labels = models.JSONField(default=list, blank=True)
    definition = models.TextField(blank=True)
    scheme = models.CharField(max_length=500, blank=True, help_text="skos:inScheme IRI")
    scheme_slug = models.CharField(max_length=64, blank=True, db_index=True)
    type_iri = models.CharField(max_length=500, blank=True, help_text="rdf:type for individuals")
    type_label = models.CharField(max_length=200, blank=True)
    broader = models.JSONField(
        default=list, blank=True, help_text="skos:broader or rdfs:subClassOf IRIs"
    )
    path = models.CharField(
        max_length=1000, blank=True, help_text="facet path of slugs from the top"
    )
    depth = models.PositiveSmallIntegerField(default=0)
    taggable = models.BooleanField(default=True)
    support = models.CharField(max_length=32, blank=True)
    term_status = models.CharField(max_length=32, blank=True)
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["pack", "scheme_slug", "path"]
        constraints = [models.UniqueConstraint(fields=["pack", "iri"], name="uniq_pack_concept")]
        indexes = [models.Index(fields=["iri"]), models.Index(fields=["pack", "kind"])]

    def __str__(self) -> str:
        return f"{self.pref_label} ({self.kind})"

    @property
    def labels(self) -> list[str]:
        return [self.pref_label, *self.alt_labels, *self.hidden_labels]


class PackBinding(models.Model):
    """Which packs a collection tags with, in precedence order."""

    collection = models.ForeignKey(
        "catalogue.Collection", on_delete=models.CASCADE, related_name="pack_bindings"
    )
    pack_id = models.SlugField(max_length=64)
    precedence = models.PositiveSmallIntegerField(default=100)
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["collection", "precedence"]
        constraints = [
            models.UniqueConstraint(fields=["collection", "pack_id"], name="uniq_binding")
        ]

    def __str__(self) -> str:
        return f"{self.collection_id}:{self.pack_id}"


class PackDelta(models.Model):
    """A curator's proposed change to a pack, exported as a bundle delta for the skill workflow.

    Deltas never change the imported pack. Until the next pack version accepts
    them, they tag only the collection they were made in, as `draft` terms.
    """

    class Kind(models.TextChoices):
        ADD_LABEL = "add_label"  # params: iri, label, label_type (alt|hidden), lang
        NEW_CONCEPT = "new_concept"  # params: label, scheme_slug, broader (iri), definition, lang
        RETIRE_LABEL = "retire_label"  # params: iri, label
        NOTE = "note"  # params: iri, text

    class Status(models.TextChoices):
        DRAFT = "draft"
        EXPORTED = "exported"
        ACCEPTED = "accepted"
        REJECTED = "rejected"

    pack_id = models.SlugField(max_length=64)
    pack_version = models.CharField(max_length=32)
    collection = models.ForeignKey(
        "catalogue.Collection", on_delete=models.CASCADE, related_name="pack_deltas"
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    params = models.JSONField(default=dict)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    proposed_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    exported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.kind} on {self.pack_id} ({self.status})"
