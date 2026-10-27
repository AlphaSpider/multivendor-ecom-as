"""Read queries for governance support threads (admin ↔ seller chat).

``support_rows_for`` produces normalised, presentation-ready rows so the
seller's storefront Messages tab (``conversations`` list) can merge these
threads with ordinary buyer↔seller conversations newest-first.
"""

from __future__ import annotations

from collections import defaultdict

from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, F, Prefetch, Q
from django.urls import reverse

from catalog.models import Listing, ListingImage
from auctions.models import Auction, AuctionImage
from governance.models import ListingChatMessage, ListingChatThread


def _cover_url(item) -> str:
    for image in item.images.all():  # prefetch puts the cover first
        return image.image.url
    return ""


def support_rows_for(user) -> list[dict]:
    """The user's moderation threads, enriched, newest activity first."""
    threads = (
        ListingChatThread.objects.filter(customer=user)
        .select_related("content_type", "customer")
        .annotate(
            unread_count=Count(
                "messages",
                filter=Q(messages__is_read=False)
                & ~Q(messages__sender_id=F("customer_id")),
            ),
        )
        .order_by("-last_message_at", "-id")
    )
    thread_list = list(threads)

    # Item titles + cover images, one query per item type (cover first).
    items: dict[tuple[int, int], tuple[str, str]] = {}
    ids_by_type: dict[int, set[int]] = defaultdict(set)
    for thread in thread_list:
        ids_by_type[thread.content_type_id].add(thread.object_id)
    for content_type_id, ids in ids_by_type.items():
        model = ContentType.objects.get_for_id(content_type_id).model_class()
        if model is None:
            continue
        base = model.objects.filter(pk__in=ids)
        if model is Listing:
            base = base.prefetch_related(
                Prefetch("images", queryset=ListingImage.objects.order_by(
                    "-is_cover", "display_order", "id")),
            )
        elif model is Auction:
            base = base.prefetch_related(
                Prefetch("images", queryset=AuctionImage.objects.order_by(
                    "-is_cover", "display_order", "id")),
            )
        else:
            continue
        for item in base:
            items[(content_type_id, item.pk)] = (
                item.title,
                _cover_url(item),
            )

    # Latest message per thread in a single query (rows are tiny).
    latest: dict[int, ListingChatMessage] = {}
    if thread_list:
        for message in ListingChatMessage.objects.filter(
            thread__in=thread_list
        ).order_by("created_at", "id"):
            latest[message.thread_id] = message

    rows = []
    for thread in thread_list:
        title, cover_url = items.get(
            (thread.content_type_id, thread.object_id),
            (str(thread.item) if thread.item else "Item", ""),
        )
        last = latest.get(thread.pk)
        is_from_customer = bool(last and last.sender_id == thread.customer_id)
        rows.append(
            {
                "pk": thread.pk,
                "url": reverse("governance:support_thread", args=[thread.pk]),
                "title": title,
                "cover_url": cover_url,
                "status": thread.status,
                "status_label": thread.get_status_display(),
                "unread": thread.unread_count,
                "has_messages": last is not None,
                "last_message_at": last.created_at if last else thread.last_message_at,
                "last_message_is_mine": is_from_customer,
            }
        )
    return rows
