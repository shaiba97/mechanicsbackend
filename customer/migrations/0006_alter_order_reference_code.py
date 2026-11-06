from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customer", "0005_backfill_order_reference_code"),
    ]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="reference_code",
            field=models.CharField(blank=True, db_index=True, max_length=32, unique=True),
        ),
    ]
