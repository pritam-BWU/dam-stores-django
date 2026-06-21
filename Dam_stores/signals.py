from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import AuditLog, InAppNotification


NOTIFICATION_ACTIONS = {
    AuditLog.Action.CREATE,
    AuditLog.Action.UPDATE,
    AuditLog.Action.DELETE,
    AuditLog.Action.PRICE_CHANGE,
    AuditLog.Action.STOCK_CHANGE,
}


@receiver(post_save, sender=AuditLog)
def create_audit_notification(sender, instance: AuditLog, created: bool, **kwargs):
    if not created or instance.action not in NOTIFICATION_ACTIONS:
        return

    user_label = instance.user.name if instance.user and instance.user.name else instance.user.username if instance.user else "System"
    action_label = instance.get_action_display()
    message = f"{user_label} performed {action_label} on {instance.object_label}"
    InAppNotification.objects.get_or_create(
        source_key=f"audit:{instance.pk}",
        defaults={
            "kind": InAppNotification.Kind.AUDIT,
            "message": message[:300],
            "actor": instance.user,
            "link": "/stock/history/",
        },
    )
