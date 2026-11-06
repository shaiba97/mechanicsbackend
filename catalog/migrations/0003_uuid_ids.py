import uuid

import django.db.models.deletion
from django.db import migrations, models


def wipe_catalog_data(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    Car = apps.get_model("catalog", "Car")
    Category = apps.get_model("catalog", "Category")
    SparePart = apps.get_model("catalog", "SparePart")
    Car.objects.all().delete()
    SparePart.objects.all().delete()
    Category.objects.all().delete()
    Brand.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0002_brand_category_car"),
    ]

    operations = [
        migrations.RunPython(wipe_catalog_data, migrations.RunPython.noop),
        migrations.RemoveField("car", "brand"),
        migrations.RemoveField("sparepart", "id"),
        migrations.AddField(
            "sparepart",
            "id",
            models.UUIDField(
                default=uuid.uuid4, editable=False, primary_key=True, serialize=False
            ),
        ),
        migrations.RemoveField("brand", "id"),
        migrations.AddField(
            "brand",
            "id",
            models.UUIDField(
                default=uuid.uuid4, editable=False, primary_key=True, serialize=False
            ),
        ),
        migrations.RemoveField("car", "id"),
        migrations.AddField(
            "car",
            "id",
            models.UUIDField(
                default=uuid.uuid4, editable=False, primary_key=True, serialize=False
            ),
        ),
        migrations.AddField(
            "car",
            "brand",
            models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="cars",
                to="catalog.brand",
            ),
        ),
        migrations.RemoveField("category", "id"),
        migrations.AddField(
            "category",
            "id",
            models.UUIDField(
                default=uuid.uuid4, editable=False, primary_key=True, serialize=False
            ),
        ),
    ]
