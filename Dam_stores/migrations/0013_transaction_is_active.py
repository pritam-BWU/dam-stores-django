from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("Dam_stores", "0012_customer_customertransaction_notificationclearance"),
    ]

    operations = [
        migrations.AddField(
            model_name="suppliertransaction",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="customertransaction",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
    ]
