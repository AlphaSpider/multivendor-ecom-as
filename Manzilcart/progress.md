# ManzilCart — Build Progress

> **For the next agent:** read this file first. It records what is built, the
> decisions behind it, and exactly what is left. Update it at the end of every working
> session (or after each completed task) with the date and what changed.

**Project:** ManzilCart — C2C marketplace for new and refurbished furniture, UAE
**Django project:** `Manzilcart` (settings package: `Manzilcart/`)
**Django app:** originally `mainapp`, renamed to `core` (agreed with the owner)
**Python venv:** `D:\Programming\My Project\ManzilAs\venv`
**Last updated:** 2026-10-19

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

