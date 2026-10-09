"""Phase 7.1 KRA configuration API (configuration only: nothing here calculates, adjusts,
deducts, finalizes or reopens a month).

Who: reading needs configure_kpis (HR) or approve_kpi_config (Admin); writing drafts, plan
defaults and overrides needs configure_kpis; activate / retire need approve_kpi_config; plan
resolution may also be read with manage_performance. What is allowed is decided in
apps.performance.config_services.
"""

from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.response import Response

from apps.core.errors import FieldValidationError
from apps.core.serializers import ErrorSerializer

from .. import config_services as svc
from .. import perms
from ..models import (
    KPI,
    BandScheme,
    DeductionRule,
    EmployeeKPIAssignment,
    KPIComponent,
    KPIPlanDefault,
    KPIWeight,
    KPIWeightVersion,
    ScoringRule,
)
from . import config_serializers as s

_ERRORS = {
    400: ErrorSerializer,
    401: ErrorSerializer,
    403: ErrorSerializer,
    404: ErrorSerializer,
    409: ErrorSerializer,
}
TAG = "performance-configuration"


class KpiConfigPermission(BasePermission):
    """Safe methods: any of view.read_perms; anything else: view.write_perm."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return any(user.has_perm(p) for p in getattr(view, "read_perms", perms.CONFIG_READERS))
        return user.has_perm(getattr(view, "write_perm", perms.CONFIGURE_KPIS))


class _ConfigView(GenericAPIView):
    """Configuration endpoints return plain lists (no pagination)."""

    permission_classes = [KpiConfigPermission]
    pagination_class = None
    read_perms = perms.CONFIG_READERS
    write_perm = perms.CONFIGURE_KPIS


class _ApproveView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes

    write_perm = perms.APPROVE_KPI_CONFIG


def _valid(serializer_class, data, *, partial_keys_only=False) -> dict:
    serializer = serializer_class(data=data)
    serializer.is_valid(raise_exception=True)
    values = dict(serializer.validated_data)
    if partial_keys_only:
        values = {k: v for k, v in values.items() if k in data}
    return values


def _plan_queryset():
    components = KPIComponent.objects.select_related("responsibility").order_by("position", "id")
    lines = (
        KPIWeight.objects.select_related("kpi", "scoring_rule")
        .prefetch_related(Prefetch("components", queryset=components))
        .order_by("position", "id")
    )
    rules = DeductionRule.objects.select_related("ceiling_band").order_by("priority", "code")
    return KPIWeightVersion.objects.select_related("band_scheme").prefetch_related(
        Prefetch("weights", queryset=lines), Prefetch("deduction_rules", queryset=rules)
    )


def _plan(pk) -> KPIWeightVersion:
    return get_object_or_404(_plan_queryset(), pk=pk)


def _plan_response(version, code=status.HTTP_200_OK):
    return Response(s.KpiCfgPlanDetailSerializer(_plan(version.pk)).data, status=code)


def _line(pk) -> KPIWeight:
    components = KPIComponent.objects.select_related("responsibility").order_by("position", "id")
    return get_object_or_404(
        KPIWeight.objects.select_related("kpi", "scoring_rule", "weight_version").prefetch_related(
            Prefetch("components", queryset=components)
        ),
        pk=pk,
    )


def _rule(pk) -> ScoringRule:
    return get_object_or_404(ScoringRule.objects.prefetch_related("steps"), pk=pk)


def _scheme(pk) -> BandScheme:
    return get_object_or_404(BandScheme.objects.prefetch_related("bands"), pk=pk)


# --- KPI master (read; description only) --------------------------------------------------


@extend_schema(tags=[TAG])
class KpiListView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgKpiSerializer

    @extend_schema(responses={200: s.KpiCfgKpiSerializer(many=True), **_ERRORS},
                   summary="KPI master records (codes and names are fixed)")
    def get(self, request):
        return Response(s.KpiCfgKpiSerializer(KPI.objects.order_by("code"), many=True).data)


@extend_schema(tags=[TAG])
class KpiDetailView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgKpiSerializer

    @extend_schema(responses={200: s.KpiCfgKpiSerializer, **_ERRORS})
    def get(self, request, pk):
        return Response(s.KpiCfgKpiSerializer(get_object_or_404(KPI, pk=pk)).data)

    @extend_schema(request=s.KpiCfgKpiUpdateInputSerializer,
                   responses={200: s.KpiCfgKpiSerializer, **_ERRORS},
                   summary="Edit a KPI's description (code and name never change)")
    def patch(self, request, pk):
        data = _valid(s.KpiCfgKpiUpdateInputSerializer, request.data)
        kpi = svc.update_kpi_description(actor=request.user, kpi=get_object_or_404(KPI, pk=pk),
                                         **data)
        return Response(s.KpiCfgKpiSerializer(kpi).data)


# --- plan versions ---------------------------------------------------------------------------


PLAN_FILTERS = [
    OpenApiParameter("configuration", OpenApiTypes.STR, description="Plan family code"),
    OpenApiParameter("status", OpenApiTypes.STR, description="DRAFT, ACTIVE or RETIRED"),
    OpenApiParameter("calculation_model", OpenApiTypes.STR,
                     description="LEGACY_WEIGHTED or KRA_POINTS"),
]


@extend_schema(tags=[TAG])
class PlanListView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgPlanSerializer

    @extend_schema(parameters=PLAN_FILTERS,
                   responses={200: s.KpiCfgPlanSerializer(many=True), **_ERRORS},
                   summary="KPI plan versions (legacy versions are listed read-only)")
    def get(self, request):
        rows = KPIWeightVersion.objects.select_related("band_scheme").order_by(
            "configuration", "version"
        )
        for name in ("configuration", "status", "calculation_model"):
            value = request.query_params.get(name)
            if value:
                rows = rows.filter(**{name: value.strip().upper()})
        return Response(s.KpiCfgPlanSerializer(rows, many=True).data)

    @extend_schema(request=s.KpiCfgPlanCreateInputSerializer,
                   responses={201: s.KpiCfgPlanDetailSerializer, **_ERRORS},
                   summary="Create a DRAFT KRA plan version (next number in its family)")
    def post(self, request):
        data = _valid(s.KpiCfgPlanCreateInputSerializer, request.data)
        version = svc.create_plan_version(actor=request.user, **data)
        return _plan_response(version, status.HTTP_201_CREATED)


@extend_schema(tags=[TAG])
class PlanDetailView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgPlanDetailSerializer

    @extend_schema(responses={200: s.KpiCfgPlanDetailSerializer, **_ERRORS})
    def get(self, request, pk):
        return Response(s.KpiCfgPlanDetailSerializer(_plan(pk)).data)

    @extend_schema(request=s.KpiCfgPlanUpdateInputSerializer,
                   responses={200: s.KpiCfgPlanDetailSerializer, **_ERRORS},
                   summary="Edit a DRAFT KRA plan version")
    def patch(self, request, pk):
        data = _valid(s.KpiCfgPlanUpdateInputSerializer, request.data, partial_keys_only=True)
        version = svc.update_plan_version(actor=request.user, version=_plan(pk), **data)
        return _plan_response(version)

    @extend_schema(request=None, responses={204: None, **_ERRORS},
                   summary="Delete a DRAFT KRA plan version")
    def delete(self, request, pk):
        svc.delete_plan_version(actor=request.user, version=_plan(pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=[TAG])
class PlanCloneView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgPlanDetailSerializer

    @extend_schema(request=None, responses={201: s.KpiCfgPlanDetailSerializer, **_ERRORS},
                   summary="New DRAFT version copied from this one (lines, components, deductions)")
    def post(self, request, pk):
        version = svc.clone_plan_version(actor=request.user, version=_plan(pk))
        return _plan_response(version, status.HTTP_201_CREATED)


@extend_schema(tags=[TAG])
class PlanActivateView(_ApproveView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgPlanDetailSerializer

    @extend_schema(request=None, responses={200: s.KpiCfgPlanDetailSerializer, **_ERRORS},
                   summary="Admin: activate a complete DRAFT KRA plan (future effective date)")
    def post(self, request, pk):
        version = svc.activate_plan_version(actor=request.user, version=_plan(pk))
        return _plan_response(version)


@extend_schema(tags=[TAG])
class PlanRetireView(_ApproveView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgPlanDetailSerializer

    @extend_schema(request=s.KpiCfgRetireInputSerializer,
                   responses={200: s.KpiCfgPlanDetailSerializer, **_ERRORS},
                   summary="Admin: retire an ACTIVE version (legacy or KRA) with a reason")
    def post(self, request, pk):
        data = _valid(s.KpiCfgRetireInputSerializer, request.data)
        version = svc.retire_plan_version(actor=request.user, version=_plan(pk), **data)
        return _plan_response(version)


# --- plan lines and components ---------------------------------------------------------------


@extend_schema(tags=[TAG])
class PlanLineListView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgLineSerializer

    @extend_schema(responses={200: s.KpiCfgLineSerializer(many=True), **_ERRORS})
    def get(self, request, pk):
        return Response(s.KpiCfgLineSerializer(_plan(pk).weights.all(), many=True).data)

    @extend_schema(request=s.KpiCfgLineCreateInputSerializer,
                   responses={201: s.KpiCfgLineSerializer, **_ERRORS},
                   summary="Add a KPI line to a DRAFT KRA plan")
    def post(self, request, pk):
        data = _valid(s.KpiCfgLineCreateInputSerializer, request.data)
        line = svc.create_plan_line(actor=request.user, version=_plan(pk), **data)
        return Response(s.KpiCfgLineSerializer(_line(line.pk)).data,
                        status=status.HTTP_201_CREATED)


@extend_schema(tags=[TAG])
class PlanLineDetailView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgLineSerializer

    @extend_schema(responses={200: s.KpiCfgLineSerializer, **_ERRORS})
    def get(self, request, pk):
        return Response(s.KpiCfgLineSerializer(_line(pk)).data)

    @extend_schema(request=s.KpiCfgLineUpdateInputSerializer,
                   responses={200: s.KpiCfgLineSerializer, **_ERRORS})
    def patch(self, request, pk):
        data = _valid(s.KpiCfgLineUpdateInputSerializer, request.data, partial_keys_only=True)
        line = svc.update_plan_line(actor=request.user, line=_line(pk), **data)
        return Response(s.KpiCfgLineSerializer(_line(line.pk)).data)

    @extend_schema(request=None, responses={204: None, **_ERRORS})
    def delete(self, request, pk):
        svc.delete_plan_line(actor=request.user, line=_line(pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=[TAG])
class ComponentListView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgComponentSerializer

    @extend_schema(responses={200: s.KpiCfgComponentSerializer(many=True), **_ERRORS})
    def get(self, request, pk):
        return Response(s.KpiCfgComponentSerializer(_line(pk).components.all(), many=True).data)

    @extend_schema(request=s.KpiCfgComponentCreateInputSerializer,
                   responses={201: s.KpiCfgComponentSerializer, **_ERRORS},
                   summary="Add a component to a line of a DRAFT KRA plan")
    def post(self, request, pk):
        data = _valid(s.KpiCfgComponentCreateInputSerializer, request.data)
        component = svc.create_component(actor=request.user, line=_line(pk), **data)
        return Response(s.KpiCfgComponentSerializer(component).data,
                        status=status.HTTP_201_CREATED)


@extend_schema(tags=[TAG])
class ComponentDetailView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgComponentSerializer

    @extend_schema(responses={200: s.KpiCfgComponentSerializer, **_ERRORS})
    def get(self, request, pk):
        component = get_object_or_404(KPIComponent.objects.select_related("responsibility"),
                                      pk=pk)
        return Response(s.KpiCfgComponentSerializer(component).data)

    @extend_schema(request=s.KpiCfgComponentUpdateInputSerializer,
                   responses={200: s.KpiCfgComponentSerializer, **_ERRORS})
    def patch(self, request, pk):
        data = _valid(s.KpiCfgComponentUpdateInputSerializer, request.data,
                      partial_keys_only=True)
        component = svc.update_component(
            actor=request.user, component=get_object_or_404(KPIComponent, pk=pk), **data
        )
        return Response(s.KpiCfgComponentSerializer(component).data)

    @extend_schema(request=None, responses={204: None, **_ERRORS})
    def delete(self, request, pk):
        svc.delete_component(actor=request.user, component=get_object_or_404(KPIComponent, pk=pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- deduction rules ---------------------------------------------------------------------------


def _deduction(pk) -> DeductionRule:
    return get_object_or_404(DeductionRule.objects.select_related("ceiling_band"), pk=pk)


@extend_schema(tags=[TAG])
class DeductionRuleListView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgDeductionRuleSerializer

    @extend_schema(responses={200: s.KpiCfgDeductionRuleSerializer(many=True), **_ERRORS})
    def get(self, request, pk):
        return Response(
            s.KpiCfgDeductionRuleSerializer(_plan(pk).deduction_rules.all(), many=True).data
        )

    @extend_schema(request=s.KpiCfgDeductionCreateInputSerializer,
                   responses={201: s.KpiCfgDeductionRuleSerializer, **_ERRORS},
                   summary="Add a deduction rule to a DRAFT KRA plan (no penalty maths assumed)")
    def post(self, request, pk):
        data = _valid(s.KpiCfgDeductionCreateInputSerializer, request.data)
        rule = svc.create_deduction_rule(actor=request.user, version=_plan(pk), **data)
        return Response(s.KpiCfgDeductionRuleSerializer(_deduction(rule.pk)).data,
                        status=status.HTTP_201_CREATED)


@extend_schema(tags=[TAG])
class DeductionRuleDetailView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgDeductionRuleSerializer

    @extend_schema(responses={200: s.KpiCfgDeductionRuleSerializer, **_ERRORS})
    def get(self, request, pk):
        return Response(s.KpiCfgDeductionRuleSerializer(_deduction(pk)).data)

    @extend_schema(request=s.KpiCfgDeductionUpdateInputSerializer,
                   responses={200: s.KpiCfgDeductionRuleSerializer, **_ERRORS})
    def patch(self, request, pk):
        data = _valid(s.KpiCfgDeductionUpdateInputSerializer, request.data,
                      partial_keys_only=True)
        rule = svc.update_deduction_rule(actor=request.user, rule=_deduction(pk), **data)
        return Response(s.KpiCfgDeductionRuleSerializer(_deduction(rule.pk)).data)

    @extend_schema(request=None, responses={204: None, **_ERRORS})
    def delete(self, request, pk):
        svc.delete_deduction_rule(actor=request.user, rule=_deduction(pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- scoring rules -----------------------------------------------------------------------------


@extend_schema(tags=[TAG])
class ScoringRuleListView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgScoringRuleSerializer

    @extend_schema(responses={200: s.KpiCfgScoringRuleSerializer(many=True), **_ERRORS})
    def get(self, request):
        rows = ScoringRule.objects.prefetch_related("steps").order_by("code", "version")
        return Response(s.KpiCfgScoringRuleSerializer(rows, many=True).data)

    @extend_schema(request=s.KpiCfgScoringRuleCreateInputSerializer,
                   responses={201: s.KpiCfgScoringRuleSerializer, **_ERRORS},
                   summary="Create a DRAFT scoring rule version (KRA benchmark steps)")
    def post(self, request):
        data = _valid(s.KpiCfgScoringRuleCreateInputSerializer, request.data)
        rule = svc.create_scoring_rule(actor=request.user, **data)
        return Response(s.KpiCfgScoringRuleSerializer(_rule(rule.pk)).data,
                        status=status.HTTP_201_CREATED)


@extend_schema(tags=[TAG])
class ScoringRuleDetailView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgScoringRuleSerializer

    @extend_schema(responses={200: s.KpiCfgScoringRuleSerializer, **_ERRORS})
    def get(self, request, pk):
        return Response(s.KpiCfgScoringRuleSerializer(_rule(pk)).data)

    @extend_schema(request=s.KpiCfgScoringRuleUpdateInputSerializer,
                   responses={200: s.KpiCfgScoringRuleSerializer, **_ERRORS},
                   summary="Edit a DRAFT scoring rule (steps are replaced in full)")
    def patch(self, request, pk):
        data = _valid(s.KpiCfgScoringRuleUpdateInputSerializer, request.data,
                      partial_keys_only=True)
        rule = svc.update_scoring_rule(actor=request.user, rule=_rule(pk), **data)
        return Response(s.KpiCfgScoringRuleSerializer(_rule(rule.pk)).data)


@extend_schema(tags=[TAG])
class ScoringRuleCloneView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgScoringRuleSerializer

    @extend_schema(request=None, responses={201: s.KpiCfgScoringRuleSerializer, **_ERRORS})
    def post(self, request, pk):
        rule = svc.clone_scoring_rule(actor=request.user, rule=_rule(pk))
        return Response(s.KpiCfgScoringRuleSerializer(_rule(rule.pk)).data,
                        status=status.HTTP_201_CREATED)


@extend_schema(tags=[TAG])
class ScoringRuleActivateView(_ApproveView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgScoringRuleSerializer

    @extend_schema(request=None, responses={200: s.KpiCfgScoringRuleSerializer, **_ERRORS})
    def post(self, request, pk):
        rule = svc.activate_scoring_rule(actor=request.user, rule=_rule(pk))
        return Response(s.KpiCfgScoringRuleSerializer(_rule(rule.pk)).data)


@extend_schema(tags=[TAG])
class ScoringRuleRetireView(_ApproveView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgScoringRuleSerializer

    @extend_schema(request=s.KpiCfgRetireInputSerializer,
                   responses={200: s.KpiCfgScoringRuleSerializer, **_ERRORS})
    def post(self, request, pk):
        data = _valid(s.KpiCfgRetireInputSerializer, request.data)
        rule = svc.retire_scoring_rule(actor=request.user, rule=_rule(pk), **data)
        return Response(s.KpiCfgScoringRuleSerializer(_rule(rule.pk)).data)


# --- band schemes ------------------------------------------------------------------------------


@extend_schema(tags=[TAG])
class BandSchemeListView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgBandSchemeSerializer

    @extend_schema(responses={200: s.KpiCfgBandSchemeSerializer(many=True), **_ERRORS})
    def get(self, request):
        rows = BandScheme.objects.prefetch_related("bands").order_by("code", "version")
        return Response(s.KpiCfgBandSchemeSerializer(rows, many=True).data)

    @extend_schema(request=s.KpiCfgBandSchemeCreateInputSerializer,
                   responses={201: s.KpiCfgBandSchemeSerializer, **_ERRORS},
                   summary="Create a DRAFT band scheme version (lower-bound bands, 10 points)")
    def post(self, request):
        data = _valid(s.KpiCfgBandSchemeCreateInputSerializer, request.data)
        scheme = svc.create_band_scheme(actor=request.user, **data)
        return Response(s.KpiCfgBandSchemeSerializer(_scheme(scheme.pk)).data,
                        status=status.HTTP_201_CREATED)


@extend_schema(tags=[TAG])
class BandSchemeDetailView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgBandSchemeSerializer

    @extend_schema(responses={200: s.KpiCfgBandSchemeSerializer, **_ERRORS})
    def get(self, request, pk):
        return Response(s.KpiCfgBandSchemeSerializer(_scheme(pk)).data)

    @extend_schema(request=s.KpiCfgBandSchemeUpdateInputSerializer,
                   responses={200: s.KpiCfgBandSchemeSerializer, **_ERRORS},
                   summary="Edit a DRAFT band scheme (bands are replaced in full)")
    def patch(self, request, pk):
        data = _valid(s.KpiCfgBandSchemeUpdateInputSerializer, request.data,
                      partial_keys_only=True)
        scheme = svc.update_band_scheme(actor=request.user, scheme=_scheme(pk), **data)
        return Response(s.KpiCfgBandSchemeSerializer(_scheme(scheme.pk)).data)


@extend_schema(tags=[TAG])
class BandSchemeCloneView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgBandSchemeSerializer

    @extend_schema(request=None, responses={201: s.KpiCfgBandSchemeSerializer, **_ERRORS})
    def post(self, request, pk):
        scheme = svc.clone_band_scheme(actor=request.user, scheme=_scheme(pk))
        return Response(s.KpiCfgBandSchemeSerializer(_scheme(scheme.pk)).data,
                        status=status.HTTP_201_CREATED)


@extend_schema(tags=[TAG])
class BandSchemeActivateView(_ApproveView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgBandSchemeSerializer

    @extend_schema(request=None, responses={200: s.KpiCfgBandSchemeSerializer, **_ERRORS})
    def post(self, request, pk):
        scheme = svc.activate_band_scheme(actor=request.user, scheme=_scheme(pk))
        return Response(s.KpiCfgBandSchemeSerializer(_scheme(scheme.pk)).data)


@extend_schema(tags=[TAG])
class BandSchemeRetireView(_ApproveView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgBandSchemeSerializer

    @extend_schema(request=s.KpiCfgRetireInputSerializer,
                   responses={200: s.KpiCfgBandSchemeSerializer, **_ERRORS})
    def post(self, request, pk):
        data = _valid(s.KpiCfgRetireInputSerializer, request.data)
        scheme = svc.retire_band_scheme(actor=request.user, scheme=_scheme(pk), **data)
        return Response(s.KpiCfgBandSchemeSerializer(_scheme(scheme.pk)).data)


# --- plan defaults and employee overrides ------------------------------------------------


@extend_schema(tags=[TAG])
class PlanDefaultListView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgPlanDefaultSerializer

    @extend_schema(
        parameters=[OpenApiParameter("department", OpenApiTypes.INT)],
        responses={200: s.KpiCfgPlanDefaultSerializer(many=True), **_ERRORS},
        summary="Department + system role plan defaults (history kept)",
    )
    def get(self, request):
        rows = KPIPlanDefault.objects.select_related("department", "role")
        department = request.query_params.get("department")
        if department:
            if not department.isdigit():
                raise FieldValidationError(fields={"department": ["Enter a whole number."]})
            rows = rows.filter(department_id=int(department))
        return Response(s.KpiCfgPlanDefaultSerializer(rows, many=True).data)

    @extend_schema(request=s.KpiCfgPlanDefaultCreateInputSerializer,
                   responses={201: s.KpiCfgPlanDefaultSerializer, **_ERRORS})
    def post(self, request):
        data = _valid(s.KpiCfgPlanDefaultCreateInputSerializer, request.data)
        row = svc.create_plan_default(actor=request.user, **data)
        return Response(s.KpiCfgPlanDefaultSerializer(row).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=[TAG])
class PlanDefaultEndView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgPlanDefaultSerializer

    @extend_schema(request=s.KpiCfgEndInputSerializer,
                   responses={200: s.KpiCfgPlanDefaultSerializer, **_ERRORS})
    def post(self, request, pk):
        data = _valid(s.KpiCfgEndInputSerializer, request.data)
        row = svc.end_plan_default(
            actor=request.user, default=get_object_or_404(KPIPlanDefault, pk=pk), **data
        )
        return Response(s.KpiCfgPlanDefaultSerializer(row).data)


@extend_schema(tags=[TAG])
class OverrideListView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgOverrideSerializer

    @extend_schema(
        parameters=[OpenApiParameter("employee", OpenApiTypes.INT)],
        responses={200: s.KpiCfgOverrideSerializer(many=True), **_ERRORS},
        summary="Employee plan assignments / HR overrides (history kept)",
    )
    def get(self, request):
        rows = EmployeeKPIAssignment.objects.select_related("employee", "weight_version")
        employee = request.query_params.get("employee")
        if employee:
            if not employee.isdigit():
                raise FieldValidationError(fields={"employee": ["Enter a whole number."]})
            rows = rows.filter(employee_id=int(employee))
        return Response(s.KpiCfgOverrideSerializer(rows, many=True).data)

    @extend_schema(request=s.KpiCfgOverrideCreateInputSerializer,
                   responses={201: s.KpiCfgOverrideSerializer, **_ERRORS},
                   summary="HR override: an ACTIVE plan version for one employee (reason required)")
    def post(self, request):
        data = _valid(s.KpiCfgOverrideCreateInputSerializer, request.data)
        data["version"] = data.pop("plan_version")
        row = svc.create_override(actor=request.user, **data)
        return Response(s.KpiCfgOverrideSerializer(row).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=[TAG])
class OverrideEndView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgOverrideSerializer

    @extend_schema(request=s.KpiCfgOverrideEndInputSerializer,
                   responses={200: s.KpiCfgOverrideSerializer, **_ERRORS})
    def post(self, request, pk):
        data = _valid(s.KpiCfgOverrideEndInputSerializer, request.data)
        row = svc.end_override(
            actor=request.user, assignment=get_object_or_404(EmployeeKPIAssignment, pk=pk),
            **data,
        )
        return Response(s.KpiCfgOverrideSerializer(row).data)


@extend_schema(tags=[TAG])
class PlanResolutionView(_ConfigView):
    permission_classes = [KpiConfigPermission]  # reads / writes: see the class attributes
    serializer_class = s.KpiCfgResolutionSerializer

    read_perms = perms.RESOLUTION_READERS

    @extend_schema(
        parameters=[
            OpenApiParameter("employee", OpenApiTypes.INT, required=True),
            OpenApiParameter("date", OpenApiTypes.DATE, required=True),
        ],
        responses={200: s.KpiCfgResolutionSerializer, **_ERRORS},
        summary="Which KPI plan applies to an employee on a date, and why",
    )
    def get(self, request):
        query = s.KpiCfgResolutionQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        employee, day = query.validated_data["employee"], query.validated_data["date"]
        result = svc.resolve_plan(employee, day)
        body = {
            "employee": employee,
            "date": day,
            "state": result.state,
            "source": result.source,
            "reason": result.reason,
            "roles": result.roles,
            "plan_version": result.version,
            "override_id": result.override.pk if result.override else None,
            "default_id": result.default.pk if result.default else None,
            "matching_default_ids": [d.pk for d in result.matching_defaults],
        }
        return Response(s.KpiCfgResolutionSerializer(body).data)
