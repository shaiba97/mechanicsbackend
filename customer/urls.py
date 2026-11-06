from django.urls import path

from . import views

app_name = "customer"

urlpatterns = [
    path("parts/search", views.customer_parts_search, name="customer-parts-search"),
    path("parts/<uuid:part_id>", views.customer_part_detail, name="customer-part-detail"),
    path("parts/<uuid:part_id>/reviews", views.part_reviews, name="customer-part-reviews"),
    path("reviews", views.create_review, name="customer-create-review"),
    path("delivery/cities", views.delivery_cities, name="customer-delivery-cities"),
    path("garage", views.CustomerVehicleListCreateView.as_view(), name="customer-garage-list-create"),
    path("garage/<uuid:vehicle_id>", views.CustomerVehicleDeleteView.as_view(), name="customer-garage-delete"),
    path("orders", views.OrderListView.as_view(), name="customer-order-list"),
    path("orders/<uuid:order_id>", views.OrderDetailView.as_view(), name="customer-order-detail"),
    path("orders/checkout", views.CheckoutView.as_view(), name="customer-checkout"),
]
