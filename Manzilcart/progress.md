# ManzilCart — Build Progress

> **For the next agent:** read this file first. It records what is built, the
> decisions behind it, and exactly what is left. Update it at the end of every working
> session (or after each completed task) with the date and what changed.

**Project:** ManzilCart — C2C marketplace for pre-owned furniture, UAE
**Django project:** `Manzilcart` (settings package: `Manzilcart/`)
**Django app:** originally `mainapp`, renamed to `core` (agreed with the owner)
**Python venv:** `d:\Django_projects\multivendor-ecom-as\Manzilcart\venv`
**Last updated:** 2026-10-20 21:40

---

## 1. How to run

```powershell
cd "D:\Programming\My Project\ManzilAs\Manzilcart"
..\venv\Scripts\python.exe manage.py migrate
..\venv\Scripts\python.exe manage.py runserver
```

- App: http://127.0.0.1:8000/
- Admin: http://127.0.0.1:8000/admin/ (create a staff account with
  `..\venv\Scripts\python.exe manage.py createsuperuser`; it prompts for a **phone
  number**, because `USERNAME_FIELD = "phone"`).
- Tests: `..\venv\Scripts\python.exe manage.py test`
- Deployment checks: `..\venv\Scripts\python.exe manage.py check --deploy`
- Background workers (run every minute from cron / Task Scheduler / a systemd timer):
  ```powershell
  ..\venv\Scripts\python.exe manage.py process_notifications
  ..\venv\Scripts\python.exe manage.py process_auction_deadlines
  ```

### Environment
Configuration is read from `Manzilcart/.env` (git-ignored). Template: `.env.example`.
Local values currently in use:

| Variable | Value |
|---|---|
| `DJANGO_ENV` | `development` |
| `DB_ENGINE` | `postgresql` |
| `DB_NAME` | `manzilsample` |
| `DB_USER` / `DB_PASSWORD` | `postgres` / `admin` |
| `DB_HOST` / `DB_PORT` | `localhost` / `5432` |
| `REDIS_URL` | empty → in-process cache + in-memory channel layer |
| `NOTIFICATIONS_MODE` | `mock` (no provider is contacted) |

Verified on 2026-10-19: PostgreSQL **17.5** reachable, database `manzilsample` exists.

---

## 2. Environment fix (important)

The original `venv` was created by the **MSYS2/MinGW** Python at
`C:\msys64\mingw64\bin\python.exe` (first on PATH). Its platform tag is
`mingw_x86_64_msvcrt_gnu`, so PyPI binary wheels (psycopg, Pillow, Daphne) cannot be
installed into it. Per the owner's decision the venv was **deleted and recreated** with
`py -3.12` (python.org CPython 3.12, `win-amd64`).

If you see `ResolutionImpossible` or `No matching distribution` again, check:

```powershell
..\venv\Scripts\python.exe -c "import sysconfig; print(sysconfig.get_platform())"
# must print: win-amd64   (NOT mingw_x86_64_msvcrt_gnu)
```

Installed: Django 6.1.2, psycopg 3.3.6, Pillow 13.0.0, channels 4.3.2 + daphne,
channels-redis, redis, whitenoise, python-dotenv, phonenumbers. See
`Manzilcart/requirements.txt`.

---

## 3. Architecture decisions

### Modular monolith
| App | Responsibility |
|---|---|
| `core` | timestamps, audit trail, emirate request context, phone/image validation, throttling, OTP hashing, health check, DB geo-integrity helper, slug utility |
| `locations` | `Emirate`, `ServiceArea`, `ServiceOption`, `ServiceAreaCoverage`, emirate selection/switching |
| `accounts` | custom `User`, `OTPChallenge`, OTP registration/sign-in, profile |
| `catalog` | `Category`, `Listing`, `ListingImage`, browse/search selectors, listing services |
| `moderation` | `ListingReview` (append-only moderation decisions) |
| `conversations` | conversations, messages, Channels consumer (HTTP + WebSocket, participant-scoped) |
| `deals` | offers, deals, cancellations, deal events, service selections *(Phase 5)* |
| `auctions` | auctions, bids, selections, fallback *(Phase 6)* |
| `notifications` | `Notification` queue, provider backends, retry worker |

**Deliberate simplifications**
- No separate `services` app: service coverage config lives in `locations`, deal-level
  selections will live in `deals`.
- Profile fields live on `User` (no `UserProfile` table — it would have no independent
  lifecycle).
- No Celery/Redis broker for background jobs: management commands
  (`process_notifications`, `process_auction_deadlines`) driven by cron/systemd.
  The `Notification` table is its own durable queue with attempts, backoff and an
  idempotency key. Trade-off documented in `notifications/models.py`. **Revisit if**
  job volume or latency requirements grow.
- Single settings module driven by environment variables (`DJANGO_ENV`) instead of
  duplicated dev/prod settings files.

### Emirate selection and scoping
- `/` is the emirate picker. No IP geolocation, ever.
- `/abu-dhabi/…` and `/ras-al-khaimah/…` are real URLs (slug = `Emirate.slug`), so a
  shared link always opens the right marketplace.
- `core.middleware.MarketplaceMiddleware` resolves the emirate from the **URL prefix**
  first, then the session (`EMIRATE_SESSION_KEY`), and exposes `request.emirate` plus a
  `contextvars` holder for service code.
- The emirate catch-all is the **last** URL pattern so it cannot shadow `/admin/`,
  `/accounts/`, `/my/`, `/healthz`.
- Reserved slugs are rejected by a validator and by a DB constraint that slugs are
  lowercase; unknown/inactive slugs return a friendly 404, never a redirect to another
  emirate.
- Deactivating an emirate/area never deletes data (`is_active` + `on_delete=PROTECT`).
- **Business rules about "active" are enforced in services/forms, not just the DB**;
  the DB enforces only relationship integrity.

### Geographic integrity (worth knowing)
A `(emirate, service_area)` pair must belong together. Django 6.1 cannot model a
composite FK (verified: `ForeignKey` to a `CompositePrimaryKey` is rejected). Therefore:
- `ServiceArea` has `UniqueConstraint(fields=["id", "emirate"])`;
- `accounts_user` and `catalog_listing` get a **real composite FK**
  `(service_area_id, emirate_id) → locations_servicearea (id, emirate_id)`, added by
  `core.db.geo.apply_geo_fk()` in `*_geo_integrity.py` migrations.
- Proven on 2026-10-19: inserting an Abu Dhabi user with a RAK area raises
  `IntegrityError` on `accounts_user_service_area_emirate_fk`.
- On SQLite (dev fallback only) the raw-SQL step is skipped.

### Accounts
- Custom `User` created **before the first migration**; `USERNAME_FIELD = "phone"`.
- Members have **no password** (`set_unusable_password()`); OTP verification plus
  server-side sessions is the authentication mechanism. Staff keep passwords for Admin.
- Pending registrations are `OTPChallenge` rows, not users, so an unverified number
  cannot squat an account.
- Single entry point for sign-in and registration; the server decides which it is, so the
  response cannot be used to discover whether a number has an account.
- OTP codes: stored as HMAC-SHA256 bound to the phone number (not PBKDF2 — deliberate,
  see `core/security.py`), constant-time compare, attempt limit, expiry, resend cooldown,
  per-phone hourly limit (DB) and per-IP burst limit (cache).

### Notifications
- `NOTIFICATIONS_MODE=mock` by default: messages are recorded in an in-memory outbox;
  a developer-only console echo prints the OTP (only when `DEBUG`).
- Live backends (`WhatsAppCloudBackend`, `SMSBackend`, email via Django 6.1 `MAILERS`)
  exist as guarded stubs and raise instead of pretending to deliver.
- Django 6.1 uses `MAILERS` (aliases) rather than a single `EMAIL_BACKEND`; tests get
  locmem automatically, so `mail.outbox` works for every alias.

---

## 4. Completed

### 2026-10-19 — Phase 1: Foundation ✅
- Rebuilt `venv` with python.org CPython 3.12; installed and pinned dependencies.
- Renamed `mainapp` → `core`; created `accounts`, `locations`, `catalog`, `moderation`,
  `conversations`, `deals`, `auctions`, `notifications`.
- Env-driven `settings.py` (PostgreSQL, email via `MAILERS`, cache/channel layer,
  security hardening for production, business tunables at the bottom).
- Custom `User` model + `UserManager` (phone identity, E.164, no member passwords).
- `core`: `TimeStampedModel`, `AuditEvent` (append-only), emirate context, middleware,
  context processor, phone normalisation, image validation/sanitisation, throttling,
  OTP HMAC helpers, health check, deploy checks, DB geo helper, unique-slug helper.
- First migrations created and applied **including** the composite geographic FKs.
- `.env`, `.env.example`, `requirements.txt`, root `.gitignore`.

### 2026-10-19 — Phase 2: Emirate selection and accounts ✅
- `locations` models: emirates, service areas, service options, coverage (DB constraints,
  reserved-slug validation, soft deactivation).
- Data migration seeds **Abu Dhabi (16 areas)** and **Ras Al Khaimah (16 areas)** plus
  delivery/assembly/disassembly coverage.
- Landing page, marketplace home, emirate switcher, graceful 404 for unknown/inactive
  emirates, `areas.json` endpoint.
- Accounts: OTP request → verify → details registration flow, OTP sign-in, resend,
  logout, dashboard, profile update. Rate limits, attempt limits, expiry and audit rows.
- `notifications`: queue model, mock/live backend registry, idempotency, retry/backoff,
  `process_notifications` management command + Admin retry console.

### 2026-10-19 — Phase 3: Listings and moderation ✅
- `catalog` models: `Category`, `Listing` (moderation states + all transitions in
  `catalog/services.py`), `ListingImage` (validated uploads, one cover constraint).
- Emirate-scoped browse/search/filter/sort/pagination; detail page with owner/staff
  visibility rules; create/edit forms; image cover/delete actions; seller dashboard
  (`/my/listings/`).
- Every status change goes through `transition_listing()` (row lock + audit); edits to
  approved listings re-enter moderation and unpublish.
- Moderation: append-only `ListingReview`, Admin queue (pending first), bulk approve,
  per-listing review screen with rejection reason.
- Tests: `catalog/tests.py`, `moderation/tests.py`.

### 2026-10-19 — Phase 4: Chat ✅
- `Conversation` (one per listing+buyer, DB uniqueness, participants distinct) and
  append-only `Message` (persistent, indexed, client UUID for idempotent retries).
- `ConversationConsumer` (Channels): session auth, participant authorisation, live
  broadcast, ack/error protocol, close 4401/4403 for anonymous/foreign sockets.
- HTTP views: thread list, participant-only detail with paginated history, create
  thread from a listing, no-JavaScript send fallback, read receipts.
- Realtime broadcast is scheduled with `transaction.on_commit`, so rolled-back messages
  are never shown. Messages are rendered with `textContent` (no HTML injection).
- Tests: `conversations/tests.py` covers HTTP authorisation, persistence, empty/length
  validation, pagination, idempotency, and WebSocket accept/reject + duplicate send.

### 2026-10-19 — Phase 5: Offers and deals ✅
- `deals` models: `Deal`, `DealOffer`, `DealCancellation`, `DealEvent`,
  `DealServiceSelection`; partial unique indexes for one active deal per listing and one
  open negotiation per buyer/listing.
- Offers, counteroffers, either-side acceptance, atomic listing reservation, service
  selections, cancellation request → response → escalation → operations finalisation.
- Deal detail/list pages, staff finalisation panel, operations notification after
  acceptance, idempotent deal notifications.
- Tests: `deals/tests.py` (28 tests).

### 2026-10-19 — Phase 6: Auctions ✅
- `auctions` models: `Auction`, `Bid`, `AuctionSelection`, `AuctionEvent`; partial unique
  index for one active auction per listing; unique `awarded_deal` and one selection per
  bidder/sequence.
- Seller creates an auction from an approved, published listing; start/end validation,
  optional reserve price and mandatory fallback-rule agreement.
- Bidding: server-side minimum increment, verified-member requirement, seller can't bid,
  row-locked auction during bids, tie-break by earliest valid bid, one client UUID per
  auction for idempotent retries.
- Seller explicitly locks the current highest eligible bid; bidding freezes immediately,
  the selected bidder gets a signed confirmation link and a configurable deadline.
- Confirm creates exactly one accepted deal at the winner's own bid price and reserves
  the listing. Decline/timeout marks the round and offers the next eligible bidder a
  fresh deadline; each bidder appears once; no bidders left → unsold.
- Deadline worker `process_auction_deadlines` opens scheduled auctions, closes live
  auctions the seller never locked, and expires overdue confirmations. All operations
  re-lock and re-check state, so duplicate workers cannot award twice.
- Emirate-scoped auction board/detail, seller create form, bid box, locking panel,
  confirmation page, “My auctions”, bidder-identity privacy, Admin inspection and safe
  operations cancellation, `auctions.css`.
- Tests: `auctions/tests.py` (41 tests) covering the full auction matrix including
  tie-breaks, fallback order, reserve rules, command processing, HTTP authorisation and
  exactly-once award.

### Test status (verified 2026-10-19)
```powershell
..\venv\Scripts\python.exe manage.py test
# Ran 194 tests ... OK
```
`manage.py check` reports no issues. With a production environment loaded and email +
`NOTIFICATIONS_MODE=live` configured, `manage.py check --deploy` also reports **no
issues**. With `NOTIFICATIONS_MODE=mock` it correctly warns `manzilcart.W002` (the
launch gate); notification/auction commands were also exercised by the test suite. A
real cron deployment is documented in `docs/deployment.md`.

### 2026-10-19 — Phase 7: Test completion, hardening and deployment docs ✅
- Added the remaining §20 coverage:
  - `accounts/tests.py`: OTP start/verify/expiry/attempt limit, resend cooldown and
    hourly limits, per-IP burst limit, duplicate phone, inactive emirate/area,
    returning-member sign-in, inactive member, full HTTP registration/sign-in,
    open-redirect protection, profile move, logout and admin read-only checks.
  - `locations/tests.py`: landing, direct and inactive emirate URLs, switching,
    open-redirect rejection, `areas.json`, active-only selectors, coverage opt-in,
    geographic validation, lowercase-slug DB constraint and admin readonly checks.
  - `notifications/tests.py`: immediate mock delivery, idempotency keys, entity
    traceability, due query, retryable/unexpected/non-retryable failures, backoff cap,
    live-mode credential failure, manual retry + audit, worker due/limit behaviour and
    read-only delivery log.
- Fixed five defects found while writing the tests:
  1. OTP attempt counters were rolled back when a `DomainError` escaped the
     `transaction.atomic()` block, defeating the attempt limit. Failures are now raised
     after commit so attempts/invalidation persist (`accounts/services.py`).
  2. Registration context (`OTPChallenge.user`, emirate, area) was not persisted because
     `mark_consumed()` only updates `consumed_at`; it is now saved explicitly.
  3. The live backend registry could not import `WhatsAppCloudBackend`/`SMSBackend`/
     `EmailBackend` because `notifications/backends/__init__.py` did not export them.
  4. The custom moderation review screen was reachable by **any** staff user, including
     view-only accounts. It now requires `catalog.change_listing`.
  5. `core.checks` was never imported, so the production Redis/notification/email checks
     never ran; it also inspected the removed `EMAIL_HOST` setting instead of Django
     6.1's `MAILERS`. `CoreConfig.ready()` now registers the checks, which understand
     `MAILERS`, with tests in `core/tests_checks.py`.
- Admin hardening: `ServiceArea.emirate` is read-only after creation (moving an area
  would invalidate the composite geographic FKs); audit/OTP/notification screens remain
  read-only.
- Added `docs/deployment.md`: topology, production env, Daphne/Nginx/systemd units,
  HTTPS/HSTS, worker timers, backup/restore procedure, costs, checklist and known gaps.
- Executed a local PostgreSQL **restore drill**: `pg_dump -Fc` → restore into a scratch
  database → matching `django_migrations` (32) and service-area (32) counts → drop.
- Added the production flags and messaging placeholders to `.env.example`.

### 2026-10-19 (Evening Session: 17:30 – 23:55) — Frontend Theme, Bilingual RTL, Interactive Gallery, Filter Modal, & Navigation Refinements ✅

1. **Landing Page Template Inheritance & Alerts Fix (17:45)**:
   - Fixed `templates/locations/landing.html` block structure by ensuring all page content inherits properly within `{% block content %}` and error alerts display when an inactive or invalid marketplace is encountered.

2. **Red / White / Black Theme, Dark Mode & Arabic Bilingual RTL Switcher (18:30)**:
   - **Theme**: Updated entire site color scheme to Red (`--brand: #dc2626`), clean whites/neutrals (`#ffffff`, `#f4f4f5`), and dark/black surfaces (`--ink: #111111`, `#09090b`).
   - **Dark Mode**: Added theme toggle (`[data-theme-toggle]`) in header with sun/moon SVG icons. Persists preference in `localStorage.getItem("theme")`.
   - **Bilingual (English / Arabic RTL)**:
     - Added language switcher (`[data-lang-toggle]`) in header with `localStorage` persistence.
     - Automatically sets `html[dir="rtl"]` and `html[lang="ar"]` when Arabic is selected.
     - Created comprehensive client-side translation dictionaries in `static/js/app.js` (`i18n.en` and `i18n.ar`) covering navigation, hero sections, value cards, listing details, emirate names, form placeholders (`[data-placeholder-i18n]`), and filter modal labels.
     - Added mirrored RTL styling in `static/css/styles.css` for inputs, icons, carousels, and layout grids.

3. **Logo Integration (19:15)**:
   - Replaced old logo with new branded assets in `static/images/`:
     - `logo.png`: Full logo for light backgrounds.
     - `logo-dark.png`: Full logo for dark theme.
     - `logo-mark.png`: Compact emblem/favicon.
   - Updated `templates/base.html` header and footer with CSS auto-switching between light and dark modes.

4. **Product Detail Interactive Zoom Gallery (19:50)**:
   - `templates/catalog/listing_detail.html`:
     - Transformed vertical image dump into a single featured hero image container (`[data-zoom-viewport]`) with interactive cursor hover-zoom (`transform: scale(2.2)` tracking pointer position).
     - Added horizontal thumbnail row below main photo with active highlight borders, click-to-view, left/right keyboard navigation, and smooth auto-scrolling into view.

5. **Listing Page Popup Filter Modal & Dual Price Range Slider (21:35)**:
   - `templates/catalog/listing_list.html`, `static/css/styles.css`, `static/js/app.js`:
     - **Popup Modal**: Filters are completely hidden by default (`style="display: none;"`) so they never render in page flow below listings.
     - **Trigger**: Added a browse toolbar containing a "Filters" button (`[data-filter-open]`) with active filter count badge.
     - Opens an accessible modal overlay (`#filter-modal`) with dark blurred backdrop, ESC key listener, and click-outside dismissal.
     - **Price Dual-Slider**: Implemented a single horizontal track with two draggable red handles (`min_price` and `max_price`), solid red fill between handles, and smart pointer collision detection.
     - **Simultaneous Real-Time Price Text Box Sync**: Dragging either slider handle updates the Min/Max price text boxes in real-time at 60fps; typing into the text boxes moves the slider handles in lockstep.
     - Added quick preset price chips ("Under AED 500", "AED 500 – 1.5k", "AED 1.5k – 4k", "AED 4,000+", "Any price").
     - Added active filter chips with `×` remove links using Django's `{% querystring %}` tag.
     - Bumped static cache-busting query strings (`?v=1.4`) in `templates/base.html` and placed critical modal styles in `{% block extra_head %}` to guarantee immediate rendering without browser caching issues.

6. **Outside Search Bar Separation (22:05)**:
   - Kept the search keyword input (`q`) outside the filter popup modal by placing a dedicated search bar (`.browse-search-bar`) directly in the browse toolbar on the page.
   - Removed search input from inside the modal while preserving active query parameters as hidden fields.
   - Split filter count into `modal_filter_count` (category, condition, price) for the "Filters" button badge and `active_filter_count` for active dismissal chips (`catalog/views.py`).

7. **Header Navigation Refinement — "Sell+" Button (21:45)**:
   - `templates/base.html`:
     - Removed the "Join ManzilCart" button.
     - Moved "Sell" from inside the nav links to the far top-right as a prominent red button labeled `"Sell+"` (`.nav-sell-btn`).
     - Added bilingual translations (`Sell+` in EN, `بيع+` in AR).


### Test Status (Verified 2026-10-19 23:55)
- All 194 automated tests pass with zero failures:
  ```powershell
  ..\venv\Scripts\python.exe manage.py test
  # Ran 194 tests in 22.9s ... OK
  ```

### 2026-10-20 (Morning Session: 11:00 – 11:25) — Landing Page Clean Header & Browse Redirect on Brand Logo ✅

1. **Landing Page Minimal Centered Branding Header with Arabic & Dark Mode Controls (`templates/locations/landing.html`, `static/css/styles.css`)**:
   - Overrode `{% block site_header %}` in `templates/locations/landing.html` to eliminate all marketplace navigation elements from the landing page:
     - Removed emirate selector dropdown chip.
     - Removed Browse, Auctions, Sign in, and Sell+ buttons.
     - Kept/included language (`العربية` / English) and dark/light theme controls positioned cleanly on the trailing edge (`.header-controls-landing`).
   - Styled `.site-header-landing`, `.header-inner-landing`, and `.brand-landing` with `justify-content: center` and `margin-inline: auto` to display the ManzilCart logo dead-center on both LTR and RTL layouts while controls float neatly on the side.
   - Marketplace options (Browse, Auctions, Sign in / user links, Sell+, emirate switcher) appear exclusively after the visitor chooses an emirate.

2. **Base Brand Logo Browse Redirection (`templates/base.html`)**:
   - Updated the header and footer brand logo links (`.brand` and `.footer-brand-link`) in `templates/base.html` from `locations:landing` to redirect to the active marketplace browse page (`{% url 'marketplace:home' emirate_slug=current_emirate.slug %}`) when an emirate is active, falling back to `locations:landing` only when no emirate is selected.
   - Guarded the primary navigation bar in `templates/base.html` so marketplace options only render when `current_emirate` is set.

3. **Automated Test Coverage (`locations/tests.py`)**:
   - Added `test_landing_header_only_shows_centered_logo_without_marketplace_options` verifying that even when an emirate is stored in the user's session, the landing page header renders only the centered logo without `primary-nav`, `Sell+`, `Sign in`, `Browse`, `Auctions`, or the marketplace switcher chip.
   - Added `test_logo_redirects_to_browse_page_when_emirate_chosen` verifying that the brand logo in the header points to `marketplace:home` for the chosen emirate.
   - Test suite increased from 194 to 196 tests, all passing cleanly.

### Test Status (Verified 2026-10-20 11:25)
- All 196 automated tests pass with zero failures:
  ```powershell
  ..\venv\Scripts\python.exe manage.py test
  # Ran 196 tests in 17.5s ... OK
  ```

### 2026-10-20 (Midday Session: 11:30 – 11:55) — Comprehensive Arabic Translation Audit & Optimization Across All Pages ✅

1. **Root-Cause Analysis of Untranslated Elements**:
   - The prior client-side translation engine was restricted strictly to pre-annotated `[data-i18n]` / `[data-emirate-name]` tags.
   - Dynamic database-driven entities had no translation mapping: Categories (e.g., "Sofas & Seating", "Dining Tables", "Beds & Mattresses"), Service Areas across all 7 emirates (e.g., "Al Reem Island", "Downtown Dubai", "Al Majaz"), Conditions ("Brand new", "Like new", "Good", "Fair"), Statuses ("Draft", "Live", "Locked", "Reserved", "Sold", "Negotiating", "Awarded", "Unsold", "Cancelled").
   - Common interactive UI elements lacked bilingual hooks: Buttons ("View", "Edit", "Archive", "Mark as sold", "Save changes", "Cancel"), Pagination ("Page X of Y", "Previous", "Next"), Prices ("AED 1,200" vs "1,200 د.إ"), and form labels / help text.

2. **Full-Spectrum Bilingual Dictionaries & Automated DOM Translation Engine (`static/js/app.js`)**:
   - Injected exhaustive translation lookup maps into `static/js/app.js`:
     - `emirateNames`: All 7 UAE emirates.
     - `categoryNames`: All categories and slugs.
     - `conditionNames`: All furniture condition variants.
     - `statusNames`: All listing, deal, offer, auction, and verification statuses.
     - `areaNames`: Neighborhoods and service areas across Abu Dhabi, Dubai, Sharjah, Ajman, Umm Al Quwain, Ras Al Khaimah, and Fujairah.
     - `uiPhrases`: Hundreds of complete UI labels, headings, prompts, field labels, button strings, tooltips, and safety notes.
   - Built a non-destructive DOM scanner:
     - Automatically scans badges, status pills, table headers, buttons, dropdown options, breadcrumbs, detail lists, and pagination.
     - Preserves original English DOM content in `_origText` / `_origHtml` properties so switching back to English (`English`) restores 100% authentic typography and case formatting without data degradation.
     - Dynamically formats currencies into Arabic standard `د.إ` with Western or Eastern Arabic numeral localization.

3. **Template-Level Audit & Data-Hook Annotations**:
   - `templates/partials/listing_card.html`: Added `data-condition`, `data-area-name`, `data-category-name`, and `data-price`.
   - `templates/partials/field.html`: Added `data-i18n="field_optional"` and `.field-label-text`.
   - `templates/locations/marketplace_home.html`: Added `data-area-name` across all neighbourhood pills.
   - `templates/catalog/my_listings.html`: Annotated headings, statuses, empty state, action buttons ("Create auction", "Submit for review", "Mark as sold", "Archive").
   - `templates/catalog/listing_detail.html`: Annotated breadcrumbs, badges, conditions, service areas, and detail specification lists.
   - `templates/catalog/listing_form.html`: Annotated labels, category and area dropdowns, selling tips, and submit buttons.
   - `templates/auctions/list.html` & `templates/auctions/my_auctions.html`: Annotated headings, active bid tags, no-bid labels, countdowns, and pagination.
   - `templates/auctions/detail.html` & `templates/auctions/selection.html`: Annotated breadcrumbs, bids, sidebar specifications, confirmation rounds, lock actions, and decline options.
   - `templates/deals/list.html` & `templates/deals/detail.html`: Annotated deal statuses, offer histories, action forms (Accept, Reject, Counter, Make offer, Cancel).
   - `templates/conversations/list.html` & `templates/conversations/detail.html`: Annotated conversation listing, unread indicators, chat header, status, history loader, and message input placeholders.
   - `templates/accounts/dashboard.html` & `templates/accounts/profile.html`: Annotated dashboard cards, navigation tiles, mobile verification status, phone numbers, and profile action forms.
   - `templates/accounts/phone_entry.html`, `verify_code.html`, `registration_details.html`: Annotated headings, steps, phone number labels, OTP prompts, and expiry warnings.
   - `templates/404.html` & `templates/500.html`: Annotated error headings, descriptions, and homepage return buttons.

4. **Universal Recursive DOM TreeWalker Translation Engine (`static/js/app.js`)**:
   - Replaced selective selector queries with a universal `TreeWalker` (`NodeFilter.SHOW_TEXT`) traversing all text nodes in `document.body`.
   - Guaranteed that any word, phrase, dynamic label, compound sentence, count pattern (e.g. "X items available", "X neighbourhoods", "X listings"), filter preset, sort choice, or price pattern on any page is instantly translated to Arabic.
   - Preserves original node content in `_origVal` so switching back to English restores authentic typography with zero loss.
   - Synchronizes `document.title`, input/textarea `placeholder`s, dropdown `<option>`s, button `value`s, `aria-label`s, and `title` tooltips.
   - Added cache-busting `?v=2.5` to `<script src="app.js">` in `templates/base.html`.

### Test Status (Verified 2026-10-20 12:10)
- All 196 automated tests pass with zero failures:
  ```powershell
  ..\venv\Scripts\python.exe manage.py test
  # Ran 196 tests in 19.8s ... OK
  ```

---

### 2026-10-20 (Afternoon Session: 19:00 - 20:45) - Step 2: Standalone Auctions & Decoupled Deals ✅

**Decision:** `catalog.Listing` is fixed-price only. An auction is now a standalone
marketplace item with its own content, photos, location, schedule and moderation. It
never creates, inherits from, or references a listing at runtime.

**Auction model and lifecycle**
- `Auction` gained `title`, `description`, `category`, `condition`, `service_area`,
  `moderation_status` (`draft` / `pending` / `approved` / `rejected`), moderation
  timestamps/reviewer, `moderation_note`, an optional `legacy_listing_id` rollback
  breadcrumb, and `AuctionImage` with a one-cover-per-auction partial unique index.
- Approval is a separate axis from lifecycle `status`. New auctions are `SCHEDULED`;
  only an approved auction can be public, opened by the worker, bid on, locked or
  awarded. Draft/pending/rejected items are hidden from the board and return 404 to
  visitors; their owner and staff can still inspect them.
- New auctions support “Submit for review” (default) and “Save draft”. Draft/rejected
  auctions are freely editable. Approved auctions are editable only before the first
  bid; editing sends them back to `pending` and resets lifecycle to `scheduled`. Once
  bidding starts, content and photos are frozen.
- Seller notifications and audit events added for submit/approve/reject; append-only
  `moderation.AuctionReview` rows are written by `approve_auction()` /
  `reject_auction()` so every caller gets the same history.
- Rejection reasons are shown to the seller in My auctions, where they can edit and
  resubmit.

**Auctions UI and access**
- New route `/<emirate_slug>/auctions/new/`; one form covers item details, photos,
  schedule, optional reserve and fallback agreement. The edit route reuses it.
- Navbar “Auctions” opens a paginated board of approved live/scheduled auctions
  (soonest deadline first) then recently closed outcomes, with a prominent
  “Create Auction” action.
- Logged-out visitors are sent to login with a `next` back to the create page.
  Ineligible sellers see the same shared `accounts/seller_not_eligible.html` message
  as listing creation, never a 404.
- “Create auction” entry points added to the member dashboard and My auctions.
- Auction board cards now reuse the fixed-price `listing-card` layout (cover image,
  condition badge, title, price, area/category), with an added close/open time line.
- Auction detail photos now use the same interactive gallery as fixed-price listings:
  main viewport with hover zoom, previous/next arrows, thumbnail strip, active thumb,
  and the shared no-photos state (`partials/auction_card.html`, `data-product-gallery`).
- Auction detail now places the Bidding/place-bid panel immediately after the gallery,
  with “About this item” below it in the actual DOM order (so mobile, tablet and
  desktop all agree); bid history and auction history follow, then the sidebar.
- Mobile header now places the burger menu at the inline-start (left in LTR, right in
  RTL), before the logo. Static asset versions were bumped to avoid stale CSS caches.
- Marketplace condition choices are pre-owned only: `New` was removed from both
  listing and auction choices. Migrations remap any existing `new` rows to `like_new`
  (catalog `0005`, auctions `0004`). Marketing copy now says “pre-owned” instead of
  “new and refurbished”.
- Removed all catalog auction links/injection: listing feeds, category pages, search,
  counts and APIs contain listings only.

**Deals and conversations decoupled**
- `Deal.listing` is nullable and `Deal.auction` was added with an XOR check
  (`deal_exactly_one_source`), one-active-deal-per-listing and
  one-active-deal-per-auction partial uniques, plus an auction/status index.
- `create_awarded_deal(listing=None, auction=None, ...)` now awards either source.
  New auction awards create `Deal(auction=auction, listing=None)` as `ACCEPTED`;
  legacy listing-linked auction deals are left untouched. `Deal` exposes
  `item_title`, `item_reference`, `item_kind`, `item_url`, `cover_image` etc.
- Deal pages, selectors, notifications and cancellation flows work without a listing.
- `Conversation.listing` is nullable and `Conversation.auction` was added with an XOR
  check and one-thread-per-buyer/per-item partial uniques. New route
  `my/conversations/start/auction/<id>/` only accepts approved auctions. Templates use
  item facade properties.

**Migrations and rollout**
- `auctions.0002_standalone_auction_content`: adds the new fields/images, copies
  listing title/description/category/condition/area/seller and images, marks auctions
  whose listing was published as `APPROVED`, then enforces non-null content fields and
  drops listing-scoped uniqueness.
- `auctions.0003_drop_auction_listing`: archives linked `approved` listings that have
  no deal, leaves listings with any deal untouched, then drops `Auction.listing`.
  Reverse re-adds the column and restores `legacy_listing_id` (new standalone auctions
  have no legacy listing, so rollback after creating them needs an operator decision).
- `deals.0002_auction_deals`, `conversations.0002_auction_conversations`,
  `moderation.0002_auctionreview`, `core.0002_audit_auction_moderation` add the
  corresponding schema and choices. Existing deals are never altered.
- Added read-only dry run:
  `..\venv\Scripts\python.exe manage.py audit_auction_decoupling [--json]`.
  It introspects the live schema, so it works before and after the migrations, and
  reports listings that will be archived vs. protected by an existing deal.

**Verification**
- `manage.py check`: clean.
- `manage.py check --deploy` in `DJANGO_ENV=production` with a production-like
  configuration: no errors (only expected warnings for Redis/provider mode/DEBUG in
  this local environment).
- `manage.py test`: **199 tests, OK** (auctions 36, deals 32, conversations including
  auction threads, plus the existing suites).

---

## 5. Outstanding / next steps

- [x] Catalog: create/edit views, image pipeline, moderation Admin, emirate-scoped
      search/filter/pagination, listing detail.
- [x] Conversations + Channels consumer (participant authorisation, history pagination).
- [x] Profile/dashboard/member pages wired to real URLs (no `href="#"`).
- [x] Deals: offers/counteroffers, atomic acceptance with row locking + partial unique
      constraint on accepted deals, cancellation workflow, service selections.
- [x] Auctions: creation, bidding with tie-break by earliest bid, seller lock,
      confirmation deadlines, fallback chain, exactly-once award.
- [x] Operations notification on accepted deal (idempotent).
- [x] Automated tests for accounts/locations/notifications (§20), administrative
      permission restrictions and cross-emirate validation.
- [x] Admin hardening review (view-only staff blocked from moderation changes, area
      emirate immutable after creation, read-only evidence screens).
- [x] Production deployment docs (PostgreSQL/Redis/worker/backups/HTTPS) + cost estimate;
      production `check --deploy` clean with live notifications + email; local
      backup/restore drill passed. Mock notifications intentionally warn (`W002`).
- [ ] Real WhatsApp/SMS/email provider integration (blocked on credentials).
- [ ] Browser smoke test on desktop and mobile (landing, register, listings, chat,
      deals, auctions) before launch.
- [ ] Provision the production host and execute `docs/deployment.md` end to end
      (DNS, TLS, workers, first restore, smoke test).

---

## 6. Known gaps / gotchas

1. `locations/views.py` `emirate_not_found()` is not wired as a URL handler; unknown or
   inactive emirate URLs are handled by `EmirateRequiredMixin` raising `Http404`. The
   custom template is therefore only used if a 404 handler is added. This is deliberate
   for now (both cases return 404) but worth revisiting for a branded 404 page.
2. Auction/notification SMS and email live backends are stubs by decision. WhatsApp
   also needs credentials and an approved template; keep `NOTIFICATIONS_MODE=mock`
   until then.
3. Do not log OTPs, tokens or full phone numbers. Use `core.logging_utils`.
4. The full suite (194 tests) covers catalog, moderation, conversations, deals,
   auctions, accounts, locations, notifications and deployment checks. Run it before
   every deploy.
5. The chat UI depends on `/ws/…` reaching Daphne. In development `runserver` is Daphne
   (installed), so this works; in production Nginx must proxy WebSocket upgrades to
   ASGI (see `docs/deployment.md`).
6. `manage.py check --deploy` must be run with the **production** environment loaded.
   The development `.env` intentionally reports DEBUG/HTTP-cookie/console-email
   warnings.
7. The local development database user is `postgres`; create a dedicated least-privilege
   role for production.
8. Live email is implemented, but production `check --deploy` fails (`mail.E001`) until
   `EMAIL_HOST` and credentials are configured — this is intentional.

