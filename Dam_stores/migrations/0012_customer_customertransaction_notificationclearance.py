import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("Dam_stores", "0011_alter_supplier_mobile_number"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Customer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("customer_name", models.CharField(max_length=180)),
                ("mobile_number", models.CharField(blank=True, max_length=20)),
                ("address", models.TextField(blank=True)),
            ],
            options={
                "ordering": ["customer_name"],
            },
        ),
        migrations.CreateModel(
            name="NotificationClearance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("clear_before", models.DateTimeField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="notification_clearance", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="CustomerTransaction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("transaction_date", models.DateField()),
                ("customer_paid_amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("you_got_amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("note", models.TextField(blank=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="created_customer_transactions", to=settings.AUTH_USER_MODEL)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="transactions", to="Dam_stores.customer")),
            ],
            options={
                "ordering": ["-transaction_date", "-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="customer",
            index=models.Index(fields=["customer_name"], name="Dam_stores__custome_cd597f_idx"),
        ),
        migrations.AddIndex(
            model_name="customer",
            index=models.Index(fields=["mobile_number"], name="Dam_stores__mobile__beae84_idx"),
        ),
        migrations.AddConstraint(
            model_name="customer",
            constraint=models.UniqueConstraint(fields=("customer_name", "mobile_number"), name="unique_customer_name_mobile"),
        ),
        migrations.AddIndex(
            model_name="notificationclearance",
            index=models.Index(fields=["user"], name="Dam_stores__user_id_f00831_idx"),
        ),
        migrations.AddIndex(
            model_name="notificationclearance",
            index=models.Index(fields=["clear_before"], name="Dam_stores__clear_b_7de91c_idx"),
        ),
        migrations.AddIndex(
            model_name="customertransaction",
            index=models.Index(fields=["customer"], name="Dam_stores__custome_a11b7f_idx"),
        ),
        migrations.AddIndex(
            model_name="customertransaction",
            index=models.Index(fields=["transaction_date"], name="Dam_stores__transac_50bcf5_idx"),
        ),
    ]
