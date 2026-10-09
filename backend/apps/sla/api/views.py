from drf_spectacular.utils import extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.response import Response

from apps.core.serializers import ErrorSerializer

from .. import perms, services
from ..models import PrioritySla, SlaRule, SlaSetting
from .serializers import (
    PrioritySlaSerializer,
    PrioritySlaUpdateSerializer,
    SlaRuleCreateSerializer,
    SlaRuleSerializer,
    SlaRuleSupersedeSerializer,
    SlaSettingSerializer,
)

_ERRORS = {400: ErrorSerializer, 401: ErrorSerializer, 403: ErrorSerializer, 404: ErrorSerializer}


class ReadOrManage(BasePermission):
    """Any signed-in user may read SLA rules and settings; changes need sla.manage_sla_rules."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return request.method in SAFE_METHODS or user.has_perm(perms.MANAGE_SLA_RULES)


@extend_schema(tags=["sla"])
class SlaRuleViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Every version of every SLA rule (old versions stay for audit)."""

    permission_classes = [ReadOrManage]
    serializer_class = SlaRuleSerializer
    pagination_class = None
    queryset = SlaRule.objects.all()
    lookup_value_regex = r"\d+"

    @extend_schema(responses={200: SlaRuleSerializer(many=True), 401: ErrorSerializer})
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        request=SlaRuleCreateSerializer,
        responses={201: SlaRuleSerializer, 409: ErrorSerializer, **_ERRORS},
        summary="Create a duration rule (Phase 5.2; later changes use supersede)",
    )
    def create(self, request):
        data = SlaRuleCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        rule = services.create_rule(actor=request.user, **data.validated_data)
        return Response(SlaRuleSerializer(rule).data, status=201)

    @extend_schema(
        request=SlaRuleSupersedeSerializer,
        responses={201: SlaRuleSerializer, 409: ErrorSerializer, **_ERRORS},
    )
    @action(detail=True, methods=["post"])
    def supersede(self, request, pk=None):
        data = SlaRuleSupersedeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        rule = services.supersede_rule(
            actor=request.user, rule=self.get_object(), **data.validated_data
        )
        return Response(SlaRuleSerializer(rule).data, status=201)


@extend_schema(tags=["sla"])
class SlaSettingView(GenericAPIView):
    """Company work_end and login fallback time. Both start empty (no default)."""

    permission_classes = [ReadOrManage]
    serializer_class = SlaSettingSerializer

    @extend_schema(responses={200: SlaSettingSerializer, 401: ErrorSerializer})
    def get(self, request):
        return Response(SlaSettingSerializer(SlaSetting.load()).data)

    @extend_schema(request=SlaSettingSerializer, responses={200: SlaSettingSerializer, **_ERRORS})
    def patch(self, request):
        data = SlaSettingSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        setting = services.update_settings(actor=request.user, **data.validated_data)
        return Response(SlaSettingSerializer(setting).data)


@extend_schema(tags=["sla"])
class PrioritySlaViewSet(viewsets.GenericViewSet):
    """Phase 5.2: task priority -> SLA rule for manually raised tasks. Nothing is configured by
    default; while a priority has no active mapping the task-type SLA applies unchanged."""

    permission_classes = [ReadOrManage]
    serializer_class = PrioritySlaSerializer
    pagination_class = None
    queryset = PrioritySla.objects.all()
    lookup_field = "priority"
    lookup_value_regex = r"[A-Z]+"
    http_method_names = ["get", "put", "head", "options"]

    @extend_schema(responses={200: PrioritySlaSerializer(many=True), 401: ErrorSerializer})
    def list(self, request):
        return Response(PrioritySlaSerializer(self.get_queryset(), many=True).data)

    @extend_schema(
        request=PrioritySlaUpdateSerializer, responses={200: PrioritySlaSerializer, **_ERRORS}
    )
    def update(self, request, priority=None):
        data = PrioritySlaUpdateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        mapping = services.set_priority_rule(
            actor=request.user, priority=priority, **data.validated_data
        )
        return Response(PrioritySlaSerializer(mapping).data)
