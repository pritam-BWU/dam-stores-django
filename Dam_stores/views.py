from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.core.paginator import Paginator
from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.forms.models import model_to_dict
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.text import slugify
from django.shortcuts import redirect
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from . import data
from .models import AuditLog, Customer, CustomerTransaction, DailySalesRecord, DamagedProduct, NotificationClearance, ProductItem, ProductNode, ProductPriceHistory, ProductStockEntry, Role, Supplier, SupplierTransaction, User
from .pdf_statement import build_statement_pdf


MASTER_QUANTITY_UNITS = {
    "pcs": "pcs",
    "pc": "pcs",
    "pkt": "pkt",
    "box": "box",
    "bag": "bag",
    "btl": "btl",
    "can": "can",
    "jar": "jar",
    "roll": "roll",
    "pair": "pair",
    "set": "set",
    "doz": "doz",
    "mg": "mg",
    "g": "g",
    "gm": "g",
    "kg": "kg",
    "ml": "ml",
    "l": "L",
    "lt": "L",
    "ltr": "L",
    "L": "L",
    "mm": "mm",
    "cm": "cm",
    "m": "m",
    "ft": "ft",
    "in": "in",
}


NAV_GROUPS = [
    {
        "group": "Overview",
        "items": [{"id": "dashboard", "href": "/", "label": "Dashboard", "icon": "layout-dashboard"}],
    },
    {
        "group": "Inventory",
        "items": [
            {"id": "products", "href": "/products/", "label": "Products", "icon": "package"},
            {"id": "stock", "href": "/stock/", "label": "Current Stock", "icon": "boxes"},
            {"id": "damaged-products", "href": "/damaged-products/", "label": "Damaged Products", "icon": "package-x"},
            {"id": "pricing", "href": "/pricing/", "label": "Price Management", "icon": "tag"},
            {"id": "stock-history", "href": "/stock/history/", "label": "History", "icon": "history"},
        ],
    },
    {
        "group": "Reporting",
        "items": [
            {"id": "sales", "href": "/sales/", "label": "Sales Analytics", "icon": "trending-up"},
            {"id": "all-sales-analytics", "href": "/sales/all/", "label": "All Sales Analytics", "icon": "table-2"},
        ],
    },
    {
        "group": "Finance",
        "items": [
            {"id": "suppliers", "href": "/suppliers/", "label": "Suppliers", "icon": "truck"},
            {"id": "customers", "href": "/customers/", "label": "Customers", "icon": "users-round"},
        ],
    },
    {
        "group": "Admin",
        "items": [{"id": "users", "href": "/users/", "label": "Users", "icon": "users"}],
    },
]


def _base(page_id: str, page_title: str) -> dict:
    return {
        "page_id": page_id,
        "page_title": page_title,
        "nav_groups": NAV_GROUPS,
        "chart_colors": data.CHART_COLORS,
    }


def _client_ip(request) -> str | None:
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user and user.is_active:
            login(request, user)
            AuditLog.objects.create(
                user=user,
                action=AuditLog.Action.LOGIN,
                model_name="User",
                object_id=str(user.pk),
                object_label=user.name or user.username,
                ip_address=_client_ip(request),
                new_data={"username": user.username, "role": user.role.code if user.role else None},
            )
            return redirect(request.GET.get("next") or "dashboard")
        messages.error(request, "Invalid username or password.")
    return render(request, "stock_management/pages/login.html")


def logout_view(request):
    if request.user.is_authenticated:
        AuditLog.objects.create(
            user=request.user,
            action=AuditLog.Action.LOGOUT,
            model_name="User",
            object_id=str(request.user.pk),
            object_label=request.user.name or request.user.username,
            ip_address=_client_ip(request),
        )
    logout(request)
    return redirect("login")


@login_required
def clear_notifications(request):
    if request.method == "POST":
        NotificationClearance.objects.update_or_create(
            user=request.user,
            defaults={"clear_before": timezone.now()},
        )
    return redirect(request.META.get("HTTP_REFERER") or "dashboard")


@login_required
def dashboard(request):
    today = timezone.localdate()
    expiry_end = today + timedelta(days=5)
    items = list(
        ProductItem.objects.select_related("product_node", "product_node__parent")
        .prefetch_related("stock_entries")
        .filter(is_active=True)
        .order_by("display_name", "pack_size", "unit")
    )
    _attach_stock_totals(items)

    expiring_entries = list(
        ProductStockEntry.objects.select_related("product_item")
        .filter(product_item__is_active=True, expiry_date__gte=today, expiry_date__lte=expiry_end)
        .order_by("expiry_date", "product_item__display_name")
    )
    expiring_product_ids = {entry.product_item_id for entry in expiring_entries}
    expiring_products = [
        {
            "name": entry.product_item.display_name,
            "quantity": _quantity_display(entry.quantity),
            "unit": entry.product_item.unit,
            "expiry_date": entry.expiry_date.strftime("%Y-%m-%d"),
        }
        for entry in expiring_entries
    ]

    net_amount = DailySalesRecord.objects.aggregate(total=Sum("net_profit"))["total"] or Decimal("0")
    damaged_total = DamagedProduct.objects.filter(deleted_at__isnull=True).aggregate(total=Sum("damaged_quantity"))["total"] or Decimal("0")
    metrics = {
        "net_amount_label": data.format_bdt(net_amount),
        "total_products_label": data.format_num(ProductItem.objects.filter(is_active=True).count()),
        "total_segments_label": data.format_num(ProductNode.objects.filter(is_active=True, node_type=ProductNode.NodeType.SEGMENT).count()),
        "expiry_count_label": data.format_num(len(expiring_product_ids)),
        "damaged_quantity_label": _quantity_display(damaged_total),
    }

    recent_movements = _dashboard_recent_stock_updates()
    recent_prices = _dashboard_recent_price_changes()
    reorder = _dashboard_reorder_items(items)
    stock_aging = _dashboard_stock_aging()
    inventory_series = _dashboard_inventory_series(today)
    category_distribution = _dashboard_category_distribution(items)
    segment_rows = _dashboard_segment_rows(items)
    inventory_value_trend = _dashboard_inventory_value_trend(today)

    context = {
        **_base("dashboard", "Dashboard"),
        "metrics": metrics,
        "category_distribution": category_distribution,
        "segment_rows": segment_rows,
        "stock_aging": stock_aging,
        "recent_movements": recent_movements,
        "recent_prices": recent_prices,
        "reorder": reorder,
        "expiring_products": expiring_products,
        "inventory_series_json": json.dumps(inventory_series),
        "category_distribution_json": json.dumps(category_distribution),
        "inventory_value_trend_json": json.dumps(inventory_value_trend),
    }
    return render(request, "stock_management/pages/dashboard.html", context)


def _dashboard_inventory_series(today: date) -> list[dict]:
    rows = []
    for offset in range(29, -1, -1):
        day = today - timedelta(days=offset)
        stock_value = Decimal("0")
        for entry in ProductStockEntry.objects.filter(created_at__date=day):
            stock_value += entry.quantity * entry.rate
        sales_value = DailySalesRecord.objects.filter(sales_date=day).aggregate(total=Sum("total_amount"))["total"] or Decimal("0")
        rows.append({"date": day.strftime("%b %d"), "stockIn": float(stock_value), "sales": float(sales_value)})
    return rows


def _dashboard_category_distribution(items: list[ProductItem]) -> list[dict]:
    totals: dict[int, dict] = {}
    for item in items:
        segment = _root_node(item.product_node)
        row = totals.setdefault(segment.id, {"id": segment.id, "name": segment.name, "value": Decimal("0"), "count": 0})
        row["value"] += item.stock_total * item.sell_rate
        row["count"] += 1
    rows = []
    for index, row in enumerate(sorted(totals.values(), key=lambda item: (item["value"], item["count"]), reverse=True)):
        chart_value = row["value"] if row["value"] > 0 else Decimal(row["count"] or 1)
        rows.append(
            {
                "id": row["id"],
                "name": row["name"],
                "value": float(chart_value),
                "stockValue": float(row["value"]),
                "count": row["count"],
                "url": f"/products/?segment={row['id']}",
                "color": data.CHART_COLORS[index % len(data.CHART_COLORS)],
            }
        )
    if rows:
        return rows
    segments = ProductNode.objects.filter(is_active=True, node_type=ProductNode.NodeType.SEGMENT).order_by("sort_order", "name")
    return [
        {
            "id": segment.id,
            "name": segment.name,
            "value": 1,
            "stockValue": 0,
            "count": 0,
            "url": f"/products/?segment={segment.id}",
            "color": data.CHART_COLORS[index % len(data.CHART_COLORS)],
        }
        for index, segment in enumerate(segments)
    ] or [{"id": "", "name": "No segment", "value": 1, "stockValue": 0, "count": 0, "url": "/products/", "color": "#64748b"}]


def _root_node_name(node: ProductNode) -> str:
    return _root_node(node).name


def _root_node(node: ProductNode) -> ProductNode:
    current = node
    while current.parent:
        current = current.parent
    return current


def _dashboard_segment_rows(items: list[ProductItem]) -> list[dict]:
    segment_totals: dict[int, dict] = {}
    for item in items:
        segment = _root_node(item.product_node)
        row = segment_totals.setdefault(segment.id, {"segment": segment, "product_count": 0, "stock_value": Decimal("0")})
        row["product_count"] += 1
        row["stock_value"] += item.stock_total * item.sell_rate
    for segment in ProductNode.objects.filter(is_active=True, node_type=ProductNode.NodeType.SEGMENT).order_by("sort_order", "name"):
        segment_totals.setdefault(segment.id, {"segment": segment, "product_count": 0, "stock_value": Decimal("0")})
    rows = []
    for row in sorted(segment_totals.values(), key=lambda item: item["segment"].name.lower()):
        rows.append(
            {
                "id": row["segment"].id,
                "name": row["segment"].name,
                "product_count": row["product_count"],
                "stock_value_label": data.format_bdt(row["stock_value"]),
                "url": f"/products/?segment={row['segment'].id}",
            }
        )
    return rows


def _dashboard_inventory_value_trend(today: date) -> list[dict]:
    rows = []
    for months_ago in range(11, -1, -1):
        start = _month_start(today, months_ago)
        end = _next_month(start)
        value = Decimal("0")
        for entry in ProductStockEntry.objects.filter(created_at__date__gte=start, created_at__date__lt=end):
            value += entry.quantity * entry.rate
        rows.append({"month": start.strftime("%b"), "amount": float(value)})
    return rows


def _month_start(today: date, months_ago: int) -> date:
    month_index = today.month - months_ago
    year = today.year + ((month_index - 1) // 12)
    month = ((month_index - 1) % 12) + 1
    return date(year, month, 1)


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _dashboard_stock_aging() -> list[dict]:
    today = timezone.localdate()
    buckets = [
        ("0-30 days", 0, 30),
        ("31-60 days", 31, 60),
        ("61-90 days", 61, 90),
        ("91-180 days", 91, 180),
        ("180+ days", 181, 99999),
    ]
    bucket_values = {label: Decimal("0") for label, _, _ in buckets}
    total = Decimal("0")
    for entry in ProductStockEntry.objects.select_related("product_item").filter(product_item__is_active=True):
        value = entry.quantity * entry.rate
        total += value
        age = (today - timezone.localtime(entry.created_at).date()).days
        for label, start, end in buckets:
            if start <= age <= end:
                bucket_values[label] += value
                break
    rows = []
    for label, _, _ in buckets:
        percent = round(float((bucket_values[label] / total) * Decimal("100")), 1) if total else 0
        rows.append({"range": label, "value": percent})
    return rows


def _dashboard_recent_stock_updates() -> list[dict]:
    rows = []
    entries = ProductStockEntry.objects.select_related("product_item", "created_by").filter(product_item__is_active=True)[:6]
    for entry in entries:
        rows.append(
            {
                "product_name": entry.product_item.display_name,
                "variant_name": f"{entry.product_item.pack_size} {entry.product_item.unit}".strip() or entry.product_item.unit,
                "user": entry.created_by.name or entry.created_by.username,
                "time_label": data.rel_time(entry.created_at),
                "quantity": _quantity_display(entry.quantity),
                "rate": entry.rate,
                "type": "Stock In",
                "type_tone": "success",
            }
        )
    return rows


def _dashboard_recent_price_changes() -> list[ProductPriceHistory]:
    rows = list(ProductPriceHistory.objects.select_related("product_item", "changed_by")[:6])
    for change in rows:
        change.change_pct = round(float(((change.new_sell_rate - change.old_sell_rate) / change.old_sell_rate) * 100), 2) if change.old_sell_rate else 0
        change.change_sign = "+" if change.change_pct > 0 else ""
        change.tone = "destructive" if change.change_pct > 0 else "success" if change.change_pct < 0 else "muted"
        change.time_label = data.rel_time(change.changed_at)
    return rows


def _dashboard_reorder_items(items: list[ProductItem]) -> list[ProductItem]:
    rows = [item for item in items if item.stock_total <= item.reorder_level]
    return rows[:6]


@login_required
def products(request):
    if request.method == "POST":
        try:
            _handle_product_post(request)
            return redirect("products")
        except (ValueError, IntegrityError) as exc:
            messages.error(request, str(exc))

    nodes = ProductNode.objects.select_related("parent").filter(is_active=True).order_by("sort_order", "name")
    items = list(
        ProductItem.objects.select_related("product_node", "product_node__parent")
        .prefetch_related("stock_entries")
        .filter(is_active=True)
        .order_by("display_name", "unit")
    )
    _attach_stock_totals(items)
    product_rows = _product_rows(items)
    selected_product = product_rows[0] if product_rows else None
    node_rows = _node_rows(nodes, items)
    root_nodes = [node for node in node_rows if node.parent_id is None]
    selected_segment = None
    selected_segment_id = request.GET.get("segment", "").strip()
    if selected_segment_id:
        selected_segment = next((node for node in root_nodes if str(node.id) == selected_segment_id), None)
    categories = ["All", *[node.name for node in root_nodes]]
    context = {
        **_base("products", "Products"),
        "product_rows": product_rows,
        "selected_product": selected_product,
        "selected_segment": selected_segment,
        "categories": categories,
        "node_rows": node_rows,
        "root_nodes": root_nodes,
        "nodes_json": json.dumps([
            {
                "id": node.id,
                "name": node.name,
                "parent_id": node.parent_id,
                "node_type": node.node_type,
                "path": node.full_path,
                "depth": getattr(node, "depth", 0),
                "version": node.version,
            }
            for node in node_rows
        ]),
        "product_count": data.format_num(len(product_rows)),
        "variant_count": data.format_num(len(items)),
    }
    return render(request, "stock_management/pages/products.html", context)


def _handle_product_post(request):
    action = request.POST.get("action", "create_new_product")
    if action == "create_new_product":
        _create_new_product_from_request(request)
        messages.success(request, "Product added successfully.")
        return
    if action == "add_existing_node":
        _add_existing_node(request)
        messages.success(request, "Category added successfully.")
        return
    if action == "add_existing_product":
        _add_existing_product(request)
        messages.success(request, "Product added under existing segment.")
        return
    if action == "save_existing_segment":
        _save_existing_segment_from_request(request)
        messages.success(request, "Existing product form saved successfully.")
        return
    if action == "update_node":
        _update_node_from_request(request)
        messages.success(request, "Category updated successfully.")
        return
    if action == "update_item":
        _update_item_from_request(request)
        messages.success(request, "Product details updated successfully.")
        return
    if action == "delete_node":
        _delete_node_from_request(request)
        messages.success(request, "Category deleted successfully.")
        return
    if action == "delete_item":
        _delete_item_from_request(request)
        messages.success(request, "Product deleted successfully.")
        return
    if action == "delete_items":
        _delete_items_from_request(request)
        messages.success(request, "Product deleted successfully.")
        return
    raise ValueError("Unsupported product action.")


def _node_rows(nodes, items):
    node_children: dict[int | None, list[ProductNode]] = {}
    item_counts: dict[int, int] = {}
    node_items: dict[int, list[ProductItem]] = {}
    for item in items:
        item_counts[item.product_node_id] = item_counts.get(item.product_node_id, 0) + 1
        node_items.setdefault(item.product_node_id, []).append(item)
    for node in nodes:
        node_children.setdefault(node.parent_id, []).append(node)

    rows = []

    def walk(parent_id=None, depth=0):
        for node in node_children.get(parent_id, []):
            node.depth = depth
            node.item_count = item_counts.get(node.id, 0)
            node.product_items = node_items.get(node.id, [])
            rows.append(node)
            walk(node.id, depth + 1)

    walk()
    return rows


def _product_rows(items):
    grouped: dict[str, dict] = {}
    for item in items:
        path_nodes = []
        node = item.product_node
        while node:
            path_nodes.append(node)
            node = node.parent
        path_nodes = list(reversed(path_nodes))
        category = path_nodes[0].name if path_nodes else "Uncategorized"
        key = f"{item.display_name}::{category}"
        row = grouped.setdefault(
            key,
            {
                "name": item.display_name,
                "brand": item.product_node.name,
                "category": category,
                "variants": [],
                "stock": Decimal("0"),
                "path": " > ".join(node.name for node in path_nodes),
            },
        )
        row["variants"].append(item)
        row["stock"] += item.stock_total
    rows = list(grouped.values())
    for row in rows:
        row["stock_display"] = int(row["stock"]) if row["stock"] == row["stock"].to_integral_value() else row["stock"]
    return rows


def _attach_stock_totals(items):
    for item in items:
        entries = list(item.stock_entries.all())
        entry_total = sum((entry.quantity for entry in entries), Decimal("0"))
        item.stock_total = item.opening_stock + entry_total
        item.stock_total_display = _quantity_display(item.stock_total)
        item.stock_entries_for_display = entries
        item.expiry_dates_display = ", ".join(
            entry.expiry_date.strftime("%Y-%m-%d") for entry in entries if entry.expiry_date
        ) or "-"
        item.stock_status = "Out" if item.stock_total <= 0 else "Low" if item.stock_total <= item.reorder_level else "OK"
        item.stock_status_tone = "destructive" if item.stock_status == "Out" else "warning" if item.stock_status == "Low" else "success"


def _quantity_display(value: Decimal) -> str:
    return str(int(value)) if value == value.to_integral_value() else format(value.normalize(), "f")


def _quantity_unit(value: str) -> str:
    clean_value = str(value or "").strip()
    if not clean_value:
        return "pcs"
    normalized = MASTER_QUANTITY_UNITS.get(clean_value) or MASTER_QUANTITY_UNITS.get(clean_value.lower())
    if not normalized:
        raise ValueError("Select a valid quantity unit.")
    return normalized


def _pack_size_value(value: str) -> str:
    clean_value = str(value or "").strip()
    if not clean_value:
        return ""
    try:
        amount = Decimal(clean_value)
    except Exception as exc:
        raise ValueError("Enter a valid quantity.") from exc
    if amount < 0:
        raise ValueError("Quantity cannot be negative.")
    return format(amount.normalize(), "f")


def _node_for_path(*, user, names: list[tuple[str, str]]) -> ProductNode:
    parent = None
    node = None
    for name, node_type in names:
        clean_name = name.strip()
        if not clean_name:
            continue
        node = ProductNode.objects.filter(parent=parent, name__iexact=clean_name).first()
        if node is None:
            node = ProductNode.objects.create(
                parent=parent,
                name=clean_name,
                node_type=node_type,
                created_by=user,
                updated_by=user,
            )
            AuditLog.objects.create(
                user=user,
                action=AuditLog.Action.CREATE,
                model_name="ProductNode",
                object_id=str(node.pk),
                object_label=node.full_path,
                new_data={"name": node.name, "node_type": node.node_type, "parent": parent.name if parent else None},
            )
        parent = node
    if node is None:
        raise ValueError("Enter at least one category or brand.")
    return node


@transaction.atomic
def _create_new_product_from_request(request) -> list[ProductItem]:
    segment = request.POST.get("segment", "").strip()
    if not segment:
        raise ValueError("Segment is required.")
    product_ids = [item for item in request.POST.getlist("product_client_id") if item]
    if not product_ids:
        product_ids = ["legacy"]

    created_items = []
    for product_id in product_ids:
        category_names = _category_path_from_request(request, product_id)
        path = [(segment, ProductNode.NodeType.SEGMENT)]
        path.extend(
            (name, ProductNode.NodeType.CATEGORY if index == 0 else ProductNode.NodeType.SUBCATEGORY)
            for index, name in enumerate(category_names)
        )
        leaf = _node_for_path(user=request.user, names=path)
        created_items.extend(_create_product_variants_from_request(request=request, leaf=leaf, product_id=product_id))
    if not created_items:
        raise ValueError("Add at least one product.")
    return created_items


@transaction.atomic
def _add_existing_product(request) -> list[ProductItem]:
    parent_id = request.POST.get("parent_node_id")
    if not parent_id:
        raise ValueError("Select where to add the product.")
    leaf = ProductNode.objects.select_for_update().get(pk=parent_id)
    product_ids = [item for item in request.POST.getlist("product_client_id") if item] or ["existing"]
    created_items = []
    for product_id in product_ids:
        created_items.extend(_create_product_variants_from_request(request=request, leaf=leaf, product_id=product_id))
    if not created_items:
        raise ValueError("Add at least one product.")
    return created_items


@transaction.atomic
def _save_existing_segment_from_request(request) -> list[ProductItem]:
    segment_id = request.POST.get("segment_id")
    if not segment_id:
        raise ValueError("Select an existing segment.")
    segment = ProductNode.objects.select_for_update().get(pk=segment_id)
    product_ids = [item for item in request.POST.getlist("product_client_id") if item]
    if not product_ids:
        raise ValueError("Add at least one product.")

    saved_items = []
    submitted_item_ids = set()
    for product_id in product_ids:
        path_names = _category_path_from_request(request, product_id)
        names = [(segment.name, ProductNode.NodeType.SEGMENT)]
        names.extend(
            (name, ProductNode.NodeType.CATEGORY if index == 0 else ProductNode.NodeType.SUBCATEGORY)
            for index, name in enumerate(path_names)
        )
        leaf = _node_for_path(user=request.user, names=names)
        saved_items.extend(_save_product_variants_from_request(request=request, leaf=leaf, product_id=product_id, submitted_item_ids=submitted_item_ids))

    segment_item_ids = set(
        ProductItem.objects.filter(product_node__in=_descendant_nodes(segment), is_active=True).values_list("id", flat=True)
    )
    for item in ProductItem.objects.select_for_update().filter(pk__in=segment_item_ids - submitted_item_ids, is_active=True):
        old_data = model_to_dict(item)
        item.is_active = False
        item.updated_by = request.user
        item.version += 1
        item.save()
        AuditLog.objects.create(
            user=request.user,
            action=AuditLog.Action.DELETE,
            model_name="ProductItem",
            object_id=str(item.pk),
            object_label=str(item),
            old_data=_audit_safe(old_data),
            new_data=_audit_safe(model_to_dict(item)),
        )
    return saved_items


def _descendant_nodes(root: ProductNode) -> list[ProductNode]:
    nodes = [root]
    index = 0
    while index < len(nodes):
        nodes.extend(nodes[index].children.filter(is_active=True))
        index += 1
    return nodes


def _save_product_variants_from_request(*, request, leaf: ProductNode, product_id: str, submitted_item_ids: set[int]) -> list[ProductItem]:
    suffix = f"_{product_id}"
    product_name = request.POST.get(f"product_name{suffix}", "").strip()
    if not product_name:
        raise ValueError("Product name is required.")
    base_sku = request.POST.get(f"sku{suffix}", "").strip()

    item_ids = request.POST.getlist(f"variant_item_id{suffix}")
    versions = request.POST.getlist(f"variant_version{suffix}")
    sku_values = request.POST.getlist(f"variant_sku{suffix}")
    pack_size_values = request.POST.getlist(f"variant_pack_size{suffix}")
    unit_values = request.POST.getlist(f"variant_unit{suffix}")
    buy_values = request.POST.getlist(f"variant_buy_rate{suffix}")
    sell_values = request.POST.getlist(f"variant_sell_rate{suffix}")
    mrp_values = request.POST.getlist(f"variant_mrp{suffix}")

    max_rows = max(len(pack_size_values), len(unit_values), len(buy_values), len(sell_values), len(mrp_values), len(item_ids))
    saved_items = []
    for index in range(max_rows):
        item_id = item_ids[index].strip() if index < len(item_ids) else ""
        expected_version = int(versions[index] or 0) if index < len(versions) and versions[index] else 0
        pack_size = _pack_size_value(pack_size_values[index] if index < len(pack_size_values) else "")
        unit = _quantity_unit(unit_values[index] if index < len(unit_values) else "")
        buy_rate = _decimal_from_value(buy_values[index] if index < len(buy_values) else "", "Buy Rate", required=True)
        sell_rate = _decimal_from_value(sell_values[index] if index < len(sell_values) else "", "Sell Rate", required=True)
        mrp = _decimal_from_value(mrp_values[index] if index < len(mrp_values) else "", "MRP")
        sku_unit = f"{pack_size} {unit}".strip()
        row_sku = (sku_values[index].strip() if index < len(sku_values) else "") or base_sku

        if item_id:
            item = ProductItem.objects.select_for_update().get(pk=item_id)
            if item.version != expected_version:
                raise ValueError("This product was changed by another user. Reload and try again.")
            old_data = model_to_dict(item)
            old_buy_rate = item.buy_rate
            old_sell_rate = item.sell_rate
            item.product_node = leaf
            item.display_name = product_name
            if base_sku and max_rows == 1:
                item.sku = base_sku.upper()
            elif row_sku:
                item.sku = row_sku.upper()
            else:
                item.sku = _generate_sku(product_name=product_name, unit=sku_unit, leaf=leaf, exclude_item_id=item.id)
            if ProductItem.objects.exclude(pk=item.pk).filter(sku__iexact=item.sku).exists():
                item.sku = _unique_sku(item.sku, exclude_item_id=item.id)
            item.pack_size = pack_size
            item.unit = unit
            item.buy_rate = buy_rate
            item.sell_rate = sell_rate
            item.mrp = mrp
            item.is_active = True
            item.updated_by = request.user
            item.version += 1
            item.save()
            if old_buy_rate != item.buy_rate or old_sell_rate != item.sell_rate:
                ProductPriceHistory.objects.create(
                    product_item=item,
                    old_buy_rate=old_buy_rate,
                    new_buy_rate=item.buy_rate,
                    old_sell_rate=old_sell_rate,
                    new_sell_rate=item.sell_rate,
                    reason="Existing product form edit",
                    changed_by=request.user,
                )
            AuditLog.objects.create(
                user=request.user,
                action=AuditLog.Action.UPDATE,
                model_name="ProductItem",
                object_id=str(item.pk),
                object_label=str(item),
                old_data=_audit_safe(old_data),
                new_data=_audit_safe(model_to_dict(item)),
            )
        else:
            sku = _variant_sku(base_sku=base_sku, product_name=product_name, unit=sku_unit, leaf=leaf, index=index, row_count=max_rows)
            item = _create_product_item(
                request=request,
                leaf=leaf,
                product_name=product_name,
                sku=sku,
                buy_rate=buy_rate,
                sell_rate=sell_rate,
                pack_size=pack_size,
                unit=unit,
                mrp=mrp,
            )
        submitted_item_ids.add(item.id)
        saved_items.append(item)
    return saved_items


def _category_path_from_request(request, product_id: str) -> list[str]:
    if product_id == "legacy":
        return [name.strip() for name in request.POST.getlist("category_names") if name.strip()]
    raw_path = request.POST.get(f"product_path_{product_id}", "[]")
    try:
        decoded = json.loads(raw_path)
    except json.JSONDecodeError as exc:
        raise ValueError("Category path is invalid.") from exc
    return [str(name).strip() for name in decoded if str(name).strip()]


def _create_product_variants_from_request(*, request, leaf: ProductNode, product_id: str) -> list[ProductItem]:
    suffix = "" if product_id == "legacy" else f"_{product_id}"
    product_name = request.POST.get(f"product_name{suffix}", "").strip() or request.POST.get("product_name", "").strip()
    if not product_name:
        raise ValueError("Product name is required.")
    base_sku = request.POST.get(f"sku{suffix}", "").strip() or request.POST.get("sku", "").strip()

    pack_size_values = request.POST.getlist(f"variant_pack_size{suffix}") or [request.POST.get("pack_size", "").strip()]
    unit_values = request.POST.getlist(f"variant_unit{suffix}") or [request.POST.get("unit", "").strip()]
    buy_values = request.POST.getlist(f"variant_buy_rate{suffix}") or [request.POST.get("buy_rate", "").strip()]
    sell_values = request.POST.getlist(f"variant_sell_rate{suffix}") or [request.POST.get("sell_rate", "").strip()]
    mrp_values = request.POST.getlist(f"variant_mrp{suffix}") or [request.POST.get("mrp", "").strip()]

    max_rows = max(len(pack_size_values), len(unit_values), len(buy_values), len(sell_values), len(mrp_values))
    created_items = []
    for index in range(max_rows):
        pack_size = _pack_size_value(pack_size_values[index] if index < len(pack_size_values) else "")
        unit = _quantity_unit(unit_values[index] if index < len(unit_values) else "")
        buy_rate = _decimal_from_value(buy_values[index] if index < len(buy_values) else "", "Buy Rate", required=True)
        sell_rate = _decimal_from_value(sell_values[index] if index < len(sell_values) else "", "Sell Rate", required=True)
        mrp = _decimal_from_value(mrp_values[index] if index < len(mrp_values) else "", "MRP")
        sku_unit = f"{pack_size} {unit}".strip()
        sku = _variant_sku(base_sku=base_sku, product_name=product_name, unit=sku_unit, leaf=leaf, index=index, row_count=max_rows)
        created_items.append(
            _create_product_item(
                request=request,
                leaf=leaf,
                product_name=product_name,
                sku=sku,
                buy_rate=buy_rate,
                sell_rate=sell_rate,
                pack_size=pack_size,
                unit=unit,
                mrp=mrp,
            )
        )
    return created_items


def _create_product_item(*, request, leaf, product_name: str, sku: str, buy_rate: Decimal, sell_rate: Decimal, pack_size: str, unit: str, mrp: Decimal) -> ProductItem:
    if ProductItem.objects.filter(sku__iexact=sku).exists():
        sku = _unique_sku(sku)

    item = ProductItem.objects.create(
        product_node=leaf,
        sku=sku,
        barcode="",
        display_name=product_name,
        unit=unit,
        pack_size=pack_size,
        buy_rate=buy_rate,
        sell_rate=sell_rate,
        mrp=mrp,
        tax_percent=Decimal("0"),
        opening_stock=Decimal("0"),
        reorder_level=Decimal("0"),
        created_by=request.user,
        updated_by=request.user,
    )
    AuditLog.objects.create(
        user=request.user,
        action=AuditLog.Action.CREATE,
        model_name="ProductItem",
        object_id=str(item.pk),
        object_label=str(item),
        new_data={"sku": item.sku, "display_name": item.display_name, "pack_size": item.pack_size, "unit": item.unit},
    )
    return item


@transaction.atomic
def _add_existing_node(request) -> ProductNode:
    parent_id = request.POST.get("parent_node_id")
    name = request.POST.get("node_name", "").strip()
    if not parent_id or not name:
        raise ValueError("Select a parent and enter a category name.")
    parent = ProductNode.objects.select_for_update().get(pk=parent_id)
    node_type = ProductNode.NodeType.CATEGORY if parent.parent_id is None else ProductNode.NodeType.SUBCATEGORY
    existing = ProductNode.objects.filter(parent=parent, name__iexact=name).first()
    if existing:
        raise ValueError("This category already exists under the selected path.")
    node = ProductNode.objects.create(
        parent=parent,
        name=name,
        node_type=node_type,
        created_by=request.user,
        updated_by=request.user,
    )
    AuditLog.objects.create(
        user=request.user,
        action=AuditLog.Action.CREATE,
        model_name="ProductNode",
        object_id=str(node.pk),
        object_label=node.full_path,
        new_data={"name": node.name, "node_type": node.node_type, "parent": parent.full_path},
    )
    return node


@transaction.atomic
def _update_node_from_request(request) -> ProductNode:
    node_id = request.POST.get("node_id")
    expected_version = int(request.POST.get("version") or 0)
    name = request.POST.get("node_name", "").strip()
    if not node_id or not name:
        raise ValueError("Select a category and enter a name.")
    node = ProductNode.objects.select_for_update().get(pk=node_id)
    if node.version != expected_version:
        raise ValueError("This category was changed by another user. Reload and try again.")
    old_data = model_to_dict(node)
    node.name = name
    node.updated_by = request.user
    node.version += 1
    node.save()
    AuditLog.objects.create(
        user=request.user,
        action=AuditLog.Action.UPDATE,
        model_name="ProductNode",
        object_id=str(node.pk),
        object_label=node.full_path,
        old_data=_audit_safe(old_data),
        new_data=_audit_safe(model_to_dict(node)),
    )
    return node


@transaction.atomic
def _delete_node_from_request(request) -> ProductNode:
    node_id = request.POST.get("node_id")
    expected_version = int(request.POST.get("version") or 0)
    if not node_id:
        raise ValueError("Select a category to delete.")
    node = ProductNode.objects.select_for_update().get(pk=node_id)
    if node.version != expected_version:
        raise ValueError("This category was changed by another user. Reload and try again.")
    if node.children.filter(is_active=True).exists():
        raise ValueError("Delete child categories before deleting this category.")
    if node.items.filter(is_active=True).exists():
        raise ValueError("Delete products under this category before deleting it.")
    old_data = model_to_dict(node)
    node.is_active = False
    node.updated_by = request.user
    node.version += 1
    node.save()
    AuditLog.objects.create(
        user=request.user,
        action=AuditLog.Action.DELETE,
        model_name="ProductNode",
        object_id=str(node.pk),
        object_label=node.full_path,
        old_data=_audit_safe(old_data),
        new_data=_audit_safe(model_to_dict(node)),
    )
    return node


@transaction.atomic
def _update_item_from_request(request) -> ProductItem:
    item_id = request.POST.get("item_id")
    expected_version = int(request.POST.get("version") or 0)
    if not item_id:
        raise ValueError("Select a product to update.")
    item = ProductItem.objects.select_for_update().get(pk=item_id)
    if item.version != expected_version:
        raise ValueError("This product was changed by another user. Reload and try again.")
    product_name = request.POST.get("product_name", "").strip()
    if not product_name:
        raise ValueError("Product name is required.")
    old_data = model_to_dict(item)
    old_buy_rate = item.buy_rate
    old_sell_rate = item.sell_rate

    item.display_name = product_name
    pack_size = _pack_size_value(request.POST.get("pack_size", ""))
    unit = _quantity_unit(request.POST.get("unit", ""))
    sku_unit = f"{pack_size} {unit}".strip()
    item.sku = request.POST.get("sku", "").strip() or _generate_sku(product_name=product_name, unit=sku_unit, leaf=item.product_node, exclude_item_id=item.id)
    if ProductItem.objects.exclude(pk=item.pk).filter(sku__iexact=item.sku).exists():
        raise ValueError("A product with this SKU already exists.")
    item.pack_size = pack_size
    item.unit = unit
    item.buy_rate = _decimal_from_post(request, "buy_rate", required=True)
    item.sell_rate = _decimal_from_post(request, "sell_rate", required=True)
    item.mrp = _decimal_from_post(request, "mrp")
    item.updated_by = request.user
    item.version += 1
    item.save()

    if old_buy_rate != item.buy_rate or old_sell_rate != item.sell_rate:
        ProductPriceHistory.objects.create(
            product_item=item,
            old_buy_rate=old_buy_rate,
            new_buy_rate=item.buy_rate,
            old_sell_rate=old_sell_rate,
            new_sell_rate=item.sell_rate,
            reason="Product detail edit",
            changed_by=request.user,
        )
    AuditLog.objects.create(
        user=request.user,
        action=AuditLog.Action.UPDATE,
        model_name="ProductItem",
        object_id=str(item.pk),
        object_label=str(item),
        old_data=_audit_safe(old_data),
        new_data=_audit_safe(model_to_dict(item)),
    )
    return item


@transaction.atomic
def _delete_item_from_request(request) -> ProductItem:
    item_id = request.POST.get("item_id")
    expected_version = int(request.POST.get("version") or 0)
    if not item_id:
        raise ValueError("Select a product to delete.")
    item = ProductItem.objects.select_for_update().get(pk=item_id)
    if item.version != expected_version:
        raise ValueError("This product was changed by another user. Reload and try again.")
    old_data = model_to_dict(item)
    item.is_active = False
    item.updated_by = request.user
    item.version += 1
    item.save()
    AuditLog.objects.create(
        user=request.user,
        action=AuditLog.Action.DELETE,
        model_name="ProductItem",
        object_id=str(item.pk),
        object_label=str(item),
        old_data=_audit_safe(old_data),
        new_data=_audit_safe(model_to_dict(item)),
    )
    return item


@transaction.atomic
def _delete_items_from_request(request) -> list[ProductItem]:
    item_ids = [item_id for item_id in request.POST.getlist("item_ids") if item_id]
    versions = [int(version or 0) for version in request.POST.getlist("versions")]
    if not item_ids:
        raise ValueError("Select a product to delete.")
    expected_versions = dict(zip(item_ids, versions, strict=False))
    items = list(ProductItem.objects.select_for_update().filter(pk__in=item_ids, is_active=True))
    if len(items) != len(item_ids):
        raise ValueError("One or more products were already deleted. Reload and try again.")
    deleted_items = []
    for item in items:
        expected_version = expected_versions.get(str(item.pk), 0)
        if item.version != expected_version:
            raise ValueError("This product was changed by another user. Reload and try again.")
        old_data = model_to_dict(item)
        item.is_active = False
        item.updated_by = request.user
        item.version += 1
        item.save()
        AuditLog.objects.create(
            user=request.user,
            action=AuditLog.Action.DELETE,
            model_name="ProductItem",
            object_id=str(item.pk),
            object_label=str(item),
            old_data=_audit_safe(old_data),
            new_data=_audit_safe(model_to_dict(item)),
        )
        deleted_items.append(item)
    return deleted_items


def _decimal_from_post(request, field: str, required: bool = False) -> Decimal:
    return _decimal_from_value(request.POST.get(field, ""), field.replace("_", " ").title(), required=required)


def _decimal_from_value(raw_value, label: str, required: bool = False) -> Decimal:
    raw_value = str(raw_value or "").strip()
    if not raw_value:
        if required:
            raise ValueError(f"{label} is required.")
        return Decimal("0")
    try:
        return Decimal(raw_value)
    except Exception as exc:
        raise ValueError(f"{label} must be a valid number.") from exc


def _variant_sku(*, base_sku: str, product_name: str, unit: str, leaf: ProductNode, index: int, row_count: int) -> str:
    if base_sku:
        base = base_sku.strip().upper()
        if row_count > 1:
            unit_slug = "-".join(re.findall(r"[A-Za-z0-9]+", unit.upper())) or f"ROW{index + 1}"
            base = f"{base}-{unit_slug}"
        return _unique_sku(base)
    return _generate_sku(product_name=product_name, unit=unit, leaf=leaf)


def _unique_sku(base: str, exclude_item_id: int | None = None) -> str:
    base = (base or "SKU").strip().upper()[:64].strip("-") or "SKU"
    candidate = base
    counter = 1
    query = ProductItem.objects.all()
    if exclude_item_id:
        query = query.exclude(pk=exclude_item_id)
    while query.filter(sku__iexact=candidate).exists():
        counter += 1
        suffix = f"-{counter:03d}"
        candidate = f"{base[: 80 - len(suffix)]}{suffix}"
    return candidate


def _generate_sku(*, product_name: str, unit: str, leaf: ProductNode, exclude_item_id: int | None = None) -> str:
    parts = []
    node = leaf
    while node:
        parts.append(node.name)
        node = node.parent
    parts = list(reversed(parts))
    tokens = [parts[0] if parts else "", parts[-1] if parts else "", product_name, unit]
    base_parts = []
    for token in tokens:
        words = re.findall(r"[A-Za-z0-9]+", token.upper())
        if not words:
            continue
        if len(words) == 1:
            base_parts.append(words[0][:5])
        else:
            base_parts.append("".join(word[0] for word in words[:3]))
    base = "-".join(base_parts[:4]) or "SKU"
    base = base[:64].strip("-")
    candidate = base
    counter = 1
    query = ProductItem.objects.all()
    if exclude_item_id:
        query = query.exclude(pk=exclude_item_id)
    while query.filter(sku__iexact=candidate).exists():
        counter += 1
        candidate = f"{base}-{counter:03d}"
    return candidate


def _audit_safe(value):
    if isinstance(value, dict):
        return {key: _audit_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_audit_safe(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    return value


def _audit_display(value) -> str:
    if value in ({}, [], None, ""):
        return "-"
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            parts.append(f"{key}: {_audit_display(item)}")
        return ", ".join(parts) if parts else "-"
    if isinstance(value, list | tuple):
        return ", ".join(_audit_display(item) for item in value) if value else "-"
    return str(value)


AUDIT_FIELD_LABELS = {
    "display_name": "Product name",
    "name": "Name",
    "sku": "SKU",
    "barcode": "Barcode",
    "unit": "Unit",
    "pack_size": "Quantity",
    "buy_rate": "Buy rate",
    "sell_rate": "Sell rate",
    "mrp": "MRP",
    "tax_percent": "Tax",
    "opening_stock": "Opening stock",
    "reorder_level": "Reorder level",
    "quantity": "Stock quantity",
    "expiry_date": "Expiry date",
    "rate": "Rate",
    "note": "Note",
    "total_amount": "Total amount",
    "net_profit": "Net profit",
    "sales_date": "Sales date",
    "parent": "Parent",
    "node_type": "Type",
    "is_active": "Status",
}

AUDIT_HIDDEN_FIELDS = {
    "id",
    "version",
    "created_by",
    "updated_by",
    "created_at",
    "updated_at",
    "product_node",
    "last_login",
    "password",
    "groups",
    "user_permissions",
}


def _audit_change_displays(old_data, new_data) -> tuple[str, str]:
    old_dict = old_data if isinstance(old_data, dict) else {}
    new_dict = new_data if isinstance(new_data, dict) else {}
    keys = sorted((set(old_dict) | set(new_dict)) - AUDIT_HIDDEN_FIELDS)
    old_lines = []
    new_lines = []

    for key in keys:
        old_value = old_dict.get(key)
        new_value = new_dict.get(key)
        if old_value == new_value:
            continue
        label = AUDIT_FIELD_LABELS.get(key, key.replace("_", " ").title())
        old_lines.append(f"{label}: {_audit_value_display(old_value)}")
        new_lines.append(f"{label}: {_audit_value_display(new_value)}")

    return "\n".join(old_lines) or "-", "\n".join(new_lines) or "-"


def _audit_change_message(audit: AuditLog) -> str:
    old_data = audit.old_data if isinstance(audit.old_data, dict) else {}
    new_data = audit.new_data if isinstance(audit.new_data, dict) else {}
    changed = _audit_changed_fields(old_data, new_data)
    model_name = audit.model_name

    if audit.action == AuditLog.Action.PRICE_CHANGE:
        return _price_change_message(audit.object_label, old_data, new_data)

    if model_name == "ProductItem":
        return _product_item_message(audit.action, audit.object_label, changed, old_data, new_data)

    if model_name == "ProductNode":
        return _product_node_message(audit.action, audit.object_label, changed, old_data, new_data)

    if model_name == "ProductStockEntry":
        return _stock_message(audit.action, audit.object_label, changed, old_data, new_data)

    if model_name == "DailySalesRecord":
        return _sales_message(audit.action, audit.object_label, changed, old_data, new_data)

    return _generic_change_message(audit.action, audit.object_label, changed, old_data, new_data)


def _audit_changed_fields(old_data: dict, new_data: dict) -> list[tuple[str, object, object]]:
    keys = sorted((set(old_data) | set(new_data)) - AUDIT_HIDDEN_FIELDS)
    return [(key, old_data.get(key), new_data.get(key)) for key in keys if not _audit_values_equal(old_data.get(key), new_data.get(key))]


def _price_change_message(label: str, old_data: dict, new_data: dict) -> str:
    messages = []
    old_sell = old_data.get("sell_rate")
    new_sell = new_data.get("sell_rate")
    if old_sell is not None and new_sell is not None and old_sell != new_sell:
        direction = "Increased" if _decimal_or_none(new_sell) and _decimal_or_none(old_sell) and _decimal_or_none(new_sell) > _decimal_or_none(old_sell) else "Reduced"
        messages.append(f"{direction} sell rate for {label} from Rs {_audit_value_display(old_sell)} to Rs {_audit_value_display(new_sell)}")
    old_buy = old_data.get("buy_rate")
    new_buy = new_data.get("buy_rate")
    if old_buy is not None and new_buy is not None and old_buy != new_buy:
        direction = "Increased" if _decimal_or_none(new_buy) and _decimal_or_none(old_buy) and _decimal_or_none(new_buy) > _decimal_or_none(old_buy) else "Reduced"
        messages.append(f"{direction} buy rate for {label} from Rs {_audit_value_display(old_buy)} to Rs {_audit_value_display(new_buy)}")
    return "\n".join(messages) if messages else f"Changed price for {label}"


def _product_item_message(action: str, label: str, changed: list[tuple[str, object, object]], old_data: dict, new_data: dict) -> str:
    product_name = new_data.get("display_name") or old_data.get("display_name") or label
    if action == AuditLog.Action.CREATE:
        quantity = new_data.get("pack_size")
        unit = new_data.get("unit")
        suffix = f" ({quantity} {unit})" if quantity and unit else f" ({unit})" if unit else ""
        return f"Added new product {product_name}{suffix}"
    if action == AuditLog.Action.DELETE:
        return f"Removed product {product_name}"

    lines = []
    for key, old_value, new_value in changed:
        if key == "is_active":
            lines.append(f"{'Restored' if new_value else 'Removed'} product {product_name}")
        elif key == "sell_rate":
            lines.append(f"{_change_direction(old_value, new_value)} sell rate for {product_name} from Rs {_audit_value_display(old_value)} to Rs {_audit_value_display(new_value)}")
        elif key == "buy_rate":
            lines.append(f"{_change_direction(old_value, new_value)} buy rate for {product_name} from Rs {_audit_value_display(old_value)} to Rs {_audit_value_display(new_value)}")
        else:
            field = AUDIT_FIELD_LABELS.get(key, key.replace("_", " ").title()).lower()
            lines.append(f"Changed {field} for {product_name} from {_audit_value_display(old_value)} to {_audit_value_display(new_value)}")
    return "\n".join(lines) if lines else f"Updated product {product_name}"


def _product_node_message(action: str, label: str, changed: list[tuple[str, object, object]], old_data: dict, new_data: dict) -> str:
    node_name = new_data.get("name") or old_data.get("name") or label
    node_type = str(new_data.get("node_type") or old_data.get("node_type") or "category").lower()
    if action == AuditLog.Action.CREATE:
        return f"Added new {node_type} {node_name}"
    if action == AuditLog.Action.DELETE:
        return f"Removed {node_type} {node_name}"
    lines = []
    for key, old_value, new_value in changed:
        if key == "is_active":
            lines.append(f"{'Restored' if new_value else 'Removed'} {node_type} {node_name}")
        else:
            field = AUDIT_FIELD_LABELS.get(key, key.replace("_", " ").title()).lower()
            lines.append(f"Changed {field} for {node_name} from {_audit_value_display(old_value)} to {_audit_value_display(new_value)}")
    return "\n".join(lines) if lines else f"Updated {node_type} {node_name}"


def _stock_message(action: str, label: str, changed: list[tuple[str, object, object]], old_data: dict, new_data: dict) -> str:
    if action == AuditLog.Action.DELETE:
        return f"Removed stock entry for {label}"
    if action == AuditLog.Action.UPDATE:
        lines = []
        for key, old_value, new_value in changed:
            field = AUDIT_FIELD_LABELS.get(key, key.replace("_", " ").title()).lower()
            prefix = "rate Rs " if key == "rate" else ""
            lines.append(f"Changed stock {field} for {label} from {prefix}{_audit_value_display(old_value)} to {prefix}{_audit_value_display(new_value)}")
        return "\n".join(lines) if lines else f"Updated stock entry for {label}"

    quantity = _audit_value_display(new_data.get("quantity"))
    expiry = new_data.get("expiry_date")
    rate = new_data.get("rate")
    parts = [f"Added stock for {label}", f"quantity {quantity}"]
    if rate not in (None, ""):
        parts.append(f"rate Rs {_audit_value_display(rate)}")
    if expiry:
        parts.append(f"expiry {expiry}")
    return ", ".join(parts)


def _sales_message(action: str, label: str, changed: list[tuple[str, object, object]], old_data: dict, new_data: dict) -> str:
    if action == AuditLog.Action.CREATE:
        return f"Added sales data for {new_data.get('sales_date') or label}: total amount Rs {_audit_value_display(new_data.get('total_amount'))}, net profit Rs {_audit_value_display(new_data.get('net_profit'))}"
    lines = []
    for key, old_value, new_value in changed:
        if key in {"total_amount", "net_profit"}:
            lines.append(f"Changed {AUDIT_FIELD_LABELS[key].lower()} for {label} from Rs {_audit_value_display(old_value)} to Rs {_audit_value_display(new_value)}")
        else:
            field = AUDIT_FIELD_LABELS.get(key, key.replace("_", " ").title()).lower()
            lines.append(f"Changed {field} for {label} from {_audit_value_display(old_value)} to {_audit_value_display(new_value)}")
    return "\n".join(lines) if lines else f"Updated sales data for {label}"


def _generic_change_message(action: str, label: str, changed: list[tuple[str, object, object]], old_data: dict, new_data: dict) -> str:
    if action == AuditLog.Action.CREATE:
        return f"Added {label}"
    if action == AuditLog.Action.DELETE:
        return f"Removed {label}"
    lines = []
    for key, old_value, new_value in changed:
        field = AUDIT_FIELD_LABELS.get(key, key.replace("_", " ").title()).lower()
        lines.append(f"Changed {field} for {label} from {_audit_value_display(old_value)} to {_audit_value_display(new_value)}")
    return "\n".join(lines) if lines else f"Updated {label}"


def _change_direction(old_value, new_value) -> str:
    old_decimal = _decimal_or_none(old_value)
    new_decimal = _decimal_or_none(new_value)
    if old_decimal is not None and new_decimal is not None:
        return "Increased" if new_decimal > old_decimal else "Reduced"
    return "Changed"


def _decimal_or_none(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _audit_value_display(value) -> str:
    if value in (None, ""):
        return "-"
    if isinstance(value, bool):
        return "Active" if value else "Inactive"
    if isinstance(value, dict | list | tuple):
        return _audit_display(value)
    return str(value)


def _audit_values_equal(left, right) -> bool:
    left_decimal = _decimal_or_none(left)
    right_decimal = _decimal_or_none(right)
    if left_decimal is not None and right_decimal is not None:
        return left_decimal == right_decimal
    return left == right


@login_required
def current_stock(request):
    if request.method == "POST":
        try:
            _create_stock_record_from_request(request)
            return redirect("stock")
        except ValueError as exc:
            messages.error(request, str(exc))

    all_items = list(
        ProductItem.objects.select_related("product_node", "product_node__parent")
        .prefetch_related("stock_entries")
        .filter(is_active=True)
        .order_by("display_name", "pack_size", "unit")
    )
    _attach_stock_totals(all_items)
    stock_paginator = Paginator(all_items, 50)
    stock_page = stock_paginator.get_page(request.GET.get("page") or 1)
    stock_entries = ProductStockEntry.objects.select_related("product_item", "created_by").filter(product_item__is_active=True)[:80]
    product_options = []
    for item in all_items:
        quantity_label = f"{item.pack_size} {item.unit}".strip() or item.unit
        product_options.append(
            {
                "id": item.id,
                "name": item.display_name,
                "sku": item.sku,
                "quantity_label": quantity_label,
                "rate": str(item.sell_rate),
                "path": item.product_node.full_path,
            }
        )
    context = {
        **_base("stock", "Current Stock"),
        "items": list(stock_page.object_list),
        "stock_page": stock_page,
        "stock_entries": stock_entries,
        "product_options_json": json.dumps(product_options),
    }
    return render(request, "stock_management/pages/stock/index.html", context)


def _product_item_payload(item: ProductItem) -> dict:
    quantity_label = f"{item.pack_size} {item.unit}".strip() or item.unit
    label = f"{item.display_name} - {quantity_label}".strip()
    return {
        "id": item.id,
        "label": label,
        "name": item.display_name,
        "sku": item.sku,
        "quantity_label": quantity_label,
        "unit": item.unit,
        "rate": str(item.sell_rate),
        "path": item.product_node.full_path,
    }


@login_required
def pricing_product_search(request):
    term = request.GET.get("q", "").strip()
    if not term:
        return JsonResponse({"results": []})

    products = (
        ProductItem.objects.select_related("product_node", "product_node__parent")
        .filter(is_active=True)
        .filter(
            Q(display_name__icontains=term)
            | Q(sku__icontains=term)
            | Q(unit__icontains=term)
            | Q(pack_size__icontains=term)
            | Q(product_node__name__icontains=term)
            | Q(product_node__parent__name__icontains=term)
        )
        .order_by("display_name", "pack_size", "unit")[:12]
    )
    return JsonResponse({"results": [_product_item_payload(item) for item in products]})


@login_required
def damaged_products(request):
    if request.method == "POST":
        action = request.POST.get("action", "create_damaged_product")
        try:
            if action == "create_damaged_product":
                _create_damaged_product_from_request(request)
                messages.success(request, "Damaged product added successfully.")
            elif action == "update_damaged_product":
                _update_damaged_product_from_request(request)
                messages.success(request, "Damaged product updated successfully.")
            elif action == "delete_damaged_product":
                _delete_damaged_product_from_request(request)
                messages.success(request, "Damaged product deleted successfully.")
            else:
                raise ValueError("Unsupported damaged product action.")
            return redirect("damaged-products")
        except ValueError as exc:
            messages.error(request, str(exc))

    query = request.GET.get("q", "").strip()
    date_filter = parse_date(request.GET.get("date", ""))
    rows = DamagedProduct.objects.select_related("product", "created_by").filter(deleted_at__isnull=True)
    if query:
        rows = rows.filter(
            Q(product__display_name__icontains=query)
            | Q(product__sku__icontains=query)
            | Q(product__product_node__name__icontains=query)
            | Q(product__product_node__parent__name__icontains=query)
        )
    if date_filter:
        rows = rows.filter(created_at__date=date_filter)
    paginator = Paginator(rows, 50)
    damage_page = paginator.get_page(request.GET.get("page") or 1)

    product_options = [
        _product_item_payload(item)
        for item in ProductItem.objects.select_related("product_node", "product_node__parent").filter(is_active=True).order_by("display_name", "pack_size", "unit")
    ]
    context = {
        **_base("damaged-products", "Damaged Products"),
        "damage_page": damage_page,
        "query": query,
        "date_filter": date_filter,
        "product_options_json": json.dumps(product_options),
    }
    return render(request, "stock_management/pages/damaged_products.html", context)


def _damaged_product_payload(row: DamagedProduct) -> dict:
    return {
        "id": row.id,
        "product_id": row.product_id,
        "product": _product_item_payload(row.product),
        "damaged_quantity": str(row.damaged_quantity),
        "damaged_quantity_label": _quantity_display(row.damaged_quantity),
        "note": row.note,
        "created_by": row.created_by.name or row.created_by.username,
        "created_at": timezone.localtime(row.created_at).strftime("%Y-%m-%d %H:%M"),
        "updated_at": timezone.localtime(row.updated_at).strftime("%Y-%m-%d %H:%M"),
    }


@transaction.atomic
def _create_damaged_product_from_request(request) -> DamagedProduct:
    product_id = request.POST.get("product_id", "").strip()
    if not product_id:
        raise ValueError("Select a product.")
    product = ProductItem.objects.select_for_update().filter(pk=product_id, is_active=True).first()
    if not product:
        raise ValueError("Selected product does not exist.")
    damaged_quantity = _decimal_from_post(request, "damaged_quantity", required=True)
    if damaged_quantity <= 0:
        raise ValueError("Damaged quantity must be greater than zero.")
    return DamagedProduct.objects.create(
        product=product,
        damaged_quantity=damaged_quantity,
        note=request.POST.get("note", "").strip(),
        created_by=request.user,
    )


@transaction.atomic
def _update_damaged_product_from_request(request) -> DamagedProduct:
    row_id = request.POST.get("damage_id", "").strip()
    if not row_id:
        raise ValueError("Select a damaged product record.")
    row = DamagedProduct.objects.select_for_update().filter(pk=row_id, deleted_at__isnull=True).first()
    if not row:
        raise ValueError("This damaged product record no longer exists.")
    product_id = request.POST.get("product_id", "").strip()
    if not product_id:
        raise ValueError("Select a product.")
    product = ProductItem.objects.filter(pk=product_id, is_active=True).first()
    if not product:
        raise ValueError("Selected product does not exist.")
    damaged_quantity = _decimal_from_post(request, "damaged_quantity", required=True)
    if damaged_quantity <= 0:
        raise ValueError("Damaged quantity must be greater than zero.")
    row.product = product
    row.damaged_quantity = damaged_quantity
    row.note = request.POST.get("note", "").strip()
    row.save()
    return row


@transaction.atomic
def _delete_damaged_product_from_request(request) -> DamagedProduct:
    row_id = request.POST.get("damage_id", "").strip()
    if not row_id:
        raise ValueError("Select a damaged product record.")
    row = DamagedProduct.objects.select_for_update().filter(pk=row_id, deleted_at__isnull=True).first()
    if not row:
        raise ValueError("This damaged product record no longer exists.")
    row.deleted_at = timezone.now()
    row.save(update_fields=["deleted_at", "updated_at"])
    return row


@transaction.atomic
def _create_stock_record_from_request(request) -> ProductStockEntry:
    item_id = request.POST.get("product_item_id")
    quantity = _decimal_from_post(request, "quantity", required=True)
    if quantity <= 0:
        raise ValueError("Stock quantity must be greater than zero.")
    if not item_id:
        raise ValueError("Select a product.")
    item = ProductItem.objects.select_for_update().get(pk=item_id, is_active=True)
    expiry_date = None
    expiry_raw = request.POST.get("expiry_date", "").strip()
    if expiry_raw:
        expiry_date = parse_date(expiry_raw)
        if not expiry_date:
            raise ValueError("Enter a valid expiry date.")
    entry = ProductStockEntry.objects.create(
        product_item=item,
        quantity=quantity,
        expiry_date=expiry_date,
        rate=item.sell_rate,
        note=request.POST.get("note", "").strip(),
        created_by=request.user,
    )
    AuditLog.objects.create(
        user=request.user,
        action=AuditLog.Action.STOCK_CHANGE,
        model_name="ProductStockEntry",
        object_id=str(entry.pk),
        object_label=str(item),
        new_data={
            "product_item": item.id,
            "quantity": str(quantity),
            "expiry_date": expiry_date.isoformat() if expiry_date else None,
            "rate": str(item.sell_rate),
        },
    )
    return entry


@login_required
def history(request):
    audits = AuditLog.objects.select_related("user").all()[:100]
    for audit in audits:
        audit.user_label = audit.user.name if audit.user and audit.user.name else audit.user.username if audit.user else "System"
        audit.old_data_display, audit.new_data_display = _audit_change_displays(audit.old_data, audit.new_data)
        audit.change_message = _audit_change_message(audit)
        audit.detail_script_id = f"audit-detail-{audit.pk}"
        audit.detail_payload = {
            "date": audit.created_at.strftime("%b %d, %Y %I:%M %p"),
            "user": audit.user_label,
            "operation": audit.get_action_display(),
            "object": audit.object_label,
            "model": f"{audit.model_name} #{audit.object_id}",
            "change": audit.change_message,
        }
    context = {
        **_base("stock-history", "History"),
        "audits": audits,
        "types": ["All", "CREATE", "UPDATE", "DELETE", "PRICE_CHANGE", "STOCK_CHANGE", "LOGIN", "LOGOUT"],
    }
    return render(request, "stock_management/pages/stock/history.html", context)


@login_required
def pricing(request):
    if request.method == "POST":
        try:
            item = _update_price_from_request(request)
            return redirect(f"/pricing/?item={item.id}")
        except ValueError as exc:
            messages.error(request, str(exc))

    product_qs = ProductItem.objects.select_related("product_node", "product_node__parent").filter(is_active=True)
    selected_id = request.GET.get("item")
    has_selected_item_query = bool(selected_id)
    selected = product_qs.filter(pk=selected_id).first() if selected_id else None
    selected = selected or product_qs.order_by("display_name", "pack_size", "unit").first()

    timeline_product = None
    timeline_item_id = request.GET.get("timeline_item")
    timeline_search = request.GET.get("timeline_q", "").strip()
    price_changes_qs = ProductPriceHistory.objects.select_related("product_item", "product_item__product_node", "changed_by")
    if timeline_item_id:
        timeline_product = product_qs.filter(pk=timeline_item_id).first()
        if timeline_product:
            price_changes_qs = price_changes_qs.filter(product_item=timeline_product)
    elif timeline_search:
        price_changes_qs = price_changes_qs.filter(
            Q(product_item__display_name__icontains=timeline_search)
            | Q(product_item__sku__icontains=timeline_search)
            | Q(product_item__unit__icontains=timeline_search)
            | Q(product_item__pack_size__icontains=timeline_search)
            | Q(product_item__product_node__name__icontains=timeline_search)
        )

    paginator = Paginator(price_changes_qs, 50)
    price_page = paginator.get_page(request.GET.get("page") or 1)
    price_changes = list(price_page.object_list)

    timeline_query = request.GET.copy()
    timeline_query.pop("page", None)
    timeline_base_query = timeline_query.urlencode()
    timeline_page_prefix = f"?{timeline_base_query}&" if timeline_base_query else "?"

    recent_cutoff = timezone.now() - timedelta(days=30)
    recent_changes = ProductPriceHistory.objects.filter(changed_at__gte=recent_cutoff)
    recent_list = list(recent_changes)
    increases = sum(1 for change in recent_list if change.new_sell_rate > change.old_sell_rate)
    decreases = sum(1 for change in recent_list if change.new_sell_rate < change.old_sell_rate)

    selected_change = Decimal("0")
    price_history = []
    if selected:
        latest_history = selected.price_history.order_by("-changed_at").first()
        previous_sell = latest_history.old_sell_rate if latest_history else selected.sell_rate
        previous_buy = latest_history.old_buy_rate if latest_history else selected.buy_rate
        selected.previous_sell_rate = previous_sell
        selected.previous_buy_rate = previous_buy
        if previous_sell:
            selected_change = ((selected.sell_rate - previous_sell) / previous_sell) * Decimal("100")
        history_rows = list(selected.price_history.order_by("changed_at"))
        if history_rows:
            first = history_rows[0]
            price_history.append({"date": first.changed_at.strftime("%b %d"), "rate": float(first.old_sell_rate)})
            price_history.extend({"date": row.changed_at.strftime("%b %d"), "rate": float(row.new_sell_rate)} for row in history_rows)
        else:
            price_history.append({"date": selected.updated_at.strftime("%b %d"), "rate": float(selected.sell_rate)})

    increase_pct = ExpressionWrapper(
        ((F("new_sell_rate") - F("old_sell_rate")) * Value(Decimal("100.0"))) / F("old_sell_rate"),
        output_field=DecimalField(max_digits=18, decimal_places=6),
    )
    top_price_increases = list(
        ProductPriceHistory.objects.select_related("product_item", "product_item__product_node", "changed_by")
        .filter(old_sell_rate__gt=0, new_sell_rate__gt=F("old_sell_rate"))
        .annotate(increase_pct=increase_pct)
        .order_by("-increase_pct", "-changed_at")[:6]
    )

    for change in [*price_changes, *top_price_increases]:
        change.change_pct = round(float(((change.new_sell_rate - change.old_sell_rate) / change.old_sell_rate) * 100), 2) if change.old_sell_rate else 0
        change.change_sign = "+" if change.change_pct > 0 else ""
        change.tone = "destructive" if change.change_pct > 0 else "success" if change.change_pct < 0 else "muted"
        change.time_label = data.rel_time(change.changed_at)

    context = {
        **_base("pricing", "Price Management"),
        "selected": selected,
        "has_selected_item_query": has_selected_item_query,
        "selected_product_label": _product_item_payload(selected)["label"] if selected else "",
        "price_changes": price_changes,
        "price_page": price_page,
        "timeline_page_prefix": timeline_page_prefix,
        "timeline_search": _product_item_payload(timeline_product)["label"] if timeline_product else timeline_search,
        "timeline_item_id": timeline_product.id if timeline_product else "",
        "top_price_increases": top_price_increases,
        "price_history_json": json.dumps(price_history),
        "changes_total": recent_changes.count(),
        "increases": increases,
        "decreases": decreases,
        "priced_products": product_qs.count(),
        "selected_change": round(selected_change, 1),
    }
    return render(request, "stock_management/pages/pricing.html", context)


@transaction.atomic
def _update_price_from_request(request) -> ProductItem:
    item_id = request.POST.get("item_id")
    expected_version = int(request.POST.get("version") or 0)
    if not item_id:
        raise ValueError("Select a product.")
    item = ProductItem.objects.select_for_update().get(pk=item_id, is_active=True)
    if item.version != expected_version:
        raise ValueError("This product was changed by another user. Reload and try again.")
    old_data = model_to_dict(item)
    old_buy_rate = item.buy_rate
    old_sell_rate = item.sell_rate
    item.buy_rate = _decimal_from_post(request, "buy_rate", required=True)
    item.sell_rate = _decimal_from_post(request, "sell_rate", required=True)
    item.updated_by = request.user
    item.version += 1
    item.save()
    if old_buy_rate != item.buy_rate or old_sell_rate != item.sell_rate:
        ProductPriceHistory.objects.create(
            product_item=item,
            old_buy_rate=old_buy_rate,
            new_buy_rate=item.buy_rate,
            old_sell_rate=old_sell_rate,
            new_sell_rate=item.sell_rate,
            reason=request.POST.get("reason", "").strip() or "Price management update",
            changed_by=request.user,
        )
        AuditLog.objects.create(
            user=request.user,
            action=AuditLog.Action.PRICE_CHANGE,
            model_name="ProductItem",
            object_id=str(item.pk),
            object_label=str(item),
            old_data=_audit_safe(old_data),
            new_data=_audit_safe(model_to_dict(item)),
        )
    return item


@login_required
def sales(request):
    if request.method == "POST":
        try:
            _save_sales_records_from_request(request)
            return redirect("sales")
        except ValueError as exc:
            messages.error(request, str(exc))

    today = date.today()
    year_start = today.replace(month=1, day=1)
    month_start = today.replace(day=1)
    week_start = today - timedelta(days=today.weekday())
    filter_start = parse_date(request.GET.get("start", "")) or month_start
    filter_end = parse_date(request.GET.get("end", "")) or today
    if filter_start > filter_end:
        filter_start, filter_end = filter_end, filter_start

    records = DailySalesRecord.objects.select_related("created_by", "updated_by").all()
    filtered_records = records.filter(sales_date__range=(filter_start, filter_end))

    yearly = _sales_totals(records.filter(sales_date__gte=year_start, sales_date__lte=today))
    monthly = _sales_totals(records.filter(sales_date__gte=month_start, sales_date__lte=today))
    weekly = _sales_totals(records.filter(sales_date__gte=week_start, sales_date__lte=today))
    filtered = _sales_totals(filtered_records)

    daily_chart_rows = []
    chart_start = filter_start
    chart_days = min((filter_end - filter_start).days + 1, 90)
    totals_by_date = {row.sales_date: row for row in filtered_records}
    for offset in range(chart_days):
        day = chart_start + timedelta(days=offset)
        row = totals_by_date.get(day)
        daily_chart_rows.append(
            {
                "date": day.strftime("%b %d"),
                "revenue": float(row.total_amount if row else 0),
                "profit": float(row.net_profit if row else 0),
            }
        )

    monthly_chart = []
    for month in range(1, 13):
        month_records = records.filter(sales_date__year=today.year, sales_date__month=month)
        totals = _sales_totals(month_records)
        monthly_chart.append({"month": date(today.year, month, 1).strftime("%b"), "revenue": float(totals["revenue"]), "profit": float(totals["profit"])})

    context = {
        **_base("sales", "Sales Analytics"),
        "yearly_revenue": data.format_bdt(yearly["revenue"]),
        "monthly_revenue": data.format_bdt(monthly["revenue"]),
        "yearly_profit": data.format_bdt(yearly["profit"]),
        "weekly_profit": data.format_bdt(weekly["profit"]),
        "filtered_revenue": data.format_bdt(filtered["revenue"]),
        "filtered_profit": data.format_bdt(filtered["profit"]),
        "filtered_margin": _margin_label(filtered["revenue"], filtered["profit"]),
        "filtered_gross": data.format_bdt(filtered["gross"]),
        "filter_start": filter_start,
        "filter_end": filter_end,
        "monthly_sales_json": json.dumps(monthly_chart),
        "daily_revenue_json": json.dumps(daily_chart_rows),
    }
    return render(request, "stock_management/pages/sales.html", context)


@login_required
def all_sales_analytics(request):
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "update_sales_record":
                _update_sales_record_from_request(request)
                messages.success(request, "Sales data updated successfully.")
            elif action == "delete_sales_record":
                _delete_sales_record_from_request(request)
                messages.success(request, "Sales data deleted successfully.")
            else:
                raise ValueError("Unsupported sales action.")
            query_string = request.GET.urlencode()
            return redirect(f"/sales/all/?{query_string}" if query_string else "all-sales-analytics")
        except ValueError as exc:
            messages.error(request, str(exc))

    today = date.today()
    filter_start = parse_date(request.GET.get("start", ""))
    filter_end = parse_date(request.GET.get("end", ""))
    single_date = parse_date(request.GET.get("date", ""))
    records = DailySalesRecord.objects.select_related("created_by", "updated_by").all()
    if single_date:
        records = records.filter(sales_date=single_date)
    elif filter_start or filter_end:
        start = filter_start or filter_end
        end = filter_end or filter_start
        if start and end and start > end:
            start, end = end, start
        records = records.filter(sales_date__range=(start, end))
    else:
        filter_start = today.replace(day=1)
        filter_end = today
        records = records.filter(sales_date__range=(filter_start, filter_end))

    sales_record_rows = _sales_record_rows(records[:300])
    context = {
        **_base("all-sales-analytics", "All Sales Analytics"),
        "sales_records": sales_record_rows,
        "filter_start": filter_start,
        "filter_end": filter_end,
        "single_date": single_date,
    }
    return render(request, "stock_management/pages/all_sales_analytics.html", context)


def _sales_totals(queryset) -> dict[str, Decimal]:
    totals = queryset.aggregate(revenue=Sum("total_amount"), profit=Sum("net_profit"))
    revenue = totals["revenue"] or Decimal("0")
    profit = totals["profit"] or Decimal("0")
    return {"revenue": revenue, "profit": profit, "gross": revenue - profit}


def _margin_label(revenue: Decimal, profit: Decimal) -> str:
    if not revenue:
        return "0.0%"
    return f"{(profit / revenue) * Decimal('100'):.1f}%"


@transaction.atomic
def _save_sales_records_from_request(request) -> list[DailySalesRecord]:
    dates = request.POST.getlist("sales_date")
    total_values = request.POST.getlist("total_amount")
    profit_values = request.POST.getlist("net_profit")
    notes = request.POST.getlist("note")
    if not dates:
        raise ValueError("Add at least one sales data row.")

    rows = []
    seen_dates = set()
    for index, raw_date in enumerate(dates):
        sales_date = parse_date(raw_date or "")
        if not sales_date:
            raise ValueError("Select a valid sales date.")
        if sales_date in seen_dates:
            raise ValueError(f"Sales data is already existed on {sales_date}.")
        seen_dates.add(sales_date)
        total_amount = _decimal_from_value(total_values[index] if index < len(total_values) else "", "Total Amount", required=True)
        net_profit = _decimal_from_value(profit_values[index] if index < len(profit_values) else "", "Net Profit", required=True)
        if total_amount < 0 or net_profit < 0:
            raise ValueError("Sales amount and net profit cannot be negative.")
        rows.append(
            {
                "sales_date": sales_date,
                "total_amount": total_amount,
                "net_profit": net_profit,
                "note": notes[index].strip() if index < len(notes) else "",
            }
        )

    existing_dates = set(DailySalesRecord.objects.filter(sales_date__in=seen_dates).values_list("sales_date", flat=True))
    if existing_dates:
        existing_label = min(existing_dates).isoformat()
        raise ValueError(f"Sales data is already existed on {existing_label}.")

    records = []
    for row in rows:
        record = DailySalesRecord.objects.create(
            sales_date=row["sales_date"],
            total_amount=row["total_amount"],
            net_profit=row["net_profit"],
            note=row["note"],
            created_by=request.user,
            updated_by=request.user,
        )
        AuditLog.objects.create(
            user=request.user,
            action=AuditLog.Action.CREATE,
            model_name="DailySalesRecord",
            object_id=str(record.pk),
            object_label=str(record),
            old_data={},
            new_data=_audit_safe(model_to_dict(record)),
        )
        records.append(record)
    return records


def _sales_record_rows(records) -> list[DailySalesRecord]:
    rows = list(records)
    for row in rows:
        row.gross_amount = row.total_amount - row.net_profit
        row.gross_label = data.format_bdt(row.gross_amount)
        row.total_label = data.format_bdt(row.total_amount)
        row.profit_label = data.format_bdt(row.net_profit)
        row.margin_label = _margin_label(row.total_amount, row.net_profit)
    return rows


@transaction.atomic
def _update_sales_record_from_request(request) -> DailySalesRecord:
    record_id = request.POST.get("record_id")
    expected_version = int(request.POST.get("version") or 0)
    if not record_id:
        raise ValueError("Select a sales record to edit.")
    record = DailySalesRecord.objects.select_for_update().filter(pk=record_id).first()
    if not record:
        raise ValueError("This sales data no longer exists.")
    if record.version != expected_version:
        raise ValueError("This sales data was changed by another user. Reload and try again.")
    sales_date = parse_date(request.POST.get("sales_date", ""))
    if not sales_date:
        raise ValueError("Select a valid sales date.")
    duplicate = DailySalesRecord.objects.exclude(pk=record.pk).filter(sales_date=sales_date).exists()
    if duplicate:
        raise ValueError(f"Sales data is already existed on {sales_date}.")
    total_amount = _decimal_from_post(request, "total_amount", required=True)
    net_profit = _decimal_from_post(request, "net_profit", required=True)
    if total_amount < 0 or net_profit < 0:
        raise ValueError("Sales amount and net profit cannot be negative.")
    old_data = model_to_dict(record)
    record.sales_date = sales_date
    record.total_amount = total_amount
    record.net_profit = net_profit
    record.note = request.POST.get("note", "").strip()
    record.updated_by = request.user
    record.version += 1
    record.save()
    AuditLog.objects.create(
        user=request.user,
        action=AuditLog.Action.UPDATE,
        model_name="DailySalesRecord",
        object_id=str(record.pk),
        object_label=str(record),
        old_data=_audit_safe(old_data),
        new_data=_audit_safe(model_to_dict(record)),
    )
    return record


@transaction.atomic
def _delete_sales_record_from_request(request) -> DailySalesRecord:
    record_id = request.POST.get("record_id")
    expected_version = int(request.POST.get("version") or 0)
    if not record_id:
        raise ValueError("Select a sales record to delete.")
    record = DailySalesRecord.objects.select_for_update().filter(pk=record_id).first()
    if not record:
        raise ValueError("This sales data no longer exists.")
    if record.version != expected_version:
        raise ValueError("This sales data was changed by another user. Reload and try again.")
    old_data = model_to_dict(record)
    label = str(record)
    pk = record.pk
    record.delete()
    AuditLog.objects.create(
        user=request.user,
        action=AuditLog.Action.DELETE,
        model_name="DailySalesRecord",
        object_id=str(pk),
        object_label=label,
        old_data=_audit_safe(old_data),
        new_data={},
    )
    return record


@login_required
def suppliers(request):
    if request.method == "POST":
        action = request.POST.get("action", "create_supplier")
        try:
            if action == "create_supplier":
                _create_supplier_from_request(request)
                messages.success(request, "Supplier added successfully.")
            elif action == "create_supplier_transaction":
                _create_supplier_transaction_from_request(request)
                messages.success(request, "Supplier data added successfully.")
            else:
                raise ValueError("Unsupported supplier action.")
            return redirect("suppliers")
        except (ValueError, IntegrityError) as exc:
            messages.error(request, str(exc))

    query = request.GET.get("q", "").strip()
    rows = Supplier.objects.annotate(
        total_outstanding=Sum("transactions__outstanding_amount", filter=Q(transactions__is_active=True)),
        total_paid=Sum("transactions__paid_amount", filter=Q(transactions__is_active=True)),
    ).order_by("supplier_name")
    if query:
        rows = rows.filter(Q(supplier_name__icontains=query) | Q(mobile_number__icontains=query))
    paginator = Paginator(rows, 50)
    supplier_page = paginator.get_page(request.GET.get("page") or 1)
    for supplier in supplier_page.object_list:
        remaining = (supplier.total_outstanding or Decimal("0")) - (supplier.total_paid or Decimal("0"))
        supplier.total_outstanding_label = _format_supplier_amount(remaining)

    supplier_options = [_supplier_payload(supplier) for supplier in Supplier.objects.order_by("supplier_name")]
    context = {
        **_base("suppliers", "Suppliers"),
        "supplier_page": supplier_page,
        "query": query,
        "supplier_options_json": json.dumps(supplier_options),
        "today": timezone.localdate(),
    }
    return render(request, "stock_management/pages/suppliers.html", context)


@login_required
def supplier_detail(request, supplier_id: int):
    supplier = Supplier.objects.filter(pk=supplier_id).first()
    if not supplier:
        messages.error(request, "Supplier not found.")
        return redirect("suppliers")

    if request.method == "POST":
        try:
            action = request.POST.get("action", "")
            if action == "update_supplier_transaction":
                _update_supplier_transaction_from_request(request, supplier)
                messages.success(request, "Supplier entry updated successfully.")
            elif action == "delete_supplier_transaction":
                _delete_supplier_transaction_from_request(request, supplier)
                messages.success(request, "Supplier entry deleted successfully.")
            else:
                raise ValueError("Unsupported supplier action.")
            redirect_url = request.path
            if request.GET.urlencode():
                redirect_url = f"{redirect_url}?{request.GET.urlencode()}"
            return redirect(redirect_url)
        except (ValueError, IntegrityError) as exc:
            messages.error(request, str(exc))

    filter_start = parse_date(request.GET.get("start", ""))
    filter_end = parse_date(request.GET.get("end", ""))
    all_transactions = supplier.transactions.select_related("created_by").filter(is_active=True)
    transactions = all_transactions
    if filter_start and filter_end:
        if filter_start > filter_end:
            filter_start, filter_end = filter_end, filter_start
        transactions = transactions.filter(transaction_date__range=(filter_start, filter_end))
    elif filter_start or filter_end:
        single_date = filter_start or filter_end
        transactions = transactions.filter(transaction_date=single_date)
        filter_start = single_date
        filter_end = single_date

    totals = all_transactions.aggregate(outstanding=Sum("outstanding_amount"), paid=Sum("paid_amount"))
    total_outstanding_value = totals["outstanding"] or Decimal("0")
    total_paid_value = totals["paid"] or Decimal("0")
    remaining_outstanding_value = total_outstanding_value - total_paid_value

    balance_by_id = {}
    running_balance = Decimal("0")
    for row in reversed(list(all_transactions)):
        running_balance += (row.outstanding_amount or Decimal("0")) - (row.paid_amount or Decimal("0"))
        balance_by_id[row.id] = running_balance

    transaction_rows = list(transactions)
    for row in transaction_rows:
        row.balance_after = balance_by_id.get(row.id, Decimal("0"))
        row.balance_after_label = _format_supplier_amount(row.balance_after)
        row.outstanding_label = _format_supplier_amount(row.outstanding_amount or Decimal("0"))
        row.paid_label = _format_supplier_amount(row.paid_amount or Decimal("0"))
        row.entry_type = _supplier_transaction_type(row)
        row.entry_amount = row.paid_amount if row.entry_type == "paid" else row.outstanding_amount
        row.entry_amount_label = _format_supplier_amount(row.entry_amount or Decimal("0"))

    paginator = Paginator(transaction_rows, 50)
    transaction_page = paginator.get_page(request.GET.get("page") or 1)
    transaction_options = [_supplier_transaction_payload(row) for row in transaction_page.object_list]
    context = {
        **_base("suppliers", "Supplier Detail"),
        "supplier": supplier,
        "transaction_page": transaction_page,
        "transaction_options_json": json.dumps(transaction_options),
        "filter_start": filter_start,
        "filter_end": filter_end,
        "total_outstanding": _format_supplier_amount(total_outstanding_value),
        "total_paid": _format_supplier_amount(total_paid_value),
        "remaining_outstanding": _format_supplier_amount(remaining_outstanding_value),
    }
    return render(request, "stock_management/pages/supplier_detail.html", context)


@login_required
def supplier_detail_pdf(request, supplier_id: int):
    supplier = Supplier.objects.filter(pk=supplier_id).first()
    if not supplier:
        messages.error(request, "Supplier not found.")
        return redirect("suppliers")

    all_transactions = supplier.transactions.select_related("created_by").filter(is_active=True)
    transactions, filter_start, filter_end = _filtered_transactions(request, all_transactions)
    transactions = transactions.order_by("transaction_date", "created_at", "id")
    totals = transactions.aggregate(outstanding=Sum("outstanding_amount"), paid=Sum("paid_amount"))
    total_outstanding = totals["outstanding"] or Decimal("0")
    total_paid = totals["paid"] or Decimal("0")
    remaining = total_outstanding - total_paid

    balance_by_id = {}
    running_balance = Decimal("0")
    for row in all_transactions.order_by("transaction_date", "created_at", "id"):
        running_balance += (row.outstanding_amount or Decimal("0")) - (row.paid_amount or Decimal("0"))
        balance_by_id[row.id] = running_balance

    report_rows = []
    for row in transactions:
        report_rows.append(
            [
                f"{row.transaction_date.strftime('%d %b %Y')} {timezone.localtime(row.created_at).strftime('%I:%M %p')}",
                _format_supplier_amount(balance_by_id.get(row.id, Decimal("0"))),
                _format_supplier_amount(row.outstanding_amount or Decimal("0")) if row.outstanding_amount else "",
                _format_supplier_amount(row.paid_amount or Decimal("0")) if row.paid_amount else "",
            ]
        )

    pdf = build_statement_pdf(
        "Supplier Name",
        supplier.supplier_name,
        "Total Outstanding",
        _format_supplier_amount(remaining),
        ["Date", "Balance", "Bill", "You Paid"],
        report_rows,
        (_format_supplier_amount(total_outstanding), _format_supplier_amount(total_paid)),
        ((0.961, 0.341, 0.251), (0.243, 0.722, 0.475)),
    )
    filename = f"supplier-{slugify(supplier.supplier_name) or supplier.id}-report.pdf"
    return _pdf_response(pdf, filename)


def _supplier_payload(supplier: Supplier) -> dict:
    label = f"{supplier.supplier_name} - {supplier.mobile_number}" if supplier.mobile_number else supplier.supplier_name
    return {
        "id": supplier.id,
        "supplier_name": supplier.supplier_name,
        "mobile_number": supplier.mobile_number,
        "address": supplier.address,
        "label": label,
    }


def _format_supplier_amount(value: Decimal) -> str:
    value = Decimal(value or Decimal("0"))
    formatted = f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"Rs {formatted}"


def _filtered_transactions(request, rows):
    filter_start = parse_date(request.GET.get("start", ""))
    filter_end = parse_date(request.GET.get("end", ""))
    if filter_start and filter_end:
        if filter_start > filter_end:
            filter_start, filter_end = filter_end, filter_start
        rows = rows.filter(transaction_date__range=(filter_start, filter_end))
    elif filter_start or filter_end:
        single_date = filter_start or filter_end
        rows = rows.filter(transaction_date=single_date)
        filter_start = single_date
        filter_end = single_date
    return rows, filter_start, filter_end


def _pdf_response(pdf_bytes: bytes, filename: str) -> HttpResponse:
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _supplier_transaction_type(row: SupplierTransaction) -> str:
    if (row.paid_amount or Decimal("0")) > 0 and (row.outstanding_amount or Decimal("0")) <= 0:
        return "paid"
    return "outstanding"


def _supplier_transaction_amounts(transaction_type: str, amount: Decimal) -> tuple[Decimal, Decimal]:
    transaction_type = (transaction_type or "").strip().lower()
    if transaction_type not in {"outstanding", "paid"}:
        raise ValueError("Select Outstanding or You Paid.")
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")
    if transaction_type == "outstanding":
        return amount, Decimal("0")
    return Decimal("0"), amount


@transaction.atomic
def _create_supplier_from_request(request) -> Supplier:
    supplier_name = request.POST.get("supplier_name", "").strip()
    mobile_number = request.POST.get("mobile_number", "").strip()
    if not supplier_name:
        raise ValueError("Supplier name is required.")
    if Supplier.objects.filter(supplier_name__iexact=supplier_name, mobile_number__iexact=mobile_number).exists():
        raise ValueError("This supplier already exists.")
    return Supplier.objects.create(
        supplier_name=supplier_name,
        mobile_number=mobile_number,
        address=request.POST.get("address", "").strip(),
    )


@transaction.atomic
def _create_supplier_transaction_from_request(request) -> SupplierTransaction:
    supplier_id = request.POST.get("supplier_id", "").strip()
    if not supplier_id:
        raise ValueError("Select a supplier.")
    supplier = Supplier.objects.filter(pk=supplier_id).first()
    if not supplier:
        raise ValueError("Selected supplier does not exist.")
    transaction_date = parse_date(request.POST.get("transaction_date", "")) or timezone.localdate()
    amount = _decimal_from_post(request, "amount", required=True)
    outstanding_amount, paid_amount = _supplier_transaction_amounts(request.POST.get("transaction_type", ""), amount)
    return SupplierTransaction.objects.create(
        supplier=supplier,
        transaction_date=transaction_date,
        outstanding_amount=outstanding_amount,
        paid_amount=paid_amount,
        note=request.POST.get("note", "").strip(),
        created_by=request.user,
    )


@transaction.atomic
def _update_supplier_transaction_from_request(request, supplier: Supplier) -> SupplierTransaction:
    transaction_id = request.POST.get("transaction_id", "").strip()
    row = supplier.transactions.filter(pk=transaction_id, is_active=True).first()
    if not row:
        raise ValueError("Supplier entry not found.")
    transaction_date = parse_date(request.POST.get("transaction_date", "")) or row.transaction_date
    amount = _decimal_from_post(request, "amount", required=True)
    outstanding_amount, paid_amount = _supplier_transaction_amounts(request.POST.get("transaction_type", ""), amount)
    row.transaction_date = transaction_date
    row.outstanding_amount = outstanding_amount
    row.paid_amount = paid_amount
    row.note = request.POST.get("note", "").strip()
    row.save()
    return row


@transaction.atomic
def _delete_supplier_transaction_from_request(request, supplier: Supplier) -> SupplierTransaction:
    transaction_id = request.POST.get("transaction_id", "").strip()
    row = supplier.transactions.filter(pk=transaction_id, is_active=True).first()
    if not row:
        raise ValueError("Supplier entry not found.")
    row.is_active = False
    row.save(update_fields=["is_active", "updated_at"])
    return row


def _request_payload(request) -> dict:
    if request.content_type and "application/json" in request.content_type:
        try:
            return json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON payload.") from exc
    return request.POST.dict()


def _json_decimal(value) -> str:
    return str(value or Decimal("0"))


def _pagination_payload(page) -> dict:
    return {
        "page": page.number,
        "page_size": page.paginator.per_page,
        "total": page.paginator.count,
        "pages": page.paginator.num_pages,
        "has_next": page.has_next(),
        "has_previous": page.has_previous(),
    }


def _json_error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"ok": False, "error": message}, status=status)


@csrf_exempt
@login_required
def api_damaged_products(request, pk: int | None = None):
    try:
        if request.method == "GET":
            if pk:
                row = DamagedProduct.objects.select_related("product", "created_by").filter(pk=pk, deleted_at__isnull=True).first()
                if not row:
                    return _json_error("Damaged product record not found.", 404)
                return JsonResponse({"ok": True, "data": _damaged_product_payload(row)})
            rows = DamagedProduct.objects.select_related("product", "created_by").filter(deleted_at__isnull=True)
            query = request.GET.get("q", "").strip()
            date_filter = parse_date(request.GET.get("date", ""))
            product_id = request.GET.get("product_id", "").strip()
            if query:
                rows = rows.filter(Q(product__display_name__icontains=query) | Q(product__sku__icontains=query))
            if date_filter:
                rows = rows.filter(created_at__date=date_filter)
            if product_id:
                rows = rows.filter(product_id=product_id)
            total_damaged = rows.aggregate(total=Sum("damaged_quantity"))["total"] or Decimal("0")
            page = Paginator(rows, int(request.GET.get("page_size") or 50)).get_page(request.GET.get("page") or 1)
            return JsonResponse(
                {
                    "ok": True,
                    "results": [_damaged_product_payload(row) for row in page.object_list],
                    "pagination": _pagination_payload(page),
                    "totals": {"damaged_quantity": _json_decimal(total_damaged)},
                }
            )
        payload = _request_payload(request)
        if request.method == "POST":
            product_id = str(payload.get("product_id") or "").strip()
            quantity = _decimal_from_value(payload.get("damaged_quantity"), "Damaged Quantity", required=True)
            if not product_id:
                raise ValueError("Product is required.")
            if quantity <= 0:
                raise ValueError("Damaged quantity must be greater than zero.")
            product = ProductItem.objects.filter(pk=product_id, is_active=True).first()
            if not product:
                raise ValueError("Selected product does not exist.")
            row = DamagedProduct.objects.create(product=product, damaged_quantity=quantity, note=str(payload.get("note") or "").strip(), created_by=request.user)
            return JsonResponse({"ok": True, "data": _damaged_product_payload(row)}, status=201)
        if request.method in {"PUT", "PATCH"} and pk:
            row = DamagedProduct.objects.filter(pk=pk, deleted_at__isnull=True).first()
            if not row:
                return _json_error("Damaged product record not found.", 404)
            product_id = str(payload.get("product_id") or row.product_id).strip()
            quantity = _decimal_from_value(payload.get("damaged_quantity", row.damaged_quantity), "Damaged Quantity", required=True)
            product = ProductItem.objects.filter(pk=product_id, is_active=True).first()
            if not product:
                raise ValueError("Selected product does not exist.")
            if quantity <= 0:
                raise ValueError("Damaged quantity must be greater than zero.")
            row.product = product
            row.damaged_quantity = quantity
            row.note = str(payload.get("note", row.note) or "").strip()
            row.save()
            return JsonResponse({"ok": True, "data": _damaged_product_payload(row)})
        if request.method == "DELETE" and pk:
            row = DamagedProduct.objects.filter(pk=pk, deleted_at__isnull=True).first()
            if not row:
                return _json_error("Damaged product record not found.", 404)
            row.deleted_at = timezone.now()
            row.save(update_fields=["deleted_at", "updated_at"])
            return JsonResponse({"ok": True})
        return _json_error("Unsupported method.", 405)
    except ValueError as exc:
        return _json_error(str(exc))


def _supplier_transaction_payload(row: SupplierTransaction) -> dict:
    transaction_type = _supplier_transaction_type(row)
    amount = row.paid_amount if transaction_type == "paid" else row.outstanding_amount
    return {
        "id": row.id,
        "supplier_id": row.supplier_id,
        "supplier_name": row.supplier.supplier_name,
        "transaction_date": row.transaction_date.isoformat(),
        "transaction_type": transaction_type,
        "amount": _json_decimal(amount),
        "outstanding_amount": _json_decimal(row.outstanding_amount),
        "paid_amount": _json_decimal(row.paid_amount),
        "note": row.note,
        "created_by": row.created_by.name or row.created_by.username,
        "created_at": timezone.localtime(row.created_at).strftime("%Y-%m-%d %H:%M"),
    }


@csrf_exempt
@login_required
def api_suppliers(request, pk: int | None = None):
    try:
        if request.method == "GET":
            if pk:
                supplier = Supplier.objects.filter(pk=pk).first()
                if not supplier:
                    return _json_error("Supplier not found.", 404)
                totals = supplier.transactions.filter(is_active=True).aggregate(outstanding=Sum("outstanding_amount"), paid=Sum("paid_amount"))
                payload = _supplier_payload(supplier)
                payload["totals"] = {
                    "outstanding_amount": _json_decimal(totals["outstanding"]),
                    "paid_amount": _json_decimal(totals["paid"]),
                    "remaining_outstanding": _json_decimal((totals["outstanding"] or Decimal("0")) - (totals["paid"] or Decimal("0"))),
                }
                return JsonResponse({"ok": True, "data": payload})
            rows = Supplier.objects.annotate(
                total_outstanding=Sum("transactions__outstanding_amount", filter=Q(transactions__is_active=True)),
                total_paid=Sum("transactions__paid_amount", filter=Q(transactions__is_active=True)),
            )
            query = request.GET.get("q", "").strip()
            if query:
                rows = rows.filter(Q(supplier_name__icontains=query) | Q(mobile_number__icontains=query))
            page = Paginator(rows.order_by("supplier_name"), int(request.GET.get("page_size") or 50)).get_page(request.GET.get("page") or 1)
            results = []
            for supplier in page.object_list:
                row = _supplier_payload(supplier)
                row["total_outstanding"] = _json_decimal(supplier.total_outstanding)
                row["total_paid"] = _json_decimal(supplier.total_paid)
                row["remaining_outstanding"] = _json_decimal((supplier.total_outstanding or Decimal("0")) - (supplier.total_paid or Decimal("0")))
                results.append(row)
            return JsonResponse({"ok": True, "results": results, "pagination": _pagination_payload(page)})
        payload = _request_payload(request)
        if request.method == "POST":
            name = str(payload.get("supplier_name") or "").strip()
            mobile = str(payload.get("mobile_number") or "").strip()
            if not name:
                raise ValueError("Supplier name is required.")
            if Supplier.objects.filter(supplier_name__iexact=name, mobile_number__iexact=mobile).exists():
                raise ValueError("This supplier already exists.")
            supplier = Supplier.objects.create(supplier_name=name, mobile_number=mobile, address=str(payload.get("address") or "").strip())
            return JsonResponse({"ok": True, "data": _supplier_payload(supplier)}, status=201)
        if request.method in {"PUT", "PATCH"} and pk:
            supplier = Supplier.objects.filter(pk=pk).first()
            if not supplier:
                return _json_error("Supplier not found.", 404)
            name = str(payload.get("supplier_name", supplier.supplier_name) or "").strip()
            mobile = str(payload.get("mobile_number", supplier.mobile_number) or "").strip()
            if not name:
                raise ValueError("Supplier name is required.")
            if Supplier.objects.exclude(pk=supplier.pk).filter(supplier_name__iexact=name, mobile_number__iexact=mobile).exists():
                raise ValueError("This supplier already exists.")
            supplier.supplier_name = name
            supplier.mobile_number = mobile
            supplier.address = str(payload.get("address", supplier.address) or "").strip()
            supplier.save()
            return JsonResponse({"ok": True, "data": _supplier_payload(supplier)})
        if request.method == "DELETE" and pk:
            supplier = Supplier.objects.filter(pk=pk).first()
            if not supplier:
                return _json_error("Supplier not found.", 404)
            supplier.delete()
            return JsonResponse({"ok": True})
        return _json_error("Unsupported method.", 405)
    except (ValueError, IntegrityError) as exc:
        return _json_error(str(exc))


@csrf_exempt
@login_required
def api_supplier_transactions(request, pk: int | None = None):
    try:
        if request.method == "GET":
            if pk:
                row = SupplierTransaction.objects.select_related("supplier", "created_by").filter(pk=pk, is_active=True).first()
                if not row:
                    return _json_error("Supplier transaction not found.", 404)
                return JsonResponse({"ok": True, "data": _supplier_transaction_payload(row)})
            rows = SupplierTransaction.objects.select_related("supplier", "created_by").filter(is_active=True)
            supplier_id = request.GET.get("supplier_id", "").strip()
            start = parse_date(request.GET.get("start", ""))
            end = parse_date(request.GET.get("end", ""))
            if supplier_id:
                rows = rows.filter(supplier_id=supplier_id)
            if start and end:
                if start > end:
                    start, end = end, start
                rows = rows.filter(transaction_date__range=(start, end))
            elif start or end:
                rows = rows.filter(transaction_date=start or end)
            totals = rows.aggregate(outstanding=Sum("outstanding_amount"), paid=Sum("paid_amount"))
            page = Paginator(rows, int(request.GET.get("page_size") or 50)).get_page(request.GET.get("page") or 1)
            return JsonResponse(
                {
                    "ok": True,
                    "results": [_supplier_transaction_payload(row) for row in page.object_list],
                    "pagination": _pagination_payload(page),
                    "totals": {
                        "outstanding_amount": _json_decimal(totals["outstanding"]),
                        "paid_amount": _json_decimal(totals["paid"]),
                    },
                }
            )
        payload = _request_payload(request)
        if request.method == "POST":
            supplier_id = str(payload.get("supplier_id") or "").strip()
            supplier = Supplier.objects.filter(pk=supplier_id).first()
            if not supplier:
                raise ValueError("Select a supplier.")
            transaction_date = parse_date(str(payload.get("transaction_date") or "")) or timezone.localdate()
            if payload.get("transaction_type"):
                amount = _decimal_from_value(payload.get("amount"), "Amount", required=True)
                outstanding, paid = _supplier_transaction_amounts(str(payload.get("transaction_type") or ""), amount)
            else:
                outstanding = _decimal_from_value(payload.get("outstanding_amount"), "Outstanding Amount", required=True)
                paid = _decimal_from_value(payload.get("paid_amount"), "Paid Amount", required=True)
                if outstanding < 0 or paid < 0:
                    raise ValueError("Supplier amounts cannot be negative.")
            row = SupplierTransaction.objects.create(
                supplier=supplier,
                transaction_date=transaction_date,
                outstanding_amount=outstanding,
                paid_amount=paid,
                note=str(payload.get("note") or "").strip(),
                created_by=request.user,
            )
            return JsonResponse({"ok": True, "data": _supplier_transaction_payload(row)}, status=201)
        if request.method in {"PUT", "PATCH"} and pk:
            row = SupplierTransaction.objects.filter(pk=pk, is_active=True).first()
            if not row:
                return _json_error("Supplier transaction not found.", 404)
            supplier_id = str(payload.get("supplier_id") or row.supplier_id).strip()
            supplier = Supplier.objects.filter(pk=supplier_id).first()
            if not supplier:
                raise ValueError("Select a supplier.")
            transaction_date = parse_date(str(payload.get("transaction_date") or "")) or row.transaction_date
            if payload.get("transaction_type"):
                amount = _decimal_from_value(payload.get("amount"), "Amount", required=True)
                outstanding, paid = _supplier_transaction_amounts(str(payload.get("transaction_type") or ""), amount)
            else:
                outstanding = _decimal_from_value(payload.get("outstanding_amount", row.outstanding_amount), "Outstanding Amount", required=True)
                paid = _decimal_from_value(payload.get("paid_amount", row.paid_amount), "Paid Amount", required=True)
                if outstanding < 0 or paid < 0:
                    raise ValueError("Supplier amounts cannot be negative.")
            row.supplier = supplier
            row.transaction_date = transaction_date
            row.outstanding_amount = outstanding
            row.paid_amount = paid
            row.note = str(payload.get("note", row.note) or "").strip()
            row.save()
            return JsonResponse({"ok": True, "data": _supplier_transaction_payload(row)})
        if request.method == "DELETE" and pk:
            row = SupplierTransaction.objects.filter(pk=pk, is_active=True).first()
            if not row:
                return _json_error("Supplier transaction not found.", 404)
            row.is_active = False
            row.save(update_fields=["is_active", "updated_at"])
            return JsonResponse({"ok": True})
        return _json_error("Unsupported method.", 405)
    except (ValueError, IntegrityError) as exc:
        return _json_error(str(exc))


@login_required
def customers(request):
    if request.method == "POST":
        action = request.POST.get("action", "create_customer")
        try:
            if action == "create_customer":
                _create_customer_from_request(request)
                messages.success(request, "Customer added successfully.")
            elif action == "create_customer_transaction":
                _create_customer_transaction_from_request(request)
                messages.success(request, "Customer data added successfully.")
            else:
                raise ValueError("Unsupported customer action.")
            return redirect("customers")
        except (ValueError, IntegrityError) as exc:
            messages.error(request, str(exc))

    query = request.GET.get("q", "").strip()
    rows = Customer.objects.annotate(
        total_customer_paid=Sum("transactions__customer_paid_amount", filter=Q(transactions__is_active=True)),
        total_you_got=Sum("transactions__you_got_amount", filter=Q(transactions__is_active=True)),
    ).order_by("customer_name")
    if query:
        rows = rows.filter(Q(customer_name__icontains=query) | Q(mobile_number__icontains=query))
    paginator = Paginator(rows, 50)
    customer_page = paginator.get_page(request.GET.get("page") or 1)
    for customer in customer_page.object_list:
        remaining = (customer.total_you_got or Decimal("0")) - (customer.total_customer_paid or Decimal("0"))
        customer.remaining_you_got_label = _format_supplier_amount(remaining)

    customer_options = [_customer_payload(customer) for customer in Customer.objects.order_by("customer_name")]
    context = {
        **_base("customers", "Customers"),
        "customer_page": customer_page,
        "query": query,
        "customer_options_json": json.dumps(customer_options),
        "today": timezone.localdate(),
    }
    return render(request, "stock_management/pages/customers.html", context)


@login_required
def customer_detail(request, customer_id: int):
    customer = Customer.objects.filter(pk=customer_id).first()
    if not customer:
        messages.error(request, "Customer not found.")
        return redirect("customers")

    if request.method == "POST":
        try:
            action = request.POST.get("action", "")
            if action == "update_customer_transaction":
                _update_customer_transaction_from_request(request, customer)
                messages.success(request, "Customer entry updated successfully.")
            elif action == "delete_customer_transaction":
                _delete_customer_transaction_from_request(request, customer)
                messages.success(request, "Customer entry deleted successfully.")
            else:
                raise ValueError("Unsupported customer action.")
            redirect_url = request.path
            if request.GET.urlencode():
                redirect_url = f"{redirect_url}?{request.GET.urlencode()}"
            return redirect(redirect_url)
        except (ValueError, IntegrityError) as exc:
            messages.error(request, str(exc))

    filter_start = parse_date(request.GET.get("start", ""))
    filter_end = parse_date(request.GET.get("end", ""))
    all_transactions = customer.transactions.select_related("created_by").filter(is_active=True)
    transactions = all_transactions
    if filter_start and filter_end:
        if filter_start > filter_end:
            filter_start, filter_end = filter_end, filter_start
        transactions = transactions.filter(transaction_date__range=(filter_start, filter_end))
    elif filter_start or filter_end:
        single_date = filter_start or filter_end
        transactions = transactions.filter(transaction_date=single_date)
        filter_start = single_date
        filter_end = single_date

    totals = all_transactions.aggregate(customer_paid=Sum("customer_paid_amount"), you_got=Sum("you_got_amount"))
    total_customer_paid_value = totals["customer_paid"] or Decimal("0")
    total_you_got_value = totals["you_got"] or Decimal("0")
    remaining_you_got_value = total_you_got_value - total_customer_paid_value

    balance_by_id = {}
    running_balance = Decimal("0")
    for row in reversed(list(all_transactions)):
        running_balance += (row.you_got_amount or Decimal("0")) - (row.customer_paid_amount or Decimal("0"))
        balance_by_id[row.id] = running_balance

    transaction_rows = list(transactions)
    for row in transaction_rows:
        row.balance_after = balance_by_id.get(row.id, Decimal("0"))
        row.balance_after_label = _format_supplier_amount(row.balance_after)
        row.customer_paid_label = _format_supplier_amount(row.customer_paid_amount or Decimal("0"))
        row.you_got_label = _format_supplier_amount(row.you_got_amount or Decimal("0"))
        row.entry_type = _customer_transaction_type(row)
        row.entry_amount = row.you_got_amount if row.entry_type == "you_got" else row.customer_paid_amount
        row.entry_amount_label = _format_supplier_amount(row.entry_amount or Decimal("0"))

    paginator = Paginator(transaction_rows, 50)
    transaction_page = paginator.get_page(request.GET.get("page") or 1)
    transaction_options = [_customer_transaction_payload(row) for row in transaction_page.object_list]
    context = {
        **_base("customers", "Customer Detail"),
        "customer": customer,
        "transaction_page": transaction_page,
        "transaction_options_json": json.dumps(transaction_options),
        "filter_start": filter_start,
        "filter_end": filter_end,
        "total_customer_paid": _format_supplier_amount(total_customer_paid_value),
        "total_you_got": _format_supplier_amount(total_you_got_value),
        "remaining_customer_paid": _format_supplier_amount(remaining_you_got_value),
        "remaining_you_got": _format_supplier_amount(remaining_you_got_value),
    }
    return render(request, "stock_management/pages/customer_detail.html", context)


@login_required
def customer_detail_pdf(request, customer_id: int):
    customer = Customer.objects.filter(pk=customer_id).first()
    if not customer:
        messages.error(request, "Customer not found.")
        return redirect("customers")

    all_transactions = customer.transactions.select_related("created_by").filter(is_active=True)
    transactions, filter_start, filter_end = _filtered_transactions(request, all_transactions)
    transactions = transactions.order_by("transaction_date", "created_at", "id")
    totals = transactions.aggregate(customer_paid=Sum("customer_paid_amount"), you_got=Sum("you_got_amount"))
    total_customer_paid = totals["customer_paid"] or Decimal("0")
    total_you_got = totals["you_got"] or Decimal("0")
    remaining = total_you_got - total_customer_paid

    balance_by_id = {}
    running_balance = Decimal("0")
    for row in all_transactions.order_by("transaction_date", "created_at", "id"):
        running_balance += (row.you_got_amount or Decimal("0")) - (row.customer_paid_amount or Decimal("0"))
        balance_by_id[row.id] = running_balance

    report_rows = []
    for row in transactions:
        report_rows.append(
            [
                f"{row.transaction_date.strftime('%d %b %Y')} {timezone.localtime(row.created_at).strftime('%I:%M %p')}",
                _format_supplier_amount(balance_by_id.get(row.id, Decimal("0"))),
                _format_supplier_amount(row.customer_paid_amount or Decimal("0")) if row.customer_paid_amount else "",
                _format_supplier_amount(row.you_got_amount or Decimal("0")) if row.you_got_amount else "",
            ]
        )

    pdf = build_statement_pdf(
        "Customer Name",
        customer.customer_name,
        "Total Outstanding",
        _format_supplier_amount(remaining),
        ["Date", "Balance", "Customer Paid", "You Give/Bill"],
        report_rows,
        (_format_supplier_amount(total_customer_paid), _format_supplier_amount(total_you_got)),
        ((0.243, 0.722, 0.475), (0.961, 0.341, 0.251)),
    )
    filename = f"customer-{slugify(customer.customer_name) or customer.id}-report.pdf"
    return _pdf_response(pdf, filename)


def _customer_payload(customer: Customer) -> dict:
    label = f"{customer.customer_name} - {customer.mobile_number}" if customer.mobile_number else customer.customer_name
    return {
        "id": customer.id,
        "customer_name": customer.customer_name,
        "mobile_number": customer.mobile_number,
        "address": customer.address,
        "label": label,
    }


def _customer_transaction_type(row: CustomerTransaction) -> str:
    if (row.you_got_amount or Decimal("0")) > 0 and (row.customer_paid_amount or Decimal("0")) <= 0:
        return "you_got"
    return "customer_paid"


def _customer_transaction_amounts(transaction_type: str, amount: Decimal) -> tuple[Decimal, Decimal]:
    transaction_type = (transaction_type or "").strip().lower()
    if transaction_type not in {"customer_paid", "you_got"}:
        raise ValueError("Select Customer Paid or You Got.")
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")
    if transaction_type == "customer_paid":
        return amount, Decimal("0")
    return Decimal("0"), amount


@transaction.atomic
def _create_customer_from_request(request) -> Customer:
    customer_name = request.POST.get("customer_name", "").strip()
    mobile_number = request.POST.get("mobile_number", "").strip()
    if not customer_name:
        raise ValueError("Customer name is required.")
    if Customer.objects.filter(customer_name__iexact=customer_name, mobile_number__iexact=mobile_number).exists():
        raise ValueError("This customer already exists.")
    return Customer.objects.create(
        customer_name=customer_name,
        mobile_number=mobile_number,
        address=request.POST.get("address", "").strip(),
    )


@transaction.atomic
def _create_customer_transaction_from_request(request) -> CustomerTransaction:
    customer_id = request.POST.get("customer_id", "").strip()
    if not customer_id:
        raise ValueError("Select a customer.")
    customer = Customer.objects.filter(pk=customer_id).first()
    if not customer:
        raise ValueError("Selected customer does not exist.")
    transaction_date = parse_date(request.POST.get("transaction_date", "")) or timezone.localdate()
    amount = _decimal_from_post(request, "amount", required=True)
    customer_paid_amount, you_got_amount = _customer_transaction_amounts(request.POST.get("transaction_type", ""), amount)
    return CustomerTransaction.objects.create(
        customer=customer,
        transaction_date=transaction_date,
        customer_paid_amount=customer_paid_amount,
        you_got_amount=you_got_amount,
        note=request.POST.get("note", "").strip(),
        created_by=request.user,
    )


@transaction.atomic
def _update_customer_transaction_from_request(request, customer: Customer) -> CustomerTransaction:
    transaction_id = request.POST.get("transaction_id", "").strip()
    row = customer.transactions.filter(pk=transaction_id, is_active=True).first()
    if not row:
        raise ValueError("Customer entry not found.")
    transaction_date = parse_date(request.POST.get("transaction_date", "")) or row.transaction_date
    amount = _decimal_from_post(request, "amount", required=True)
    customer_paid_amount, you_got_amount = _customer_transaction_amounts(request.POST.get("transaction_type", ""), amount)
    row.transaction_date = transaction_date
    row.customer_paid_amount = customer_paid_amount
    row.you_got_amount = you_got_amount
    row.note = request.POST.get("note", "").strip()
    row.save()
    return row


@transaction.atomic
def _delete_customer_transaction_from_request(request, customer: Customer) -> CustomerTransaction:
    transaction_id = request.POST.get("transaction_id", "").strip()
    row = customer.transactions.filter(pk=transaction_id, is_active=True).first()
    if not row:
        raise ValueError("Customer entry not found.")
    row.is_active = False
    row.save(update_fields=["is_active", "updated_at"])
    return row


def _customer_transaction_payload(row: CustomerTransaction) -> dict:
    transaction_type = _customer_transaction_type(row)
    amount = row.you_got_amount if transaction_type == "you_got" else row.customer_paid_amount
    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "customer_name": row.customer.customer_name,
        "transaction_date": row.transaction_date.isoformat(),
        "transaction_type": transaction_type,
        "amount": _json_decimal(amount),
        "customer_paid_amount": _json_decimal(row.customer_paid_amount),
        "you_got_amount": _json_decimal(row.you_got_amount),
        "note": row.note,
        "created_by": row.created_by.name or row.created_by.username,
        "created_at": timezone.localtime(row.created_at).strftime("%Y-%m-%d %H:%M"),
    }


@csrf_exempt
@login_required
def api_customers(request, pk: int | None = None):
    try:
        if request.method == "GET":
            if pk:
                customer = Customer.objects.filter(pk=pk).first()
                if not customer:
                    return _json_error("Customer not found.", 404)
                totals = customer.transactions.filter(is_active=True).aggregate(customer_paid=Sum("customer_paid_amount"), you_got=Sum("you_got_amount"))
                payload = _customer_payload(customer)
                payload["totals"] = {
                    "customer_paid_amount": _json_decimal(totals["customer_paid"]),
                    "you_got_amount": _json_decimal(totals["you_got"]),
                    "remaining_customer_paid": _json_decimal((totals["you_got"] or Decimal("0")) - (totals["customer_paid"] or Decimal("0"))),
                    "remaining_you_got": _json_decimal((totals["you_got"] or Decimal("0")) - (totals["customer_paid"] or Decimal("0"))),
                }
                return JsonResponse({"ok": True, "data": payload})
            rows = Customer.objects.annotate(
                total_customer_paid=Sum("transactions__customer_paid_amount", filter=Q(transactions__is_active=True)),
                total_you_got=Sum("transactions__you_got_amount", filter=Q(transactions__is_active=True)),
            )
            query = request.GET.get("q", "").strip()
            if query:
                rows = rows.filter(Q(customer_name__icontains=query) | Q(mobile_number__icontains=query))
            page = Paginator(rows.order_by("customer_name"), int(request.GET.get("page_size") or 50)).get_page(request.GET.get("page") or 1)
            results = []
            for customer in page.object_list:
                row = _customer_payload(customer)
                row["total_customer_paid"] = _json_decimal(customer.total_customer_paid)
                row["total_you_got"] = _json_decimal(customer.total_you_got)
                row["remaining_customer_paid"] = _json_decimal((customer.total_you_got or Decimal("0")) - (customer.total_customer_paid or Decimal("0")))
                row["remaining_you_got"] = row["remaining_customer_paid"]
                results.append(row)
            return JsonResponse({"ok": True, "results": results, "pagination": _pagination_payload(page)})
        payload = _request_payload(request)
        if request.method == "POST":
            name = str(payload.get("customer_name") or "").strip()
            mobile = str(payload.get("mobile_number") or "").strip()
            if not name:
                raise ValueError("Customer name is required.")
            if Customer.objects.filter(customer_name__iexact=name, mobile_number__iexact=mobile).exists():
                raise ValueError("This customer already exists.")
            customer = Customer.objects.create(customer_name=name, mobile_number=mobile, address=str(payload.get("address") or "").strip())
            return JsonResponse({"ok": True, "data": _customer_payload(customer)}, status=201)
        if request.method in {"PUT", "PATCH"} and pk:
            customer = Customer.objects.filter(pk=pk).first()
            if not customer:
                return _json_error("Customer not found.", 404)
            name = str(payload.get("customer_name", customer.customer_name) or "").strip()
            mobile = str(payload.get("mobile_number", customer.mobile_number) or "").strip()
            if not name:
                raise ValueError("Customer name is required.")
            if Customer.objects.exclude(pk=customer.pk).filter(customer_name__iexact=name, mobile_number__iexact=mobile).exists():
                raise ValueError("This customer already exists.")
            customer.customer_name = name
            customer.mobile_number = mobile
            customer.address = str(payload.get("address", customer.address) or "").strip()
            customer.save()
            return JsonResponse({"ok": True, "data": _customer_payload(customer)})
        if request.method == "DELETE" and pk:
            customer = Customer.objects.filter(pk=pk).first()
            if not customer:
                return _json_error("Customer not found.", 404)
            customer.delete()
            return JsonResponse({"ok": True})
        return _json_error("Unsupported method.", 405)
    except (ValueError, IntegrityError) as exc:
        return _json_error(str(exc))


@csrf_exempt
@login_required
def api_customer_transactions(request, pk: int | None = None):
    try:
        if request.method == "GET":
            if pk:
                row = CustomerTransaction.objects.select_related("customer", "created_by").filter(pk=pk, is_active=True).first()
                if not row:
                    return _json_error("Customer transaction not found.", 404)
                return JsonResponse({"ok": True, "data": _customer_transaction_payload(row)})
            rows = CustomerTransaction.objects.select_related("customer", "created_by").filter(is_active=True)
            customer_id = request.GET.get("customer_id", "").strip()
            start = parse_date(request.GET.get("start", ""))
            end = parse_date(request.GET.get("end", ""))
            if customer_id:
                rows = rows.filter(customer_id=customer_id)
            if start and end:
                if start > end:
                    start, end = end, start
                rows = rows.filter(transaction_date__range=(start, end))
            elif start or end:
                rows = rows.filter(transaction_date=start or end)
            totals = rows.aggregate(customer_paid=Sum("customer_paid_amount"), you_got=Sum("you_got_amount"))
            page = Paginator(rows, int(request.GET.get("page_size") or 50)).get_page(request.GET.get("page") or 1)
            return JsonResponse(
                {
                    "ok": True,
                    "results": [_customer_transaction_payload(row) for row in page.object_list],
                    "pagination": _pagination_payload(page),
                    "totals": {
                        "customer_paid_amount": _json_decimal(totals["customer_paid"]),
                        "you_got_amount": _json_decimal(totals["you_got"]),
                    },
                }
            )
        payload = _request_payload(request)
        if request.method == "POST":
            customer_id = str(payload.get("customer_id") or "").strip()
            customer = Customer.objects.filter(pk=customer_id).first()
            if not customer:
                raise ValueError("Select a customer.")
            transaction_date = parse_date(str(payload.get("transaction_date") or "")) or timezone.localdate()
            if payload.get("transaction_type"):
                amount = _decimal_from_value(payload.get("amount"), "Amount", required=True)
                customer_paid, you_got = _customer_transaction_amounts(str(payload.get("transaction_type") or ""), amount)
            else:
                customer_paid = _decimal_from_value(payload.get("customer_paid_amount"), "Customer Paid Amount", required=True)
                you_got = _decimal_from_value(payload.get("you_got_amount"), "You Got Amount", required=True)
                if customer_paid < 0 or you_got < 0:
                    raise ValueError("Customer amounts cannot be negative.")
            row = CustomerTransaction.objects.create(
                customer=customer,
                transaction_date=transaction_date,
                customer_paid_amount=customer_paid,
                you_got_amount=you_got,
                note=str(payload.get("note") or "").strip(),
                created_by=request.user,
            )
            return JsonResponse({"ok": True, "data": _customer_transaction_payload(row)}, status=201)
        if request.method in {"PUT", "PATCH"} and pk:
            row = CustomerTransaction.objects.filter(pk=pk, is_active=True).first()
            if not row:
                return _json_error("Customer transaction not found.", 404)
            customer_id = str(payload.get("customer_id") or row.customer_id).strip()
            customer = Customer.objects.filter(pk=customer_id).first()
            if not customer:
                raise ValueError("Select a customer.")
            transaction_date = parse_date(str(payload.get("transaction_date") or "")) or row.transaction_date
            if payload.get("transaction_type"):
                amount = _decimal_from_value(payload.get("amount"), "Amount", required=True)
                customer_paid, you_got = _customer_transaction_amounts(str(payload.get("transaction_type") or ""), amount)
            else:
                customer_paid = _decimal_from_value(payload.get("customer_paid_amount", row.customer_paid_amount), "Customer Paid Amount", required=True)
                you_got = _decimal_from_value(payload.get("you_got_amount", row.you_got_amount), "You Got Amount", required=True)
                if customer_paid < 0 or you_got < 0:
                    raise ValueError("Customer amounts cannot be negative.")
            row.customer = customer
            row.transaction_date = transaction_date
            row.customer_paid_amount = customer_paid
            row.you_got_amount = you_got
            row.note = str(payload.get("note", row.note) or "").strip()
            row.save()
            return JsonResponse({"ok": True, "data": _customer_transaction_payload(row)})
        if request.method == "DELETE" and pk:
            row = CustomerTransaction.objects.filter(pk=pk, is_active=True).first()
            if not row:
                return _json_error("Customer transaction not found.", 404)
            row.is_active = False
            row.save(update_fields=["is_active", "updated_at"])
            return JsonResponse({"ok": True})
        return _json_error("Unsupported method.", 405)
    except (ValueError, IntegrityError) as exc:
        return _json_error(str(exc))


@login_required
def users(request):
    user_rows = User.objects.select_related("role").order_by("username")
    for user in user_rows:
        user.initials = "".join(part[0] for part in (user.name or user.username).split())[:2].upper()
        user.last_active_label = data.rel_time(user.last_login) if user.last_login else "never"
        user.status_tone = "success" if user.is_active else "muted"
        role_code = user.role.code if user.role else Role.Code.USER
        user.role_icon = {
            Role.Code.ADMIN: "shield",
            Role.Code.MANAGER: "shield-check",
            Role.Code.STAFF: "user",
            Role.Code.USER: "eye",
        }.get(role_code, "user")
    context = {**_base("users", "Users & Permissions"), "users": user_rows}
    return render(request, "stock_management/pages/users.html", context)
