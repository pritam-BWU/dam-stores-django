from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import AuditLog, DamagedProduct, ProductItem, ProductNode, ProductPriceHistory, Role, Supplier, SupplierTransaction, User


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active", "created_at", "updated_at")
    search_fields = ("code", "name")
    list_filter = ("is_active",)


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("Store profile", {"fields": ("name", "mobile_number", "role", "created_at", "updated_at")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Store profile", {"fields": ("name", "email", "mobile_number", "role")}),
    )
    readonly_fields = ("created_at", "updated_at")
    list_display = ("username", "name", "email", "mobile_number", "role", "is_active", "is_staff")
    list_filter = ("role", "is_active", "is_staff", "is_superuser")
    search_fields = ("username", "name", "email", "mobile_number")


@admin.register(ProductNode)
class ProductNodeAdmin(admin.ModelAdmin):
    list_display = ("name", "node_type", "parent", "is_active", "version", "updated_at")
    list_filter = ("node_type", "is_active")
    search_fields = ("name", "description")
    autocomplete_fields = ("parent", "created_by", "updated_by")


@admin.register(ProductItem)
class ProductItemAdmin(admin.ModelAdmin):
    list_display = ("sku", "display_name", "unit", "buy_rate", "sell_rate", "product_node", "is_active")
    list_filter = ("is_active", "product_node__node_type")
    search_fields = ("sku", "barcode", "display_name", "product_node__name")
    autocomplete_fields = ("product_node", "created_by", "updated_by")


@admin.register(ProductPriceHistory)
class ProductPriceHistoryAdmin(admin.ModelAdmin):
    list_display = ("product_item", "old_buy_rate", "new_buy_rate", "old_sell_rate", "new_sell_rate", "changed_by", "changed_at")
    search_fields = ("product_item__display_name", "product_item__sku", "reason")
    list_filter = ("changed_at",)
    autocomplete_fields = ("product_item", "changed_by")


@admin.register(DamagedProduct)
class DamagedProductAdmin(admin.ModelAdmin):
    list_display = ("created_at", "product", "damaged_quantity", "created_by", "deleted_at")
    search_fields = ("product__display_name", "product__sku", "note")
    list_filter = ("created_at", "deleted_at")
    autocomplete_fields = ("product", "created_by")


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("supplier_name", "mobile_number", "created_at", "updated_at")
    search_fields = ("supplier_name", "mobile_number", "address")


@admin.register(SupplierTransaction)
class SupplierTransactionAdmin(admin.ModelAdmin):
    list_display = ("transaction_date", "supplier", "outstanding_amount", "paid_amount", "created_by")
    search_fields = ("supplier__supplier_name", "supplier__mobile_number")
    list_filter = ("transaction_date",)
    autocomplete_fields = ("supplier", "created_by")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "action", "model_name", "object_label", "ip_address")
    list_filter = ("action", "model_name", "created_at")
    search_fields = ("object_label", "model_name", "object_id", "user__username", "user__name")
    readonly_fields = ("created_at",)
