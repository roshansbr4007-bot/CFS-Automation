"""Phase 7.3 KRA monthly review API.

Who (apps.performance.perms): reading needs manage_performance, finalize_performance or
reopen_performance (HR; Admin reads through reopen_performance); calculating, HR inputs,
adjustments, deductions, submit and return need manage_performance (HR); finalize needs
finalize_performance (HR); reopen needs reopen_performance (HR, Admin). Operations Manager and
Employee: none. What is allowed in which state is decided in apps.performance.review_services.
Evidence, reasons and HR users are part of these HR / Admin responses only (E19).
"""

from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.response import Response

from apps.core.errors import FieldValidationError
from apps.core.serializers import ErrorSerializer

from .. import annual, perms
from .. import review_services as review
from ..models import ApprovedLeave, ManualTaskOverride, MonthlyPerformance
from . import review_serializers as s

_ERRORS = {
    400: ErrorSerializer,
    401: ErrorSerializer,
    403: ErrorSerializer,
    404: ErrorSerializer,
    409: ErrorSerializer,
}
TAG = "performance-review"


class KraReviewPermission(BasePermission):
    """Safe methods: any of view.read_perms; anything else: view.write_perm."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return any(user.has_perm(p) for p in view.read_perms)
        return user.has_perm(view.write_perm)


class _ReviewView(GenericAPIView):
    permission_classes = [KraReviewPermission]
    pagination_class = None
    read_perms = perms.REVIEW_READERS
    write_perm = perms.MANAGE_PERFORMANCE


def _valid(serializer_class, data) -> dict:
    serializer = serializer_class(data=data)
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


def _month(pk) -> MonthlyPerformance:
    return get_object_or_404(MonthlyPerformance, pk=pk)


def _detail(performance, code=status.HTTP_200_OK):
    fresh = MonthlyPerformance.objects.get(pk=performance.pk)
    return Response(s.KraRevMonthSerializer(review.month_detail(fresh)).data, status=code)


# --- months --------------------------------------------------------------------------------------


@extend_schema(tags=[TAG])
class KraMonthCalculateView(_ReviewView):
    permission_classes = [KraReviewPermission]  # reads / writes: see the class attributes
    serializer_class = s.KraRevMonthSerializer

    @extend_schema(request=s.KraRevCalculateInputSerializer,
                   responses={200: s.KraRevMonthSerializer, **_ERRORS},
                   summary="Calculate or recalculate an employee's KRA month (not under review "
                           "or finalized); existing adjustments and deductions are re-applied")
    def post(self, request):
        data = _valid(s.KraRevCalculateInputSerializer, request.data)
        performance = review.calculate(actor=request.user, **data)
        return _detail(performance)


@extend_schema(tags=[TAG])
class KraMonthDetailView(_ReviewView):
    permission_classes = [KraReviewPermission]  # reads / writes: see the class attributes
    serializer_class = s.KraRevMonthSerializer

    @extend_schema(responses={200: s.KraRevMonthSerializer, **_ERRORS},
                   summary="A KRA month: automatic and final points, adjustments, deductions, "
                           "settlement lines and open blockers (HR / Admin)")
    def get(self, request, pk):
        return _detail(_month(pk))


@extend_schema(tags=[TAG])
class KraMonthBlockersView(_ReviewView):
    permission_classes = [KraReviewPermission]  # reads / writes: see the class attributes
    serializer_class = s.KraRevBlockerSerializer

    @extend_schema(responses={200: s.KraRevBlockerSerializer(many=True), **_ERRORS},
                   summary="What must be resolved before the month can be finalized")
    def get(self, request, pk):
        blockers = review.blockers(_month(pk))
        return Response(s.KraRevBlockerSerializer(blockers, many=True).data)


@extend_schema(tags=[TAG])
class KraMonthSubmitView(_ReviewView):
    permission_classes = [KraReviewPermission]  # reads / writes: see the class attributes
    serializer_class = s.KraRevMonthSerializer

    @extend_schema(request=s.KraRevVersionInputSerializer,
                   responses={200: s.KraRevMonthSerializer, **_ERRORS},
                   summary="Submit a closed month for review (CALCULATED -> UNDER_REVIEW)")
    def post(self, request, pk):
        data = _valid(s.KraRevVersionInputSerializer, request.data)
        performance = review.submit(actor=request.user, performance=_month(pk), **data)
        return _detail(performance)


@extend_schema(tags=[TAG])
class KraMonthReturnView(_ReviewView):
    permission_classes = [KraReviewPermission]  # reads / writes: see the class attributes
    serializer_class = s.KraRevMonthSerializer

    @extend_schema(request=s.KraRevReturnInputSerializer,
                   responses={200: s.KraRevMonthSerializer, **_ERRORS},
                   summary="Return for recalculation (UNDER_REVIEW -> CALCULATED): recalculated "
                           "at once; existing adjustments and deductions are re-applied (reason "
                           "required)")
    def post(self, request, pk):
        data = _valid(s.KraRevReturnInputSerializer, request.data)
        performance = review.return_for_recalculation(actor=request.user,
                                                      performance=_month(pk), **data)
        return _detail(performance)


@extend_schema(tags=[TAG])
class KraMonthFinalizeView(_ReviewView):
    permission_classes = [KraReviewPermission]  # reads / writes: see the class attributes
    serializer_class = s.KraRevMonthSerializer

    write_perm = perms.FINALIZE_PERFORMANCE

    @extend_schema(request=s.KraRevVersionInputSerializer,
                   responses={200: s.KraRevMonthSerializer, **_ERRORS},
                   summary="Finalize (UNDER_REVIEW -> FINALIZED); refused while blockers remain")
    def post(self, request, pk):
        data = _valid(s.KraRevVersionInputSerializer, request.data)
        performance = review.finalize(actor=request.user, performance=_month(pk), **data)
        return _detail(performance)


@extend_schema(tags=[TAG])
class KraMonthReopenView(_ReviewView):
    permission_classes = [KraReviewPermission]  # reads / writes: see the class attributes
    serializer_class = s.KraRevMonthSerializer

    write_perm = perms.REOPEN_PERFORMANCE

    @extend_schema(request=s.KraRevReasonInputSerializer,
                   responses={200: s.KraRevMonthSerializer, **_ERRORS},
                   summary="Reopen a finalized KRA month (FINALIZED -> UNDER_REVIEW); reason "
                           "required (HR / Admin)")
    def post(self, request, pk):
        data = _valid(s.KraRevReasonInputSerializer, request.data)
        performance = review.reopen(actor=request.user, performance=_month(pk), **data)
        return _detail(performance)


# --- HR adjustments and deductions -------------------------------------------------------------


@extend_schema(tags=[TAG])
class KraKpiAdjustView(_ReviewView):
    permission_classes = [KraReviewPermission]  # reads / writes: see the class attributes
    serializer_class = s.KraRevMonthSerializer

    @extend_schema(request=s.KraRevAdjustInputSerializer,
                   responses={200: s.KraRevMonthSerializer, **_ERRORS},
                   summary="HR adjustment: set a KPI's points (0 to its maximum) with a reason")
    def post(self, request, pk, kpi_id):
        data = _valid(s.KraRevAdjustInputSerializer, request.data)
        performance = review.adjust_kpi(actor=request.user, performance=_month(pk),
                                        kpi_id=kpi_id, **data)
        return _detail(performance)


@extend_schema(tags=[TAG])
class KraDeductionListView(_ReviewView):
    permission_classes = [KraReviewPermission]  # reads / writes: see the class attributes
    serializer_class = s.KraRevMonthSerializer

    @extend_schema(request=s.KraRevDeductionInputSerializer,
                   responses={201: s.KraRevMonthSerializer, **_ERRORS},
                   summary="Apply a deduction rule of the month's plan (reason required)")
    def post(self, request, pk):
        data = _valid(s.KraRevDeductionInputSerializer, request.data)
        performance = review.apply_deduction(
            actor=request.user, performance=_month(pk), version=data["version"],
            rule_id=data["rule"], percent=data["percent"], kpi_id=data["kpi"],
            component_id=data["component"], evidence=data["evidence"], reason=data["reason"],
        )
        return _detail(performance, status.HTTP_201_CREATED)


@extend_schema(tags=[TAG])
class KraDeductionReverseView(_ReviewView):
    permission_classes = [KraReviewPermission]  # reads / writes: see the class attributes
    serializer_class = s.KraRevMonthSerializer

    @extend_schema(request=s.KraRevReasonInputSerializer,
                   responses={200: s.KraRevMonthSerializer, **_ERRORS},
                   summary="Reverse a deduction (append-only; reason required)")
    def post(self, request, pk, application_id):
        data = _valid(s.KraRevReasonInputSerializer, request.data)
        performance = review.reverse_deduction(
            actor=request.user, performance=_month(pk), application_id=application_id, **data
        )
        return _detail(performance)


# --- HR inputs that change the automatic score ---------------------------------------------


@extend_schema(tags=[TAG])
class KraGapDecisionView(_ReviewView):
    permission_classes = [KraReviewPermission]  # reads / writes: see the class attributes
    serializer_class = s.KraRevMonthSerializer

    @extend_schema(request=s.KraRevGapDecisionInputSerializer,
                   responses={200: s.KraRevMonthSerializer, **_ERRORS},
                   summary="Decide (or change) a generation gap of this month")
    def post(self, request, pk):
        data = _valid(s.KraRevGapDecisionInputSerializer, request.data)
        performance = review.decide_gap(
            actor=request.user, performance=_month(pk), version=data["version"],
            occurrence_id=data["occurrence"], decision=data["decision"], reason=data["reason"],
        )
        return _detail(performance)


@extend_schema(tags=[TAG])
class KraManualEntryView(_ReviewView):
    permission_classes = [KraReviewPermission]  # reads / writes: see the class attributes
    serializer_class = s.KraRevMonthSerializer

    @extend_schema(request=s.KraRevManualEntryInputSerializer,
                   responses={200: s.KraRevMonthSerializer, **_ERRORS},
                   summary="Enter HR's achievement % for a manual-entry component (reason "
                           "required)")
    def post(self, request, pk, component_id):
        data = _valid(s.KraRevManualEntryInputSerializer, request.data)
        performance = review.enter_manual_score(
            actor=request.user, performance=_month(pk), component_id=component_id, **data
        )
        return _detail(performance)


@extend_schema(tags=[TAG])
class KraManualNaView(_ReviewView):
    permission_classes = [KraReviewPermission]  # reads / writes: see the class attributes
    serializer_class = s.KraRevMonthSerializer

    @extend_schema(request=s.KraRevReasonInputSerializer,
                   responses={200: s.KraRevMonthSerializer, **_ERRORS},
                   summary="Mark a manual-entry component not applicable (reason required)")
    def post(self, request, pk, component_id):
        data = _valid(s.KraRevReasonInputSerializer, request.data)
        performance = review.mark_manual_na(
            actor=request.user, performance=_month(pk), component_id=component_id, **data
        )
        return _detail(performance)


# --- approved leave and manual-task exceptions -----------------------------------------------


def _int_param(request, name):
    value = request.query_params.get(name)
    if value in (None, ""):
        return None
    if not value.isdigit():
        raise FieldValidationError(fields={name: ["Enter a whole number."]})
    return int(value)


@extend_schema(tags=[TAG])
class KraLeaveListView(_ReviewView):
    permission_classes = [KraReviewPermission]  # reads / writes: see the class attributes
    serializer_class = s.KraRevLeaveSerializer

    @extend_schema(parameters=[OpenApiParameter("employee", OpenApiTypes.INT)],
                   responses={200: s.KraRevLeaveSerializer(many=True), **_ERRORS},
                   summary="Approved leave recorded for performance (history kept)")
    def get(self, request):
        rows = ApprovedLeave.objects.order_by("employee_id", "start_date", "id")
        employee = _int_param(request, "employee")
        if employee is not None:
            rows = rows.filter(employee_id=employee)
        return Response(s.KraRevLeaveSerializer(rows, many=True).data)

    @extend_schema(request=s.KraRevLeaveInputSerializer,
                   responses={201: s.KraRevLeaveSerializer, **_ERRORS},
                   summary="Record approved leave (used by the next calculation)")
    def post(self, request):
        data = _valid(s.KraRevLeaveInputSerializer, request.data)
        leave = review.record_leave(actor=request.user, **data)
        return Response(s.KraRevLeaveSerializer(leave).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=[TAG])
class KraLeaveCancelView(_ReviewView):
    permission_classes = [KraReviewPermission]  # reads / writes: see the class attributes
    serializer_class = s.KraRevLeaveSerializer

    @extend_schema(request=s.KraRevTextReasonInputSerializer,
                   responses={200: s.KraRevLeaveSerializer, **_ERRORS},
                   summary="Cancel recorded leave (reason required; the record is kept)")
    def post(self, request, pk):
        data = _valid(s.KraRevTextReasonInputSerializer, request.data)
        leave = review.cancel_leave(actor=request.user,
                                    leave=get_object_or_404(ApprovedLeave, pk=pk), **data)
        return Response(s.KraRevLeaveSerializer(leave).data)


@extend_schema(tags=[TAG])
class KraTaskOverrideListView(_ReviewView):
    permission_classes = [KraReviewPermission]  # reads / writes: see the class attributes
    serializer_class = s.KraRevTaskOverrideSerializer

    @extend_schema(parameters=[OpenApiParameter("task", OpenApiTypes.INT),
                               OpenApiParameter("responsibility", OpenApiTypes.INT)],
                   responses={200: s.KraRevTaskOverrideSerializer(many=True), **_ERRORS},
                   summary="HR exceptions: manual tasks included in / excluded from a "
                           "responsibility")
    def get(self, request):
        rows = ManualTaskOverride.objects.order_by("task_id", "responsibility_id")
        for name in ("task", "responsibility"):
            value = _int_param(request, name)
            if value is not None:
                rows = rows.filter(**{f"{name}_id": value})
        return Response(s.KraRevTaskOverrideSerializer(rows, many=True).data)

    @extend_schema(request=s.KraRevTaskOverrideInputSerializer,
                   responses={201: s.KraRevTaskOverrideSerializer, **_ERRORS},
                   summary="Include or exclude one manual task for a responsibility (reason "
                           "required; used by the next calculation)")
    def post(self, request):
        data = _valid(s.KraRevTaskOverrideInputSerializer, request.data)
        row = review.create_task_override(actor=request.user, **data)
        return Response(s.KraRevTaskOverrideSerializer(row).data,
                        status=status.HTTP_201_CREATED)


@extend_schema(tags=[TAG])
class KraTaskOverrideRemoveView(_ReviewView):
    permission_classes = [KraReviewPermission]  # reads / writes: see the class attributes
    serializer_class = s.KraRevTaskOverrideSerializer

    @extend_schema(request=s.KraRevTextReasonInputSerializer,
                   responses={204: None, **_ERRORS},
                   summary="Remove an HR task exception (reason required; audited)")
    def post(self, request, pk):
        data = _valid(s.KraRevTextReasonInputSerializer, request.data)
        review.remove_task_override(
            actor=request.user, override=get_object_or_404(ManualTaskOverride, pk=pk), **data
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- annual ----------------------------------------------------------------------------------


@extend_schema(tags=[TAG])
class KraAnnualView(_ReviewView):
    permission_classes = [KraReviewPermission]  # reads / writes: see the class attributes
    serializer_class = s.KraRevAnnualSerializer

    @extend_schema(
        parameters=[OpenApiParameter("employee", OpenApiTypes.INT, required=True),
                    OpenApiParameter("year", OpenApiTypes.INT, required=True)],
        responses={200: s.KraRevAnnualSerializer, **_ERRORS},
        summary="Annual total and average over the finalized applicable KRA months only",
    )
    def get(self, request):
        query = s.KraRevAnnualQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        body = annual.annual_summary(query.validated_data["employee"],
                                     query.validated_data["year"])
        return Response(s.KraRevAnnualSerializer(body).data)
