from rest_framework import serializers

from ..models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            "id", "occurred_at", "actor_user", "actor_email", "action", "entity_type",
            "entity_id", "old_value", "new_value", "ip", "request_id", "context",
        ]
        read_only_fields = fields

    def get_actor_email(self, obj) -> str | None:
        if obj.actor_user_id is None:
            return None
        return obj.context.get("actor_email") or obj.actor_user.email
