"""Phase 7.1: Employee.date_of_joining (optional; existing employees get NULL)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("org", "0003_employeedailylogin_is_valid")]

    operations = [
        migrations.AddField(
            model_name="employee",
            name="date_of_joining",
            field=models.DateField(blank=True, null=True),
        ),
    ]
