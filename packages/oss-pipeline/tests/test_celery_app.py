def test_queues_are_declared() -> None:
    from oss_pipeline.celery_app import app

    assert {q.name for q in app.conf.task_queues} == {"io", "cpu"}
    assert app.conf.task_acks_late is True


def test_redelivery_waits_longer_than_a_task_can_run() -> None:
    from oss_core.config import MAX_RETRY_COUNTDOWN_S, Settings
    from oss_pipeline.celery_app import app, settings

    timeout = app.conf.broker_transport_options["visibility_timeout"]
    assert timeout == settings.visibility_timeout
    assert timeout > settings.task_time_limit + MAX_RETRY_COUNTDOWN_S
    cpu = Settings(semascope_extract_provider="tika")
    mineru = Settings(semascope_extract_provider="mineru")
    assert (cpu.task_time_limit, cpu.visibility_timeout) == (1800, 2700)
    assert (mineru.task_time_limit, mineru.visibility_timeout) == (5400, 6300)
    tuned = Settings(semascope_task_time_limit_s=600)
    assert (tuned.task_time_limit, tuned.visibility_timeout) == (600, 1500)
