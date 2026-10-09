from django.urls import path
from rest_framework.routers import SimpleRouter

from .monitoring_views import (
    EmployeeAssignedTasksView,
    EmployeeDailyActivitiesView,
    OperationsSummaryView,
    TeamEmployeeAssignedTasksView,
    TeamEmployeeDailyActivitiesView,
    TeamOperationsSummaryView,
)
from .views import TaskCategoryViewSet, TaskTemplateListView, TaskViewSet

router = SimpleRouter()
router.register("tasks", TaskViewSet, basename="task")
router.register("task-categories", TaskCategoryViewSet, basename="task-category")

urlpatterns = [
    path("task-templates/", TaskTemplateListView.as_view(), name="task-template-list"),
    # Phase 5.1 Admin (Boss) operations monitoring
    path(
        "operations/daily-summary/",
        OperationsSummaryView.as_view(),
        name="operations-daily-summary",
    ),
    path(
        "operations/employees/<int:pk>/daily-activities/",
        EmployeeDailyActivitiesView.as_view(),
        name="operations-employee-daily-activities",
    ),
    path(
        "operations/employees/<int:pk>/assigned-tasks/",
        EmployeeAssignedTasksView.as_view(),
        name="operations-employee-assigned-tasks",
    ),
    # Operations Manager team monitoring (own department, forced on the server)
    path(
        "operations/team/daily-summary/",
        TeamOperationsSummaryView.as_view(),
        name="operations-team-daily-summary",
    ),
    path(
        "operations/team/employees/<int:pk>/daily-activities/",
        TeamEmployeeDailyActivitiesView.as_view(),
        name="operations-team-employee-daily-activities",
    ),
    path(
        "operations/team/employees/<int:pk>/assigned-tasks/",
        TeamEmployeeAssignedTasksView.as_view(),
        name="operations-team-employee-assigned-tasks",
    ),
    *router.urls,
]
