"""Overdue cases API (Phase 9). Session auth + CSRF as everywhere else.

Visibility is server-side (selectors.visible_cases): a case outside the caller's scope is a 404.
Submitting and reviewing are checked again in the services (owner only; scope and no
self-review). Exports follow the Phase 7 convention: organisation-wide roles only."""

from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.serializers import ErrorSerializer

from .. import perms, reports, selectors, services
from ..filters import apply_filters
from ..models import OverdueCause, OverdueStatus
from .serializers import OverdueCaseSerializer, ReviewSerializer, SubmitReasonSerializer

FILTERS = [
    OpenApiParameter("status", OpenApiTypes.STR, enum=list(OverdueStatus.values)),
    OpenApiParameter("employee", OpenApiTypes.INT),
    OpenApiParameter("department", OpenApiTypes.INT, description="The task's department"),
    OpenApiParameter("date_from", OpenApiTypes.DATE, description="Opened on or after (IST)"),
    OpenApiParameter("date_to", OpenApiTypes.DATE, description="Opened on or before (IST)"),
    OpenApiParameter("cause", OpenApiTypes.STR, enum=list(OverdueCause.values)),
    OpenApiParameter("reason_category", OpenApiTypes.STR, enum=list(OverdueCause.values)),
    OpenApiParameter("task", OpenApiTypes.INT),
    OpenApiParameter("priority", OpenApiTypes.STR),
]
_ERRORS = {400: ErrorSerializer, 401: ErrorSerializer}


@extend_schema(tags=["overdue cases"])
class OverdueCaseViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                         viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = OverdueCaseSerializer
    lookup_value_regex = r"\d+"

    def get_queryset(self):
        if self.action == "list" and self.request.query_params.get("reviewable") in ("1", "true"):
            qs = selectors.reviewable_cases(self.request.user)
        else:
            qs = selectors.visible_cases(self.request.user)
        if self.action == "list":
            qs = apply_filters(qs, self.request.query_params)
        return qs

    @extend_schema(
        parameters=[*FILTERS, OpenApiParameter(
            "reviewable", OpenApiTypes.BOOL, description="Only cases waiting for MY review"
        )],
        responses={200: OverdueCaseSerializer(many=True), **_ERRORS},
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def _fresh(self, request, case):
        return self.get_serializer(selectors.visible_cases(request.user).get(pk=case.pk)).data

    @extend_schema(responses={200: OverdueCaseSerializer, 404: ErrorSerializer, **_ERRORS})
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(
        request=SubmitReasonSerializer,
        responses={200: OverdueCaseSerializer, 403: ErrorSerializer, 404: ErrorSerializer,
                   409: ErrorSerializer, **_ERRORS},
        summary="The employee on the case submits the reason (category + explanation)",
    )
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        data = SubmitReasonSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        case = services.submit_reason(
            actor=request.user, case=self.get_object(), version=data.validated_data["version"],
            reason_category=data.validated_data["reason_category"],
            explanation=data.validated_data["explanation"],
        )
        return Response(self._fresh(request, case))

    @extend_schema(
        request=ReviewSerializer,
        responses={200: OverdueCaseSerializer, 403: ErrorSerializer, 404: ErrorSerializer,
                   409: ErrorSerializer, **_ERRORS},
        summary="An eligible reviewer records the authoritative cause and a remark",
    )
    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        data = ReviewSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        case = services.review_case(
            actor=request.user, case=self.get_object(), version=data.validated_data["version"],
            cause=data.validated_data["cause"], remark=data.validated_data["remark"],
        )
        return Response(self._fresh(request, case))


def _report_cases(request):
    qs = apply_filters(selectors.visible_cases(request.user), request.query_params)
    return qs.order_by(*reports.REPORT_ORDER)


@extend_schema(tags=["overdue cases"])
class OverdueReportView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = OverdueCaseSerializer

    @extend_schema(parameters=FILTERS,
                   responses={200: OverdueCaseSerializer(many=True), **_ERRORS},
                   summary="Overdue-reason report (cases the caller may see, fixed order)")
    def get(self, request):
        page = self.paginate_queryset(_report_cases(request))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)


class _ExportView(GenericAPIView):
    def export(self, request):
        if not request.user.has_perm(perms.VIEW_ALL_CASES):
            raise PermissionDenied("Exporting overdue reports needs organisation-wide access.")
        return _report_cases(request)


@extend_schema(tags=["overdue cases"])
class OverdueCsvExportView(_ExportView):
    permission_classes = [IsAuthenticated]

    @extend_schema(parameters=FILTERS,
                   responses={200: OpenApiResponse(OpenApiTypes.BINARY, description="text/csv"),
                              403: ErrorSerializer, **_ERRORS})
    def get(self, request):
        response = HttpResponse(reports.write_csv(self.export(request)),
                                content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="overdue-cases.csv"'
        return response


@extend_schema(tags=["overdue cases"])
class OverdueExcelExportView(_ExportView):
    permission_classes = [IsAuthenticated]

    @extend_schema(parameters=FILTERS,
                   responses={200: OpenApiResponse(OpenApiTypes.BINARY, description="xlsx"),
                              403: ErrorSerializer, **_ERRORS})
    def get(self, request):
        response = HttpResponse(reports.write_xlsx(self.export(request)),
                                content_type=reports.XLSX_CONTENT_TYPE)
        response["Content-Disposition"] = 'attachment; filename="overdue-cases.xlsx"'
        return response
