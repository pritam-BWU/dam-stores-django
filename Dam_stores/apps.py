from django.apps import AppConfig


class DamStoresConfig(AppConfig):
    name = 'Dam_stores'

    def ready(self):
        from . import signals  # noqa: F401
