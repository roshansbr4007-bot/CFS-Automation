from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

api_v1 = [
    path("", include("apps.core.api.urls")),
    path("", include("apps.accounts.api.urls")),
    path("", include("apps.audit.api.urls")),
    path("", include("apps.org.api.urls")),
    path("", include("apps.tasks.api.urls")),
    path("", include("apps.sla.api.urls")),
    path("", include("apps.notifications.api.urls")),
    path("", include("apps.calendars.api.urls")),
    path("", include("apps.recurring.api.urls")),
    path("", include("apps.command_center.api.urls")),
    path("", include("apps.performance.api.urls")),
    path("", include("apps.overdue.api.urls")),
]

urlpatterns = [
    path("api/v1/", include(api_v1)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]
