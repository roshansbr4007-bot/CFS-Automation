from django.contrib.auth.models import Group
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.serializers import ErrorSerializer

from .. import roles as role_defs
from .. import selectors, services
from ..models import User
from .permissions import CanManageUsers
from .serializers import (
    LoginSerializer,
    MeSerializer,
    PasswordChangeSerializer,
    RoleSerializer,
    SetPasswordSerializer,
    UserCreateSerializer,
    UserSerializer,
    UserUpdateSerializer,
)

_Detail = inline_serializer(name="Detail", fields={"detail": serializers.CharField()})
_ERRORS = {400: ErrorSerializer, 401: ErrorSerializer, 403: ErrorSerializer}


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(tags=["auth"], responses={200: _Detail})
    def get(self, request):
        return Response({"detail": "CSRF cookie set."})


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(tags=["auth"], request=LoginSerializer,
                   responses={200: MeSerializer, 400: ErrorSerializer, 403: ErrorSerializer})
    def post(self, request):
        data = LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = services.login_user(
            request, email=data.validated_data["email"], password=data.validated_data["password"]
        )
        return Response(MeSerializer(user).data)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["auth"], request=None, responses={204: None, **_ERRORS})
    def post(self, request):
        services.logout_user(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["auth"], responses={200: MeSerializer, 401: ErrorSerializer})
    def get(self, request):
        return Response(MeSerializer(request.user).data)


class PasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["auth"], request=PasswordChangeSerializer, responses={204: None, **_ERRORS}
    )
    def post(self, request):
        data = PasswordChangeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        services.change_own_password(request, **data.validated_data)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["users"])
class UserViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Admin user management. There is no DELETE: users are deactivated, never removed."""

    permission_classes = [CanManageUsers]
    serializer_class = UserSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        if self.action == "list":
            return selectors.list_users(self.request.query_params)
        return User.objects.prefetch_related("groups")

    @extend_schema(
        parameters=[
            OpenApiParameter("search", OpenApiTypes.STR),
            OpenApiParameter("role", OpenApiTypes.STR, enum=list(role_defs.ROLE_NAMES)),
            OpenApiParameter("is_active", OpenApiTypes.BOOL),
        ],
        responses={200: UserSerializer(many=True), **_ERRORS},
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(responses={200: UserSerializer, 404: ErrorSerializer, **_ERRORS})
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(request=UserCreateSerializer,
                   responses={201: UserSerializer, 409: ErrorSerializer, **_ERRORS})
    def create(self, request):
        data = UserCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = services.create_user(actor=request.user, **data.validated_data)
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=UserUpdateSerializer,
                   responses={200: UserSerializer, 404: ErrorSerializer, **_ERRORS})
    def partial_update(self, request, pk=None):
        user = self.get_object()
        data = UserUpdateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = services.update_user(actor=request.user, user=user, **data.validated_data)
        user = User.objects.prefetch_related("groups").get(pk=user.pk)
        return Response(UserSerializer(user).data)

    @extend_schema(request=SetPasswordSerializer,
                   responses={204: None, 404: ErrorSerializer, **_ERRORS})
    @action(detail=True, methods=["post"], url_path="set-password")
    def set_password(self, request, pk=None):
        user = self.get_object()
        data = SetPasswordSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        services.set_password_by_admin(
            actor=request.user, user=user, password=data.validated_data["password"]
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class RoleListView(APIView):
    permission_classes = [CanManageUsers]

    @extend_schema(tags=["roles"], responses={200: RoleSerializer(many=True), **_ERRORS})
    def get(self, request):
        groups = {g.name: g for g in Group.objects.prefetch_related("permissions__content_type")}
        data = []
        for name in role_defs.ROLE_NAMES:
            group = groups.get(name)
            perms = (
                sorted(f"{p.content_type.app_label}.{p.codename}" for p in group.permissions.all())
                if group
                else []
            )
            data.append({"name": name, "permissions": perms})
        return Response(RoleSerializer(data, many=True).data)
