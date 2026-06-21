from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from datetime import date, datetime
from typing import Any

from django.db import transaction
from django.forms.models import model_to_dict

from .models import AuditLog, ProductItem, ProductPriceHistory, ProductNode, User


class VersionConflictError(Exception):
    pass


@dataclass(frozen=True)
class RequestMeta:
    user: User
    ip_address: str | None = None


def record_audit(
    *,
    meta: RequestMeta,
    action: str,
    instance: Any,
    old_data: dict | None = None,
    new_data: dict | None = None,
) -> AuditLog:
    return AuditLog.objects.create(
        user=meta.user,
        action=action,
        model_name=instance.__class__.__name__,
        object_id=str(instance.pk),
        object_label=str(instance),
        old_data=_json_safe(old_data or {}),
        new_data=_json_safe(new_data or {}),
        ip_address=meta.ip_address,
    )


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    return value


@transaction.atomic
def update_product_node(
    *,
    node_id: int,
    expected_version: int,
    meta: RequestMeta,
    **changes,
) -> ProductNode:
    node = ProductNode.objects.select_for_update().get(pk=node_id)
    if node.version != expected_version:
        raise VersionConflictError("This catalog node was changed by another user.")
    old_data = model_to_dict(node)
    for field, value in changes.items():
        setattr(node, field, value)
    node.updated_by = meta.user
    node.version += 1
    node.save()
    record_audit(meta=meta, action=AuditLog.Action.UPDATE, instance=node, old_data=old_data, new_data=model_to_dict(node))
    return node


@transaction.atomic
def update_product_item_prices(
    *,
    item_id: int,
    expected_version: int,
    buy_rate: Decimal,
    sell_rate: Decimal,
    meta: RequestMeta,
    reason: str = "",
) -> ProductItem:
    item = ProductItem.objects.select_for_update().get(pk=item_id)
    if item.version != expected_version:
        raise VersionConflictError("This item was changed by another user.")

    old_data = model_to_dict(item)
    old_buy_rate = item.buy_rate
    old_sell_rate = item.sell_rate
    item.buy_rate = buy_rate
    item.sell_rate = sell_rate
    item.updated_by = meta.user
    item.version += 1
    item.save()

    ProductPriceHistory.objects.create(
        product_item=item,
        old_buy_rate=old_buy_rate,
        new_buy_rate=buy_rate,
        old_sell_rate=old_sell_rate,
        new_sell_rate=sell_rate,
        reason=reason,
        changed_by=meta.user,
    )
    record_audit(
        meta=meta,
        action=AuditLog.Action.PRICE_CHANGE,
        instance=item,
        old_data=old_data,
        new_data=model_to_dict(item),
    )
    return item


@transaction.atomic
def bulk_create_product_items(*, rows: list[dict], meta: RequestMeta) -> list[ProductItem]:
    created = []
    for row in rows:
        item = ProductItem.objects.create(
            **row,
            created_by=meta.user,
            updated_by=meta.user,
        )
        record_audit(meta=meta, action=AuditLog.Action.CREATE, instance=item, new_data=model_to_dict(item))
        created.append(item)
    return created
