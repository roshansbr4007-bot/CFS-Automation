"""Admin (Boss) Command Center - read-only. Reuses the existing Admin-only permission of the
Operations Monitor (tasks.manage_all_tasks): anonymous -> 401, other roles -> 403."""

from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.core.serializers import ErrorSerializer
from apps.org.models import Employee
from apps.sla.models import SlaState
from apps.tasks.api.monitoring_views import (
    OperationsMonitoringPermission,
    _choice_param,
    _int_param,
    business_date,
)
from apps.tasks.models import TaskStatus

from .. import services
from .serializers import (
    CommandCenterEmployeeDetailSerializer,
    CommandCenterHealthSerializer,
    CommandCenterSummarySerializer,
    SlaAttentionSerializer,
)

_ERRORS = {401: ErrorSerializer, 403: ErrorSerializer}
_FILTER_ERRORS = {400: ErrorSerializer, **_ERRORS}
ROW_FILTERS = [
    OpenApiParameter("date", OpenApiTypes.DATE, description="Business date (IST); default today."),
    OpenApiParameter("source", OpenApiTypes.STR, enum=list(services.SOURCES)),
    OpenApiParameter("status", OpenApiTypes.STR, enum=list(TaskStatus.values)),
    OpenApiParameter("sla_state", OpenApiTypes.STR, enum=list(SlaState.values)),
]
EMPLOYEE_FILTERS = [
    OpenApiParameter("department", OpenApiTypes.INT),
    OpenApiParameter("employee", OpenApiTypes.INT),
]


def _filters(request, now, *, with_employee_filters=True) -> services.Filters:
    """Server-side filters, validated with the Operations Monitor's existing helpers."""
    return services.Filters(
        day=business_date(request, now),
        department_id=_int_param(request, "department") if with_employee_filters else None,
        employee_id=_int_param(request, "employee") if with_employee_filters else None,
        source=_choice_param(request, "source", services.SOURCES),
        status=_choice_param(request, "status", tuple(TaskStatus.values)),
        sla_state=_choice_param(request, "sla_state", tuple(SlaState.values)),
    )


@extend_schema(tags=["command center"])
class CommandCenterSummaryView(GenericAPIView):
    permission_classes = [OperationsMonitoringPermission]
    serializer_class = CommandCenterSummarySerializer

    @extend_schema(
        parameters=[*ROW_FILTERS, *EMPLOYEE_FILTERS],
        responses={200: CommandCenterSummarySerializer, **_FILTER_ERRORS},
        summary="Scheduler status, operations and SLA snapshot, employees, recent events",
    )
    def get(self, request):
        now = timezone.now()
        body = services.summary(now, _filters(request, now))
        return Response(CommandCenterSummarySerializer(body).data)


@extend_schema(tags=["command center"])
class CommandCenterHealthView(GenericAPIView):
    permission_classes = [OperationsMonitoringPermission]
    serializer_class = CommandCenterHealthSerializer

    @extend_schema(
        responses={200: CommandCenterHealthSerializer, **_ERRORS},
        summary="Live checks: API, database, Redis, Celery workers (short timeouts)",
    )
    def get(self, request):
        return Response(CommandCenterHealthSerializer(services.health()).data)


@extend_schema(tags=["command center"])
class CommandCenterEmployeeView(GenericAPIView):
    """Read-only drill-down for one employee (Phase 6B)."""

    permission_classes = [OperationsMonitoringPermission]
    serializer_class = CommandCenterEmployeeDetailSerializer

    @extend_schema(
        parameters=ROW_FILTERS,
        responses={
            200: CommandCenterEmployeeDetailSerializer,
            404: ErrorSerializer,
            **_FILTER_ERRORS,
        },
        summary="One employee's daily activities and assigned tasks (kept separate)",
    )
    def get(self, request, pk):
        now = timezone.now()
        filters = _filters(request, now, with_employee_filters=False)
        employee = get_object_or_404(Employee.objects.select_related("department"), pk=pk)
        body = services.employee_detail(employee, filters, now)
        return Response(CommandCenterEmployeeDetailSerializer(body).data)


@extend_schema(tags=["command center"])
class SlaAttentionView(GenericAPIView):
    """Open work in CRITICAL, WARNING or OVERDUE (existing SLA states), grouped (Phase 6B)."""

    permission_classes = [OperationsMonitoringPermission]
    serializer_class = SlaAttentionSerializer

    @extend_schema(
        parameters=[*ROW_FILTERS, *EMPLOYEE_FILTERS],
        responses={200: SlaAttentionSerializer, **_FILTER_ERRORS},
        summary="Work needing attention by SLA state; ON_TRACK only when requested",
    )
    def get(self, request):
        now = timezone.now()
        body = services.sla_attention(_filters(request, now), now)
        return Response(SlaAttentionSerializer(body).data)
