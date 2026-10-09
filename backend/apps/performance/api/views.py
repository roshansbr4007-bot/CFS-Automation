"""Performance reporting API (Phase 7 Stage B). Read-only: nothing here changes a record.

Everyone signed in may read the report within their own scope (selectors module docstring);
exports require organisation-wide visibility (HR, Admin).
"""

from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated

from apps.core.serializers import ErrorSerializer

from .. import reports, selectors
from ..models import PerformanceStatus
from .serializers import PerformanceReportRowSerializer

REPORT_FILTERS = [
    OpenApiParameter("year", OpenApiTypes.INT),
    OpenApiParameter("month", OpenApiTypes.INT, description="1 to 12"),
    OpenApiParameter("date_from", OpenApiTypes.DATE, description="Period overlaps from"),
    OpenApiParameter("date_to", OpenApiTypes.DATE, description="Period overlaps to"),
    OpenApiParameter("employee", OpenApiTypes.INT),
    OpenApiParameter("department", OpenApiTypes.INT),
    OpenApiParameter("kpi", OpenApiTypes.STR, description="KPI code"),
    OpenApiParameter("status", OpenApiTypes.STR, enum=list(PerformanceStatus.values)),
    OpenApiParameter("performance_band", OpenApiTypes.STR,
                     enum=list(selectors.PERFORMANCE_BANDS)),
]
_ERRORS = {400: ErrorSerializer, 401: ErrorSerializer}


def _records(request):
    filters = selectors.parse_report_filters(request.query_params)
    return selectors.report_queryset(request.user, filters)


@extend_schema(tags=["performance"])
class PerformanceReportView(GenericAPIView):
    permission_classes = [IsAuthenticated]  # who sees which records: selectors.report_queryset
    serializer_class = PerformanceReportRowSerializer

    @extend_schema(
        parameters=REPORT_FILTERS,
        responses={200: PerformanceReportRowSerializer(many=True), **_ERRORS},
        summary="Monthly performance report (stored snapshots, filtered, in a fixed order)",
    )
    def get(self, request):
        page = self.paginate_queryset(_records(request))
        return self.get_paginated_response(PerformanceReportRowSerializer(page, many=True).data)


class _ExportView(GenericAPIView):
    def export(self, request):
        if not selectors.can_export(request.user):
            raise PermissionDenied("Exporting performance reports needs organisation-wide access.")
        return _records(request)


@extend_schema(tags=["performance"])
class PerformanceCsvExportView(_ExportView):
    permission_classes = [IsAuthenticated]  # export rights: selectors.can_export (HR, Admin)

    @extend_schema(
        parameters=REPORT_FILTERS,
        responses={200: OpenApiResponse(OpenApiTypes.BINARY, description="text/csv"),
                   403: ErrorSerializer, **_ERRORS},
        summary="Performance report as CSV (same filters and rows as the report)",
    )
    def get(self, request):
        body = reports.write_csv(self.export(request))
        response = HttpResponse(body, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="performance-report.csv"'
        return response


@extend_schema(tags=["performance"])
class PerformanceExcelExportView(_ExportView):
    permission_classes = [IsAuthenticated]  # export rights: selectors.can_export (HR, Admin)

    @extend_schema(
        parameters=REPORT_FILTERS,
        responses={200: OpenApiResponse(OpenApiTypes.BINARY, description="xlsx workbook"),
                   403: ErrorSerializer, **_ERRORS},
        summary="Performance report as an Excel workbook (Summary and KPI Details sheets)",
    )
    def get(self, request):
        body = reports.write_xlsx(self.export(request))
        response = HttpResponse(body, content_type=reports.XLSX_CONTENT_TYPE)
        response["Content-Disposition"] = 'attachment; filename="performance-report.xlsx"'
        return response
