from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db.models.signals import post_delete, post_save

from .models import Brand, Car, Category
from .serializers import BrandSerializer, CarSerializer, CategorySerializer

CATALOG_GROUP = "catalog"

ENTITY_SERIALIZERS = {
    Brand: ("brand", BrandSerializer),
    Car: ("car", CarSerializer),
    Category: ("category", CategorySerializer),
}


def broadcast_change(instance, action):
    entity, serializer_class = ENTITY_SERIALIZERS[type(instance)]
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    async_to_sync(channel_layer.group_send)(
        CATALOG_GROUP,
        {
            "type": "catalog.event",
            "entity": entity,
            "action": action,
            "data": serializer_class(instance).data,
        },
    )


def handle_save(sender, instance, created, **kwargs):
    broadcast_change(instance, "created" if created else "updated")


def handle_delete(sender, instance, **kwargs):
    broadcast_change(instance, "deleted")


for model in (Brand, Car, Category):
    post_save.connect(
        handle_save,
        sender=model,
        dispatch_uid=f"catalog_broadcast_save_{model.__name__}",
    )
    post_delete.connect(
        handle_delete,
        sender=model,
        dispatch_uid=f"catalog_broadcast_delete_{model.__name__}",
    )
