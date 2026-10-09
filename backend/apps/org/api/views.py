from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.core.errors import AppError
from apps.core.serializers import ErrorSerializer

from .. import selectors, services
from ..models import Department, Employee
from .permissions import DepartmentPermission, EmployeePermission
from .serializers import (
    DailyLoginSerializer,
    DepartmentCreateSerializer,
    DepartmentSerializer,
    DepartmentUpdateSerializer,
    EmployeeCreateSerializer,
    EmployeeSerializer,
    EmployeeUpdateSerializer,
    LinkLoginSerializer,
    VersionSerializer,
)

_ERRORS = {400: ErrorSerializer, 401: ErrorSerializer, 403: ErrorSerializer}
_DATE_PARAMS = [
    OpenApiParameter("from", OpenApiTypes.DATE, description="First IST date (inclusive)"),
    OpenApiParameter("to", OpenApiTypes.DATE, description="Last IST date (inclusive)"),
]


class NoEmployeeRecord(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "no_employee_record"
    message = "You do not have an employee record yet."


@extend_schema(tags=["departments"])
class DepartmentListCreateView(GenericAPIView):
    permission_classes = [DepartmentPermission]
    serializer_class = DepartmentSerializer
    pagination_class = None
    queryset = Department.objects.all()

    @extend_schema(responses={200: DepartmentSerializer(many=True), 401: ErrorSerializer})
    def get(self, request):
        return Response(DepartmentSerializer(Department.objects.order_by("code"), many=True).data)

    @extend_schema(
        request=DepartmentCreateSerializer,
        responses={201: DepartmentSerializer, 409: ErrorSerializer, **_ERRORS},
    )
    def post(self, request):
        data = DepartmentCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        department = services.create_department(actor=request.user, **data.validated_data)
        return Response(DepartmentSerializer(department).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["departments"])
class DepartmentDetailView(GenericAPIView):
    permission_classes = [DepartmentPermission]
    serializer_class = DepartmentSerializer
    queryset = Department.objects.all()

    @extend_schema(
        responses={200: DepartmentSerializer, 404: ErrorSerializer, 401: ErrorSerializer}
    )
    def get(self, request, pk):
        return Response(DepartmentSerializer(get_object_or_404(Department, pk=pk)).data)

    @extend_schema(
        request=DepartmentUpdateSerializer,
        responses={200: DepartmentSerializer, 404: ErrorSerializer, **_ERRORS},
    )
    def patch(self, request, pk):
        department = get_object_or_404(Department, pk=pk)
        data = DepartmentUpdateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        department = services.update_department(
            actor=request.user, department=department, **data.validated_data
        )
        return Response(DepartmentSerializer(department).data)


@extend_schema(tags=["employees"])
class EmployeeViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Employee records. No DELETE: employees are deactivated, never removed."""

    permission_classes = [EmployeePermission]
    serializer_class = EmployeeSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]
    lookup_value_regex = r"\d+"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # OpenAPI generation, no real user
            return Employee.objects.none()
        if self.action == "list":
            return selectors.list_employees(self.request.user, self.request.query_params)
        return selectors.visible_employees(self.request.user)

    def _fresh(self, employee):
        return selectors.visible_employees(self.request.user).get(pk=employee.pk)

    @extend_schema(
        parameters=[
            OpenApiParameter("search", OpenApiTypes.STR),
            OpenApiParameter("department", OpenApiTypes.INT),
            OpenApiParameter("is_active", OpenApiTypes.BOOL),
            OpenApiParameter("has_login", OpenApiTypes.BOOL),
        ],
        responses={200: EmployeeSerializer(many=True), **_ERRORS},
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(responses={200: EmployeeSerializer, 404: ErrorSerializer, 401: ErrorSerializer})
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(
        request=EmployeeCreateSerializer,
        responses={201: EmployeeSerializer, 409: ErrorSerializer, **_ERRORS},
    )
    def create(self, request):
        data = EmployeeCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        employee = services.create_employee(actor=request.user, **data.validated_data)
        return Response(
            EmployeeSerializer(self._fresh(employee)).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(
        request=EmployeeUpdateSerializer,
        responses={200: EmployeeSerializer, 404: ErrorSerializer, 409: ErrorSerializer, **_ERRORS},
    )
    def partial_update(self, request, pk=None):
        employee = self.get_object()
        data = EmployeeUpdateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        changes = dict(data.validated_data)
        version = changes.pop("version")
        employee = services.update_employee(
            actor=request.user, employee=employee, version=version, **changes
        )
        return Response(EmployeeSerializer(self._fresh(employee)).data)

    @extend_schema(
        request=LinkLoginSerializer,
        responses={200: EmployeeSerializer, 404: ErrorSerializer, 409: ErrorSerializer, **_ERRORS},
    )
    @action(detail=True, methods=["post"], url_path="link-login")
    def link_login(self, request, pk=None):
        employee = self.get_object()
        data = LinkLoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        employee = services.link_login(
            actor=request.user,
            employee=employee,
            user=data.validated_data["user"],
            version=data.validated_data["version"],
        )
        return Response(EmployeeSerializer(self._fresh(employee)).data)

    @extend_schema(
        request=VersionSerializer,
        responses={200: EmployeeSerializer, 404: ErrorSerializer, 409: ErrorSerializer, **_ERRORS},
    )
    @action(detail=True, methods=["post"], url_path="unlink-login")
    def unlink_login(self, request, pk=None):
        employee = self.get_object()
        data = VersionSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        employee = services.unlink_login(
            actor=request.user, employee=employee, version=data.validated_data["version"]
        )
        return Response(EmployeeSerializer(self._fresh(employee)).data)

    @extend_schema(
        parameters=_DATE_PARAMS,
        responses={200: DailyLoginSerializer(many=True), 404: ErrorSerializer, **_ERRORS},
    )
    @action(detail=True, methods=["get"], url_path="logins")
    def logins(self, request, pk=None):
        employee = self.get_object()
        return self._paginated_logins(employee)

    @extend_schema(responses={200: EmployeeSerializer, 404: ErrorSerializer, 401: ErrorSerializer})
    @action(detail=False, methods=["get"], url_path="me")
    def me(self, request):
        return Response(EmployeeSerializer(self._own_employee()).data)

    @extend_schema(
        parameters=_DATE_PARAMS,
        responses={200: DailyLoginSerializer(many=True), 404: ErrorSerializer, **_ERRORS},
    )
    @action(detail=False, methods=["get"], url_path="me/logins")
    def me_logins(self, request):
        return self._paginated_logins(self._own_employee())

    def _own_employee(self):
        user = self.request.user
        employee = selectors.visible_employees(user).filter(user=user).first()
        if employee is None:
            raise NoEmployeeRecord()
        return employee

    def _paginated_logins(self, employee):
        facts = selectors.login_facts(employee, self.request.query_params)
        page = self.paginate_queryset(facts)
        return self.get_paginated_response(DailyLoginSerializer(page, many=True).data)
