from django.contrib.auth.hashers import make_password
from django.db import migrations


def seed(apps, schema_editor):
    Role = apps.get_model("Dam_stores", "Role")
    User = apps.get_model("Dam_stores", "User")
    AuditLog = apps.get_model("Dam_stores", "AuditLog")

    role_data = [
        ("ADMIN", "Admin", "Full access to user, catalog, stock, pricing and audit operations."),
        ("USER", "User", "Basic authenticated user access."),
        ("MANAGER", "Manager", "Can manage catalog, pricing and operational workflows."),
        ("STAFF", "Staff", "Can perform day-to-day product and stock work."),
    ]
    roles = {}
    for code, name, description in role_data:
        role, _ = Role.objects.update_or_create(
            code=code,
            defaults={"name": name, "description": description, "is_active": True},
        )
        roles[code] = role

    admin, created = User.objects.update_or_create(
        username="demo_admin",
        defaults={
            "name": "Demo Admin",
            "email": "demo.admin@damstores.local",
            "mobile_number": "9000000000",
            "role": roles["ADMIN"],
            "is_active": True,
            "is_staff": True,
            "is_superuser": True,
            "password": make_password("Demo@12345"),
        },
    )

    AuditLog.objects.get_or_create(
        action="CREATE",
        model_name="User",
        object_id=str(admin.pk),
        object_label="Demo Admin",
        defaults={
            "user": admin,
            "new_data": {"username": "demo_admin", "role": "ADMIN"},
        },
    )


def unseed(apps, schema_editor):
    User = apps.get_model("Dam_stores", "User")
    Role = apps.get_model("Dam_stores", "Role")
    AuditLog = apps.get_model("Dam_stores", "AuditLog")
    AuditLog.objects.filter(object_label="Demo Admin").delete()
    User.objects.filter(username="demo_admin").delete()
    Role.objects.filter(code__in=["ADMIN", "USER", "MANAGER", "STAFF"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("Dam_stores", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
