from django.apps import AppConfig


class OrgConfig(AppConfig):
    name = "apps.org"
    label = "org"

    def ready(self):
        from . import signals  # noqa: F401  (records the first login per IST day)
