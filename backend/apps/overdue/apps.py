from django.apps import AppConfig


class OverdueConfig(AppConfig):
    """Phase 9: overdue case — employee reason and reviewer authoritative cause."""

    name = "apps.overdue"
    label = "overdue"

    def ready(self):
        from apps.sla.dispatch import resolution_clock_overdue

        from .receivers import on_resolution_clock_overdue

        resolution_clock_overdue.connect(
            on_resolution_clock_overdue, dispatch_uid="overdue.open_case_on_resolution_overdue"
        )
