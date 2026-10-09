from django.urls import path

from .views import (
    CalendarDayCreateView,
    CalendarDayDetailView,
    CompanyCalendarView,
    WorkingDaysView,
)

urlpatterns = [
    path("calendars/company/", CompanyCalendarView.as_view(), name="company-calendar"),
    path("calendars/company/days/", CalendarDayCreateView.as_view(), name="calendar-day-create"),
    path(
        "calendars/company/days/<int:pk>/",
        CalendarDayDetailView.as_view(),
        name="calendar-day-detail",
    ),
    path(
        "calendars/company/working-days/", WorkingDaysView.as_view(), name="company-working-days"
    ),
]
