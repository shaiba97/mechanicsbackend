import uuid

from django.db import models


class SparePart(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    part_number = models.CharField(max_length=100, unique=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    store = models.CharField(max_length=255)
    # JSONField maps to PostgreSQL's native jsonb type.
    compatibility = models.JSONField(
        default=list,
        blank=True,
        help_text="Vehicle mapping data, e.g. [{'make': 'Toyota', 'model': 'Corolla', 'year_from': 2019, 'year_to': 2024}]",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.part_number})"


class Brand(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    name = models.JSONField(default=dict)
    logo_url = models.URLField(max_length=500, blank=True, default="")
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.code


class Car(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    model_name = models.JSONField(default=dict)
    year = models.PositiveIntegerField()
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="cars")
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.model_name} ({self.year})"


class Category(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.JSONField(default=dict)
    active = models.BooleanField(default=True)

    def __str__(self):
        return str(self.name)
