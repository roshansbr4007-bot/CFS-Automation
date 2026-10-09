from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import PrioritySlaViewSet, SlaRuleViewSet, SlaSettingView

router = SimpleRouter()
router.register("sla-rules", SlaRuleViewSet, basename="sla-rule")
router.register("sla-priority-rules", PrioritySlaViewSet, basename="sla-priority-rule")

urlpatterns = [
    path("sla-settings/", SlaSettingView.as_view(), name="sla-settings"),
    *router.urls,
]
