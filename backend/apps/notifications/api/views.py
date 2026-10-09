from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.serializers import ErrorSerializer

from .. import services
from ..models import Notification
from .serializers import MarkedSerializer, NotificationSerializer, UnreadCountSerializer


@extend_schema(tags=["notifications"])
class NotificationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """The signed-in user's own notifications. Nobody can read another person's."""

    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer
    lookup_value_regex = r"\d+"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Notification.objects.none()
        qs = Notification.objects.filter(recipient=self.request.user)
        if self.request.query_params.get("unread") in ("1", "true"):
            qs = qs.filter(read_at__isnull=True)
        return qs

    @extend_schema(
        parameters=[OpenApiParameter("unread", OpenApiTypes.BOOL)],
        responses={200: NotificationSerializer(many=True), 401: ErrorSerializer},
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(responses={200: UnreadCountSerializer, 401: ErrorSerializer})
    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        count = Notification.objects.filter(recipient=request.user, read_at__isnull=True).count()
        return Response({"unread": count})

    @extend_schema(
        request=None,
        responses={200: NotificationSerializer, 401: ErrorSerializer, 404: ErrorSerializer},
    )
    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        get_object_or_404(Notification, pk=pk, recipient=request.user)
        row = services.mark_read(user=request.user, notification_id=int(pk))
        return Response(NotificationSerializer(row).data)

    @extend_schema(request=None, responses={200: MarkedSerializer, 401: ErrorSerializer})
    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        return Response({"marked": services.mark_all_read(user=request.user)})
