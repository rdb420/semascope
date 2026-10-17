from django.apps import AppConfig


class AuditConfig(AppConfig):
    name = "oss_server.audit"
    verbose_name = "Audit"

    def ready(self) -> None:
        from oss_server.audit import signals

        signals.connect()
