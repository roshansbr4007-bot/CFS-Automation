from django.urls import path

from .views import (
    CommandCenterEmployeeView,
    CommandCenterHealthView,
    CommandCenterSummaryView,
    SlaAttentionView,
)

urlpatterns = [
    path(
        "command-center/summary/",
        CommandCenterSummaryView.as_view(),
        name="command-center-summary",
    ),
    path("command-center/health/", CommandCenterHealthView.as_view(), name="command-center-health"),
    path(
        "command-center/employees/<int:pk>/",
        CommandCenterEmployeeView.as_view(),
        name="command-center-employee",
    ),
    path(
        "command-center/sla-attention/",
        SlaAttentionView.as_view(),
        name="command-center-sla-attention",
    ),
]
