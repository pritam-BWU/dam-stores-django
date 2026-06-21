from __future__ import annotations

from datetime import timedelta

from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone

from .models import InAppNotification, ProductStockEntry


def notifications(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {"app_notifications": [], "app_notification_count": 0}

    try:
        _create_expiry_notifications()
        cutoff = timezone.now() - timedelta(hours=48)
        rows = list(InAppNotification.objects.filter(created_at__gte=cutoff)[:25])
    except (OperationalError, ProgrammingError):
        rows = []

    return {
        "app_notifications": rows,
        "app_notification_count": len(rows),
    }


def _create_expiry_notifications():
    today = timezone.localdate()
    expiry_end = today + timedelta(days=5)
    entries = (
        ProductStockEntry.objects.select_related("product_item")
        .filter(product_item__is_active=True, expiry_date__gte=today, expiry_date__lte=expiry_end)
        .order_by("expiry_date", "product_item__display_name")
    )
    for entry in entries:
        source_key = f"expiry:{entry.pk}:{today.isoformat()}"
        message = f"{entry.product_item.display_name} will expire on {entry.expiry_date:%Y-%m-%d}"
        InAppNotification.objects.get_or_create(
            source_key=source_key,
            defaults={
                "kind": InAppNotification.Kind.EXPIRY,
                "message": message[:300],
                "link": "/stock/",
            },
        )
