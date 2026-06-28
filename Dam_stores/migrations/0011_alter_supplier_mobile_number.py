from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("Dam_stores", "0010_suppliertransaction_note"),
    ]

    operations = [
        migrations.AlterField(
            model_name="supplier",
            name="mobile_number",
            field=models.CharField(blank=True, max_length=20),
        ),
    ]
