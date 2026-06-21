from django.db import migrations


DEMO_SKUS = [
    "OIL-MUST-FORT-1L",
    "OIL-MUST-FORT-5L",
    "OIL-MUST-DHARA-1L",
    "OIL-SOY-FRESH-1L",
    "PERF-FOGG-120ML",
    "PERF-ENG-150ML",
]

DEMO_NODES = ["Oil", "Mustard Oil", "Soybean Oil", "Fortune", "Dhara", "Fresh", "Perfume", "Fogg", "Engage"]


def remove_demo_catalog(apps, schema_editor):
    ProductItem = apps.get_model("Dam_stores", "ProductItem")
    ProductNode = apps.get_model("Dam_stores", "ProductNode")
    AuditLog = apps.get_model("Dam_stores", "AuditLog")

    demo_items = list(ProductItem.objects.filter(sku__in=DEMO_SKUS).values_list("id", flat=True))
    ProductItem.objects.filter(id__in=demo_items).delete()
    for name in reversed(DEMO_NODES):
        ProductNode.objects.filter(name=name).delete()
    AuditLog.objects.filter(model_name="ProductItem", object_id__in=[str(item_id) for item_id in demo_items]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("Dam_stores", "0002_seed_roles_demo_catalog"),
    ]

    operations = [
        migrations.RunPython(remove_demo_catalog, migrations.RunPython.noop),
    ]
