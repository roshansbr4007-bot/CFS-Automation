"""Organisation core: departments, employees and their daily login facts (Phase 2, Q1-Q9).

Rows are deactivated, never deleted; every foreign key is PROTECT. All writes go through
apps.org.services so that each change is audited in the same transaction.
"""

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower, Upper


class Department(models.Model):
    code = models.CharField(max_length=16, unique=True)  # immutable after creation (service)
    name = models.CharField(max_length=100)
    is_live = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "org_department"
        ordering = ["code"]
        default_permissions = ()
        permissions = [("manage_departments", "Create departments and edit name or live flag")]
        constraints = [
            models.CheckConstraint(
                condition=Q(code=Upper("code")) & ~Q(code=""),
                name="org_department_code_upper_chk",
            ),
        ]

    def __str__(self):
        return self.code


class Employee(models.Model):
    """Business identity of a person. The login (accounts.User) is optional and linked 1:1."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="employee",
    )
    # NULL (not "") when absent, so the partial unique constraint allows many uncoded employees.
    employee_code = models.CharField(max_length=32, null=True, blank=True)  # noqa: DJ001
    full_name = models.CharField(max_length=150)
    email = models.EmailField()
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="employees")
    reporting_manager = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="direct_reports",
    )
    designation = models.CharField(max_length=100, blank=True)
    # Phase 7.1: start of performance history (mid-month joining is N/A before this date).
    date_of_joining = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "org_employee"
        ordering = ["full_name", "id"]
        default_permissions = ()
        permissions = [
            ("view_all_employees", "See every employee and their login history"),
            ("view_team_employees", "See employees in one's own department"),
            ("manage_employees", "Create and edit employee records"),
            ("link_employee_login", "Link or unlink an employee's login"),
        ]
        constraints = [
            models.UniqueConstraint(Lower("email"), name="org_employee_email_ci_uniq"),
            models.UniqueConstraint(
                fields=["employee_code"],
                condition=Q(employee_code__isnull=False) & ~Q(employee_code=""),
                name="org_employee_code_uniq_when_set",
            ),
            models.CheckConstraint(
                condition=Q(reporting_manager__isnull=True) | ~Q(reporting_manager=F("id")),
                name="org_employee_not_own_manager_chk",
            ),
        ]
        indexes = [models.Index(fields=["department", "is_active"], name="org_emp_dept_active_idx")]

    def __str__(self):
        return self.full_name

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)


class EmployeeDailyLogin(models.Model):
    """First successful application login of an employee on an IST calendar date.

    Written once, never updated. Whether the day is a working day (is_valid) is decided by the
    calendar engine in Phase 5 (approved Q6).
    """

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="daily_logins")
    work_date = models.DateField()
    first_login_at = models.DateTimeField()
    # Approved R20 (decided with the calendar in Phase 5): only a login on a Company Calendar
    # working day is a valid business login. Non-working-day logins are kept as facts.
    is_valid = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "org_employee_daily_login"
        ordering = ["-work_date"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["employee", "work_date"], name="org_daily_login_uniq"),
        ]
        indexes = [models.Index(fields=["work_date"], name="org_daily_login_date_idx")]

    def __str__(self):
        return f"{self.employee_id} {self.work_date}"
