from rest_framework.routers import SimpleRouter

from .views import RecurringScheduleViewSet, ResponsibilityViewSet, ScheduleOccurrenceViewSet

router = SimpleRouter()
router.register("responsibilities", ResponsibilityViewSet, basename="responsibility")
router.register("recurring-schedules", RecurringScheduleViewSet, basename="recurring-schedule")
router.register("schedule-occurrences", ScheduleOccurrenceViewSet, basename="schedule-occurrence")

urlpatterns = router.urls
