"""Phase 5.2: same-day owner correction keeps the mistaken row (superseded) for history."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("recurring", "0002_seed_operations_responsibilities"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="responsibilityowner",
            name="superseded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="responsibilityowner",
            name="superseded_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="responsibilityowner",
            name="recurring_one_open_owner",
        ),
        migrations.AddConstraint(
            model_name="responsibilityowner",
            constraint=models.UniqueConstraint(
                condition=models.Q(("effective_to__isnull", True))
                & models.Q(("superseded_at__isnull", True)),
                fields=("responsibility",),
                name="recurring_one_open_owner",
            ),
        ),
    ]
