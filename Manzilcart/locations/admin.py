"""
Admin for geographic configuration and service coverage.

Staff can add/edit/activate/deactivate emirates, areas and service availability
without a deployment. Deletion is intentionally unavailable: records are retired with
``is_active`` so history is never destroyed.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from core.audit import record_audit
from core.models import AuditEvent
from locations.models import (
    Emirate,
    ServiceArea,
    ServiceAreaCoverage,
    ServiceOption,
)


class ReadOnlyAdminMixin:
    """Audit rows and other append-only records must not be editable."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Emirate)
class EmirateAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "display_order", "service_area_count", "updated_at")
    list_editable = ("is_active", "display_order")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    readonly_fields = ("created_at", "updated_at")
    actions = ("activate_emirates", "deactivate_emirates")

    @admin.display(description="Service areas")
    def service_area_count(self, obj):
        return obj.service_areas.count()

    def has_delete_permission(self, request, obj=None):
        # Deleting an emirate would orphan or destroy historical records.
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        record_audit(
            action=(
                AuditEvent.Action.EMIRATE_UPDATED
                if change
                else AuditEvent.Action.EMIRATE_CREATED
            ),
            actor=request.user,
            entity=obj,
            summary=f"Emirate '{obj.name}' saved",
            metadata={"is_active": obj.is_active, "slug": obj.slug},
            request=request,
        )

    @admin.action(description="Activate selected emirates")
    def activate_emirates(self, request, queryset):
        self._set_active(request, queryset, True)

    @admin.action(description="Deactivate selected emirates")
    def deactivate_emirates(self, request, queryset):
        self._set_active(request, queryset, False)

    def _set_active(self, request, queryset, is_active: bool):
        changed = 0
        for emirate in queryset:
            if emirate.is_active != is_active:
                emirate.is_active = is_active
                emirate.save(update_fields=["is_active", "updated_at"])
                changed += 1
                record_audit(
                    action=AuditEvent.Action.EMIRATE_UPDATED,
                    actor=request.user,
                    entity=emirate,
                    summary=f"Emirate '{emirate.name}' {'activated' if is_active else 'deactivated'}",
                    request=request,
                )
        self.message_user(
            request,
            f"{changed} emirate(s) {'activated' if is_active else 'deactivated'}.",
            messages.SUCCESS,
        )


class ServiceAreaInline(admin.TabularInline):
    model = ServiceArea
    extra = 0
    fields = ("name", "slug", "is_active", "display_order", "coverage_note")
    show_change_link = True


@admin.register(ServiceArea)
class ServiceAreaAdmin(admin.ModelAdmin):
    list_display = ("name", "emirate", "slug", "is_active", "display_order", "updated_at")
    list_editable = ("is_active", "display_order")
    list_filter = ("emirate", "is_active")
    search_fields = ("name", "slug", "emirate__name")
    list_select_related = ("emirate",)
    autocomplete_fields = ("emirate",)
    readonly_fields = ("created_at", "updated_at")
    actions = ("activate_areas", "deactivate_areas")

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            # Moving an area between emirates would invalidate the composite
            # (service_area, emirate) foreign keys on users and listings. Retire it and
            # create the area in the correct emirate instead.
            readonly.append("emirate")
        return readonly

    fieldsets = (
        (None, {"fields": ("emirate", "name", "slug")}),
        ("Availability", {"fields": ("is_active", "display_order", "coverage_note")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.action(description="Activate selected areas")
    def activate_areas(self, request, queryset):
        self._set_active(request, queryset, True)

    @admin.action(description="Deactivate selected areas")
    def deactivate_areas(self, request, queryset):
        self._set_active(request, queryset, False)

    def _set_active(self, request, queryset, is_active: bool):
        changed = queryset.filter(is_active=not is_active)
        for area in changed:
            area.is_active = is_active
            area.save(update_fields=["is_active", "updated_at"])
            record_audit(
                action=AuditEvent.Action.SERVICE_AREA_UPDATED,
                actor=request.user,
                entity=area,
                summary=f"Area '{area.name}' {'activated' if is_active else 'deactivated'}",
                emirate=area.emirate,
                request=request,
            )
        self.message_user(
            request, f"{changed.count()} area(s) updated.", messages.SUCCESS
        )

    def save_model(self, request, obj, form, change):
        try:
            super().save_model(request, obj, form, change)
        except ValidationError as exc:
            # Surface model-level validation as a form error rather than a 500.
            self.message_user(request, "; ".join(exc.messages), messages.ERROR)
            raise
        record_audit(
            action=(
                AuditEvent.Action.SERVICE_AREA_UPDATED
                if change
                else AuditEvent.Action.SERVICE_AREA_CREATED
            ),
            actor=request.user,
            entity=obj,
            emirate=obj.emirate,
            summary=f"Area '{obj.name}' saved",
            metadata={"is_active": obj.is_active},
            request=request,
        )


@admin.register(ServiceOption)
class ServiceOptionAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active", "display_order")
    list_editable = ("is_active", "display_order")
    list_filter = ("is_active",)
    search_fields = ("name", "code")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ServiceAreaCoverage)
class ServiceAreaCoverageAdmin(admin.ModelAdmin):
    """
    Coverage is edited as a list of ``(service, area, available)`` rows.

    Editing one row at a time is deliberately out of scope; operators use the filters
    below to work area by area (a bulk matrix UI would be a future improvement).
    """

    list_display = ("service_option", "service_area", "area_emirate", "is_available")
    list_editable = ("is_available",)
    list_filter = ("service_option", "is_available", "service_area__emirate")
    search_fields = ("service_area__name", "service_option__name")
    list_select_related = ("service_option", "service_area", "service_area__emirate")
    autocomplete_fields = ("service_option", "service_area")

    @admin.display(description="Emirate", ordering="service_area__emirate__name")
    def area_emirate(self, obj):
        return obj.service_area.emirate.name


@admin.register(AuditEvent)
class AuditEventAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """Read-only audit trail. Sensitive metadata is restricted to staff."""

    list_display = ("created_at", "action", "actor", "entity_type", "entity_id", "summary")
    list_filter = ("action", "entity_type", "emirate", "created_at")
    search_fields = ("summary", "entity_id", "actor__phone")
    date_hierarchy = "created_at"
    list_select_related = ("actor", "emirate")
    readonly_fields = (
        "created_at",
        "actor",
        "action",
        "entity_type",
        "entity_id",
        "emirate",
        "summary",
        "metadata",
        "ip_address",
        "user_agent",
    )

    def has_view_permission(self, request, obj=None):
        # Audit data is for authorised staff only.
        return request.user.is_active and request.user.is_staff
