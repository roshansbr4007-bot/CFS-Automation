"""Operations monitoring - read-only; views only validate parameters, check the permission,
call the monitoring module and serialize.

- Admin (Boss), Phase 5.1: organisation-wide, /operations/... (unchanged).
- Operations Manager team monitoring: the same monitoring data for their OWN department only,
  /operations/team/... The department is always the one of the caller's active employee record;
  it is forced on the server and never taken from the request.
"""

from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from apps.core.errors import FieldValidationError
from apps.core.serializers import ErrorSerializer
from apps.core.timeutils import to_ist
from apps.org.models import Department, Employee

from .. import monitoring, perms, policy
from .serializers import (
    EmployeeAssignedTasksSerializer,
    EmployeeDailyActivitiesSerializer,
    OperationsSummarySerializer,
    TeamOperationsSummarySerializer,
)

_ERRORS = {400: ErrorSerializer, 401: ErrorSerializer, 403: ErrorSerializer, 404: ErrorSerializer}
DATE = OpenApiParameter(
    "date", OpenApiTypes.DATE, description="Business date (IST); default today."
)
STATUS = OpenApiParameter(
    "status", OpenApiTypes.STR, enum=list(monitoring.DAILY_STATUS_FILTERS)
)


class OperationsMonitoringPermission(BasePermission):
    """Admin = Boss for monitoring. Reuses the existing Admin-only task permission, so no role
    grant changes; HR, Operations Managers and Employees are refused."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.has_perm(perms.MANAGE_ALL_TASKS))


def business_date(request, now):
    raw = request.query_params.get("date")
    if not raw:
        return to_ist(now).date()
    try:
        value = parse_date(raw)
    except ValueError:
        value = None
    if value is None:
        raise FieldValidationError(fields={"date": ["Use a real date as YYYY-MM-DD."]})
    return value


def _int_param(request, name):
    raw = request.query_params.get(name)
    if not raw:
        return None
    if not str(raw).isdigit():
        raise FieldValidationError(fields={name: ["Must be a number."]})
    return int(raw)


def _choice_param(request, name, choices):
    raw = request.query_params.get(name)
    if raw and raw not in choices:
        raise FieldValidationError(fields={name: [f"Use one of: {', '.join(choices)}."]})
    return raw or None


def _daily_activities(request, employees, pk):
    """One employee's daily activities for one date; `employees` is the caller's scope."""
    now = timezone.now()
    day = business_date(request, now)
    status = _choice_param(request, "status", monitoring.DAILY_STATUS_FILTERS)
    sla_state = _choice_param(request, "sla_state", monitoring.SLA_STATE_FILTERS)
    employee = get_object_or_404(employees, pk=pk)
    tasks = monitoring.daily_activity_tasks([employee], day)
    rows = monitoring.activity_rows(tasks, now)
    body = {
        "date": day,
        "server_time": now,
        "employee": employee,
        "counts": monitoring.daily_counts(rows),
        "activities": monitoring.filter_daily(rows, status, sla_state),
    }
    return Response(EmployeeDailyActivitiesSerializer(body).data)


def _assigned_tasks(request, employees, pk):
    """One employee's assigned (manual) task workload for one date; `employees` is the scope."""
    now = timezone.now()
    day = business_date(request, now)
    status = _choice_param(request, "status", monitoring.DAILY_STATUS_FILTERS)
    employee = get_object_or_404(employees, pk=pk)
    rows = [
        monitoring.assigned_row(t, day, now)
        for t in monitoring.assigned_workload_tasks([employee], day)
    ]
    body = {
        "date": day,
        "server_time": now,
        "employee": employee,
        "counts": monitoring.assigned_counts(rows),
        "tasks": monitoring.filter_assigned(rows, status),
    }
    return Response(EmployeeAssignedTasksSerializer(body).data)


@extend_schema(tags=["operations monitoring"])
class OperationsSummaryView(GenericAPIView):
    permission_classes = [OperationsMonitoringPermission]
    serializer_class = OperationsSummarySerializer

    @extend_schema(
        parameters=[
            DATE,
            OpenApiParameter("department", OpenApiTypes.INT),
            OpenApiParameter("employee", OpenApiTypes.INT),
        ],
        responses={200: OperationsSummarySerializer, **_ERRORS},
        summary="Employee-wise daily activity and assigned task counts for one date",
    )
    def get(self, request):
        now = timezone.now()
        day = business_date(request, now)
        rows = monitoring.employee_summary(
            day,
            now,
            department_id=_int_param(request, "department"),
            employee_id=_int_param(request, "employee"),
        )
        body = {"date": day, "server_time": now, "employees": rows}
        return Response(OperationsSummarySerializer(body).data)


@extend_schema(tags=["operations monitoring"])
class EmployeeDailyActivitiesView(GenericAPIView):
    permission_classes = [OperationsMonitoringPermission]
    serializer_class = EmployeeDailyActivitiesSerializer

    @extend_schema(
        parameters=[
            DATE,
            STATUS,
            OpenApiParameter(
                "sla_state", OpenApiTypes.STR, enum=list(monitoring.SLA_STATE_FILTERS)
            ),
        ],
        responses={200: EmployeeDailyActivitiesSerializer, **_ERRORS},
        summary="One employee's daily activities for one date",
    )
    def get(self, request, pk):
        return _daily_activities(request, Employee.objects.select_related("department"), pk)


@extend_schema(tags=["operations monitoring"])
class EmployeeAssignedTasksView(GenericAPIView):
    permission_classes = [OperationsMonitoringPermission]
    serializer_class = EmployeeAssignedTasksSerializer

    @extend_schema(
        parameters=[
            DATE,
            STATUS,
        ],
        responses={200: EmployeeAssignedTasksSerializer, **_ERRORS},
        summary="One employee's assigned (manual) task workload for one date",
    )
    def get(self, request, pk):
        return _assigned_tasks(request, Employee.objects.select_related("department"), pk)


# --- Operations Manager team monitoring (own department only) ---------------------------------


class TeamMonitoringPermission(BasePermission):
    """An Operations Manager (tasks.manage_team_tasks) with an ACTIVE employee record: their
    department is the scope. Reuses the existing team permission, so no role grant changes.
    Admin keeps the organisation-wide endpoints; HR and Employees are refused."""

    message = "Team monitoring is for Operations Managers with an active employee record."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.has_perm(perms.MANAGE_TEAM_TASKS)
            and policy.team_department_id(user) is not None
        )


def _team_employees(request):
    """The caller's own department only (forced on the server; request values are ignored)."""
    return Employee.objects.select_related("department").filter(
        department_id=policy.team_department_id(request.user)
    )


@extend_schema(tags=["operations monitoring"])
class TeamOperationsSummaryView(GenericAPIView):
    permission_classes = [TeamMonitoringPermission]
    serializer_class = TeamOperationsSummarySerializer

    @extend_schema(
        parameters=[DATE, OpenApiParameter("employee", OpenApiTypes.INT)],
        responses={200: TeamOperationsSummarySerializer, **_ERRORS},
        summary="My department: employee-wise daily activity and assigned task counts",
    )
    def get(self, request):
        now = timezone.now()
        day = business_date(request, now)
        department = Department.objects.get(pk=policy.team_department_id(request.user))
        rows = monitoring.employee_summary(
            day,
            now,
            department_id=department.pk,  # never the request's department
            employee_id=_int_param(request, "employee"),
        )
        body = {"date": day, "server_time": now, "department": department, "employees": rows}
        return Response(TeamOperationsSummarySerializer(body).data)


@extend_schema(tags=["operations monitoring"])
class TeamEmployeeDailyActivitiesView(GenericAPIView):
    permission_classes = [TeamMonitoringPermission]
    serializer_class = EmployeeDailyActivitiesSerializer

    @extend_schema(
        parameters=[
            DATE,
            STATUS,
            OpenApiParameter(
                "sla_state", OpenApiTypes.STR, enum=list(monitoring.SLA_STATE_FILTERS)
            ),
        ],
        responses={200: EmployeeDailyActivitiesSerializer, **_ERRORS},
        summary="One employee of my department: daily activities for one date",
    )
    def get(self, request, pk):
        return _daily_activities(request, _team_employees(request), pk)


@extend_schema(tags=["operations monitoring"])
class TeamEmployeeAssignedTasksView(GenericAPIView):
    permission_classes = [TeamMonitoringPermission]
    serializer_class = EmployeeAssignedTasksSerializer

    @extend_schema(
        parameters=[DATE, STATUS],
        responses={200: EmployeeAssignedTasksSerializer, **_ERRORS},
        summary="One employee of my department: assigned (manual) task workload for one date",
    )
    def get(self, request, pk):
        return _assigned_tasks(request, _team_employees(request), pk)
