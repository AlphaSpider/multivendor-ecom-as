"""
The five governance pages served by :class:`governance.admin_site.ManzilcartAdminSite`.

Every view is wrapped with ``admin_view()`` where the URLs are declared, so staff
authentication and active-user checks come from the admin site. Writes run inside
``transaction.atomic()`` through the domain services; these views only orchestrate.

Schema note: this marketplace has no orders, escrow or payout ledger — "orders"
are ``deals.Deal`` rows whose price is ``agreed_price`` (payment happens directly
between the parties), so the financials page reports real deal GMV and dispute
resolution instead of inventing payout states the business flows do not have.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Max, OuterRef, Q, Subquery, Sum, TextField, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from auctions import services as auction_services
from auctions.models import Auction, Bid
from catalog.models import Listing
from deals.models import Deal, DealCancellation
from governance import services as governance_services
from governance.models import ListingChatMessage, ListingChatThread

PAGE_SIZE = 30


def _each_context(request, **extra):
    """Merge the admin site chrome context (site_header, governance nav…).

    These custom pages are plain module-level views (not ModelAdmin methods),
    so the admin context must be merged explicitly; without it ``base_site.html``
    would fall back to the default "Django administration" branding and an
    empty governance navigation.
    """
    from django.contrib import admin as django_admin

    return {**django_admin.site.each_context(request), **extra}


CUSTOMER_MESSAGE = Subquery(
    ListingChatMessage.objects.filter(
        thread=OuterRef("pk"), sender_id=OuterRef("customer_id")
    ).values("message")[:1]
)


def _thread_rows(queryset):
    """Annotate thread rows for split-pane inbox rendering."""
    return queryset.annotate(
        # The most recent message regardless of who sent it - this is what the
        # inbox preview shows, so staff always know what came in last.
        last_message=Coalesce(
            Subquery(
                ListingChatMessage.objects.filter(thread=OuterRef("pk"))
                .order_by("-id")
                .values("message")[:1]
            ),
            Value(""),
            output_field=TextField(),
        ),
        customer_message=Coalesce(CUSTOMER_MESSAGE, Value(""), output_field=TextField()),
        admin_message=Coalesce(
            Subquery(
                ListingChatMessage.objects.filter(thread=OuterRef("pk"))
                .exclude(sender_id=OuterRef("customer_id"))
                .values("message")[:1]
            ),
            Value(""),
            output_field=TextField(),
        ),
        unread_customer_messages=Count(
            "messages",
            filter=Q(messages__is_read=False, messages__sender_id=F("customer_id")),
        ),
        unread_seller_messages=Count(
            "messages",
            filter=Q(messages__is_read=False) & ~Q(messages__sender_id=F("customer_id")),
        ),
    )


def _attach_item_details(threads) -> None:
    """
    Attach per-thread ``item_title`` / ``item_cover_url`` / ``item_admin_url``
    for the inbox cards. Runs after pagination: at most PAGE_SIZE rows, two
    catalog queries total.
    """
    from django.contrib.contenttypes.models import ContentType
    from django.db.models import Prefetch

    from auctions.models import Auction, AuctionImage
    from catalog.models import Listing, ListingImage

    by_content_type: dict[int, set[int]] = {}
    for thread in threads:
        by_content_type.setdefault(thread.content_type_id, set()).add(thread.object_id)

    prefetched_items: dict[tuple[int, int], Listing | Auction] = {}
    for content_type_id, ids in by_content_type.items():
        model = ContentType.objects.get(pk=content_type_id).model_class()
        if model is None:
            continue
        if model is Listing:
            base = Listing.objects.filter(pk__in=ids).prefetch_related(
                Prefetch(
                    "images",
                    queryset=ListingImage.objects.order_by(
                        "-is_cover", "display_order", "id"
                    ),
                )
            )
        else:
            base = Auction.objects.filter(pk__in=ids).prefetch_related(
                Prefetch(
                    "images",
                    queryset=AuctionImage.objects.order_by(
                        "-is_cover", "display_order", "id"
                    ),
                )
            )
        for item in base:
            prefetched_items[(content_type_id, item.pk)] = item

    for thread in threads:
        item = prefetched_items.get((thread.content_type_id, thread.object_id))
        thread.item_title = item.title if item is not None else str(thread.item)
        thread.item_cover_url = ""
        if item is not None:
            for image in item.images.all():
                thread.item_cover_url = image.image.url
                break
            thread.item_admin_url = thread_change_url(thread)
            if getattr(item, "status", "") == "pending":
                try:
                    from django.urls import reverse as _reverse

                    endpoint = (
                        "admin:moderation_review"
                        if isinstance(item, Listing)
                        else "admin:moderation_auction_review"
                    )
                    thread.item_admin_url = _reverse(endpoint, args=[item.pk])
                except Exception:  # noqa: BLE001 - URL may not exist yet
                    pass


def thread_change_url(thread: ListingChatThread) -> str:
    """Deep link from the inbox straight to the item's admin edit screen."""
    model = thread.content_type.model_class()
    if model is None:
        return ""
    from django.contrib.contenttypes.models import ContentType

    return reverse(
        f"admin:{thread.content_type.app_label}_{thread.content_type.model}_change",
        args=[thread.object_id],
    )


# ---------------------------------------------------------------------------
# 1. Executive command centre
# ---------------------------------------------------------------------------


def _recent_chat_rows():
    """Latest threads with unread customer messages, enriched for the dashboard."""
    attachments = _thread_rows(
        ListingChatThread.objects.filter(
            messages__is_read=False, messages__sender_id=F("customer_id")
        )
        .select_related("customer", "content_type")
        .distinct()
        .order_by("-last_message_at", "-id")[:10]
    )
    _attach_item_details(attachments)
    return attachments


def dashboard(request):
    now = timezone.now()
    gmv = (
        Deal.objects.filter(
            status__in=[Deal.Status.ACCEPTED, Deal.Status.CANCELLATION_REQUESTED],
            agreed_price__isnull=False,
        ).aggregate(total=Sum("agreed_price"))["total"]
        or 0
    )

    context = {
        "title": "Command centre",
        "kpis": {
            "total_listings": Listing.objects.count(),
            "total_auctions": Auction.objects.count(),
            "pending_listings": Listing.objects.filter(
                status=Listing.Status.PENDING
            ).count(),
            "pending_auctions": Auction.objects.filter(
                moderation_status=Auction.ModerationStatus.PENDING
            ).count(),
            "unread_threads": ListingChatThread.unread_staff_threads(),
            "ending_soon": Auction.objects.filter(
                status=Auction.Status.LIVE,
                moderation_status=Auction.ModerationStatus.APPROVED,
                end_at__gt=now,
                end_at__lte=now + timedelta(hours=24),
            ).count(),
            "gmv": gmv,
            "open_disputes": DealCancellation.objects.filter(
                status__in=[
                    DealCancellation.Status.PENDING,
                    DealCancellation.Status.ESCALATED,
                ]
            ).count(),
            "suspended_items": (
                Listing.objects.filter(status=Listing.Status.SUSPENDED).count()
                + Auction.objects.filter(
                    moderation_status=Auction.ModerationStatus.SUSPENDED
                ).count()
            ),
        },
        "pending_listings": Listing.objects.filter(status=Listing.Status.PENDING)
        .select_related("seller", "category", "emirate")
        .order_by("submitted_at", "id")[:10],
        "pending_auctions": Auction.objects.filter(
            moderation_status=Auction.ModerationStatus.PENDING
        )
        .select_related("seller", "category", "emirate")
        .order_by("-submitted_at", "-id")[:10],
        "recent_chats": _recent_chat_rows(),
        "escalated": DealCancellation.objects.filter(
            status__in=[
                DealCancellation.Status.PENDING,
                DealCancellation.Status.ESCALATED,
            ]
        )
        .select_related("deal", "deal__seller", "deal__buyer", "requested_by")
        .order_by("-created_at")[:8],
    }
    return render(
        request,
        "admin/governance/dashboard.html",
        _each_context(request, **context),
    )


# ---------------------------------------------------------------------------
# 2. Centralised moderation inbox
# ---------------------------------------------------------------------------


def moderation_inbox(request):
    queryset = _thread_rows(
        ListingChatThread.objects.select_related("customer", "content_type").order_by(
            F("last_message_at").desc(nulls_last=True), "-id"
        )
    )

    chosen_view = request.GET.get("view", "attention")
    if chosen_view == "attention":
        # The view staff should land on: anything a customer is waiting on.
        queryset = queryset.filter(
            Q(status=ListingChatThread.Status.NEEDS_SELLER_REPLY)
            | Q(unread_customer_messages__gt=0)
        )
    elif chosen_view == "needs_reply":
        queryset = queryset.filter(status=ListingChatThread.Status.NEEDS_SELLER_REPLY)
    elif chosen_view == "open":
        queryset = queryset.filter(status=ListingChatThread.Status.OPEN)
    elif chosen_view == "resolved":
        queryset = queryset.filter(status=ListingChatThread.Status.RESOLVED)

    if request.GET.get("seller"):
        queryset = queryset.filter(customer_id=request.GET["seller"])

    item_type = request.GET.get("item_type")
    target_content_type = governance_services.content_type_for(item_type)
    if target_content_type is not None:
        queryset = queryset.filter(content_type=target_content_type)

    sellers = (
        User.objects.filter(governance_chat_threads__isnull=False)
        .distinct()
        .order_by("display_name", "phone")
    )
    paginator = Paginator(queryset, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    _attach_item_details(page.object_list)

    return render(
        request,
        "admin/governance/moderation_inbox.html",
        _each_context(
            request,
            title="Moderation inbox",
            threads=page,
            page_obj=page,
            sellers=sellers,
            item_type=item_type or "",
            chosen_view=chosen_view,
        ),
    )


# ---------------------------------------------------------------------------
# 3. Live auction & bid audit monitor
# ---------------------------------------------------------------------------


def _auction_flags(auction: Auction) -> list[str]:
    """Simple, explainable suspicious-bidding flags computed on real querysets."""
    flags: list[str] = []
    if auction.bids.filter(bidder_id=auction.seller_id).exists():
        flags.append("Seller bid on own auction")
    recent_cut = timezone.now() - timedelta(minutes=10)
    rapid = (
        auction.bids.filter(created_at__gte=recent_cut)
        .values("bidder_id")
        .annotate(count=Count("id"))
        .filter(count__gte=4)
        .count()
    )
    if rapid:
        flags.append(f"{rapid} bidder(s) with 4+ bids in 10 minutes")
    if auction.bids.exclude(bidder_id=auction.seller_id).values("bidder").annotate(
        count=Count("id")
    ).filter(count__gte=8).exists():
        flags.append("Very high single-bidder activity")
    return flags


def bid_monitor(request):
    auctions = (
        Auction.objects.filter(
            status__in=[Auction.Status.SCHEDULED, Auction.Status.LIVE, Auction.Status.LOCKED]
        )
        .select_related("seller", "emirate", "category")
        .annotate(
            bid_count=Count("bids"),
            top_bid=Max("bids__amount", filter=Q(bids__voided_at__isnull=True)),
            distinct_bidders=Count("bids__bidder", distinct=True),
        )
        .order_by("-status", "end_at")
    )
    rows = [(auction, _auction_flags(auction)) for auction in auctions]

    recently_voided = (
        Bid.objects.filter(voided_at__isnull=False)
        .select_related("auction", "bidder", "voided_by")
        .order_by("-voided_at")[:10]
    )
    return render(
        request,
        "admin/governance/bid_monitor.html",
        _each_context(
            request,
            title="Bid monitor",
            rows=rows,
            recently_voided=recently_voided,
        ),
    )


def bid_auction(request, auction_id: int):
    auction = get_object_or_404(
        Auction.objects.select_related("seller", "emirate", "service_area", "category"),
        pk=auction_id,
    )
    bids = auction.bids.select_related("bidder").order_by("-amount", "created_at", "id")

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            with transaction.atomic():
                if action == "void_bid":
                    bid = get_object_or_404(Bid, pk=request.POST.get("bid_id"), auction=auction)
                    auction_services.void_bid(
                        auction, bid, reviewer=request.user, request=request
                    )
                    messages.success(request, f"Bid #{bid.pk} voided.")
                elif action == "pause":
                    auction_services.pause_auction(auction, actor=request.user, request=request)
                    messages.success(request, "Auction paused.")
                elif action == "resume":
                    auction_services.resume_auction(auction, actor=request.user, request=request)
                    messages.success(request, "Auction resumed.")
                elif action == "extend":
                    hours = int(request.POST.get("hours", "24"))
                    auction_services.extend_auction(
                        auction, hours=hours, actor=request.user, request=request
                    )
                    messages.success(request, f"End time extended by {hours} h.")
                elif action == "cancel":
                    auction_services.cancel_auction(
                        auction,
                        actor=request.user,
                        reason=request.POST.get("reason", "Cancelled by admin"),
                        request=request,
                    )
                    messages.success(request, "Auction cancelled.")
                else:
                    raise PermissionDenied("Unknown action.")
        except (ValueError, PermissionDenied) as exc:
            messages.error(request, str(exc))
        except Exception as exc:  # noqa: BLE001 - surfaced to staff like admin actions do
            messages.error(request, f"Could not apply the action: {exc}")
        return redirect("admin:governance_bid_auction", auction_id=auction.pk)

    return render(
        request,
        "admin/governance/bid_auction.html",
        _each_context(
            request,
            title=f"Bid audit — {auction.reference}",
            auction=auction,
            bids=bids,
            flags=_auction_flags(auction),
        ),
    )


# ---------------------------------------------------------------------------
# 4. Vendor / seller KYC & store governance
# ---------------------------------------------------------------------------


def vendors(request):
    if request.method == "POST":
        vendor = get_object_or_404(User, pk=request.POST.get("vendor_id"))
        try:
            with transaction.atomic():
                if request.POST.get("action") == "suspend_vendor":
                    governance_services.suspend_vendor(vendor, actor=request.user, request=request)
                    messages.success(request, f"{vendor} suspended; active items delisted.")
                elif request.POST.get("action") == "restore_vendor":
                    governance_services.restore_vendor(vendor, actor=request.user, request=request)
                    messages.success(request, f"{vendor} restored to verified.")
                else:
                    raise PermissionDenied("Unknown action.")
        except Exception as exc:  # noqa: BLE001
            messages.error(request, f"Could not update the vendor: {exc}")
        return redirect("admin:governance_vendors")

    seller_listings = Count(
        "listings",
        filter=Q(listings__status__in=[
            Listing.Status.PENDING,
            Listing.Status.APPROVED,
            Listing.Status.CHANGES_REQUESTED,
        ]),
        distinct=True,
    )
    seller_auctions = Count(
        "auctions",
        filter=Q(auctions__moderation_status__in=[
            Auction.ModerationStatus.PENDING,
            Auction.ModerationStatus.APPROVED,
            Auction.ModerationStatus.CHANGES_REQUESTED,
        ]),
        distinct=True,
    )
    vendor_list = (
        User.objects.filter(is_active=True)
        .select_related("emirate")
        .annotate(
            active_listings=seller_listings,
            active_auctions=seller_auctions,
            chat_threads=Count("governance_chat_threads", distinct=True),
        )
        .order_by("-active_listings", "-active_auctions", "-id")
    )

    status = request.GET.get("status")
    if status in {"pending", "verified", "suspended"}:
        vendor_list = vendor_list.filter(seller_status=status)
    search = (request.GET.get("q") or "").strip()
    if search:
        vendor_list = vendor_list.filter(
            Q(display_name__icontains=search) | Q(phone__icontains=search.replace(" ", ""))
        )
    paginator = Paginator(vendor_list, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "admin/governance/vendors.html",
        _each_context(
            request,
            title="Vendor governance",
            vendors=page,
            page_obj=page,
            status=status or "",
        ),
    )


# ---------------------------------------------------------------------------
# 5. Financials, escrow & dispute resolution view
# ---------------------------------------------------------------------------


def financials(request):
    if request.method == "POST":
        cancellation = get_object_or_404(
            DealCancellation.objects.select_related("deal"),
            pk=request.POST.get("cancellation_id"),
        )
        from deals import services as deal_services

        accept = request.POST.get("decision") == "accept"
        try:
            with transaction.atomic():
                if accept:
                    deal_services.finalize_cancellation(
                        cancellation.deal,
                        actor=request.user,
                        note=request.POST.get("note", ""),
                        request=request,
                    )
                else:
                    deal_services.staff_reject_cancellation(
                        cancellation,
                        actor=request.user,
                        note=request.POST.get("note", ""),
                        request=request,
                    )
        except Exception as exc:  # noqa: BLE001
            messages.error(request, f"Could not resolve the dispute: {exc}")
        else:
            messages.success(
                request,
                "Cancellation approved." if accept else "Deal restored; dispute closed.",
            )
        return redirect("admin:governance_financials")

    now = timezone.now()
    month_cut = now - timedelta(days=180)
    monthly = (
        Deal.objects.filter(
            status__in=[Deal.Status.ACCEPTED, Deal.Status.CANCELLATION_REQUESTED],
            agreed_price__isnull=False,
            accepted_at__isnull=False,
            accepted_at__gte=month_cut,
        )
        .values("accepted_at__month")
        .annotate(
            deals=Count("id"),
            value=Sum("agreed_price"),
        )
        .order_by("-accepted_at__month")[:6]
    )

    deals = (
        Deal.objects.select_related(
            "listing", "auction", "buyer", "seller", "accepted_offer"
        )
        .order_by("-accepted_at", "-id")[:25]
    )
    disputes = (
        DealCancellation.objects.select_related(
            "deal", "deal__buyer", "deal__seller", "requested_by"
        )
        .order_by("-created_at")[:25]
    )
    return render(
        request,
        "admin/governance/financials.html",
        _each_context(
            request,
            title="Deals, escrow & disputes",
            monthly=monthly,
            gmv=(
                Deal.objects.filter(
                    status__in=[Deal.Status.ACCEPTED, Deal.Status.CANCELLATION_REQUESTED],
                    agreed_price__isnull=False,
                ).aggregate(total=Sum("agreed_price"))["total"]
                or 0
            ),
            deals=deals,
            disputes=disputes,
        ),
    )
