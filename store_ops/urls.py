from django.urls import path

from . import views

app_name = "store_ops"

urlpatterns = [
    path("orders", views.store_orders, name="store-orders"),
    path("orders/<uuid:order_id>/status", views.order_status, name="store-order-status"),
    path("orders/<uuid:order_id>/verify-otp", views.verify_otp, name="store-order-verify-otp"),
    path("reviews", views.store_reviews, name="store-reviews"),
    path("delivery-fees", views.delivery_fees, name="store-delivery-fees"),
    path("earnings", views.earnings, name="store-earnings"),
    path("bank-account", views.store_bank_account, name="store-bank-account"),
    path("payouts", views.store_payouts, name="store-payouts"),
    path("payouts/execute", views.execute_payouts, name="execute-payouts"),
    path("financials/summary", views.financials_summary, name="financials-summary"),
    path("financials/flash-report", views.flash_report, name="flash-report"),
]
