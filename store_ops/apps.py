from django.apps import AppConfig


class StoreOpsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "store_ops"

    def ready(self):
        import store_ops.signals  # noqa: F401
