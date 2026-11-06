import uuid

from django.db import models


class Condition(models.TextChoices):
    OEM_ORIGINAL = 'OEM_ORIGINAL', 'OEM Original'
    AFTERMARKET_COMMERCIAL = 'AFTERMARKET_COMMERCIAL', 'Aftermarket Commercial'
    USED = 'USED', 'Used'


class InventoryItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        'authentication.AuthStore',
        on_delete=models.CASCADE,
        related_name='inventory_items',
    )
    part_name_ar = models.CharField(max_length=200)
    part_name_en = models.CharField(max_length=200)
    part_number = models.CharField(max_length=100, db_index=True)
    category = models.ForeignKey(
        'catalog.Category',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inventory_items',
    )
    brand = models.ForeignKey(
        'catalog.Brand',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inventory_items',
    )
    model = models.CharField(max_length=100, blank=True, help_text='e.g. Tiggo 2 Pro')
    year_from = models.PositiveIntegerField(null=True, blank=True, help_text='e.g. 2022')
    year_to = models.PositiveIntegerField(null=True, blank=True, help_text='e.g. 2025')
    condition = models.CharField(
        max_length=30,
        choices=Condition.choices,
        default=Condition.AFTERMARKET_COMMERCIAL,
    )
    price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.IntegerField(default=0)
    min_stock_alert = models.IntegerField(default=2)
    compatibility_vins = models.JSONField(default=list, blank=True)
    image_url = models.URLField(max_length=500, blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'inventory_items'
        constraints = [
            models.UniqueConstraint(
                fields=['store', 'part_number'],
                name='unique_part_number_per_store',
            ),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.part_number} ({self.store})'


class InventoryImage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        'authentication.AuthStore',
        on_delete=models.CASCADE,
        related_name='inventory_images',
    )
    file_name = models.CharField(max_length=255, blank=True, default='')
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()
    file_data = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'inventory_images'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.file_name} ({self.store_id})'
