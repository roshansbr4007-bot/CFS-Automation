from rest_framework import serializers

from apps.accounts.models import User

from ..models import Department, Employee, EmployeeDailyLogin


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "code", "name", "is_live", "created_at", "updated_at"]
        read_only_fields = fields


class DepartmentCreateSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=16)
    name = serializers.CharField(max_length=100)
    is_live = serializers.BooleanField(required=False, default=False)


class DepartmentUpdateSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=16, required=False)  # accepted only to reject changes
    name = serializers.CharField(max_length=100, required=False)
    is_live = serializers.BooleanField(required=False)


class DepartmentRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "code", "name"]
        read_only_fields = fields


class EmployeeRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Employee
        fields = ["id", "full_name"]
        read_only_fields = fields


class LoginRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "is_active"]
        read_only_fields = fields


class EmployeeSerializer(serializers.ModelSerializer):
    department = DepartmentRefSerializer(read_only=True)
    reporting_manager = EmployeeRefSerializer(read_only=True, allow_null=True)
    user = LoginRefSerializer(read_only=True, allow_null=True)

    class Meta:
        model = Employee
        fields = [
            "id",
            "employee_code",
            "full_name",
            "email",
            "department",
            "reporting_manager",
            "designation",
            "date_of_joining",
            "is_active",
            "user",
            "version",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class EmployeeCreateSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=150)
    email = serializers.EmailField(max_length=254)
    department = serializers.PrimaryKeyRelatedField(queryset=Department.objects.all())
    employee_code = serializers.CharField(
        max_length=32, required=False, allow_null=True, allow_blank=True, default=None
    )
    reporting_manager = serializers.PrimaryKeyRelatedField(
        queryset=Employee.objects.all(), required=False, allow_null=True, default=None
    )
    designation = serializers.CharField(
        max_length=100, required=False, allow_blank=True, default=""
    )
    date_of_joining = serializers.DateField(required=False, allow_null=True, default=None)


class EmployeeUpdateSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)
    full_name = serializers.CharField(max_length=150, required=False)
    email = serializers.EmailField(max_length=254, required=False)
    department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(), required=False
    )
    employee_code = serializers.CharField(
        max_length=32, required=False, allow_null=True, allow_blank=True
    )
    reporting_manager = serializers.PrimaryKeyRelatedField(
        queryset=Employee.objects.all(), required=False, allow_null=True
    )
    designation = serializers.CharField(max_length=100, required=False, allow_blank=True)
    date_of_joining = serializers.DateField(required=False, allow_null=True)
    is_active = serializers.BooleanField(required=False)


class LinkLoginSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())


class VersionSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)


class DailyLoginSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeDailyLogin
        fields = ["work_date", "first_login_at"]
        read_only_fields = fields
