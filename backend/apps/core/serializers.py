from rest_framework import serializers


class ErrorSerializer(serializers.Serializer):
    """Documents the shared error shape in the OpenAPI schema."""

    code = serializers.CharField()
    message = serializers.CharField()
    fields = serializers.DictField(child=serializers.JSONField(), required=False)
