from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.generics import ListAPIView

from apps.core.permissions import require_perm
from apps.core.serializers import ErrorSerializer

from .. import selectors
from .serializers import AuditLogSerializer

CanViewAuditLog = require_perm("audit.view_audit_log")


@extend_schema(
    tags=["audit"],
    parameters=[
        OpenApiParameter("actor", OpenApiTypes.INT, description="User id of the actor"),
        OpenApiParameter("action", OpenApiTypes.STR),
        OpenApiParameter("entity_type", OpenApiTypes.STR),
        OpenApiParameter("entity_id", OpenApiTypes.STR),
        OpenApiParameter("from", OpenApiTypes.STR, description="Date (IST day) or ISO datetime"),
        OpenApiParameter(
            "to", OpenApiTypes.STR, description="Date (IST day, inclusive) or ISO datetime"
        ),
    ],
    responses={200: AuditLogSerializer(many=True), 400: ErrorSerializer, 401: ErrorSerializer,
               403: ErrorSerializer},
)
class AuditLogListView(ListAPIView):
    """Read-only. There is no write method on the audit log."""

    permission_classes = [CanViewAuditLog]
    serializer_class = AuditLogSerializer

    def get_queryset(self):
        return selectors.search(self.request.query_params)
