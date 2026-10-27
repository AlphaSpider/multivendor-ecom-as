"""Governance app configuration: moderation fields mixin, admin chat, custom admin."""

from django.apps import AppConfig


class GovernanceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "governance"
    verbose_name = "Governance & admin chat"

    def ready(self) -> None:
        # Give the default admin site the governance URLs (command centre,
        # moderation inbox, bid monitor, vendors, financials) without
        # re-registering every ModelAdmin: the registry on django.contrib's
        # admin.site instance is preserved, only the class behaviour changes.
        # ModelAdmin instances were constructed against this same instance, so
        # admin_view()/each_context() keep working.
        from django.contrib import admin

        from governance.admin_site import ManzilcartAdminSite

        if isinstance(admin.site, ManzilcartAdminSite):  # pragma: no cover
            return
        admin.site.__class__ = ManzilcartAdminSite
