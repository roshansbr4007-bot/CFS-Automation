from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import exceptions, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from apps.core.serializers import ErrorSerializer

from .. import generator, perms, selectors, services
from ..models import RecurringSchedule, Responsibility, ScheduleOccurrence
from .serializers import (
    AssignOwnerSerializer,
    EndOwnershipSerializer,
    OwnerAssignmentResultSerializer,
    RecurringScheduleSerializer,
    ResponsibilityCreateSerializer,
    ResponsibilityOwnerSerializer,
    ResponsibilitySerializer,
    ResponsibilitySetupResultSerializer,
    ResponsibilitySetupSerializer,
    ResponsibilityUpdateSerializer,
    ScheduleCreateSerializer,
    ScheduleOccurrenceSerializer,
    ScheduleUpdateSerializer,
)

_ERRORS = {
    400: ErrorSerializer,
    401: ErrorSerializer,
    403: ErrorSerializer,
    404: ErrorSerializer,
    409: ErrorSerializer,
}


class RecurringPermission(BasePermission):
    """Reading needs a responsibility permission (HR, Ops Manager, Admin); record scope comes
    from the selectors (404) and every write is re-checked in the services."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        writing = request.method not in ("GET", "HEAD", "OPTIONS")
        if writing and getattr(view, "admin_only_writes", False):
            return user.has_perm(perms.MANAGE_SCHEDULES)
        return selectors.can_view_any(user)


def _validated(serializer_class, data):
    serializer = serializer_class(data=data)
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


def _flatten(errors, prefix=""):
    """{"schedule": {"run_time": [...]}} -> {"schedule.run_time": [...]} (the API's flat
    `fields` shape, so a form can show each error on its own field)."""
    flat = {}
    for key, value in errors.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{name}."))
        else:
            flat[name] = value
    return flat


@extend_schema(tags=["responsibilities"])
class ResponsibilityViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Responsibilities are deactivated, never deleted (tasks and history refer to them)."""

    permission_classes = [RecurringPermission]
    serializer_class = ResponsibilitySerializer
    pagination_class = None
    http_method_names = ["get", "post", "patch", "head", "options"]
    lookup_value_regex = r"\d+"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Responsibility.objects.none()
        return selectors.visible_responsibilities(self.request.user)

    def _fresh(self, responsibility, code=status.HTTP_200_OK):
        fresh = Responsibility.objects.get(pk=responsibility.pk)
        context = {"request": self.request}
        return Response(ResponsibilitySerializer(fresh, context=context).data, status=code)

    @extend_schema(
        request=ResponsibilitySetupSerializer,
        responses={201: ResponsibilitySetupResultSerializer, **_ERRORS},
        summary="Create a responsibility, its optional owner and its first schedule at once",
    )
    @action(detail=False, methods=["post"], url_path="setup")
    def setup(self, request):
        serializer = ResponsibilitySetupSerializer(data=request.data)
        if not serializer.is_valid():
            raise exceptions.ValidationError(_flatten(serializer.errors))
        data = dict(serializer.validated_data)
        owner = data.pop("owner", None)
        schedule = dict(data.pop("schedule"))
        if not (schedule.get("title") or "").strip():
            schedule["title"] = data["name"]
        responsibility = services.setup_responsibility(
            actor=request.user,
            responsibility=data,
            owner=dict(owner) if owner else None,
            schedule=schedule,
        )
        today = []
        if owner and owner["effective_from"] == services.today_ist():
            # Locked rule D: today's eligible occurrence at once, after the setup committed.
            today = generator.generate_today(responsibility)
        fresh = Responsibility.objects.get(pk=responsibility.pk)
        context = {"request": request, "today_generation": today}
        return Response(
            ResponsibilitySetupResultSerializer(fresh, context=context).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(responses={200: ResponsibilitySerializer(many=True), **_ERRORS})
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(responses={200: ResponsibilitySerializer, **_ERRORS})
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(
        request=ResponsibilityCreateSerializer, responses={201: ResponsibilitySerializer, **_ERRORS}
    )
    def create(self, request):
        data = _validated(ResponsibilityCreateSerializer, request.data)
        responsibility = services.create_responsibility(actor=request.user, **data)
        return self._fresh(responsibility, status.HTTP_201_CREATED)

    @extend_schema(
        request=ResponsibilityUpdateSerializer, responses={200: ResponsibilitySerializer, **_ERRORS}
    )
    def partial_update(self, request, pk=None):
        data = _validated(ResponsibilityUpdateSerializer, request.data)
        version = data.pop("version")
        responsibility = services.update_responsibility(
            actor=request.user, responsibility=self.get_object(), version=version, **data
        )
        return self._fresh(responsibility)

    @extend_schema(
        methods=["GET"], responses={200: ResponsibilityOwnerSerializer(many=True), **_ERRORS}
    )
    @extend_schema(
        methods=["POST"],
        request=AssignOwnerSerializer,
        responses={201: OwnerAssignmentResultSerializer, **_ERRORS},
    )
    @action(detail=True, methods=["get", "post"])
    def owners(self, request, pk=None):
        responsibility = self.get_object()
        if request.method == "GET":
            rows = responsibility.owners.select_related("employee", "assigned_by").order_by(
                "effective_from", "id"
            )
            return Response(ResponsibilityOwnerSerializer(rows, many=True).data)
        data = _validated(AssignOwnerSerializer, request.data)
        row = services.assign_owner(actor=request.user, responsibility=responsibility, **data)
        # Committed. Locked rule D: when the owner starts today, today's eligible occurrence is
        # generated (or recovered) now instead of waiting for the per-minute run; the outcome is
        # reported as it happened, never assumed.
        row.today_generation = (
            generator.generate_today(responsibility)
            if row.effective_from == services.today_ist()
            else []
        )
        return Response(
            OwnerAssignmentResultSerializer(row).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(
        request=EndOwnershipSerializer, responses={200: ResponsibilityOwnerSerializer, **_ERRORS}
    )
    @action(detail=True, methods=["post"], url_path="end-ownership")
    def end_ownership(self, request, pk=None):
        data = _validated(EndOwnershipSerializer, request.data)
        row = services.end_ownership(actor=request.user, responsibility=self.get_object(), **data)
        return Response(ResponsibilityOwnerSerializer(row).data)


@extend_schema(tags=["responsibilities"])
class RecurringScheduleViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Recurring schedules: readable with responsibility access; Admin creates and edits.
    Deactivate rather than delete. Changes apply to future occurrences only."""

    permission_classes = [RecurringPermission]
    serializer_class = RecurringScheduleSerializer
    pagination_class = None
    http_method_names = ["get", "post", "patch", "head", "options"]
    lookup_value_regex = r"\d+"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return RecurringSchedule.objects.none()
        return selectors.visible_schedules(self.request.user)

    @extend_schema(responses={200: RecurringScheduleSerializer(many=True), **_ERRORS})
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(responses={200: RecurringScheduleSerializer, **_ERRORS})
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(
        request=ScheduleCreateSerializer, responses={201: RecurringScheduleSerializer, **_ERRORS}
    )
    def create(self, request):
        data = _validated(ScheduleCreateSerializer, request.data)
        responsibility = get_object_or_404(
            selectors.visible_responsibilities(request.user), pk=data.pop("responsibility").pk
        )
        schedule = services.create_schedule(
            actor=request.user, responsibility=responsibility, **data
        )
        data = RecurringScheduleSerializer(schedule, context={"request": request}).data
        return Response(data, status=status.HTTP_201_CREATED)

    @extend_schema(
        request=ScheduleUpdateSerializer, responses={200: RecurringScheduleSerializer, **_ERRORS}
    )
    def partial_update(self, request, pk=None):
        data = _validated(ScheduleUpdateSerializer, request.data)
        version = data.pop("version")
        schedule = services.update_schedule(
            actor=request.user, schedule=self.get_object(), version=version, **data
        )
        return Response(RecurringScheduleSerializer(schedule, context={"request": request}).data)


@extend_schema(tags=["responsibilities"])
class ScheduleOccurrenceViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Read-only ledger of generated / skipped / missed / failed occurrences."""

    permission_classes = [RecurringPermission]
    serializer_class = ScheduleOccurrenceSerializer
    lookup_value_regex = r"\d+"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ScheduleOccurrence.objects.none()
        params = self.request.query_params if self.action == "list" else None
        return selectors.visible_occurrences(self.request.user, params)

    @extend_schema(
        parameters=[
            OpenApiParameter("status", OpenApiTypes.STR),
            OpenApiParameter("from", OpenApiTypes.DATE),
            OpenApiParameter("to", OpenApiTypes.DATE),
            OpenApiParameter("responsibility", OpenApiTypes.INT),
            OpenApiParameter("schedule", OpenApiTypes.INT),
        ],
        responses={200: ScheduleOccurrenceSerializer(many=True), **_ERRORS},
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(responses={200: ScheduleOccurrenceSerializer, **_ERRORS})
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)
