from django.urls import path

from . import views


urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("products/", views.products, name="products"),
    path("stock/", views.current_stock, name="stock"),
    path("damaged-products/", views.damaged_products, name="damaged-products"),
    path("stock/history/", views.history, name="stock-history"),
    path("pricing/", views.pricing, name="pricing"),
    path("pricing/product-search/", views.pricing_product_search, name="pricing-product-search"),
    path("sales/", views.sales, name="sales"),
    path("sales/all/", views.all_sales_analytics, name="all-sales-analytics"),
    path("suppliers/", views.suppliers, name="suppliers"),
    path("suppliers/<int:supplier_id>/", views.supplier_detail, name="supplier-detail"),
    path("suppliers/<int:supplier_id>/pdf/", views.supplier_detail_pdf, name="supplier-detail-pdf"),
    path("customers/", views.customers, name="customers"),
    path("customers/<int:customer_id>/", views.customer_detail, name="customer-detail"),
    path("customers/<int:customer_id>/pdf/", views.customer_detail_pdf, name="customer-detail-pdf"),
    path("notifications/clear/", views.clear_notifications, name="clear-notifications"),
    path("api/damaged-products/", views.api_damaged_products, name="api-damaged-products"),
    path("api/damaged-products/<int:pk>/", views.api_damaged_products, name="api-damaged-product-detail"),
    path("api/suppliers/", views.api_suppliers, name="api-suppliers"),
    path("api/suppliers/<int:pk>/", views.api_suppliers, name="api-supplier-detail"),
    path("api/supplier-transactions/", views.api_supplier_transactions, name="api-supplier-transactions"),
    path("api/supplier-transactions/<int:pk>/", views.api_supplier_transactions, name="api-supplier-transaction-detail"),
    path("api/customers/", views.api_customers, name="api-customers"),
    path("api/customers/<int:pk>/", views.api_customers, name="api-customer-detail"),
    path("api/customer-transactions/", views.api_customer_transactions, name="api-customer-transactions"),
    path("api/customer-transactions/<int:pk>/", views.api_customer_transactions, name="api-customer-transaction-detail"),
    path("users/", views.users, name="users"),
]
