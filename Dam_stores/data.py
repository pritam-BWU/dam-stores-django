from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone


CATEGORIES = ["Rice", "Dal", "Oil", "Masala", "Soap", "Cold Drinks", "Biscuits"]
WAREHOUSES = ["WH-Main", "WH-Dhaka", "WH-Chittagong", "WH-Sylhet", "WH-Khulna"]
USER_NAMES = [
    "Karim Ahmed",
    "Rashida Begum",
    "Sajid Hossain",
    "Nusrat Jahan",
    "Imran Khan",
    "Fatima Ali",
    "Rafiq Mia",
]

SEEDS = {
    "Rice": {
        "brands": ["Pran", "Rashid", "ACI", "Teer", "Diamond", "Chashi", "Mojar", "Fresh", "Aarong"],
        "names": ["Miniket", "Najirshail", "Basmati", "Kataribhog", "Polao", "Chinigura", "Atap", "Paijam", "Brown Rice", "Sticky Rice"],
        "units": ["1kg", "2kg", "5kg", "10kg", "25kg", "50kg"],
        "base": (60, 180),
    },
    "Dal": {
        "brands": ["Pran", "Teer", "Radhuni", "ACI", "Rupchanda", "Fresh"],
        "names": ["Mosur Dal", "Mug Dal", "Chola Dal", "Khesari Dal", "Anchor Dal", "Boot Dal", "Mash Kalai", "Arhar Dal"],
        "units": ["500g", "1kg", "2kg", "5kg"],
        "base": (80, 200),
    },
    "Oil": {
        "brands": ["Rupchanda", "Fresh", "Teer", "Pusti", "Veola", "Mustard King", "Radhuni"],
        "names": ["Soybean Oil", "Sunflower Oil", "Mustard Oil", "Rice Bran Oil", "Olive Oil", "Coconut Oil", "Palm Oil"],
        "units": ["500ml", "1L", "2L", "5L", "8L"],
        "base": (120, 1400),
    },
    "Masala": {
        "brands": ["Radhuni", "Pran", "BD Foods", "Ahmed", "Square", "Fresh"],
        "names": ["Chili Powder", "Turmeric Powder", "Cumin Powder", "Coriander Powder", "Garam Masala", "Meat Masala", "Biryani Masala", "Curry Powder", "Black Pepper", "Cardamom"],
        "units": ["50g", "100g", "200g", "500g"],
        "base": (40, 280),
    },
    "Soap": {
        "brands": ["Lifebuoy", "Lux", "Dove", "Dettol", "Tibet", "Keya", "Sandalina", "Cute"],
        "names": ["Bath Soap", "Beauty Soap", "Antibacterial Soap", "Herbal Soap", "Glycerin Soap", "Sandal Soap", "Rose Soap", "Aloe Vera Soap"],
        "units": ["75g", "100g", "125g", "150g", "Pack of 3", "Pack of 4"],
        "base": (35, 220),
    },
    "Cold Drinks": {
        "brands": ["Coca-Cola", "Pepsi", "Sprite", "Mojo", "Speed", "RC Cola", "7Up", "Mountain Dew", "Pran Frooto"],
        "names": ["Cola", "Lemon", "Orange", "Mango Juice", "Energy Drink", "Lychee Drink", "Apple Drink"],
        "units": ["250ml", "500ml", "1L", "1.5L", "2L"],
        "base": (25, 180),
    },
    "Biscuits": {
        "brands": ["Pran", "Olympic", "Bengal", "Nabisco", "Haque", "Britannia", "Danish", "Ifad"],
        "names": ["Energy Plus", "Glucose", "Cream Cracker", "Chocolate", "Marie", "Digestive", "Butter Cookies", "Salted", "Nutty Biscuit", "Milk Biscuit"],
        "units": ["50g", "100g", "150g", "250g", "Family Pack"],
        "base": (15, 180),
    },
}

CHART_COLORS = ["#2563eb", "#10b981", "#f59e0b", "#db2777", "#ef4444", "#7c3aed", "#06b6d4"]


def _rng() -> random.Random:
    return random.Random(42)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def format_bdt(value: float) -> str:
    value = float(value)
    if value >= 10000000:
        return f"Rs {value / 10000000:.2f} Cr"
    if value >= 100000:
        return f"Rs {value / 100000:.2f} L"
    if value >= 1000:
        return f"Rs {value / 1000:.1f}K"
    return f"Rs {value:.0f}"


def format_num(value: int) -> str:
    return f"{value:,}"


def rel_time(value: datetime) -> str:
    diff = _now() - value
    minutes = max(0, int(diff.total_seconds() // 60))
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    return value.strftime("%b %d, %Y")


def _make_products() -> list[dict]:
    rng = _rng()
    products: list[dict] = []
    pid = 1
    for category in CATEGORIES:
        seed = SEEDS[category]
        for brand in seed["brands"]:
            for name in seed["names"]:
                product_name = f"{brand} {name}"
                product_id = f"P{pid:05d}"
                unit_count = rng.randint(2, len(seed["units"]))
                created_at = _now() - timedelta(days=rng.randint(30, 720))
                variants = []
                for index, unit in enumerate(seed["units"][:unit_count]):
                    base = rng.randint(*seed["base"])
                    current_rate = round(base * (1 + index * 0.6))
                    previous_rate = round(current_rate * (0.9 + rng.random() * 0.2))
                    purchase_rate = round(current_rate * (0.7 + rng.random() * 0.15))
                    variant = {
                        "id": f"{product_id}-V{index + 1}",
                        "product_id": product_id,
                        "product_name": product_name,
                        "category": category,
                        "name": unit,
                        "sku": f"{category[:2].upper()}-{pid}-{index + 1}",
                        "barcode": f"8901{pid:04d}{index:03d}",
                        "unit": unit,
                        "current_rate": current_rate,
                        "purchase_rate": purchase_rate,
                        "previous_rate": previous_rate,
                        "stock": rng.randint(0, 800),
                        "reorder_level": rng.randint(20, 80),
                        "warehouse": rng.choice(WAREHOUSES),
                        "batch": f"BT-{rng.randint(1000, 9999)}",
                        "expiry_months": rng.randint(3, 24),
                    }
                    variant["status"] = "Out" if variant["stock"] == 0 else "Low" if variant["stock"] <= variant["reorder_level"] else "OK"
                    variant["status_tone"] = "destructive" if variant["status"] == "Out" else "warning" if variant["status"] == "Low" else "success"
                    variant["stock_value"] = variant["stock"] * variant["purchase_rate"]
                    variants.append(variant)
                products.append({
                    "id": product_id,
                    "name": product_name,
                    "brand": brand,
                    "category": category,
                    "created_at": created_at,
                    "created_label": created_at.strftime("%b %d, %Y"),
                    "variants": variants,
                    "total_stock": sum(v["stock"] for v in variants),
                })
                pid += 1
    return products


PRODUCTS = _make_products()
ALL_VARIANTS = [variant for product in PRODUCTS for variant in product["variants"]]


def _make_movements(count: int) -> list[dict]:
    rng = random.Random(91)
    types = ["Stock In", "Sale", "Return"]
    rows = []
    for index in range(count):
        variant = rng.choice(ALL_VARIANTS)
        movement_type = rng.choice(types)
        quantity = rng.randint(5, 120)
        previous = rng.randint(20, 600)
        delta = -quantity if movement_type == "Sale" else quantity
        next_value = max(0, previous + delta)
        row = {
            "id": f"MV{index + 1}",
            "product_name": variant["product_name"],
            "variant_name": variant["name"],
            "sku": variant["sku"],
            "type": movement_type,
            "qty": quantity,
            "prev": previous,
            "next": next_value,
            "user": rng.choice(USER_NAMES),
            "warehouse": variant["warehouse"],
            "time": _now() - timedelta(minutes=index * rng.randint(10, 600)),
        }
        row["delta"] = row["next"] - row["prev"]
        row["delta_abs"] = abs(row["delta"])
        row["delta_sign"] = "+" if row["delta"] > 0 else ""
        row["time_label"] = rel_time(row["time"])
        row["type_tone"] = "success" if movement_type == "Stock In" else "info" if movement_type == "Sale" else "muted"
        rows.append(row)
    return rows


MOVEMENTS = _make_movements(140)


def _make_price_changes(count: int) -> list[dict]:
    rng = random.Random(23)
    rows = []
    for index in range(count):
        variant = rng.choice(ALL_VARIANTS)
        old = variant["previous_rate"]
        new = variant["current_rate"]
        change_pct = round(((new - old) / old) * 100, 2) if old else 0
        row = {
            "id": f"PC{index + 1}",
            "product_name": variant["product_name"],
            "variant_name": variant["name"],
            "sku": variant["sku"],
            "old_rate": old,
            "new_rate": new,
            "change_pct": change_pct,
            "change_sign": "+" if change_pct > 0 else "",
            "user": rng.choice(USER_NAMES),
            "time": _now() - timedelta(minutes=index * rng.randint(60, 2000)),
        }
        row["time_label"] = rel_time(row["time"])
        row["tone"] = "warning" if change_pct > 0 else "success"
        rows.append(row)
    return rows


PRICE_CHANGES = _make_price_changes(60)

USERS = [
    {"id": "U1", "name": "Abdul Karim", "email": "karim@damstores.com", "role": "Owner", "warehouse": "All", "last_active": _now(), "status": "Active"},
    {"id": "U2", "name": "Rashida Begum", "email": "rashida@damstores.com", "role": "Manager", "warehouse": "WH-Main", "last_active": _now() - timedelta(hours=1), "status": "Active"},
    {"id": "U3", "name": "Sajid Hossain", "email": "sajid@damstores.com", "role": "Manager", "warehouse": "WH-Dhaka", "last_active": _now() - timedelta(hours=2), "status": "Active"},
    {"id": "U4", "name": "Imran Khan", "email": "imran@damstores.com", "role": "Stock Operator", "warehouse": "WH-Main", "last_active": _now() - timedelta(minutes=10), "status": "Active"},
    {"id": "U5", "name": "Nusrat Jahan", "email": "nusrat@damstores.com", "role": "Stock Operator", "warehouse": "WH-Chittagong", "last_active": _now() - timedelta(minutes=30), "status": "Active"},
    {"id": "U6", "name": "Rafiq Mia", "email": "rafiq@damstores.com", "role": "Stock Operator", "warehouse": "WH-Sylhet", "last_active": _now() - timedelta(days=1), "status": "Inactive"},
    {"id": "U7", "name": "Fatima Ali", "email": "fatima@damstores.com", "role": "Viewer", "warehouse": "All", "last_active": _now() - timedelta(days=2), "status": "Active"},
]
for user in USERS:
    user["initials"] = "".join(part[0] for part in user["name"].split())[:2]
    user["last_active_label"] = rel_time(user["last_active"])
    user["status_tone"] = "success" if user["status"] == "Active" else "muted"
    user["role_icon"] = {
        "Owner": "shield",
        "Manager": "shield-check",
        "Stock Operator": "user",
        "Viewer": "eye",
    }[user["role"]]


METRICS = {
    "total_value": sum(v["stock"] * v["purchase_rate"] for v in ALL_VARIANTS),
    "total_products": len(PRODUCTS),
    "total_variants": len(ALL_VARIANTS),
    "today_changes": sum(1 for movement in MOVEMENTS if (_now() - movement["time"]).total_seconds() < 86400),
    "low_stock": sum(1 for v in ALL_VARIANTS if v["stock"] <= v["reorder_level"]),
}
METRICS["total_value_label"] = format_bdt(METRICS["total_value"])
METRICS["total_products_label"] = format_num(METRICS["total_products"])
METRICS["total_variants_label"] = format_num(METRICS["total_variants"])
METRICS["low_stock_label"] = format_num(METRICS["low_stock"])
METRICS["healthy_variants_label"] = format_num(METRICS["total_variants"] - METRICS["low_stock"])


def _series() -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict]]:
    rng = random.Random(102)
    inventory = []
    for index in range(30):
        date = _now() - timedelta(days=29 - index)
        inventory.append({"date": date.strftime("%b %d"), "in": rng.randint(200, 900), "out": rng.randint(180, 800)})
    categories = []
    for index, category in enumerate(CATEGORIES):
        value = sum(v["stock"] * v["purchase_rate"] for v in ALL_VARIANTS if v["category"] == category)
        categories.append({"name": category, "value": value, "value_label": format_bdt(value), "color": CHART_COLORS[index % len(CHART_COLORS)]})
    months = ["Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun"]
    inventory_value = [{"month": month, "amount": rng.randint(800000, 2600000), "orders": rng.randint(20, 60)} for month in months]
    aging = [
        {"range": "0-30 days", "value": rng.randint(40, 60)},
        {"range": "31-60 days", "value": rng.randint(15, 30)},
        {"range": "61-90 days", "value": rng.randint(8, 18)},
        {"range": "91-180 days", "value": rng.randint(4, 12)},
        {"range": "180+ days", "value": rng.randint(2, 8)},
    ]
    sales = [{"month": p["month"], "revenue": p["amount"] + rng.randint(300000, 800000), "cost": p["amount"], "profit": rng.randint(150000, 500000)} for p in inventory_value]
    return inventory, categories, inventory_value, aging, sales


INVENTORY_SERIES, CATEGORY_DISTRIBUTION, MONTHLY_INVENTORY_VALUE_TREND, STOCK_AGING, MONTHLY_SALES = _series()
DAILY_REVENUE = [
    {"date": (_now() - timedelta(days=13 - index)).strftime("%b %d"), "revenue": random.Random(300 + index).randint(45000, 180000)}
    for index in range(14)
]


def json_data(value) -> str:
    return json.dumps(value)


def price_history() -> list[dict]:
    rows = []
    for index in range(14):
        date = _now() - timedelta(days=13 - index)
        rows.append({"date": date.strftime("%b %d"), "rate": 80 + round(math.sin(index / 2) * 8 + index * 0.6)})
    return rows
