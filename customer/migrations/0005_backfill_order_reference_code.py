from django.db import migrations


def backfill(apps, schema_editor):
    Order = apps.get_model("customer", "Order")
    for order in Order.objects.filter(reference_code__isnull=True).iterator():
        date_part = order.created_at.strftime("%Y%m%d")
        code = f"ORD-{date_part}-{str(order.pk)[:8].upper()}"
        suffix_len = 8
        while (
            Order.objects.filter(reference_code=code)
            .exclude(pk=order.pk)
            .exists()
        ):
            suffix_len += 2
            code = f"ORD-{date_part}-{str(order.pk)[:suffix_len].upper()}"
        order.reference_code = code
        order.save(update_fields=["reference_code"])


def clear(apps, schema_editor):
    Order = apps.get_model("customer", "Order")
    Order.objects.update(reference_code=None)


class Migration(migrations.Migration):

    dependencies = [
        ("customer", "0004_order_reference_code_order_vehicle"),
    ]

    operations = [
        migrations.RunPython(backfill, clear),
    ]
