def test_queues_are_declared() -> None:
    from oss_pipeline.celery_app import app

    assert {q.name for q in app.conf.task_queues} == {"io", "cpu", "ocr", "nlp", "graph"}
    assert app.conf.task_acks_late is True
