from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("Dam_stores", "0009_supplier_damagedproduct_suppliertransaction"),
    ]

    operations = [
        migrations.AddField(
            model_name="suppliertransaction",
            name="note",
            field=models.TextField(blank=True),
        ),
    ]
