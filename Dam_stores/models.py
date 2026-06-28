from __future__ import annotations

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Role(TimeStampedModel):
    class Code(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        USER = "USER", "User"
        MANAGER = "MANAGER", "Manager"
        STAFF = "STAFF", "Staff"

    code = models.CharField(max_length=20, choices=Code.choices, unique=True)
    name = models.CharField(max_length=80)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return self.name


class User(AbstractUser):
    name = models.CharField(max_length=150)
    mobile_number = models.CharField(max_length=20, blank=True)
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="users", null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_admin_role(self) -> bool:
        return bool(self.role and self.role.code == Role.Code.ADMIN)

    def save(self, *args, **kwargs):
        if not self.name:
            self.name = self.get_full_name() or self.username
        super().save(*args, **kwargs)


class ProductNode(TimeStampedModel):
    class NodeType(models.TextChoices):
        SEGMENT = "SEGMENT", "Segment"
        CATEGORY = "CATEGORY", "Category"
        SUBCATEGORY = "SUBCATEGORY", "Subcategory"
        BRAND = "BRAND", "Brand"
        GROUP = "GROUP", "Group"

    parent = models.ForeignKey("self", on_delete=models.CASCADE, related_name="children", null=True, blank=True)
    name = models.CharField(max_length=180)
    node_type = models.CharField(max_length=20, choices=NodeType.choices, default=NodeType.CATEGORY)
    description = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_product_nodes")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="updated_product_nodes")
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["parent", "name"], name="unique_product_node_name_per_parent"),
        ]

    def __str__(self) -> str:
        return self.full_path

    @property
    def full_path(self) -> str:
        parts = [self.name]
        parent = self.parent
        while parent:
            parts.append(parent.name)
            parent = parent.parent
        return " > ".join(reversed(parts))


class ProductItem(TimeStampedModel):
    product_node = models.ForeignKey(ProductNode, on_delete=models.PROTECT, related_name="items")
    sku = models.CharField(max_length=80, unique=True)
    barcode = models.CharField(max_length=80, blank=True)
    display_name = models.CharField(max_length=220)
    unit = models.CharField(max_length=40)
    pack_size = models.CharField(max_length=60, blank=True)
    buy_rate = models.DecimalField(max_digits=12, decimal_places=2)
    sell_rate = models.DecimalField(max_digits=12, decimal_places=2)
    mrp = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    opening_stock = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    reorder_level = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_product_items")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="updated_product_items")
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["display_name", "unit"]

    def __str__(self) -> str:
        return f"{self.display_name} ({self.unit})"


class ProductPriceHistory(models.Model):
    product_item = models.ForeignKey(ProductItem, on_delete=models.CASCADE, related_name="price_history")
    old_buy_rate = models.DecimalField(max_digits=12, decimal_places=2)
    new_buy_rate = models.DecimalField(max_digits=12, decimal_places=2)
    old_sell_rate = models.DecimalField(max_digits=12, decimal_places=2)
    new_sell_rate = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=220, blank=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="price_changes")
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at"]

    def __str__(self) -> str:
        return f"{self.product_item} price change"


class ProductStockEntry(TimeStampedModel):
    product_item = models.ForeignKey(ProductItem, on_delete=models.PROTECT, related_name="stock_entries")
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    expiry_date = models.DateField(null=True, blank=True)
    rate = models.DecimalField(max_digits=12, decimal_places=2)
    note = models.CharField(max_length=220, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_stock_entries")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["expiry_date"]),
            models.Index(fields=["product_item", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.product_item} +{self.quantity}"


class DamagedProduct(TimeStampedModel):
    product = models.ForeignKey(ProductItem, on_delete=models.PROTECT, related_name="damage_records")
    damaged_quantity = models.DecimalField(max_digits=12, decimal_places=3)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_damaged_products")
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "damaged_products"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["product"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.product} damaged {self.damaged_quantity}"


class DailySalesRecord(TimeStampedModel):
    sales_date = models.DateField(unique=True)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)
    net_profit = models.DecimalField(max_digits=14, decimal_places=2)
    note = models.CharField(max_length=220, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_sales_records")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="updated_sales_records")
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-sales_date"]
        indexes = [
            models.Index(fields=["sales_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.sales_date} sales"


class Supplier(TimeStampedModel):
    supplier_name = models.CharField(max_length=180)
    mobile_number = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)

    class Meta:
        ordering = ["supplier_name"]
        indexes = [
            models.Index(fields=["supplier_name"]),
            models.Index(fields=["mobile_number"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["supplier_name", "mobile_number"], name="unique_supplier_name_mobile"),
        ]

    def __str__(self) -> str:
        return f"{self.supplier_name} ({self.mobile_number})" if self.mobile_number else self.supplier_name


class SupplierTransaction(TimeStampedModel):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name="transactions")
    transaction_date = models.DateField()
    outstanding_amount = models.DecimalField(max_digits=14, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=14, decimal_places=2)
    note = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_supplier_transactions")

    class Meta:
        ordering = ["-transaction_date", "-created_at"]
        indexes = [
            models.Index(fields=["supplier"]),
            models.Index(fields=["transaction_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.supplier} {self.transaction_date}"


class Customer(TimeStampedModel):
    customer_name = models.CharField(max_length=180)
    mobile_number = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)

    class Meta:
        ordering = ["customer_name"]
        indexes = [
            models.Index(fields=["customer_name"]),
            models.Index(fields=["mobile_number"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["customer_name", "mobile_number"], name="unique_customer_name_mobile"),
        ]

    def __str__(self) -> str:
        return f"{self.customer_name} ({self.mobile_number})" if self.mobile_number else self.customer_name


class CustomerTransaction(TimeStampedModel):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="transactions")
    transaction_date = models.DateField()
    customer_paid_amount = models.DecimalField(max_digits=14, decimal_places=2)
    you_got_amount = models.DecimalField(max_digits=14, decimal_places=2)
    note = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_customer_transactions")

    class Meta:
        ordering = ["-transaction_date", "-created_at"]
        indexes = [
            models.Index(fields=["customer"]),
            models.Index(fields=["transaction_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.customer} {self.transaction_date}"


class AuditLog(models.Model):
    class Action(models.TextChoices):
        CREATE = "CREATE", "Create"
        UPDATE = "UPDATE", "Update"
        DELETE = "DELETE", "Delete"
        PRICE_CHANGE = "PRICE_CHANGE", "Price change"
        STOCK_CHANGE = "STOCK_CHANGE", "Stock change"
        LOGIN = "LOGIN", "Login"
        LOGOUT = "LOGOUT", "Logout"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="audit_logs", null=True, blank=True)
    action = models.CharField(max_length=30, choices=Action.choices)
    model_name = models.CharField(max_length=120)
    object_id = models.CharField(max_length=80, blank=True)
    object_label = models.CharField(max_length=220)
    old_data = models.JSONField(default=dict, blank=True)
    new_data = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["action"]),
            models.Index(fields=["model_name", "object_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} {self.action} {self.object_label}"


class InAppNotification(models.Model):
    class Kind(models.TextChoices):
        AUDIT = "AUDIT", "Audit"
        EXPIRY = "EXPIRY", "Expiry"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    message = models.CharField(max_length=300)
    source_key = models.CharField(max_length=160, unique=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="triggered_notifications", null=True, blank=True)
    link = models.CharField(max_length=220, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["kind"]),
        ]

    def __str__(self) -> str:
        return self.message


class NotificationClearance(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_clearance")
    clear_before = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["clear_before"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} cleared before {self.clear_before}"
