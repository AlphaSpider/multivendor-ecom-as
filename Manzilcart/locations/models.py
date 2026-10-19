"""
Geographic and service-coverage configuration.

Everything here is database-backed so operations staff can add an emirate or a
neighbourhood from Django Admin without a code deployment.

Records are never hard-deleted: ``is_active`` retires an emirate or area while
preserving every historical listing, deal and audit row that references it
(``on_delete=PROTECT`` makes an accidental delete impossible from the ORM).
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower

from core.models import TimeStampedModel
from locations.validators import validate_slug_allowed, validate_slug_is_lowercase


class EmirateQuerySet(models.QuerySet):
    def active(self) -> "EmirateQuerySet":
        return self.filter(is_active=True)

    def for_display(self) -> "EmirateQuerySet":
        return self.order_by("display_order", "name")


class Emirate(TimeStampedModel):
    """
    A marketplace region. Abu Dhabi and Ras Al Khaimah launch first; additional
    emirates are added by creating a row (no code change).
    """

    name = models.CharField(max_length=64, unique=True)
    slug = models.SlugField(
        max_length=64,
        unique=True,
        validators=[validate_slug_allowed],
        help_text="URL segment for the marketplace, e.g. 'abu-dhabi'. Cannot be changed casually: it is part of every shared listing URL.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text=(
            "Inactive emirates are hidden from the switcher and their URLs return "
            "404. Existing users, listings and deals are preserved."
        ),
    )
    display_order = models.PositiveSmallIntegerField(
        default=0, help_text="Lower numbers appear first in the emirate picker."
    )

    objects = EmirateQuerySet.as_manager()

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name = "emirate"
        verbose_name_plural = "emirates"
        constraints = [
            # Slugs are URL segments; keeping them lowercase in the database (not just
            # in forms) means a direct URL can never be case-ambiguous.
            models.CheckConstraint(
                condition=models.Q(slug=Lower("slug")),
                name="emirate_slug_is_lowercase",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self):
        super().clean()
        if self.slug:
            self.slug = self.slug.strip().lower()
            try:
                validate_slug_allowed(self.slug)
                validate_slug_is_lowercase(self.slug)
            except ValidationError as exc:
                raise ValidationError({"slug": exc.messages}) from exc

    def save(self, *args, **kwargs):
        if self.slug:
            self.slug = self.slug.strip().lower()
        return super().save(*args, **kwargs)

    @property
    def url_slug(self) -> str:
        return self.slug


class ServiceArea(TimeStampedModel):
    """
    A residential neighbourhood a member can register in and a listing can be
    published from. Always belongs to exactly one emirate.

    The unique index on ``(id, emirate_id)`` is not cosmetic: it is the referenced
    key of the composite foreign key that makes a mismatched
    ``(emirate, service_area)`` pair impossible at the database level. See
    ``core.db.geo``.
    """

    emirate = models.ForeignKey(
        Emirate,
        on_delete=models.PROTECT,
        related_name="service_areas",
    )
    name = models.CharField(max_length=96)
    slug = models.SlugField(max_length=96)
    is_active = models.BooleanField(
        default=True,
        help_text="Deactivating an area prevents new listings/registrations there "
        "but keeps existing members and listings working.",
    )
    coverage_note = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional internal note about coverage for this area.",
    )
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["emirate__display_order", "display_order", "name"]
        verbose_name = "service area"
        verbose_name_plural = "service areas"
        constraints = [
            models.UniqueConstraint(
                fields=["emirate", "slug"], name="servicearea_emirate_slug_uniq"
            ),
            # Referenced by the composite geographic foreign key (core.db.geo).
            models.UniqueConstraint(
                fields=["id", "emirate"], name="servicearea_id_emirate_uniq"
            ),
            models.CheckConstraint(
                condition=models.Q(slug=Lower("slug")),
                name="servicearea_slug_is_lowercase",
            ),
        ]
        indexes = [
            models.Index(
                fields=["emirate", "is_active"], name="servicearea_emirate_active_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.emirate.name})"

    def clean(self):
        super().clean()
        if self.slug:
            self.slug = self.slug.strip().lower()
        if self.emirate_id is None:
            raise ValidationError({"emirate": "A service area must belong to an emirate."})

    def save(self, *args, **kwargs):
        if self.slug:
            self.slug = self.slug.strip().lower()
        return super().save(*args, **kwargs)


class ServiceOption(TimeStampedModel):
    """
    An optional, offline-managed add-on service the buyer can request for a deal
    (delivery/transportation, assembly, disassembly).

    ManzilCart only *records* the selection and notifies the operations team; there
    is deliberately no scheduling, pricing or fulfilment model behind it.
    """

    class Code(models.TextChoices):
        DELIVERY = "delivery", "Delivery / transportation"
        ASSEMBLY = "assembly", "Assembly"
        DISASSEMBLY = "disassembly", "Disassembly"

    code = models.SlugField(
        max_length=32,
        unique=True,
        choices=Code.choices,
        help_text="Stable identifier used in code and reporting.",
    )
    name = models.CharField(max_length=64)
    description = models.CharField(
        max_length=255,
        blank=True,
        help_text="Shown to buyers when they choose optional services.",
    )
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name = "service option"
        verbose_name_plural = "service options"

    def __str__(self) -> str:
        return self.name


class ServiceAreaCoverage(TimeStampedModel):
    """
    Whether a :class:`ServiceOption` is offered in a given :class:`ServiceArea`.

    Coverage is **opt-in**: a service is offered only where an operator has created a
    row with ``is_available=True``. Erring on the side of "not offered" is deliberate -
    promising a service we cannot deliver in a neighbourhood is worse than omitting
    it. Areas with no row simply do not see the option.
    """

    service_option = models.ForeignKey(
        ServiceOption,
        on_delete=models.CASCADE,
        related_name="coverage",
    )
    service_area = models.ForeignKey(
        ServiceArea,
        on_delete=models.CASCADE,
        related_name="service_coverage",
    )
    is_available = models.BooleanField(
        default=True,
        help_text="Untick to explicitly record that this service is unavailable here.",
    )

    class Meta:
        verbose_name = "service coverage"
        verbose_name_plural = "service coverage"
        constraints = [
            models.UniqueConstraint(
                fields=["service_option", "service_area"],
                name="servicecoverage_option_area_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["service_area", "is_available"],
                name="servicecoverage_area_avail_idx",
            ),
        ]

    def __str__(self) -> str:
        state = "available" if self.is_available else "unavailable"
        return f"{self.service_option} / {self.service_area}: {state}"
