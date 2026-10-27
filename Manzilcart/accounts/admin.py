"""Admin for members and mobile verification records."""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from accounts.models import OTPChallenge, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """
    Member administration.

    Members authenticate with an OTP rather than a password, so the password fields are
    only meaningful for staff accounts.
    """

    ordering = ("-date_joined",)
    list_display = (
        "phone",
        "display_name",
        "seller_status",
        "emirate",
        "service_area",
        "is_mobile_verified",
        "is_active",
        "is_staff",
        "date_joined",
    )
    list_filter = (
        "seller_status",
        "is_active",
        "is_staff",
        "emirate",
        "service_area",
        "phone_verified_at",
    )
    search_fields = ("phone", "display_name", "email")
    list_select_related = ("emirate", "service_area")
    autocomplete_fields = ("emirate", "service_area")
    readonly_fields = ("date_joined", "last_login", "phone_verified_at", "created_at", "updated_at")
    filter_horizontal = ("groups", "user_permissions")

    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        ("Member", {"fields": ("display_name", "first_name", "last_name", "email")}),
        ("Marketplace", {"fields": ("emirate", "service_area", "seller_status")}),
        ("Verification", {"fields": ("phone_verified_at",)}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Dates", {"fields": ("last_login", "date_joined", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("phone", "display_name", "password1", "password2"),
            },
        ),
    )

    @admin.display(boolean=True, description="Mobile verified")
    def is_mobile_verified(self, obj):
        return obj.is_mobile_verified

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            # Changing a verified number must go through the OTP flow.
            readonly.append("phone")
        return readonly


@admin.register(OTPChallenge)
class OTPChallengeAdmin(admin.ModelAdmin):
    """
    Read-only view of verification attempts, for abuse investigation.

    The code hash is shown (it is useless without the server secret) but the plaintext
    code is never stored, so it cannot be exposed here.
    """

    list_display = (
        "phone",
        "purpose",
        "status_label",
        "attempts",
        "sends",
        "last_sent_at",
        "expires_at",
        "request_ip",
    )
    list_filter = ("purpose", "created_at")
    search_fields = ("phone", "request_ip")
    date_hierarchy = "created_at"
    readonly_fields = [field.name for field in OTPChallenge._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Verification records are evidence: they expire, they are not deleted.
        return False

    @admin.display(description="Status")
    def status_label(self, obj):
        return obj.status_label
