"""View mixins that enforce marketplace (emirate) scoping on the server."""

from __future__ import annotations

from django.http import Http404

from locations.selectors import active_emirate_for_slug


class EmirateRequiredMixin:
    """
    Require a valid, active emirate slug in the URL.

    Views handling marketplace content inherit this. An unknown, inactive or
    reserved slug raises ``Http404`` so a deactivated emirate's URLs stop working
    immediately (while all of its historical records remain intact).

    The resolved emirate is available as ``self.emirate`` and is injected into the
    template context, so every page under ``/<emirate>/`` knows its marketplace.
    """

    emirate_kwarg = "emirate_slug"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        slug = kwargs.get(self.emirate_kwarg) or getattr(request, "emirate", None)
        if hasattr(slug, "slug"):
            self.emirate = slug
        else:
            self.emirate = active_emirate_for_slug(slug)
        if self.emirate is None:
            raise Http404("Marketplace not found.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("emirate", self.emirate)
        return context


class EmirateScopedQuerysetMixin(EmirateRequiredMixin):
    """
    Helper for views that serve emirate-filtered collections.

    ``get_emirate_scope()`` is the single source of the filter value, which keeps
    "the selected emirate is the default scope" true for lists, search, counts and
    pagination alike.
    """

    def get_emirate_scope(self):
        return self.emirate
