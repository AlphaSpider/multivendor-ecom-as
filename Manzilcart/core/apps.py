from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Core"

    def ready(self):
        # Importing the module registers the deployment checks. Django does not
        # auto-import an app's ``checks`` module.
        from core import checks  # noqa: F401
