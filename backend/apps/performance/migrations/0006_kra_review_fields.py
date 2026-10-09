"""Phase 7.3 (KRA review): the points a component-scope deduction takes from a component
(approved E3: the component's share of the KPI's points). Additive; existing rows get 0."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("performance", "0005_kra_engine_fields")]

    operations = [
        migrations.AddField(
            model_name="monthlycomponentresult",
            name="deduction_points",
            field=models.DecimalField(decimal_places=6, default=0, max_digits=12),
        ),
    ]
