from rest_framework import serializers

from ..models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    task_reference = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id",
            "kind",
            "title",
            "body",
            "task",
            "task_reference",
            "created_at",
            "read_at",
            "email_status",
        ]
        read_only_fields = fields

    def get_task_reference(self, row) -> str | None:
        return f"T-{row.task_id:06d}" if row.task_id else None


class UnreadCountSerializer(serializers.Serializer):
    unread = serializers.IntegerField()


class MarkedSerializer(serializers.Serializer):
    marked = serializers.IntegerField()
