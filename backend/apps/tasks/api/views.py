from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.serializers import ErrorSerializer
from apps.sla import services as sla

from .. import monitoring, selectors, services
from ..models import Task, TaskCategory, TaskPriority, TaskStatus, TaskTemplate
from . import monitoring_views
from .permissions import TaskCategoryPermission, TaskPermission
from .serializers import (
    AssigneeOptionSerializer,
    AttachmentUploadSerializer,
    CommentCreateSerializer,
    DailyActivityListSerializer,
    DeleteQuerySerializer,
    ReasonSerializer,
    ReassignSerializer,
    RejectVerificationSerializer,
    SlaPreviewRequestSerializer,
    TaskAttachmentSerializer,
    TaskCategoryCreateSerializer,
    TaskCategorySerializer,
    TaskCategoryUpdateSerializer,
    TaskCommentSerializer,
    TaskCreateSerializer,
    TaskDetailSerializer,
    TaskSerializer,
    TaskSlaSerializer,
    TaskTemplateSerializer,
    TaskUpdateSerializer,
    VerifySerializer,
    VersionSerializer,
)

_ERRORS = {
    400: ErrorSerializer,
    401: ErrorSerializer,
    403: ErrorSerializer,
    404: ErrorSerializer,
    409: ErrorSerializer,
}


def _action_schema(request_serializer, summary):
    return extend_schema(
        request=request_serializer,
        responses={200: TaskDetailSerializer, **_ERRORS},
        summary=summary,
    )


@extend_schema(tags=["tasks"])
class TaskViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Tasks. Status changes only happen through the explicit action endpoints.
    DELETE is a physical delete for HR / Admin (Phase 4) and needs ?version=N."""

    permission_classes = [TaskPermission]
    serializer_class = TaskSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    lookup_value_regex = r"\d+"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # OpenAPI generation, no real user
            return Task.objects.none()
        if self.action == "list":
            return selectors.list_tasks(self.request.user, self.request.query_params)
        return selectors.visible_tasks(self.request.user)

    def get_serializer_class(self):
        return TaskSerializer if self.action == "list" else TaskDetailSerializer

    def _detail(self, task, status_code=status.HTTP_200_OK):
        """Serialize the task after a successful, service-authorized write."""
        fresh = selectors.visible_tasks(self.request.user).filter(pk=task.pk).first()
        if fresh is None:
            # The authorized write itself moved the task out of this user's scope (e.g. an
            # Operations Manager changed its department). Report the result of that write;
            # allowed_actions is computed for the new state, and later requests get 404.
            fresh = selectors.task_after_authorized_write(task.pk)
        data = TaskDetailSerializer(fresh, context=self.get_serializer_context()).data
        return Response(data, status=status_code)

    def _validated(self, serializer_class):
        data = serializer_class(data=self.request.data)
        data.is_valid(raise_exception=True)
        return dict(data.validated_data)

    @extend_schema(
        parameters=[
            OpenApiParameter("view", OpenApiTypes.STR, enum=list(selectors.VIEWS),
                             description="received = assigned to me; sent = created by me"),
            OpenApiParameter(
                "source",
                OpenApiTypes.STR,
                enum=list(selectors.SOURCES),
                description="scheduled = generated from a responsibility; manual = assigned",
            ),
            OpenApiParameter("status", OpenApiTypes.STR, enum=TaskStatus.values),
            OpenApiParameter("priority", OpenApiTypes.STR, enum=TaskPriority.values),
            OpenApiParameter("assignee", OpenApiTypes.INT, description="Employee id"),
            OpenApiParameter("department", OpenApiTypes.INT),
            OpenApiParameter("search", OpenApiTypes.STR),
            OpenApiParameter("from", OpenApiTypes.DATE, description="Created on/after (IST date)"),
            OpenApiParameter("to", OpenApiTypes.DATE, description="Created on/before (IST date)"),
        ],
        responses={200: TaskSerializer(many=True), **_ERRORS},
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(responses={200: TaskDetailSerializer, **_ERRORS})
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(request=TaskCreateSerializer, responses={201: TaskDetailSerializer, **_ERRORS})
    def create(self, request):
        task = services.create_task(actor=request.user, **self._validated(TaskCreateSerializer))
        return self._detail(task, status.HTTP_201_CREATED)

    @extend_schema(request=TaskUpdateSerializer, responses={200: TaskDetailSerializer, **_ERRORS})
    def partial_update(self, request, pk=None):
        task = self.get_object()
        changes = self._validated(TaskUpdateSerializer)
        version = changes.pop("version")
        reason = changes.pop("received_at_reason", None)
        task = services.update_task(
            actor=request.user, task=task, version=version, received_at_reason=reason, **changes
        )
        return self._detail(task)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "version",
                OpenApiTypes.INT,
                required=True,
                description="The task's current version; a stale version returns 409.",
            )
        ],
        responses={204: None, **_ERRORS},
        summary="Physically delete the task (HR / Admin)",
    )
    def destroy(self, request, pk=None):
        params = DeleteQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        services.delete_task(
            actor=request.user, task=self.get_object(), version=params.validated_data["version"]
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        parameters=[monitoring_views.DATE],
        responses={200: DailyActivityListSerializer, **_ERRORS},
        summary="My daily activities (generated by the scheduler; reading never creates any)",
    )
    @action(detail=False, methods=["get"], url_path="daily-activities")
    def daily_activities(self, request):
        now = timezone.now()
        day = monitoring_views.business_date(request, now)
        rows = monitoring.my_daily_activities(request.user, day, now)
        body = {"date": day, "server_time": now, "activities": rows}
        return Response(DailyActivityListSerializer(body).data)

    @extend_schema(
        request=SlaPreviewRequestSerializer,
        responses={200: TaskSlaSerializer, **_ERRORS},
        summary="Backend-calculated SLA for a task that is about to be created",
    )
    @action(detail=False, methods=["post"], url_path="sla-preview")
    def sla_preview(self, request):
        data = self._validated(SlaPreviewRequestSerializer)
        template = data.get("template")
        ack = data.get("acknowledgment_required")
        if ack is None:
            ack = bool(template and template.acknowledgment_required)
        result = sla.preview(
            template=template,
            assignee=data.get("assigned_to"),
            acknowledgment_required=ack,
            trigger_at=data.get("trigger_at"),
            now=timezone.now(),
            priority=data.get("priority"),
        )
        return Response(TaskSlaSerializer(result).data)

    @extend_schema(responses={200: AssigneeOptionSerializer(many=True), **_ERRORS})
    @action(detail=False, methods=["get"], url_path="assignees", pagination_class=None)
    def assignees(self, request):
        employees = selectors.assignable_employees(request.user)
        return Response(AssigneeOptionSerializer(employees, many=True).data)

    @_action_schema(ReassignSerializer, "Reassign the task")
    @action(detail=True, methods=["post"])
    def reassign(self, request, pk=None):
        data = self._validated(ReassignSerializer)
        task = services.reassign_task(actor=request.user, task=self.get_object(), **data)
        return self._detail(task)

    @_action_schema(VersionSerializer, "Acknowledge (assignee only)")
    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        data = self._validated(VersionSerializer)
        task = services.acknowledge_task(actor=request.user, task=self.get_object(), **data)
        return self._detail(task)

    @_action_schema(VersionSerializer, "Start (assignee only)")
    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        data = self._validated(VersionSerializer)
        return self._detail(services.start_task(actor=request.user, task=self.get_object(), **data))

    @_action_schema(VersionSerializer, "Complete (assignee only)")
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        data = self._validated(VersionSerializer)
        task = services.complete_task(actor=request.user, task=self.get_object(), **data)
        return self._detail(task)

    @_action_schema(ReasonSerializer, "Block / put on hold (reason required)")
    @action(detail=True, methods=["post"])
    def block(self, request, pk=None):
        data = self._validated(ReasonSerializer)
        return self._detail(services.block_task(actor=request.user, task=self.get_object(), **data))

    @_action_schema(VersionSerializer, "Unblock")
    @action(detail=True, methods=["post"])
    def unblock(self, request, pk=None):
        data = self._validated(VersionSerializer)
        task = services.unblock_task(actor=request.user, task=self.get_object(), **data)
        return self._detail(task)

    @_action_schema(ReasonSerializer, "Cancel (reason required)")
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        data = self._validated(ReasonSerializer)
        task = services.cancel_task(actor=request.user, task=self.get_object(), **data)
        return self._detail(task)

    @_action_schema(VerifySerializer, "Approve verification")
    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        data = self._validated(VerifySerializer)
        task = services.verify_task(actor=request.user, task=self.get_object(), **data)
        return self._detail(task)

    @_action_schema(RejectVerificationSerializer, "Reject verification (reason + remarks)")
    @action(detail=True, methods=["post"], url_path="reject-verification")
    def reject_verification(self, request, pk=None):
        data = self._validated(RejectVerificationSerializer)
        task = services.reject_verification(actor=request.user, task=self.get_object(), **data)
        return self._detail(task)

    @extend_schema(methods=["GET"], responses={200: TaskCommentSerializer(many=True), **_ERRORS})
    @extend_schema(
        methods=["POST"],
        request=CommentCreateSerializer,
        responses={201: TaskCommentSerializer, **_ERRORS},
    )
    @action(detail=True, methods=["get", "post"], pagination_class=None)
    def comments(self, request, pk=None):
        task = self.get_object()
        if request.method == "GET":
            rows = task.comments.select_related("author")
            return Response(TaskCommentSerializer(rows, many=True).data)
        data = self._validated(CommentCreateSerializer)
        comment = services.add_comment(actor=request.user, task=task, **data)
        return Response(TaskCommentSerializer(comment).data, status=status.HTTP_201_CREATED)

    @extend_schema(methods=["GET"], responses={200: TaskAttachmentSerializer(many=True), **_ERRORS})
    @extend_schema(
        methods=["POST"],
        request={"multipart/form-data": AttachmentUploadSerializer},
        responses={201: TaskAttachmentSerializer, **_ERRORS},
    )
    @action(
        detail=True,
        methods=["get", "post"],
        pagination_class=None,
        parser_classes=[MultiPartParser],
    )
    def attachments(self, request, pk=None):
        task = self.get_object()
        if request.method == "GET":
            rows = task.attachments.select_related("uploaded_by")
            return Response(TaskAttachmentSerializer(rows, many=True).data)
        upload = AttachmentUploadSerializer(data=request.data)
        upload.is_valid(raise_exception=True)
        attachment = services.add_attachment(
            actor=request.user, task=task, upload=upload.validated_data["file"]
        )
        return Response(TaskAttachmentSerializer(attachment).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        parameters=[
            OpenApiParameter("attachment_id", OpenApiTypes.INT, OpenApiParameter.PATH),
        ],
        responses={
            (200, "application/octet-stream"): OpenApiResponse(OpenApiTypes.BINARY),
            **_ERRORS,
        },
    )
    @action(
        detail=True,
        methods=["get"],
        url_path=r"attachments/(?P<attachment_id>\d+)/download",
    )
    def download_attachment(self, request, pk=None, attachment_id=None):
        task = self.get_object()  # 404 unless the caller may see the task
        attachment = get_object_or_404(task.attachments, pk=attachment_id)
        response = FileResponse(
            attachment.file.open("rb"),
            as_attachment=True,
            filename=attachment.original_filename,
            content_type="application/octet-stream",
        )
        return response


@extend_schema(
    tags=["tasks"], responses={200: TaskTemplateSerializer(many=True), 401: ErrorSerializer}
)
class TaskTemplateListView(generics.ListAPIView):
    """Active task types (templates) for the New Task form."""

    permission_classes = [IsAuthenticated]
    serializer_class = TaskTemplateSerializer
    pagination_class = None
    queryset = TaskTemplate.objects.filter(is_active=True).select_related("department")


@extend_schema(tags=["tasks"])
class TaskCategoryViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet
):
    """Task categories. Everyone signed in reads the active ones; Admin manages the list.
    Categories are deactivated, never deleted (tasks keep referring to them)."""

    permission_classes = [TaskCategoryPermission]
    serializer_class = TaskCategorySerializer
    pagination_class = None
    http_method_names = ["get", "post", "patch", "head", "options"]
    lookup_value_regex = r"\d+"

    def get_queryset(self):
        qs = TaskCategory.objects.all()
        if self.request.query_params.get("include_inactive") not in ("1", "true"):
            qs = qs.filter(is_active=True)
        return qs

    @extend_schema(
        parameters=[OpenApiParameter("include_inactive", OpenApiTypes.BOOL)],
        responses={200: TaskCategorySerializer(many=True), 401: ErrorSerializer},
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        request=TaskCategoryCreateSerializer, responses={201: TaskCategorySerializer, **_ERRORS}
    )
    def create(self, request, *args, **kwargs):
        data = TaskCategoryCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        category = services.create_category(actor=request.user, **data.validated_data)
        return Response(TaskCategorySerializer(category).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        request=TaskCategoryUpdateSerializer, responses={200: TaskCategorySerializer, **_ERRORS}
    )
    def partial_update(self, request, *args, **kwargs):
        category = get_object_or_404(TaskCategory, pk=kwargs["pk"])
        data = TaskCategoryUpdateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        category = services.update_category(
            actor=request.user, category=category, **data.validated_data
        )
        return Response(TaskCategorySerializer(category).data)
