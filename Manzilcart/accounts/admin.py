"""Admin for members and mobile verification records."""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from accounts.models import EmailSender, MemberEmail, OTPChallenge, User


class MemberEmailInline(admin.StackedInline):
    model = MemberEmail
    extra = 0
    max_num = 1
    can_delete = True
    readonly_fields = ("verified_at",)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """
    Member administration.

    Members can authenticate with a WhatsApp OTP or an optional password. The password
    fields are therefore meaningful for both member and staff accounts.
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
    search_fields = (
        "phone",
        "display_name",
        "email",
        "member_email__email",
    )
    list_select_related = ("emirate", "service_area")
    autocomplete_fields = ("emirate", "service_area")
    readonly_fields = ("date_joined", "last_login", "phone_verified_at", "created_at", "updated_at")
    filter_horizontal = ("groups", "user_permissions")
    inlines = (MemberEmailInline,)

    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        ("Member", {"fields": ("display_name", "first_name", "last_name")}),
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


@admin.register(MemberEmail)
class MemberEmailAdmin(admin.ModelAdmin):
    """Canonical member email addresses, one per account."""

    list_display = ("email", "user", "verified_at", "created_at")
    search_fields = ("email", "user__phone", "user__display_name")
    list_select_related = ("user",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(EmailSender)
class EmailSenderAdmin(admin.ModelAdmin):
    """Manage the SMTP account used for verification-code emails."""

    list_display = (
        "sender_email",
        "smtp_host",
        "smtp_port",
        "use_tls",
        "smtp_username",
        "is_active",
    )
    list_filter = ("is_active", "use_tls")
    search_fields = ("sender_email", "smtp_host", "smtp_username")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "sender_email",
                    "smtp_host",
                    "smtp_port",
                    "use_tls",
                    "smtp_username",
                    "is_active",
                )
            },
        ),
        (
            "Password",
            {
                "description": (
                    "Set OTP_EMAIL_SMTP_PASSWORD in the environment. The password is "
                    "never stored in the database."
                ),
                "fields": ("created_at", "updated_at"),
            },
        ),
    )


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
