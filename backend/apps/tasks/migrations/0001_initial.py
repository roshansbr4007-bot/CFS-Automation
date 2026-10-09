import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("org", "0002_seed_departments"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Task",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True)),
                (
                    "task_type",
                    models.CharField(
                        choices=[("REGULAR", "Regular"), ("RECURRING", "Recurring"), ("ADHOC", "Ad-hoc"), ("INCENTIVE", "Incentive")],
                        default="ADHOC",
                        max_length=16,
                    ),
                ),
                (
                    "priority",
                    models.CharField(
                        choices=[("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High"), ("URGENT", "Urgent")],
                        default="MEDIUM",
                        max_length=8,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pending"),
                            ("IN_PROGRESS", "In Progress"),
                            ("BLOCKED", "Blocked"),
                            ("COMPLETED", "Completed"),
                            ("CANCELLED", "Cancelled"),
                        ],
                        default="PENDING",
                        max_length=16,
                    ),
                ),
                ("assigned_at", models.DateTimeField()),
                ("received_at", models.DateTimeField(blank=True, null=True)),
                (
                    "received_at_source",
                    models.CharField(
                        blank=True,
                        choices=[("EMAIL", "Email"), ("API", "API"), ("IMPORT", "Import"), ("MANUAL", "Manual"), ("SYSTEM", "System")],
                        max_length=8,
                        null=True,
                    ),
                ),
                ("acknowledgment_required", models.BooleanField(default=False)),
                ("acknowledged_at", models.DateTimeField(blank=True, null=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("completion_recorded_at", models.DateTimeField(blank=True, null=True)),
                (
                    "completion_source",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("DIRECT", "Marked complete by the assignee"),
                            ("COMPLETION_REVIEW", "Approved completion review"),
                        ],
                        max_length=20,
                        null=True,
                    ),
                ),
                ("verification_required", models.BooleanField(default=False)),
                (
                    "verification_status",
                    models.CharField(
                        choices=[
                            ("NOT_REQUIRED", "Not required"),
                            ("PENDING", "Pending"),
                            ("VERIFIED", "Verified"),
                            ("REJECTED", "Rejected"),
                        ],
                        default="NOT_REQUIRED",
                        max_length=16,
                    ),
                ),
                ("rework_count", models.PositiveIntegerField(default=0)),
                ("blocked_reason", models.TextField(blank=True)),
                ("blocked_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_reason", models.TextField(blank=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("version", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "assigned_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="assigned_tasks",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "assigned_to",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="assigned_tasks",
                        to="org.employee",
                    ),
                ),
                (
                    "completed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="completed_tasks",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_tasks",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "department",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="tasks",
                        to="org.department",
                    ),
                ),
            ],
            options={
                "db_table": "tasks_task",
                "ordering": ["-created_at", "-id"],
                "default_permissions": (),
                "permissions": [
                    ("create_task", "Create tasks (for oneself unless also allowed to assign)"),
                    ("assign", "Assign tasks to other employees"),
                    ("view_all_tasks", "See every task"),
                    ("view_team_tasks", "See tasks of one's own department"),
                    ("manage_team_tasks", "Assign, edit, cancel and verify tasks of own department"),
                    ("manage_all_tasks", "Assign, edit, cancel and verify any task"),
                ],
                "indexes": [
                    models.Index(fields=["assigned_to", "status"], name="tasks_assignee_status_idx"),
                    models.Index(fields=["created_by", "status"], name="tasks_creator_status_idx"),
                    models.Index(fields=["department", "status"], name="tasks_dept_status_idx"),
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("received_at__isnull", True), ("received_at_source__isnull", True))
                        | models.Q(("received_at__isnull", False), ("received_at_source__isnull", False)),
                        name="tasks_received_pair_chk",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("status", "COMPLETED"), _negated=True)
                        | models.Q(("completed_at__isnull", False)),
                        name="tasks_completed_has_time_chk",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="TaskAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("assigned_at", models.DateTimeField()),
                ("note", models.TextField(blank=True)),
                (
                    "assigned_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL
                    ),
                ),
                (
                    "from_employee",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="org.employee",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="assignments", to="tasks.task"
                    ),
                ),
                (
                    "to_employee",
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="org.employee"),
                ),
            ],
            options={"db_table": "tasks_assignment", "ordering": ["assigned_at", "id"], "default_permissions": ()},
        ),
        migrations.CreateModel(
            name="TaskVerification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cycle_no", models.PositiveIntegerField()),
                ("submitted_at", models.DateTimeField()),
                ("decision", models.CharField(choices=[("VERIFIED", "Verified"), ("REJECTED", "Rejected")], max_length=8)),
                ("rejection_reason", models.TextField(blank=True)),
                ("remarks", models.TextField(blank=True)),
                ("decided_at", models.DateTimeField()),
                ("rework_seconds", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "decided_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="verifications", to="tasks.task"
                    ),
                ),
            ],
            options={
                "db_table": "tasks_verification",
                "ordering": ["cycle_no"],
                "default_permissions": (),
                "constraints": [
                    models.UniqueConstraint(fields=("task", "cycle_no"), name="tasks_verification_cycle_uniq"),
                    models.CheckConstraint(
                        condition=models.Q(("decision", "REJECTED"), _negated=True)
                        | (models.Q(("rejection_reason", ""), _negated=True) & models.Q(("remarks", ""), _negated=True)),
                        name="tasks_rejection_needs_reason_chk",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="TaskComment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("body", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "author",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="comments", to="tasks.task"
                    ),
                ),
            ],
            options={"db_table": "tasks_comment", "ordering": ["created_at", "id"], "default_permissions": ()},
        ),
        migrations.CreateModel(
            name="TaskAttachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(upload_to="task-attachments/%Y/%m/")),
                ("original_filename", models.CharField(max_length=255)),
                ("size_bytes", models.PositiveIntegerField()),
                ("sha256", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="attachments", to="tasks.task"
                    ),
                ),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL
                    ),
                ),
            ],
            options={"db_table": "tasks_attachment", "ordering": ["created_at", "id"], "default_permissions": ()},
        ),
    ]
