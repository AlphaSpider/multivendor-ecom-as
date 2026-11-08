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

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Max, OuterRef, Q, Subquery, Sum, TextField, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.models import User
from auctions import services as auction_services
from auctions.forms import StaffAuctionForm
from auctions.models import Auction, AuctionApplication, AuctionApplicationImage, AuctionImage, Bid
from catalog.models import Listing
from core.exceptions import DomainError
from core.images import validate_image_upload
from core.models import AuditEvent
from deals.models import Deal, DealCancellation, DeliveryRequest
from governance import services as governance_services
from governance.models import ListingChatMessage, ListingChatThread
from locations.models import Emirate

APPLICATION_QUEUE_STATUSES = (
    AuctionApplication.Status.SUBMITTED,
    AuctionApplication.Status.IN_REVIEW,
)


def _require_perm(request, perm: str) -> None:
    if not request.user.has_perm(perm):
        raise PermissionDenied("You do not have permission for this action.")

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
    top_wishlisted = (
        Listing.objects.filter(wishlist_items__isnull=False)
        .annotate(wishlist_total=Count("wishlist_items"))
        .select_related("category", "emirate", "seller")
        .prefetch_related("images")
        .order_by("-wishlist_total", "-published_at", "-id")[:10]
    )
    open_delivery_requests = DeliveryRequest.objects.exclude(
        status=DeliveryRequest.Status.DELIVERED
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
            "pending_applications": AuctionApplication.objects.filter(
                status__in=APPLICATION_QUEUE_STATUSES
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
            "open_delivery_requests": open_delivery_requests.count(),
        },
        "pending_listings": Listing.objects.filter(status=Listing.Status.PENDING)
        .select_related("seller", "category", "emirate")
        .order_by("submitted_at", "id")[:10],
        "pending_auctions": Auction.objects.filter(
            moderation_status=Auction.ModerationStatus.PENDING
        )
        .select_related("seller", "category", "emirate")
        .order_by("-submitted_at", "-id")[:10],
        "pending_applications": AuctionApplication.objects.filter(
            status__in=APPLICATION_QUEUE_STATUSES
        )
        .select_related("applicant", "category", "emirate", "service_area")
        .prefetch_related("images")
        .order_by("submitted_at", "id")[:10],
        "top_wishlisted": top_wishlisted,
        "delivery_requests": (
            open_delivery_requests.select_related(
                "listing",
                "listing__emirate",
                "listing__service_area",
                "buyer",
                "seller",
            )
            .prefetch_related("listing__images")
            .order_by("-created_at", "-id")[:8]
        ),
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
# 2. Auction application queue and staff listing workflow
# ---------------------------------------------------------------------------


def _application_history(application):
    return AuditEvent.objects.filter(
        entity_type="auctions.auctionapplication", entity_id=str(application.pk)
    ).select_related("actor")[:40]


def auction_applications(request):
    _require_perm(request, "auctions.change_auctionapplication")
    queryset = AuctionApplication.objects.select_related(
        "applicant", "emirate", "service_area", "category", "auction", "reviewed_by"
    ).prefetch_related("images")

    status = (request.GET.get("status") or "queue").strip()
    if status == "queue":
        queryset = queryset.filter(
            status__in=[
                AuctionApplication.Status.SUBMITTED,
                AuctionApplication.Status.IN_REVIEW,
            ]
        )
    elif status == "changes_requested":
        queryset = queryset.filter(
            status=AuctionApplication.Status.CHANGES_REQUESTED
        )
    elif status == "listed":
        queryset = queryset.filter(status=AuctionApplication.Status.LISTED)
    elif status == "rejected":
        queryset = queryset.filter(status=AuctionApplication.Status.REJECTED)
    elif status != "all":
        status = "queue"
        queryset = queryset.filter(
            status__in=[
                AuctionApplication.Status.SUBMITTED,
                AuctionApplication.Status.IN_REVIEW,
            ]
        )

    search = (request.GET.get("q") or "").strip()
    if search:
        queryset = queryset.filter(
            Q(title__icontains=search)
            | Q(applicant__phone__icontains=search.replace(" ", ""))
            | Q(applicant__display_name__icontains=search)
            | Q(pk__in=[int(search)] if search.isdigit() else [])
        )
    emirate_id = request.GET.get("emirate")
    if emirate_id and str(emirate_id).isdigit():
        queryset = queryset.filter(emirate_id=int(emirate_id))

    queryset = queryset.order_by("submitted_at", "id")
    paginator = Paginator(queryset, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    counts = {
        "queue": AuctionApplication.objects.filter(
            status__in=[
                AuctionApplication.Status.SUBMITTED,
                AuctionApplication.Status.IN_REVIEW,
            ]
        ).count(),
        "changes_requested": AuctionApplication.objects.filter(
            status=AuctionApplication.Status.CHANGES_REQUESTED
        ).count(),
        "listed": AuctionApplication.objects.filter(
            status=AuctionApplication.Status.LISTED
        ).count(),
        "rejected": AuctionApplication.objects.filter(
            status=AuctionApplication.Status.REJECTED
        ).count(),
    }
    return render(
        request,
        "admin/governance/auction_applications.html",
        _each_context(
            request,
            title="Auction applications",
            applications=page,
            page_obj=page,
            status=status,
            counts=counts,
            search=search,
            emirates=Emirate.objects.order_by("display_order", "name"),
            emirate_id=emirate_id or "",
        ),
    )


def auction_application_review(request, application_id: int):
    _require_perm(request, "auctions.change_auctionapplication")
    application = get_object_or_404(
        AuctionApplication.objects.select_related(
            "applicant", "emirate", "service_area", "category", "auction", "reviewed_by"
        ).prefetch_related("images"),
        pk=application_id,
    )

    if request.method == "POST":
        action = request.POST.get("action")
        note = (request.POST.get("decision_note") or "").strip()
        try:
            with transaction.atomic():
                if action == "start_review":
                    auction_services.start_application_review(
                        application, actor=request.user, request=request
                    )
                    messages.success(request, "Application marked as in review.")
                elif action == "request_changes":
                    auction_services.request_application_changes(
                        application,
                        actor=request.user,
                        note=note,
                        request=request,
                    )
                    messages.success(request, "Changes requested from the applicant.")
                elif action == "reject":
                    auction_services.reject_application(
                        application,
                        actor=request.user,
                        note=note,
                        request=request,
                    )
                    messages.success(request, "Application rejected.")
                elif action == "save_notes":
                    auction_services.save_application_notes(
                        application,
                        actor=request.user,
                        notes=request.POST.get("admin_notes", ""),
                        request=request,
                    )
                    messages.success(request, "Internal notes saved.")
                else:
                    raise PermissionDenied("Unknown action.")
        except (ValidationError, DomainError) as exc:
            if hasattr(exc, "message_dict"):
                messages.error(
                    request,
                    " ".join(
                        error
                        for errors in exc.message_dict.values()
                        for error in errors
                    ),
                )
            else:
                messages.error(
                    request,
                    getattr(exc, "message", None) or " ".join(exc.messages),
                )
        return redirect(
            "admin:governance_auction_application_review", application_id=application.pk
        )

    # Opening a submitted application puts it into the explicit in-review state.
    if application.status == AuctionApplication.Status.SUBMITTED:
        try:
            with transaction.atomic():
                application = auction_services.start_application_review(
                    application, actor=request.user, request=request
                )
        except (ValidationError, DomainError):
            messages.warning(
                request, "The application could not be marked as in review."
            )

    return render(
        request,
        "admin/governance/auction_application_review.html",
        _each_context(
            request,
            title=f"Application {application.reference}",
            application=application,
            images=application.images.all(),
            history=_application_history(application),
            can_list=request.user.has_perm("auctions.add_auction")
            and application.status
            in (
                AuctionApplication.Status.SUBMITTED,
                AuctionApplication.Status.IN_REVIEW,
                AuctionApplication.Status.CHANGES_REQUESTED,
            ),
        ),
    )


def _application_form_initial(application, auction) -> dict:
    now = timezone.localtime(timezone.now()).replace(second=0, microsecond=0)
    initial = {
        "start_at": now,
        "end_at": now + timedelta(days=3),
    }
    if auction is not None:
        initial.update(
            {
                "seller": auction.seller_id,
                "title": auction.title,
                "description": auction.description,
                "category": auction.category_id,
                "condition": auction.condition,
                "emirate": auction.emirate_id,
                "service_area": auction.service_area_id,
                "start_at": timezone.localtime(auction.start_at).replace(
                    second=0, microsecond=0
                ),
                "end_at": timezone.localtime(auction.end_at).replace(
                    second=0, microsecond=0
                ),
                "reserve_price": auction.reserve_price,
            }
        )
    elif application is not None:
        initial.update(
            {
                "seller": application.applicant_id,
                "title": application.title,
                "description": application.description,
                "category": application.category_id,
                "condition": application.condition,
                "emirate": application.emirate_id,
                "service_area": application.service_area_id,
                "reserve_price": application.proposed_starting_bid,
            }
        )
        if application.preferred_start_at and application.preferred_end_at:
            initial["start_at"] = timezone.localtime(
                application.preferred_start_at
            ).replace(second=0, microsecond=0)
            initial["end_at"] = timezone.localtime(
                application.preferred_end_at
            ).replace(second=0, microsecond=0)
    return initial


def _image_ids_from_post(request) -> list[int]:
    ids = []
    for raw in request.POST.getlist("remove_images"):
        if str(raw).isdigit():
            ids.append(int(raw))
    return ids


def _apply_staff_image_changes(
    *, request, auction=None, application=None, uploads=()
):
    """Apply cover/remove/upload operations for the relevant image collection."""
    remove_ids = _image_ids_from_post(request)
    if auction is not None:
        if remove_ids:
            for image in auction.images.filter(pk__in=remove_ids):
                auction_services.delete_auction_image(
                    auction, image, actor=request.user
                )
        for uploaded in uploads:
            auction_services.add_auction_image(
                auction, uploaded, actor=request.user
            )
        cover_id = request.POST.get("cover_image", "")
        if cover_id.isdigit():
            cover = auction.images.filter(pk=int(cover_id)).first()
            if cover is not None and not cover.is_cover:
                auction_services.set_auction_cover(auction, cover, actor=request.user)
    elif application is not None:
        if remove_ids:
            for image in application.images.filter(pk__in=remove_ids):
                auction_services.delete_application_image(
                    application, image, actor=request.user
                )
        for uploaded in uploads:
            auction_services.add_application_image(
                application, uploaded, actor=request.user
            )
        cover_id = request.POST.get("cover_image", "")
        if cover_id.isdigit():
            cover = application.images.filter(pk=int(cover_id)).first()
            if cover is not None and not cover.is_cover:
                auction_services.set_application_cover(
                    application, cover, actor=request.user
                )


def _count_remaining_images(collection, request) -> int:
    remove_ids = set(_image_ids_from_post(request))
    return sum(1 for image in collection if image.pk not in remove_ids)


def staff_auction_form(request, application_id=None, auction_id=None):
    _require_perm(
        request,
        "auctions.change_auction" if auction_id is not None else "auctions.add_auction",
    )
    application = None
    auction = None
    if application_id is not None:
        application = get_object_or_404(
            AuctionApplication.objects.select_related(
                "applicant", "emirate", "service_area", "category", "auction"
            ).prefetch_related("images"),
            pk=application_id,
        )
        if application.status in (
            AuctionApplication.Status.REJECTED,
            AuctionApplication.Status.WITHDRAWN,
        ):
            messages.error(request, "This application is closed.")
            return redirect(
                "admin:governance_auction_application_review",
                application_id=application.pk,
            )
        if (
            application.status == AuctionApplication.Status.LISTED
            and application.auction_id
        ):
            return redirect(
                "admin:governance_auction_edit", auction_id=application.auction_id
            )
        auction = application.auction
    elif auction_id is not None:
        auction = get_object_or_404(
            Auction.objects.select_related(
                "seller", "emirate", "service_area", "category"
            ).prefetch_related("images"),
            pk=auction_id,
        )
    else:
        _require_perm(request, "auctions.add_auction")

    fixed_emirate = (
        application.emirate
        if application is not None
        else auction.emirate
        if auction is not None
        else None
    )
    fixed_seller = (
        application.applicant
        if application is not None
        else auction.seller
        if auction is not None
        else None
    )
    initial = _application_form_initial(application, auction)

    if request.method == "POST":
        form = StaffAuctionForm(
            request.POST,
            request.FILES,
            fixed_emirate=fixed_emirate,
            fixed_seller=fixed_seller,
        )
        uploads = request.FILES.getlist("images")
        image_errors = []
        for index, uploaded in enumerate(uploads, start=1):
            try:
                validate_image_upload(uploaded)
            except ValidationError as exc:
                image_errors.append(f"Photo {index}: {' '.join(exc.messages)}")
        for error in image_errors:
            form.add_error("images", error)

        action = request.POST.get("action", "publish")
        publish = action != "draft"

        if form.is_valid() and not image_errors:
            try:
                with transaction.atomic():
                    if application is not None:
                        if auction is None:
                            # Manage the application image set first; the publishing
                            # service copies the final set to the new auction.
                            _apply_staff_image_changes(
                                request=request,
                                application=application,
                                uploads=uploads,
                            )
                            if (
                                publish
                                and application.images.count()
                                < settings.MIN_IMAGES_PER_LISTING
                            ):
                                raise ValidationError(
                                    {
                                        "images": (
                                            f"Add at least "
                                            f"{settings.MIN_IMAGES_PER_LISTING} photos "
                                            "before publishing."
                                        )
                                    }
                                )
                            auction = auction_services.create_auction_from_application(
                                application,
                                actor=request.user,
                                title=form.cleaned_data["title"],
                                description=form.cleaned_data["description"],
                                category=form.cleaned_data["category"],
                                condition=form.cleaned_data["condition"],
                                service_area=form.cleaned_data["service_area"],
                                start_at=form.cleaned_data["start_at"],
                                end_at=form.cleaned_data["end_at"],
                                reserve_price=form.cleaned_data["reserve_price"],
                                publish=publish,
                                allow_suspended=form.cleaned_data.get(
                                    "allow_suspended", False
                                ),
                                request=request,
                            )
                        else:
                            _apply_staff_image_changes(
                                request=request,
                                auction=auction,
                                uploads=uploads,
                            )
                            if (
                                publish
                                and auction.images.count()
                                < settings.MIN_IMAGES_PER_LISTING
                            ):
                                raise ValidationError(
                                    {
                                        "images": (
                                            f"Add at least "
                                            f"{settings.MIN_IMAGES_PER_LISTING} photos "
                                            "before publishing."
                                        )
                                    }
                                )
                            auction = auction_services.create_auction_from_application(
                                application,
                                actor=request.user,
                                title=form.cleaned_data["title"],
                                description=form.cleaned_data["description"],
                                category=form.cleaned_data["category"],
                                condition=form.cleaned_data["condition"],
                                service_area=form.cleaned_data["service_area"],
                                start_at=form.cleaned_data["start_at"],
                                end_at=form.cleaned_data["end_at"],
                                reserve_price=form.cleaned_data["reserve_price"],
                                publish=publish,
                                allow_suspended=form.cleaned_data.get(
                                    "allow_suspended", False
                                ),
                                request=request,
                            )
                    elif auction is not None:
                        _apply_staff_image_changes(
                            request=request, auction=auction, uploads=uploads
                        )
                        if (
                            publish
                            and auction.images.count()
                            < settings.MIN_IMAGES_PER_LISTING
                        ):
                            raise ValidationError(
                                {
                                    "images": (
                                        f"Add at least "
                                        f"{settings.MIN_IMAGES_PER_LISTING} photos "
                                        "before publishing."
                                    )
                                }
                            )
                        auction = auction_services.update_auction(
                            auction,
                            actor=request.user,
                            title=form.cleaned_data["title"],
                            description=form.cleaned_data["description"],
                            category=form.cleaned_data["category"],
                            condition=form.cleaned_data["condition"],
                            service_area=form.cleaned_data["service_area"],
                            start_at=form.cleaned_data["start_at"],
                            end_at=form.cleaned_data["end_at"],
                            reserve_price=form.cleaned_data["reserve_price"],
                            agree_fallback=True,
                            staff_publish=True if publish else False,
                            request=request,
                        )
                    else:
                        if publish and len(uploads) < settings.MIN_IMAGES_PER_LISTING:
                            raise ValidationError(
                                {
                                    "images": (
                                        f"Add at least "
                                        f"{settings.MIN_IMAGES_PER_LISTING} photos "
                                        "before publishing."
                                    )
                                }
                            )
                        auction = auction_services.create_auction(
                            seller=form.cleaned_data["seller"],
                            emirate=form.cleaned_data["emirate"],
                            title=form.cleaned_data["title"],
                            description=form.cleaned_data["description"],
                            category=form.cleaned_data["category"],
                            condition=form.cleaned_data["condition"],
                            service_area=form.cleaned_data["service_area"],
                            start_at=form.cleaned_data["start_at"],
                            end_at=form.cleaned_data["end_at"],
                            reserve_price=form.cleaned_data["reserve_price"],
                            agree_fallback=True,
                            submit_for_review=False,
                            approved_by=request.user if publish else None,
                            request=request,
                        )
                        _apply_staff_image_changes(
                            request=request, auction=auction, uploads=uploads
                        )
            except ValidationError as exc:
                if hasattr(exc, "message_dict"):
                    for field, errors in exc.message_dict.items():
                        form.add_error(field if field in form.fields else None, errors)
                else:
                    form.add_error(None, " ".join(exc.messages))
            except DomainError as exc:
                form.add_error(None, exc.message)
            else:
                if publish:
                    messages.success(
                        request,
                        f"{auction.reference} is listed and public."
                        if application is None
                        else f"Application {application.reference} is now listed as {auction.reference}.",
                    )
                else:
                    messages.success(
                        request,
                        f"Draft auction {auction.reference} saved. It is not public yet.",
                    )
                if application is not None:
                    return redirect(
                        "admin:governance_auction_application_review",
                        application_id=application.pk,
                    )
                return redirect(
                    "admin:moderation_auction_review", auction_id=auction.pk
                )
    else:
        form = StaffAuctionForm(
            initial=initial,
            fixed_emirate=fixed_emirate,
            fixed_seller=fixed_seller,
        )

    if auction is not None:
        image_source = "auction"
        gallery_images = list(auction.images.all())
    elif application is not None:
        image_source = "application"
        gallery_images = list(application.images.all())
    else:
        image_source = ""
        gallery_images = []
    return render(
        request,
        "admin/governance/auction_form.html",
        _each_context(
            request,
            title=(
                f"List application {application.reference}"
                if application is not None
                else f"Edit {auction.reference}"
                if auction is not None
                else "Create auction"
            ),
            form=form,
            application=application,
            auction=auction,
            gallery_images=gallery_images,
            image_source=image_source,
            min_images=settings.MIN_IMAGES_PER_LISTING,
            max_images=settings.MAX_IMAGES_PER_LISTING,
        ),
    )


# ---------------------------------------------------------------------------
# 3. Centralised moderation inbox
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
    _require_perm(request, "auctions.change_auction")
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
                elif action == "lock_bid":
                    raw_bid_id = request.POST.get("bid_id", "")
                    if not str(raw_bid_id).isdigit():
                        raise ValidationError("Choose a valid bid to lock.")
                    selection = auction_services.staff_lock_bid(
                        auction,
                        actor=request.user,
                        bid_id=int(raw_bid_id),
                        request=request,
                    )
                    messages.success(
                        request,
                        f"Bid of AED {selection.bid.amount} locked. The bidder has "
                        f"until {timezone.localtime(selection.confirmation_deadline):%d %b %H:%M} "
                        "to confirm.",
                    )
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
        except (ValueError, ValidationError, PermissionDenied) as exc:
            messages.error(request, str(exc))
        except DomainError as exc:
            messages.error(request, exc.message)
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


# ---------------------------------------------------------------------------
# 6. Delivery requests / fulfilment coordination
# ---------------------------------------------------------------------------


def delivery_requests(request):
    """Products buyers have asked ManzilCart to collect and deliver."""
    if request.method == "POST":
        _require_perm(request, "deals.change_deliveryrequest")
        raw_delivery_id = (request.POST.get("delivery_id") or "").strip()
        delivery = get_object_or_404(
            DeliveryRequest.objects.select_related("listing__emirate"),
            pk=raw_delivery_id if raw_delivery_id.isdigit() else None,
        )
        from deals import services as deal_services

        new_status = (request.POST.get("status") or "").strip()
        try:
            with transaction.atomic():
                updated = deal_services.update_delivery_status(
                    delivery,
                    actor=request.user,
                    status=new_status,
                    request=request,
                )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        except DomainError as exc:
            messages.error(request, exc.message)
        except Exception as exc:  # noqa: BLE001 - surface like other admin actions
            messages.error(request, f"Could not update the delivery request: {exc}")
        else:
            messages.success(
                request,
                f"{updated.reference} marked as {updated.get_status_display()}.",
            )

        next_url = request.POST.get("next", "")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            {request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(next_url)
        return redirect("admin:governance_delivery_requests")

    status = (request.GET.get("status") or "active").strip().lower()
    queryset = DeliveryRequest.objects.select_related(
        "listing",
        "listing__emirate",
        "listing__service_area",
        "listing__category",
        "buyer",
        "seller",
        "status_updated_by",
    ).prefetch_related("listing__images")

    if status == "active":
        queryset = queryset.exclude(status=DeliveryRequest.Status.DELIVERED)
    elif status in DeliveryRequest.Status.values:
        queryset = queryset.filter(status=status)
    elif status != "all":
        status = "active"
        queryset = queryset.exclude(status=DeliveryRequest.Status.DELIVERED)

    search = (request.GET.get("q") or "").strip()
    if search:
        phone = search.replace(" ", "")
        query = (
            Q(listing__title__icontains=search)
            | Q(buyer__display_name__icontains=search)
            | Q(seller__display_name__icontains=search)
            | Q(buyer__phone__icontains=phone)
            | Q(seller__phone__icontains=phone)
        )
        reference_digits = ""
        if search.isdigit():
            reference_digits = search
        elif search.upper().startswith("MC-DL-"):
            reference_digits = search[6:]
        if reference_digits.isdigit():
            query |= Q(pk=int(reference_digits)) | Q(listing_id=int(reference_digits))
        queryset = queryset.filter(query)

    emirate_id = request.GET.get("emirate", "")
    if emirate_id.isdigit():
        queryset = queryset.filter(listing__emirate_id=int(emirate_id))

    queryset = queryset.order_by("-created_at", "-id")
    paginator = Paginator(queryset, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))

    counts = {
        "active": DeliveryRequest.objects.exclude(
            status=DeliveryRequest.Status.DELIVERED
        ).count(),
        "all": DeliveryRequest.objects.count(),
    }
    for value, _label in DeliveryRequest.Status.choices:
        counts[value] = DeliveryRequest.objects.filter(status=value).count()

    return render(
        request,
        "admin/governance/delivery_requests.html",
        _each_context(
            request,
            title="Delivery requests",
            delivery_requests=page,
            page_obj=page,
            status=status,
            counts=counts,
            search=search,
            emirates=Emirate.objects.order_by("display_order", "name"),
            emirate_id=emirate_id,
            status_choices=DeliveryRequest.Status.choices,
            can_update=request.user.has_perm("deals.change_deliveryrequest"),
        ),
    )
