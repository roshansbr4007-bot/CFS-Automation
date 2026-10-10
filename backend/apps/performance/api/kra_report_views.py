"""Phase 7.4 KRA performance reading API. Read-only: nothing here changes a record.

- my/...  : any signed-in user, ONLY their own linked employee record (404 without one).
            Organisation-wide permissions never widen these (apps.performance.
            employee_performance).
- months/ and months/export/...: organisation-wide KRA month listing and CSV / Excel exports,
            for the KRA readers only (HR; Admin read-only - perms.REVIEW_READERS). The
            Operations Manager has no access (P11). One filter definition and one query serve
            the list and both exports (apps.performance.kra_reports).
"""

from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.serializers import ErrorSerializer

from .. import employee_performance as mine
from .. import kra_reports, perms, reports
from ..models import PerformanceStatus
from . import kra_report_serializers as s
from .review_views import KraReviewPermission

_ERRORS = {400: ErrorSerializer, 401: ErrorSerializer, 404: ErrorSerializer}
TAG = "performance-kra-reports"


# --- the caller's own performance ------------------------------------------------------------


@extend_schema(tags=[TAG])
class KraMyHistoryView(GenericAPIView):
    permission_classes = [IsAuthenticated]  # own linked employee record only (see module doc)
    serializer_class = s.KraMyHistorySerializer
    pagination_class = None

    @extend_schema(responses={200: s.KraMyHistorySerializer, **_ERRORS},
                   operation_id="performance_my_kra_months_history",
                   summary="My KRA months, newest first (scores only for provisional and "
                           "finalized months)")
    def get(self, request):
        return Response(s.KraMyHistorySerializer(mine.history(request.user)).data)


@extend_schema(tags=[TAG])
class KraMyMonthView(GenericAPIView):
    permission_classes = [IsAuthenticated]  # own linked employee record only (see module doc)
    serializer_class = s.KraMyMonthSerializer
    pagination_class = None

    @extend_schema(responses={200: s.KraMyMonthSerializer, **_ERRORS},
                   summary="One of my KRA months (detail only when provisional or finalized)")
    def get(self, request, pk):
        return Response(s.KraMyMonthSerializer(mine.month_detail(request.user, pk)).data)


@extend_schema(tags=[TAG])
class KraMyAnnualView(GenericAPIView):
    permission_classes = [IsAuthenticated]  # own linked employee record only (see module doc)
    serializer_class = s.KraMyAnnualSerializer
    pagination_class = None

    @extend_schema(parameters=[OpenApiParameter("year", OpenApiTypes.INT, required=True)],
                   responses={200: s.KraMyAnnualSerializer, **_ERRORS},
                   summary="My annual KRA total and average (finalized applicable months only)")
    def get(self, request):
        query = s.KraMyAnnualQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        body = mine.annual_summary(request.user, query.validated_data["year"])
        return Response(s.KraMyAnnualSerializer(body).data)



# --- organisation-wide KRA months (HR / Admin) -----------------------------------------------


KRA_FILTERS = [
    OpenApiParameter("year", OpenApiTypes.INT),
    OpenApiParameter("month", OpenApiTypes.INT, description="1 to 12"),
    OpenApiParameter("employee", OpenApiTypes.INT),
    OpenApiParameter("department", OpenApiTypes.INT,
                     description="Department recorded when the month was calculated"),
    OpenApiParameter("status", OpenApiTypes.STR, enum=list(PerformanceStatus.values)),
    OpenApiParameter("band", OpenApiTypes.STR, description="Band name"),
    OpenApiParameter("provisional", OpenApiTypes.BOOL),
]
_READ_ERRORS = {400: ErrorSerializer, 401: ErrorSerializer, 403: ErrorSerializer}


class _KraReportView(GenericAPIView):
    permission_classes = [KraReviewPermission]  # read: perms.REVIEW_READERS (HR, Admin)
    read_perms = perms.REVIEW_READERS
    write_perm = perms.MANAGE_PERFORMANCE  # no write method exists on these views

    def records(self, request):
        filters = kra_reports.parse_kra_filters(request.query_params)
        return kra_reports.kra_report_queryset(request.user, filters)


@extend_schema(tags=[TAG])
class KraMonthListView(_KraReportView):
    permission_classes = [KraReviewPermission]  # read: perms.REVIEW_READERS (HR, Admin)
    serializer_class = s.KraListRowSerializer

    @extend_schema(parameters=KRA_FILTERS,
                   responses={200: s.KraListRowSerializer(many=True), **_READ_ERRORS},
                   summary="KRA months (stored values, filtered, in a fixed order)")
    def get(self, request):
        page = self.paginate_queryset(self.records(request))
        return self.get_paginated_response(s.KraListRowSerializer(page, many=True).data)


def _attachment(body, content_type, filename):
    response = HttpResponse(body, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@extend_schema(tags=[TAG])
class KraMonthCsvExportView(_KraReportView):
    permission_classes = [KraReviewPermission]  # read: perms.REVIEW_READERS (HR, Admin)

    @extend_schema(parameters=KRA_FILTERS,
                   responses={200: OpenApiResponse(OpenApiTypes.BINARY, description="text/csv"),
                              **_READ_ERRORS},
                   summary="KRA months as CSV (all rows of the filtered list; one row per KPI)")
    def get(self, request):
        records = kra_reports.with_export_details(self.records(request))
        return _attachment(kra_reports.write_csv(records), "text/csv; charset=utf-8",
                           "kra-performance.csv")


@extend_schema(tags=[TAG])
class KraMonthExcelExportView(_KraReportView):
    permission_classes = [KraReviewPermission]  # read: perms.REVIEW_READERS (HR, Admin)

    @extend_schema(parameters=KRA_FILTERS,
                   responses={200: OpenApiResponse(OpenApiTypes.BINARY,
                                                   description="xlsx workbook"),
                              **_READ_ERRORS},
                   summary="KRA months as Excel (KRA Summary, KPI Details, Deduction Records, "
                           "About)")
    def get(self, request):
        records = kra_reports.with_export_details(self.records(request))
        return _attachment(kra_reports.write_xlsx(records), reports.XLSX_CONTENT_TYPE,
                           "kra-performance.xlsx")
