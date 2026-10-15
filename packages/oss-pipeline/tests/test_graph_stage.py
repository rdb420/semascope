from oss_core.contracts import Tag
from oss_pipeline.stages.graph import entity_class, entity_key


def test_entity_keys_and_classes() -> None:
    linked = Tag(
        iri="https://p#odysseus",
        label="Odysseus",
        kind="entity",
        pack_id="p",
        pack_version="1",
        stage="tag",
    )
    ner = Tag(
        iri="https://semascope.dev/ontology/base#Person/jane doe",
        label="Jane Doe",
        kind="entity",
        stage="ner",
    )
    assert entity_key(linked, None) == "https://p#odysseus" and entity_class(linked) == "Individual"
    assert entity_key(ner, None) == "Person/jane doe" and entity_class(ner) == "Person"
    hashed = entity_key(ner, "secret")
    assert hashed.startswith("h:") and hashed != entity_key(ner, "other")
    assert entity_key(linked, "secret") == "https://p#odysseus", (
        "pack individuals are never pseudonymised"
    )
