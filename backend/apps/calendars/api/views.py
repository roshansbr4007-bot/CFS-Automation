from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.response import Response

from apps.core.errors import FieldValidationError
from apps.core.serializers import ErrorSerializer

from .. import perms, services
from ..models import CalendarDay
from .serializers import (
    CalendarDayCreateSerializer,
    CalendarDaySerializer,
    CompanyCalendarSerializer,
    WorkingDaysSerializer,
)

_ERRORS = {400: ErrorSerializer, 401: ErrorSerializer, 403: ErrorSerializer, 404: ErrorSerializer}


class CalendarPermission(BasePermission):
    """Everyone signed in reads the calendar; Admin manages holidays and special days."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return request.method in SAFE_METHODS or user.has_perm(perms.MANAGE_COMPANY_CALENDAR)


def _date_param(request, name):
    raw = request.query_params.get(name)
    try:
        value = parse_date(raw) if raw else None
    except ValueError:
        value = None
    if value is None:
        raise FieldValidationError(fields={name: ["Use a real date as YYYY-MM-DD."]})
    return value


@extend_schema(tags=["calendar"])
class CompanyCalendarView(GenericAPIView):
    permission_classes = [CalendarPermission]
    serializer_class = CompanyCalendarSerializer

    @extend_schema(responses={200: CompanyCalendarSerializer, 401: ErrorSerializer})
    def get(self, request):
        return Response(CompanyCalendarSerializer(services.company_calendar()).data)


@extend_schema(tags=["calendar"])
class CalendarDayCreateView(GenericAPIView):
    permission_classes = [CalendarPermission]
    serializer_class = CalendarDaySerializer

    @extend_schema(
        request=CalendarDayCreateSerializer,
        responses={201: CalendarDaySerializer, 409: ErrorSerializer, **_ERRORS},
    )
    def post(self, request):
        data = CalendarDayCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        entry = services.add_day(
            actor=request.user,
            day=data.validated_data["date"],
            kind=data.validated_data["kind"],
            name=data.validated_data["name"],
        )
        return Response(CalendarDaySerializer(entry).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["calendar"])
class CalendarDayDetailView(GenericAPIView):
    permission_classes = [CalendarPermission]
    serializer_class = CalendarDaySerializer
    queryset = CalendarDay.objects.all()

    @extend_schema(responses={204: None, **_ERRORS})
    def delete(self, request, pk):
        services.remove_day(actor=request.user, entry=get_object_or_404(CalendarDay, pk=pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["calendar"])
class WorkingDaysView(GenericAPIView):
    permission_classes = [CalendarPermission]
    serializer_class = WorkingDaysSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter("from", OpenApiTypes.DATE, required=True),
            OpenApiParameter("to", OpenApiTypes.DATE, required=True),
        ],
        responses={200: WorkingDaysSerializer, **_ERRORS},
    )
    def get(self, request):
        days = services.working_days(_date_param(request, "from"), _date_param(request, "to"))
        return Response({"working_days": days})
