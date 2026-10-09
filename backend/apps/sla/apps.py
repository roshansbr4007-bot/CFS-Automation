from django.apps import AppConfig


class SlaConfig(AppConfig):
    name = "apps.sla"
    label = "sla"

    def ready(self):
        from . import signals  # noqa: F401  (starts LOGIN-triggered clocks)
