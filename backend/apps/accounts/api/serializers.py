from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .. import roles as role_defs
from ..models import User


class UserSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "email", "first_name", "last_name", "full_name", "roles", "is_active",
            "last_login", "date_joined",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_roles(self, obj) -> list[str]:
        return sorted(group.name for group in obj.groups.all())


class MeSerializer(UserSerializer):
    permissions = serializers.SerializerMethodField()

    class Meta(UserSerializer.Meta):
        fields = [*UserSerializer.Meta.fields, "permissions"]
        read_only_fields = fields

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_permissions(self, obj) -> list[str]:
        return sorted(obj.get_all_permissions())


class LoginSerializer(serializers.Serializer):
    email = serializers.CharField(max_length=254)
    password = serializers.CharField(trim_whitespace=False, write_only=True)


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(trim_whitespace=False, write_only=True)
    new_password = serializers.CharField(trim_whitespace=False, write_only=True)




class UserCreateSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    first_name = serializers.CharField(max_length=150, allow_blank=True, required=False, default="")
    last_name = serializers.CharField(max_length=150, allow_blank=True, required=False, default="")
    roles = serializers.ListField(
        child=serializers.ChoiceField(choices=role_defs.ROLE_NAMES), allow_empty=True,
        required=False, default=list,
    )
    password = serializers.CharField(trim_whitespace=False, write_only=True)


class UserUpdateSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150, allow_blank=True, required=False)
    last_name = serializers.CharField(max_length=150, allow_blank=True, required=False)
    roles = serializers.ListField(
        child=serializers.ChoiceField(choices=role_defs.ROLE_NAMES), allow_empty=True,
        required=False,
    )
    is_active = serializers.BooleanField(required=False)


class SetPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(trim_whitespace=False, write_only=True)


class RoleSerializer(serializers.Serializer):
    name = serializers.CharField()
    permissions = serializers.ListField(child=serializers.CharField())
