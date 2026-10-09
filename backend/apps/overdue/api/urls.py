from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import (
    OverdueCaseViewSet,
    OverdueCsvExportView,
    OverdueExcelExportView,
    OverdueReportView,
)

router = SimpleRouter()
router.register("overdue-cases", OverdueCaseViewSet, basename="overdue-case")

urlpatterns = [
    path("overdue-cases/reports/", OverdueReportView.as_view(), name="overdue-report"),
    path("overdue-cases/reports/export/csv/", OverdueCsvExportView.as_view(),
         name="overdue-report-csv"),
    path("overdue-cases/reports/export/excel/", OverdueExcelExportView.as_view(),
         name="overdue-report-excel"),
    *router.urls,
]
