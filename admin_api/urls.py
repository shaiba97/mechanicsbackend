from django.urls import path

from . import views

app_name = "admin_api"

urlpatterns = [
    path("admin/stores", views.admin_stores, name="admin-stores"),
    path("admin/stores/<uuid:store_id>", views.admin_store_detail, name="admin-store-detail"),
    path(
        "admin/stores/<uuid:store_id>/status",
        views.admin_store_status,
        name="admin-store-status",
    ),
    path(
        "admin/stores/<uuid:store_id>/bank-account",
        views.admin_store_bank_account,
        name="admin-store-bank-account",
    ),
    path(
        "admin/stores/<uuid:store_id>/bank-account/verify",
        views.admin_store_bank_account_verify,
        name="admin-store-bank-account-verify",
    ),
    path(
        "admin/commission-rate",
        views.admin_commission_rate,
        name="admin-commission-rate",
    ),
    path(
        "admin/commission-rate/update",
        views.admin_commission_rate_update,
        name="admin-commission-rate-update",
    ),
    path("admin/inventory", views.admin_inventory, name="admin-inventory"),
    path(
        "admin/documents/<int:doc_id>/view",
        views.admin_document_view,
        name="admin-document-view",
    ),
    path("admin/dashboard/kpi", views.admin_dashboard_kpi, name="admin-dashboard-kpi"),
    path("admin/orders", views.admin_orders, name="admin-orders"),
    path("admin/commissions", views.admin_commissions, name="admin-commissions"),
    path("admin/zatca-invoices", views.admin_zatca_invoices, name="admin-zatca-invoices"),
    path("admin/customers", views.admin_customers, name="admin-customers"),
    path(
        "admin/customers/<uuid:user_id>/status",
        views.admin_customer_status,
        name="admin-customer-status",
    ),
    path(
        "admin/customers/<uuid:user_id>",
        views.admin_customer_delete,
        name="admin-customer-delete",
    ),
    path("admin/notifications", views.admin_notifications, name="admin-notifications"),
    path(
        "admin/delivery/overview",
        views.admin_delivery_overview,
        name="admin-delivery-overview",
    ),
    path(
        "admin/delivery/matrix",
        views.admin_delivery_matrix,
        name="admin-delivery-matrix",
    ),
    path("admin/delivery/fee", views.admin_delivery_fee, name="admin-delivery-fee"),
    path(
        "admin/financials/payouts",
        views.admin_financials_payouts,
        name="admin-financials-payouts",
    ),
    path(
        "admin/financials/accounting",
        views.admin_financials_accounting,
        name="admin-financials-accounting",
    ),
    path(
        "admin/financials/expenses",
        views.admin_financials_expenses,
        name="admin-financials-expenses",
    ),
    path(
        "admin/financials/payment-accounts",
        views.admin_financials_payment_accounts,
        name="admin-financials-payment-accounts",
    ),
    path(
        "admin/financials/payout-requests",
        views.admin_financials_payout_requests,
        name="admin-financials-payout-requests",
    ),
    path(
        "admin/financials/payouts/process",
        views.admin_financials_payouts_process,
        name="admin-financials-payouts-process",
    ),
    path(
        "admin/financials/transactions",
        views.admin_financials_transactions,
        name="admin-financials-transactions",
    ),
    path(
        "admin/financials/accruals",
        views.admin_financials_accruals,
        name="admin-financials-accruals",
    ),
    path(
        "admin/financials/accruals/<uuid:adjustment_id>",
        views.admin_financials_accrual_detail,
        name="admin-financials-accrual-detail",
    ),
    path(
        "admin/financials/commission-rate-log",
        views.admin_financials_commission_rate_log,
        name="admin-financials-commission-rate-log",
    ),
    path(
        "admin/financials/platform-account",
        views.admin_financials_platform_account,
        name="admin-financials-platform-account",
    ),
    path("admin/profile", views.admin_profile, name="admin-profile"),
]
