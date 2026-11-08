# Redesign the ManzilCart Django Admin and Move Auction Creation to an Application Workflow

## Role

You are a senior product designer and Django front-end engineer. You will work on the existing ManzilCart marketplace.

This engagement has two connected workstreams:

1. **Workstream A — Auction workflow change:** Customers apply to auction an item. ManzilCart staff review the application, create the actual auction, and manage the auction lifecycle. Customers no longer create auctions directly.
2. **Workstream B — Admin visual redesign:** Redesign the staff-facing Django admin so it matches the customer-facing ManzilCart design language while remaining a fast, dense operations tool.

Implement Workstream A first with functional tests. Then apply the redesign to all existing and new screens.

## Project context

- Repository: `D:\Programming\My Project\ManzilAs\Manzilcart`
- Stack: Django 6.1.2, Django templates, vanilla CSS/JS, PostgreSQL.
- Customer-facing design system: `static/css/styles.css`.
- Customer-facing base template: `templates/base.html`.
- Customer auction templates: `templates/auctions/`.
- Admin shell: `governance/templates/admin/base_site.html`.
- Admin design system: `governance/static/governance/admin.css`.
- Admin site: `governance/admin_site.py`.
- Admin custom pages: `governance/admin_views.py` and `governance/templates/admin/governance/`.
- Auction domain: `auctions/models.py`, `auctions/forms.py`, `auctions/views.py`, `auctions/services.py`, `auctions/urls.py`, `auctions/urls_marketplace.py`.
- Auction admin: `moderation/admin.py` (currently contains `AuctionAdmin`).
- Moderation review screen: `templates/admin/moderation/review.html`.
- Chat panel: `governance/templates/admin/governance/chat_panel.html`.
- Client assets: `static/images/logo.png` for light backgrounds, `static/images/logo-dark.png` for dark backgrounds, `static/images/logo-mark.png`.

Read these files before making changes. Also read `prompt and doc/prompt.md` for product context, but do not redesign product workflows beyond the auction-application change described here.

## Current auction behavior — as-is

Today:

- Customers visit `/<emirate_slug>/auctions/new/` and create a standalone auction directly.
- The form collects title, description, category, condition, service area, start/end times, a minimum/starting amount, fallback consent, and images.
- Customers can save a draft or submit the auction for review.
- Customers can edit their auction before bidding starts.
- Customers can lock the highest eligible bid before the auction ends.
- Customers can cancel before bids exist.
- Staff currently moderate auctions, pause/resume/extend/cancel them, audit bids, and chat with sellers.
- Bidders place bids and the selected bidder confirms or declines. That bidding and confirmation workflow must remain intact.

## Required auction behavior — to-be

### Core rule

**Customers submit auction applications. Staff create, list, and manage the actual auctions.**

The customer must never be able to create an `Auction` directly after this change.

### Customer flow

1. The customer chooses **“Apply for auction”** from:
   - The auction board.
   - Their account dashboard.
   - Their auctions/applications page.
2. The application form collects:
   - Item title.
   - Description.
   - Category.
   - Condition.
   - Emirate, derived from the URL and locked to the active marketplace.
   - Service area.
   - **Proposed starting bid / minimum amount in AED.**
   - Preferred start and end time, optional.
   - At least the minimum required product images, up to the maximum, using the existing image validation rules.
   - Acknowledgement that ManzilCart will review the item, set the final schedule and starting price, and list it if approved.
   - Acknowledgement of the existing fallback rule: if the selected bidder declines or times out, the auction may be offered to the next highest eligible bidder.
3. The customer submits the application.
4. **No auction is created at submission time.** The application is stored with a status such as `submitted`.
5. The customer sees a confirmation page or message:
   - “Your auction application has been received.”
   - “ManzilCart will review it and contact you if anything needs changing.”
6. The customer can view their applications and their statuses from **My auction applications**.
7. The customer can edit and resubmit an application only when staff have requested changes.
8. Once listed, the customer sees the live public auction and its status, but does not get seller-side edit, lock, or cancel controls.

### Admin flow

Add a new admin/governance area: **Auction applications**.

- It appears in the governance navigation and on the command centre.
- It shows a queue of applications needing review, with counts.
- The review screen shows:
  - Applicant name, phone, email, seller status, and verification state.
  - Item title, description, category, condition, emirate, and service area.
  - All uploaded images, including a cover image.
  - Proposed starting bid.
  - Preferred schedule, if provided.
  - Submission time.
  - Application status and status history.
  - Applicant-visible message and internal-only admin notes, kept separate.
- Actions:
  - **Mark in review** when staff open an application.
  - **Request changes** with a reason shown to the applicant.
  - **Reject** with a reason shown to the applicant.
  - **Create and list auction** from the application.
  - **Save admin notes** without changing the applicant-visible status.
- “Create and list auction” opens a pre-filled admin auction form:
  - Applicant is the seller.
  - Emirate and service area are pre-filled.
  - Title, description, category, condition, and images are pre-filled.
  - Proposed starting bid is pre-filled as the starting/minimum amount.
  - Staff choose the final start and end times.
  - Staff may adjust the final starting price and content before listing.
  - Staff can add, remove, or reorder images.
  - Staff can save a draft or publish the auction.
- When staff publish:
  - Exactly one `Auction` is created for that application.
  - `Auction.seller` is the applicant.
  - Images are copied or safely associated with the new auction.
  - The auction is created with `moderation_status = approved`.
  - `approved_by` and `approved_at` record the acting staff member.
  - The existing lifecycle worker opens the auction at its start time.
  - The application status becomes `listed` and links to the created auction.
  - An audit event is recorded.
  - The applicant is notified.
  - The operation is atomic and idempotent; double-clicking or retrying must never create two auctions.
- Staff can also create an auction directly without an application, for operational use.
- Staff can edit an auction before its first bid, using the same lifecycle guards that protect seller edits today. Once bidding starts, content is frozen.
- Staff can lock the highest eligible bid from the admin, using the existing lock/selection service. The bidder still confirms or declines.
- Staff can pause, resume, extend, or cancel an auction. These controls already exist and must keep working.
- Staff must not place bids through the admin.

### Customer “My auctions” area

Redesign the current customer auctions page into a combined view:

- **My auction applications:** status, submitted date, proposed starting bid, admin message, edit/resubmit action when changes are requested, and a link to the live auction when listed.
- **Auctions I am bidding on:** existing bid/selection cards, confirmation actions, and auction links.
- Remove every “Create Auction” call to action from customer-facing pages.
- Replace it with “Apply for auction.”
- Keep a redirect from the old create URL so old bookmarks do not break, but do not leave a direct customer auction-creation path active.

### Permissions and security

- Only an authenticated, seller-eligible, mobile-verified customer can submit an auction application.
- An applicant can see only their own applications.
- Staff can see all applications.
- Only staff with the appropriate auction permissions can create or list auctions.
- Status changes must be POST-only and CSRF-protected.
- Applicants must not be able to set status, reviewer fields, the linked auction, or internal admin notes.
- Validate images with the existing upload and sanitisation rules.
- Validate the service area against the emirate using the existing geography validation.
- Prevent cross-emirate applications.
- Reuse existing rate limiting for public form submissions.
- Do not expose draft, rejected, or unlisted applications publicly.
- Do not allow a suspended or ineligible seller to be listed without an explicit staff decision.

### Data model

Use a dedicated application model. Recommended names:

- `AuctionApplication`
- `AuctionApplicationImage`

`AuctionApplication` should include at least:

- Applicant foreign key.
- Emirate foreign key.
- Service area foreign key.
- Title.
- Description.
- Category.
- Condition.
- Proposed starting bid / minimum amount.
- Preferred start and end time, optional.
- Status:
  - `draft`
  - `submitted`
  - `in_review`
  - `changes_requested`
  - `listed`
  - `rejected`
  - `withdrawn`
- Applicant-visible message.
- Internal admin notes.
- Reviewed by / reviewed at.
- Submitted at.
- Linked auction, as a nullable one-to-one relationship.
- Created and updated timestamps.

`AuctionApplicationImage` should include:

- Application foreign key.
- Image.
- Caption.
- Cover flag.
- Display order.
- Width, height, and byte size.
- Created timestamp.

Constraints:

- Exactly one cover image per application.
- Starting bid must be at least AED 1.00.
- A service area must belong to its emirate.
- One application can produce at most one auction.
- Do not add a required foreign key from `Auction` to the application. Link from the application to the auction so existing auctions remain valid.

### Notifications and audit

Extend the existing notification and audit systems; do not build a parallel system.

Add notification kinds or equivalent values for:

- Application received.
- Changes requested.
- Application rejected.
- Application listed / auction published.

Use the existing `queue_notification` flow and mock/live modes. Do not send live messages in mock mode.

Add audit events for:

- Application submitted.
- Application reviewed.
- Changes requested.
- Application rejected.
- Auction created from an application.
- Auction listed by staff.

Record the acting staff member, applicant, emirate, and relevant application/auction identifiers.

### Existing data and compatibility

- Existing seller-created auctions must remain valid.
- Existing auctions must not be deleted, migrated away, or lose their bids, selections, deals, images, or audit history.
- Existing pending auctions remain reviewable through the existing moderation screen.
- Existing seller-created auctions become admin-managed for future lifecycle actions.
- The public auction board and bidder experience must remain backward-compatible.
- The existing deal/award flow remains unchanged.

## Visual redesign — overall direction

The storefront is warm, spacious, and brand-led. The admin must translate that language, not copy consumer layouts literally.

Do not turn the admin into a marketing landing page. It must remain:

- Information-dense.
- Fast to scan.
- Keyboard-friendly.
- Predictable for daily staff use.
- Optimised for tables, queues, decisions, and exceptions.

Use the storefront’s typography, red/white/black palette, rounded surfaces, pill controls, subtle shadows, and warm neutral backgrounds. Use red sparingly: primary actions, active navigation, urgent attention states, and selected controls. Do not make the whole interface red.

## Visual design tokens

Use these as admin-scoped tokens, adapted from the storefront:

```css
--mc-brand: #dc2626;
--mc-brand-strong: #991b1b;
--mc-brand-soft: #fef2f2;
--mc-brand-glow: rgba(220, 38, 38, 0.22);
--mc-ink: #111111;
--mc-ink-soft: #262626;
--mc-muted: #6b7280;
--mc-bg: #fafafa;
--mc-surface: #ffffff;
--mc-sand: #f4f4f5;
--mc-sand-deep: #e4e4e7;
--mc-line: #e5e7eb;
--mc-radius-sm: 10px;
--mc-radius: 16px;
--mc-radius-lg: 24px;
--mc-shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.06), 0 2px 8px rgba(0, 0, 0, 0.04);
--mc-shadow: 0 10px 30px rgba(0, 0, 0, 0.10);
Dark mode tokens:

--mc-brand: #ef4444;
--mc-brand-strong: #f87171;
--mc-brand-soft: rgba(239, 68, 68, 0.16);
--mc-ink: #f9fafb;
--mc-ink-soft: #e5e7eb;
--mc-muted: #9ca3af;
--mc-bg: #09090b;
--mc-surface: #18181b;
--mc-sand: #27272a;
--mc-sand-deep: #3f3f46;
--mc-line: #27272a;
Bridge the stock Django admin variables to these tokens where it is safe to do so, so Django’s own widgets inherit the brand. Keep using Django’s variables for components that rely on them.

The current admin uses a blue accent (#0ea5e9). Remove it. Blue may remain only as a restrained informational status colour if necessary, but it must not compete with the ManzilCart red.

Typography
UI font: Plus Jakarta Sans, with the same system fallbacks as the storefront.
Arabic/RTL fallback: Cairo.
Body text: approximately 16px.
Dense admin table text: approximately 13–14px.
Headings: 700 weight, tight letter spacing, matching the storefront.
Prices, counts, and IDs: font-variant-numeric: tabular-nums.
Keep the admin primarily English. Use CSS logical properties so the layout remains RTL-safe.
Shape and depth
Cards and panels: 16px radius.
Small controls and thumbnails: 10px radius.
Buttons, filters, and status badges: fully rounded pills.
Thin borders first; subtle shadows second.
Avoid heavy glows or oversized floating cards.
Dark mode should use borders and surface separation more than shadows.
Shell and navigation
Create an app shell that feels related to the storefront header:

Sticky top header, approximately 64–72px tall.
Light, slightly translucent background with a blur, matching the storefront header.
ManzilCart logo on the left, linking to /admin/.
A small “Governance” or “Operations” label next to the logo.
Primary navigation: Command centre, Auction applications, Moderation inbox, Bid monitor, Vendors, Deals & disputes.
Active navigation state must be obvious: red text, red underline or soft red pill, and aria-current="page".
Preserve the unread moderation badge from governance_unread_threads.
Add an auction-application count badge using a new governance context value.
Right side: user tools, “View site”, theme toggle, and POST logout.
Use logo.png on light backgrounds and logo-dark.png on dark backgrounds.
Keep Django’s sidebar navigation working and expose every registered model. Style it as a clean left rail with grouped sections, clear active states, and readable hover/focus states.
On mobile, keep navigation reachable via a horizontal scroll strip or an accessible drawer. Do not hide critical actions behind hover-only interactions.
Do not fork Django’s admin shell wholesale. Prefer base_site.html, scoped CSS, and targeted template overrides. Preserve Django’s admin JavaScript, blocks, CSRF handling, theme mechanism, and form behaviour.

Page-by-page redesign
1. Command centre dashboard
Keep the existing title “Command centre.”

KPI hierarchy:

Needs attention band

Auction applications awaiting review.
Fixed-price posts to review.
Auctions to review — existing legacy/direct submissions.
Messages needing attention.
Auctions ending in 24h.
Open disputes.
Suspended items.
Marketplace pulse band

Total fixed-price products.
Total auction products.
Agreed deal value (AED, thousands-separated).
Each KPI card should include:

A clear label.
A strong value.
A small line icon.
A subtle urgency treatment where appropriate.
A clickable destination that already exists.
Do not invent trends, percentages, charts, or historical comparisons.

Below the KPIs, present the queues as a responsive grid of cards:

Auction applications waiting for review.
Fixed-price posts waiting for review.
Auctions waiting for review.
Messages waiting on staff.
Open deal disputes.
Each panel should have:

A clear heading.
A count or severity treatment using real data only.
A table with thumbnail, item, seller/applicant/customer, time or preview, status pill, and action.
A useful empty state with an icon and a short message.
A subtle “View all” link where an existing destination exists.
Add a dedicated Auction applications queue page if the dashboard panel becomes too dense.

2. Auction applications queue
Segmented filters: Needs review, In review, Changes requested, Listed, Rejected, All.
Search by applicant, phone, title, or reference if backend support is added.
Each row or card shows:
Item thumbnail.
Application reference.
Item title.
Applicant.
Proposed starting bid.
Preferred schedule, if provided.
Submitted time.
Status pill.
“Review application” action.
Empty state explains that no applications are waiting.
Keep pagination.
3. Auction application review
Use a two-column desktop layout:

Left:
Photo gallery with the cover image and thumbnails.
Item facts: title, description, category, condition, emirate, service area, proposed starting bid, preferred schedule, submission time.
Status history/timeline.
Right:
Applicant card: name, phone, email, seller status, verification, member since, link to the vendor page.
Decision card:
Request changes.
Reject.
Create and list auction.
Applicant-visible note field.
Internal admin notes field.
Make the right rail sticky on desktop, but ensure it never covers content or traps focus.
Decision options should be large selectable cards with a red selected state.
Primary action: red pill button.
Secondary action: quiet bordered button.
On mobile, stack columns and keep the decision card easily reachable.
The “Create and list auction” action should not directly create the auction without showing the pre-filled auction form.
4. Admin auction create/edit form
Add a proper staff auction form, pre-filled from an application or empty for a direct staff-created auction.
Fields:
Seller/applicant.
Title.
Description.
Category.
Condition.
Emirate.
Service area.
Start and end times.
Starting/minimum amount.
Images with cover selection and reordering.
Show a clear mode indicator:
“From application MC-APP-000123”
“Direct staff auction.”
Primary action: “Create and list auction” or “Save draft.”
Use the existing auction service layer for creation and updates. Do not write lifecycle state directly in the view or admin.
Preserve the existing lifecycle guard: once the first bid is placed, content is frozen.
Show the applicant’s proposed values next to the staff-editable final values if they differ.
5. Auction moderation review — legacy/direct auctions
Keep the existing auction review screen for legacy pending auctions and direct staff submissions.

Apply the same visual language as the application review screen:

Photo gallery.
Item facts.
Decision history.
Seller details.
Decision form.
Chat panel.
Do not remove the existing moderation state machine.

6. Bid monitor and bid audit
Replace the dense tables with panels that match the dashboard cards.
Active auctions table: reference/title, status pill, bid count, distinct bidders, top bid, end time, flags, and actions.
Add an admin action to lock the highest eligible bid when the auction is live, before its deadline, and has an eligible bid.
The lock action must use the existing service layer with an explicit staff-authorised path.
The bidder still confirms or declines through the existing signed link.
Keep pause, resume, extend, and cancel actions.
Voided bids should be visually recessive and clearly labelled.
Ensure tables scroll inside their panel on narrow screens; never cause page-level horizontal overflow.
7. Moderation inbox
Replace the plain button row with a segmented pill control for inbox views.
Keep the item-type and seller filters, styled as a cohesive filter bar.
Each thread becomes a clear card or row with:
Rounded item thumbnail.
Item title.
Item-type and thread-status pills.
Customer/seller identity.
Last-message preview.
Unread treatment: soft red or amber background, left accent border, and an accessible unread badge.
A clear “Open item & chat” action.
Keep pagination and all existing query parameters.
On mobile, stack the card content and make the action full-width.
8. Vendors
Replace the plain filter links with a segmented pill filter: All, Verified, Pending, Suspended.
Use a seller cell with an initials avatar or logo mark, display name, and phone.
Status pills must use a consistent semantic mapping.
Show active listings, active auctions, verification state, and chat count clearly.
Show a warning when a vendor has a pending auction application.
Keep suspend/restore actions, confirmation prompts, and POST behaviour.
On mobile, convert the wide table into stacked vendor cards or allow contained horizontal scrolling.
9. Deals and disputes
Keep the agreed-deal-value headline card.
Keep the explanatory note that payments are settled directly between buyer and seller and that there is no escrow, payout ledger, or commission table. Treat it as a muted information panel, not a warning.
Dispute resolution panel:
Deal reference.
Buyer and seller.
Reason.
Status pill.
Decision controls.
Approve cancellation and keep-deal actions must be visually distinct.
Accepted deals ledger:
Reference.
Buyer/seller.
Item.
Agreed price.
Status.
Accepted date.
Preserve the #disputes anchor and all POST decision values.
10. Customer-facing auction application pages
These are new pages and must match the storefront, not the admin.

Apply for auction page:
Storefront header and footer.
Clear eyebrow: “Auction application.”
Heading: “Apply to auction your item.”
Short explanation: ManzilCart reviews applications, sets the final schedule and starting price, and lists approved items.
Item details, images, proposed starting bid, preferred schedule, and fallback acknowledgement.
Image previews with cover selection.
Primary action: “Submit application.”
Secondary action: “Save draft” if supported.
My auction applications page:
Status cards or rows with item thumbnail, title, reference, proposed starting bid, submitted date, and status.
Applicant-visible message from staff when changes are requested or rejected.
“Edit and resubmit” only for changes_requested.
“View live auction” for listed.
Empty state: “No auction applications yet.”
Application detail page:
Item summary and images.
Status timeline.
Staff message.
Link to the live auction when listed.
No internal notes.
Auction detail page for the applicant:
Show a small “Managed by ManzilCart” note.
Link to the application.
Do not show seller edit, lock, or cancel controls.
Bidding remains available to other eligible bidders.
11. Customer chat panel
Restyle the panel to match the storefront chat:
Rounded container.
Clear header with thread status.
Scrollable message area with a sensible fixed height.
Customer messages on a neutral surface.
Staff messages on a brand-tinted or dark surface with accessible contrast.
System messages visually distinct.
Readable timestamps and sender labels.
Attachment affordance with a visible filename.
Red primary “Send” button.
Inline error state.
Preserve the critical form rule: the chat composer is intentionally outside the main admin item form, uses type="button" and fetch, and must never submit or validate the parent form.
Preserve all data-messages-url, data-send-url, polling, CSRF, attachment, and message-length behaviour.
The panel must not overlap the change form or cause layout jumps.
12. Bulk moderation decision screen
Add a clear warning heading with the number of affected items.
Explain that the reason is delivered to sellers as a system chat message and, where possible, a WhatsApp notification.
Use a prominent reason textarea with a clear label and helper text.
Show the affected items in a compact table with thumbnail, title, seller, and status.
Primary action uses the brand red; cancel is a quiet bordered button.
Preserve the existing POST action, mode, note field, and back behaviour.
13. OTP login
Make it feel like ManzilCart, not a stock Django login page.
Use a compact, centered card or a restrained split layout with the brand mark.
Include a subtle red radial glow or warm background treatment, not a full marketing hero.
Clear heading, short explanation that the code is sent by WhatsApp, and a large OTP input with inputmode="numeric" and autocomplete="one-time-code".
Primary button: red pill.
Secondary actions: resend, use a different number.
Keep the password fallback collapsed under a disclosure for command-line superusers.
Preserve all authentication logic, error messages, rate limiting, and security behaviour.
Keep the login page usable at 360px width and at 200% zoom.
14. Standard Django admin screens
The redesign must not stop at custom pages. Polish the stock admin experience:

Changelists: page header, search bar, filter sidebar, action bar, result table, sortable headers, checkboxes, pagination, and empty states.
Change forms: fieldsets, labels, help text, errors, required markers, inputs, selects, textareas, checkboxes, radios, date/time widgets, autocomplete widgets, inlines, formsets, and submit rows.
Delete confirmation and history pages.
Permission-denied and validation-error states.
The app index or model navigation, if reachable.
The theme toggle and logged-out state.
Keep Django admin’s table density. Do not turn changelists into oversized consumer cards. Ensure long tables can scroll inside a panel, and that the page itself never scrolls horizontally because of a wide table.

Status system
Apply a single semantic status system everywhere.

Auction application statuses:

draft — neutral.
submitted — amber.
in_review — amber.
changes_requested — orange.
listed — green.
rejected — red.
withdrawn — neutral.
Existing auction and deal statuses to cover:

approved, verified, accepted, finalized, pending, needs_seller_reply, open, esc, escalated, changes_requested, paused, rejected, suspended, outbid, resolved, live, scheduled, locked, draft, awarded, sold, cancellation_requested, unsold, cancelled, archived, selected.

Use:

Green for approved, verified, live, accepted, finalized, listed.
Amber for pending, submitted, in review, waiting, scheduled, needs reply.
Red for rejected, suspended, destructive, outbid.
Neutral charcoal or sand for sold, archived, cancelled, withdrawn, closed.
Status pills must remain readable in both light and dark mode. Do not rely on colour alone; include text.

Responsive requirements
Support at least:

1440px desktop: comfortable multi-column layout.
1024px laptop: narrower sidebar and content.
768px tablet: stacked panels, two-column KPI grid.
390px and 360px phones: single-column cards, full-width actions, no clipped text.
Rules:

No page-level horizontal scroll.
Tables either scroll within their own container or convert to stacked card rows.
Tap targets at least 44px high for primary controls.
Mobile navigation must remain accessible.
Sticky headers and review panels must not cover form fields or actions.
Test at 200% browser zoom.
Accessibility requirements
Target WCAG 2.1 AA:

Normal text contrast at least 4.5:1.
Large text at least 3:1.
Visible focus rings on all interactive elements.
Never remove focus outlines without replacing them.
Use semantic headings, <nav>, <main>, and proper table headers.
Add scope="col" to table headers in touched templates.
Use aria-current for active navigation.
Label icon-only controls.
Announce unread counts, application counts, and queue counts in text, not colour alone.
Respect prefers-reduced-motion.
Keep keyboard order logical.
Ensure the chat panel, decision cards, filters, pagination, and image upload controls are fully keyboard operable.
Implementation constraints
The only permitted schema changes are the new AuctionApplication and AuctionApplicationImage models, their constraints/indexes, and notification/audit choice additions.
Do not change existing Auction, Bid, AuctionSelection, or Deal schema unless absolutely required for the new application link. Prefer linking from the application to the auction.
Do not change bid validation, bidding increments, selection/fallback rules, award rules, or deal creation rules.
Do not introduce payments, escrow, payouts, commissions, or subscriptions. Those remain out of scope.
Do not replace Django admin with a SPA, React app, or separate frontend.
Do not add Tailwind, Bootstrap, or another CSS framework.
Do not add a new JavaScript framework or dependency.
Do not load the storefront styles.css globally into the admin. It contains consumer components and global element styles that will conflict with Django admin. Extract and adapt the tokens instead.
Prefer template and CSS changes for the redesign. For the auction workflow, keep business rules in auctions/services.py, not in views or admin methods.
Preserve governance_nav, governance_unread_threads, site_header, site_title, and index_title.
Preserve the OTP login flow and password fallback.
Preserve POST logout, “View site”, theme toggle, and all admin URLs.
Preserve the chat composer’s separation from the main admin form and all fetch endpoints.
Preserve confirmation dialogs for destructive actions such as suspend vendor, void bid, cancel auction, and approve cancellation.
Keep existing template block names and context variable names stable where possible.
Move presentational inline styles out of touched templates into CSS where practical.
Reuse partials for repeated KPI cards, queue panels, status pills, and empty states if that improves consistency without changing behaviour.
Use CSS logical properties for margins, padding, borders, and inset positioning.
Use inline SVG line icons rather than emoji for functional icons. Emoji may remain only where it is genuinely part of the content.
Bump the admin.css cache-busting version in base_site.html after the redesign.
Do not commit or push. Leave the working tree available for review.
Empty, loading, and error states
Every queue and table needs a designed empty state.
Empty states should explain what the screen is for and what to do next.
Do not leave a blank white panel.
Chat should show a clear “No messages yet” state.
The auction applications queue should show “No applications waiting” or “No applications match these filters.”
The customer applications page should show a useful empty state with an “Apply for auction” action.
Form errors and permission errors should use the same visual language: red-tinted panel, clear heading, actionable text.
Do not fabricate data to fill empty screens.
Validation and definition of done
Workstream A — auction workflow
Customer can submit an auction application with item details, images, and proposed starting bid.
Submission does not create an Auction.
Customer cannot create an auction directly through any customer-facing URL.
Only the applicant can view their application; other customers receive a 404 or permission denial.
Staff can view all applications.
Staff can request changes with a reason; applicant sees it and can edit/resubmit.
Staff can reject with a reason; applicant sees it.
Staff can create and list an auction from an application.
The created auction’s seller is the applicant.
The auction is approved, with approved_by and approved_at set.
Images are preserved on the auction.
The application becomes listed and links to exactly one auction.
Double submission or double-click cannot create two auctions.
Staff can create a direct auction without an application.
Staff can edit an auction before the first bid.
Staff can lock the highest eligible bid using the existing service.
Staff can pause, resume, extend, and cancel.
Bidders can still bid and confirm/decline exactly as before.
Existing auctions and deals continue to work.
Notifications and audit events are queued/recorded for the new workflow.
Cross-emirate service areas are rejected.
Image count, file type, file size, and starting-bid validation are enforced.
Suspended/ineligible applicants are blocked or explicitly flagged.
Workstream B — visual redesign
All admin routes render with the new design:
/admin/ command centre.
Auction applications queue.
Auction application review.
Moderation inbox.
Bid monitor and bid audit.
Vendors.
Deals and disputes.
Listing review.
Auction review.
Bulk moderation decision.
Standard changelist.
Standard change form with inlines/formsets.
OTP login and password fallback.
New customer application pages match the storefront.
Light and dark mode work on every major screen.
Layouts work at 1440px, 1024px, 768px, 390px, and 360px.
Keyboard-only navigation, visible focus, and 200% zoom work.
All filters, search, sorting, pagination, bulk actions, save buttons, confirmation dialogs, and chat sends still work.
No page-level horizontal overflow.
No console errors or failed network requests introduced by the redesign.
No migrations, URL changes, permission changes, or business-rule changes beyond the auction-application workflow.
Commands
Run:

cd "D:\Programming\My Project\ManzilAs\Manzilcart"
..\venv\Scripts\python.exe manage.py test
..\venv\Scripts\python.exe manage.py check
..\venv\Scripts\python.exe manage.py makemigrations --check --dry-run
Update or add tests for the new workflow. Do not delete existing auction/bid/deal coverage; adapt it where the seller-side create/edit/lock paths have intentionally moved to staff.

Deliverables
When finished, provide:

A concise summary of the design direction.
A summary of the auction-application workflow change.
The list of files changed.
The new database migrations.
A page-by-page list of what was redesigned.
Test and check results.
Before/after screenshots for desktop and mobile if browser tooling is available.
Any known limitations or follow-up items.
Update Manzilcart/progress.md with the workflow change, visual redesign, and test status.
If any of these assumptions are wrong, stop and ask before implementing:

“Bidding amount” means the applicant’s proposed starting/minimum bid, not a bid placed by the applicant.
Customers should no longer create auctions directly.
Staff should own seller-side auction creation, editing before bids, locking, cancelling, pausing, resuming, and extending.
Existing auctions and deals must remain valid and unchanged.
The public bidder experience must remain unchanged.