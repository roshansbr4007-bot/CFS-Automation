import django.db.models.deletion
import django.db.models.functions.text
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Department",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=16, unique=True)),
                ("name", models.CharField(max_length=100)),
                ("is_live", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "org_department",
                "ordering": ["code"],
                "default_permissions": (),
                "permissions": [("manage_departments", "Create departments and edit name or live flag")],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("code", django.db.models.functions.text.Upper("code")))
                        & models.Q(("code", ""), _negated=True),
                        name="org_department_code_upper_chk",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="Employee",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("employee_code", models.CharField(blank=True, max_length=32, null=True)),
                ("full_name", models.CharField(max_length=150)),
                ("email", models.EmailField(max_length=254)),
                ("designation", models.CharField(blank=True, max_length=100)),
                ("is_active", models.BooleanField(default=True)),
                ("version", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "department",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="employees",
                        to="org.department",
                    ),
                ),
                (
                    "reporting_manager",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="direct_reports",
                        to="org.employee",
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="employee",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "org_employee",
                "ordering": ["full_name", "id"],
                "default_permissions": (),
                "permissions": [
                    ("view_all_employees", "See every employee and their login history"),
                    ("view_team_employees", "See employees in one's own department"),
                    ("manage_employees", "Create and edit employee records"),
                    ("link_employee_login", "Link or unlink an employee's login"),
                ],
                "indexes": [
                    models.Index(fields=["department", "is_active"], name="org_emp_dept_active_idx")
                ],
                "constraints": [
                    models.UniqueConstraint(
                        django.db.models.functions.text.Lower("email"),
                        name="org_employee_email_ci_uniq",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("employee_code__isnull", False))
                        & models.Q(("employee_code", ""), _negated=True),
                        fields=("employee_code",),
                        name="org_employee_code_uniq_when_set",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("reporting_manager__isnull", True))
                        | models.Q(("reporting_manager", models.F("id")), _negated=True),
                        name="org_employee_not_own_manager_chk",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="EmployeeDailyLogin",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("work_date", models.DateField()),
                ("first_login_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "employee",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="daily_logins",
                        to="org.employee",
                    ),
                ),
            ],
            options={
                "db_table": "org_employee_daily_login",
                "ordering": ["-work_date"],
                "default_permissions": (),
                "indexes": [models.Index(fields=["work_date"], name="org_daily_login_date_idx")],
                "constraints": [
                    models.UniqueConstraint(fields=("employee", "work_date"), name="org_daily_login_uniq")
                ],
            },
        ),
    ]
