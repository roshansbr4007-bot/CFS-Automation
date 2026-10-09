from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import DepartmentDetailView, DepartmentListCreateView, EmployeeViewSet

router = SimpleRouter()
router.register("employees", EmployeeViewSet, basename="employee")

urlpatterns = [
    path("departments/", DepartmentListCreateView.as_view(), name="department-list"),
    path("departments/<int:pk>/", DepartmentDetailView.as_view(), name="department-detail"),
    *router.urls,
]
