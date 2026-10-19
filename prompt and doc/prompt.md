1. Project Overview
I am building ManzilCart, a customer-to-customer (C2C) marketplace for buying and selling new and refurbished furniture in the UAE.

The platform will initially operate in two emirates:

Abu Dhabi
Ras Al Khaimah (RAK)
The platform must support both emirates from the initial launch, while allowing more emirates to be added later through configuration.

The objective is to launch a practical, secure, responsive, production-ready marketplace with a straightforward architecture that a small team can maintain and improve.

This is a real project, not just a demo. Prioritize correctness, security, maintainability, affordability, and a good user experience over unnecessary architectural complexity.

2. Technology Stack
Use the following stack:

Backend: Python, Django, Django REST Framework
Database: PostgreSQL
Frontend: Django templates, HTML, CSS, and vanilla JavaScript
Real-time messaging: Django Channels and WebSockets
Cache/message layer: Redis where needed
Background tasks: A lightweight worker and scheduler for auction deadlines, notification retries, and other asynchronous operations
Image storage: Appropriate object storage or an affordable managed storage solution
Deployment: AWS or another suitable affordable hosting arrangement, with India-based hosting initially considered
Do not introduce React, microservices, Kubernetes, Kafka, or a complex distributed architecture unless there is a demonstrated business or technical need.

Build a modular monolith with clearly separated Django apps and shared business logic.

Create a custom Django user model before the initial database migration.

3. Landing Page and Emirate Selection
The website root URL / must display a landing page where visitors choose their preferred emirate.

Display two clear, attractive options:

Abu Dhabi
Ras Al Khaimah
Each option should be presented as a clickable card or button. Once selected, the visitor should enter the corresponding marketplace.

Suggested URL structure:

/ — Emirate selection landing page
/abu-dhabi/ — Abu Dhabi marketplace
/ras-al-khaimah/ — Ras Al Khaimah marketplace
Use URL slugs and database-backed configuration rather than hardcoding separate marketplace applications.

Requirements:

Visitors can browse listings without registering.
The selected emirate must be clearly visible in the marketplace header.
Provide an easy emirate switcher.
Remember the selected emirate using an appropriate session or preference mechanism.
A direct URL must open the specified emirate's marketplace.
Do not automatically determine the user's emirate from their IP address.
Switching emirates should update the marketplace context, listings, searches, filters, and navigation appropriately.
Invalid or inactive emirate URLs must be handled gracefully.
Deactivating an emirate must not delete historical records.
Make the landing page and marketplace responsive on desktop, tablet, and mobile.
The two emirates must share the same codebase, authentication system, database structure, and business logic.

4. Emirate and Service-Area Management
Create configurable geographic entities.

Emirate
Suggested fields:

ID
Name
Unique slug
Active/inactive status
Optional display order
Created and updated timestamps
ServiceArea
Suggested fields:

ID
Name or area label
Foreign key to Emirate
Active/inactive status
Optional area-specific service coverage
Created and updated timestamps
The initial active emirates are Abu Dhabi and Ras Al Khaimah.

Individual neighbourhoods and residential areas should be managed through Django Admin rather than scattered hardcoded lists.

The system must validate that every service area belongs to the specified emirate.

Authorized staff should be able to add, edit, activate, and deactivate emirates and service areas without requiring a code deployment.

Keep the geographic model flexible enough for future emirates and additional service areas.

5. User Accounts and Registration
Only individual users are allowed. Do not implement business or vendor accounts.

Users must verify their mobile numbers through WhatsApp OTP during registration.

Registration flow:

User selects an emirate or begins from that emirate's marketplace.
User enters their mobile number.
The platform sends a WhatsApp OTP through an approved provider.
The user verifies the OTP.
The user selects a supported residential service area within the chosen emirate.
The server validates the emirate, service area, and their relationship.
The account is created only after all required checks pass.
Requirements:

Normalize mobile numbers into a consistent international format.
Enforce uniqueness for verified mobile numbers.
Set OTP expiry, attempt limits, resend cooldowns, and rate limits.
Avoid exposing whether a particular phone number already has an account.
Prevent brute-force attempts and OTP abuse.
Do not trust emirate or service-area values supplied by the browser.
Prevent registration in inactive emirates or unsupported areas.
Preserve existing accounts if an area later becomes inactive.
Allow profile management without letting users bypass service-area validation.
Apply appropriate session security and CSRF protection.
Use a suitable Django authentication design. Prefer secure server-managed sessions for the template-based application unless a genuine API client requires another approach.

6. Marketplace Listings
Users must be able to create listings for new and refurbished furniture.

Each listing should support appropriate fields such as:

Seller
Title
Description
Category
Condition
Asking price
Images
Emirate
Service area
Publication/moderation status
Created and updated timestamps
Use suitable choices for furniture condition, such as new, like new, good, and fair.

Listing moderation
Every newly submitted listing must be reviewed by an administrator before becoming publicly visible.

Suggested listing states:

Draft
Pending review
Approved/published
Rejected
Archived
Sold, where appropriate
Enforce allowed transitions on the server.

Only approved and published listings should appear in ordinary marketplace searches.

Marketplace isolation
The selected emirate is the default scope for browsing, searching, category navigation, filters, sorting, and pagination.

Requirements:

Listings must be associated with a valid service area.
The listing's service area must belong to its emirate.
Prevent unauthorized listing creation or changes to geographic associations.
Clearly display the listing's emirate and relevant area.
Changing emirates should show that emirate's listings by default.
Validate scope on the backend and API, not only in the frontend.
Avoid leaking private information across emirate contexts.
Listing detail URLs may be shareable, but access to a detail page must not bypass moderation, ownership, or other authorization rules.

Use validated image uploads, sensible file-size limits, safe file handling, and appropriate storage permissions.

7. Buyer-Seller Chat
Implement real-time text chat between buyers and sellers using Django Channels and WebSockets.

Requirements:

A buyer can start a conversation about a listing.
Buyers and sellers can exchange text messages.
Conversations must reference the relevant listing.
Only authorized conversation participants can access messages or establish WebSocket connections.
Messages must be stored persistently in PostgreSQL.
Prevent unauthorized message reading, sending, or modification.
Add pagination for message history.
Handle reconnects and duplicate submissions sensibly.
Validate and safely render message content.
Prevent cross-user and cross-listing information leakage.
Use Redis as the Channels layer if appropriate for the deployment.

Do not build voice calls, video calls, or elaborate chat features unless added to the requirements later.

8. Offers, Negotiation, and Deals
Buyers and sellers negotiate through offers and counteroffers.

A buyer can submit an offer. The seller can accept, reject, or counter it. The buyer can then respond to the counteroffer.

Keep a clear history of offers and counteroffers, including the amount, sender, timestamps, and status.

Deal acceptance
A deal becomes accepted only when the seller accepts the final valid offer.

When a deal is accepted:

Record the agreed price.
Link the buyer, seller, and listing.
Record the emirate.
Preserve the relevant offer history.
Reserve the listing so another buyer cannot create a competing accepted deal.
Record a deal event for audit and history.
Prevent simultaneous requests from creating multiple accepted deals for the same listing.
Use database transactions, appropriate row locking, and database constraints where necessary. Do not rely on frontend button disabling for concurrency control.

Suggested deal states:

Negotiating
Accepted
Cancellation requested
Cancelled
Completed, only if a legitimate completion process is later defined
Keep the workflow explicit and validate all state transitions on the server.

Cancellation
Either the buyer or seller can request cancellation after acceptance.

A cancellation request must not automatically cancel the deal.
Record who requested cancellation, when, and any reason.
Notify the other party.
Allow the other party to respond.
Escalate unresolved disputes to the admin team.
Finalize cancellation through an authorized, explicit transition.
Reopen the listing only when cancellation is finalized and no other valid reservation prevents it.
Preserve the complete deal and cancellation history.
Do not silently delete cancelled deals.

9. Furniture Payment Rules
The furniture purchase payment takes place directly between the buyer and seller.

ManzilCart does not process, hold, settle, or reconcile the furniture purchase price in the initial release.

Requirements:

Do not integrate a payment gateway for the furniture price.
Do not claim that ManzilCart has received or secured the purchase money.
Do not mark the purchase as paid without a separately defined and verified process.
The agreed furniture price must be recorded for deal history.
Any future payment feature must be treated as a separate scope decision.
The optional ManzilCart services described below must not accidentally turn the platform into a payment-processing workflow.

10. Optional Delivery, Transportation, Assembly, and Disassembly Services
ManzilCart offers optional service assistance for a furniture deal.

The available service options are:

Delivery/transportation
Assembly
Disassembly
Service availability may vary by emirate and residential service area.

Required workflow
The buyer negotiates with the seller.
The buyer chooses the relevant optional services during the deal workflow.
The buyer submits the deal proposal or final service selection.
The seller accepts the final offer, making the deal accepted.
The accepted deal records the buyer's service selections.
The system sends a notification to the ManzilCart operations team with the deal reference and the information needed for offline follow-up.
The operations team contacts the buyer and seller directly.
All further coordination happens offline.
The service request is not an online fulfilment-management workflow.

Explicit scope boundary
In the initial release, do not build:

In-app service scheduling
Staff or driver assignments
Delivery tracking
Assembly or disassembly progress tracking
Service-provider dashboards
Online service booking calendars
Service fee calculation or invoicing
Service payment collection
Service-payment reconciliation
Service completion or settlement workflows
Automated logistics integrations
The application only records the selected services and notifies the operations team.

The operations team manages the remaining process outside the platform.

Service coverage
Service options must respect coverage settings for the selected emirate and area.
If a service is unavailable in an area, disable it or clearly indicate its unavailability.
The direct buyer-seller deal must remain possible without optional services.
Validate service selections on the backend.
Store service selections against the relevant deal.
Notification reliability
Notification failure must not roll back an otherwise valid accepted deal.

Record notification status and retry failed notifications safely. Avoid sending duplicate operational requests when background tasks retry.

Send only the contact details required for operations follow-up, and restrict access to those details.

11. Auctions
Auctions are intended to help sellers sell furniture quickly.

Each auction is associated with a listing and inherits its emirate and service-area context.

Auction creation
Allow a seller to create an auction for an eligible listing.

Suggested fields:

Listing
Seller
Start and end timestamps
Optional reserve price or minimum acceptable price
Auction status
Current eligible bids
Selected bidder
Confirmation deadline
Final outcome
Validate ownership, listing eligibility, auction timing, and the seller's permitted actions.

Bidding
Eligible users can place bids during the bidding period.
Bid amounts are visible to users.
Bidder identities remain private to other bidders by default.
Store the bidder, amount, and timestamp for each valid bid.
Validate minimum bid rules and auction status on the server.
Prevent bids after the auction closes or after the seller locks a bid.
Protect against simultaneous bid updates and duplicate requests.
Use a consistent tie-breaking rule: if two eligible bids have the same amount, the earliest valid bid takes precedence.

Seller locks a bid
The seller can select and lock the current highest eligible bid before the auction's scheduled end.

Once the seller locks a bid:

Stop accepting new bids immediately.
Freeze the auction's eligible-bid sequence.
Notify the selected bidder.
Start a configurable confirmation deadline.
Do not automatically reopen bidding if the selected bidder declines or times out.
Locking is an explicit seller action. Do not automatically lock the highest bid just because the auction reaches its end unless a separate approved rule is introduced.

Bidder confirmation and fallback
Use a configurable confirmation window. A proposed default is six hours, but keep it configurable rather than hardcoding it.

The workflow must be:

The seller locks the selected highest eligible bid.
The selected bidder receives an email/SMS notification with a secure confirmation or decline mechanism and a deadline.
If the bidder confirms before the deadline, finalize the auction award and create the corresponding deal at that bidder's own bid price.
If the bidder declines or the deadline expires, mark that bidder's selection as declined or timed out.
Offer the opportunity to the next-highest eligible bidder.
Send that bidder a new notification and confirmation deadline.
Repeat until a bidder confirms or no eligible bidders remain.
If no eligible bidders remain, mark the auction unsold.
Important rules:

Each bidder appears only once in the fallback sequence, using their highest valid bid.
Do not reoffer an auction to a bidder who has already declined or timed out.
If bids are tied, prioritize the earliest valid bid.
The next bidder must receive a fresh confirmation deadline.
The final deal price must be the winning bidder's own bid, not the first bidder's bid.
The seller must agree to the fallback rules before the auction begins.
An optional reserve price may determine whether bids qualify for selection.
Once the seller locks a bid, never reopen the auction automatically.
Do not create multiple winning deals for the same auction or listing.
Do not create an accepted deal before a bidder has confirmed.
Use background jobs to process confirmation deadlines and send fallback notifications.

Make deadline processing idempotent and concurrency-safe. Two workers processing the same deadline must not award the auction twice or send conflicting offers.

12. Email and SMS Notifications
Use transactional email for account-related notifications, deal updates, auction confirmations, auction deadlines, fallback invitations, and operations alerts.

Consider Amazon SES or a similarly affordable email provider.

Use SMS only where necessary, particularly for auction confirmation and fallback if required. WhatsApp is the primary registration OTP channel.

Requirements:

Use an approved WhatsApp Business Platform provider for OTP.
Apply OTP expiry, resend limits, and abuse prevention.
Use email for appropriate transactional notifications.
Store notification status and relevant delivery/error information.
Retry transient failures with backoff.
Use idempotency keys or equivalent safeguards for background tasks.
Keep credentials in environment variables or a secrets manager.
Do not include OTPs, sensitive tokens, or unnecessary personal information in logs.
Do not assume that a notification was delivered just because an API request succeeded.

13. Django Admin and Operations
Build a minimal, secure admin interface using Django Admin and small custom views where necessary.

Authorized staff must be able to manage:

Users and supported areas
Emirates and service coverage
Listing moderation
Reports and disputes
Deals and cancellation requests
Auction status and fallback history
Notification failures and retry status
Relevant audit events
Display the emirate and service area in relevant administrative records.

Restrict sensitive information to authorized staff. Record significant administrative actions in an audit trail.

Do not build a large custom back-office platform unless needed.

14. Suggested Django App Structure
Use a practical structure such as:

config — settings, routing, ASGI, deployment configuration
accounts — custom user model, registration, mobile verification, profiles
locations — emirates, service areas, and coverage configuration
catalog — furniture categories, listings, images, search
moderation — listing reviews and moderation actions, if separate from catalog
conversations — conversations, messages, Channels consumers
deals — offers, counteroffers, accepted deals, cancellations, deal events
auctions — auctions, bids, selections, confirmation deadlines, fallback
notifications — email, SMS, WhatsApp provider integration, retry tracking
core — shared utilities, common permissions, health checks, and audit support
Do not create a separate Django app for every small model. Group functionality where that improves clarity.

Review this structure before implementing it and simplify where appropriate.

15. Suggested Database Entities
Use appropriate foreign keys, indexes, unique constraints, timestamps, and transaction boundaries.

Likely entities include:

User
UserProfile, if profile data warrants a separate model
Emirate
ServiceArea
Category
Listing
ListingImage
ListingReview or moderation record
Conversation
Message
Deal
DealOffer
DealCancellation
DealEvent
Auction
Bid
AuctionSelection or equivalent bidder-confirmation record
AuctionEvent
Notification
AuditEvent
An OutboxEvent model may be appropriate if it materially improves reliable delivery of notifications and background tasks.

Do not create unnecessary tables merely because they appear in this list. Explain each entity and its responsibilities before implementation.

Important database rules:

Create the custom user model before the first migration.
Enforce unique and valid geographic relationships.
Prevent conflicting accepted deals for a listing.
Protect auction award finalization from duplicate execution.
Keep deal and auction histories auditable.
Index common filtering and lookup fields.
Use transactions for multi-step state changes.
Use row-level locking where appropriate for concurrent operations.
16. Security Requirements
Treat security as a core requirement from the start.

Implement:

Secure authentication and session handling
CSRF protection
Object-level authorization
Server-side validation for every important state transition
OTP expiry, attempt limits, and rate limits
WebSocket authorization for chat
Validated image uploads
Safe output rendering and XSS prevention
Protection against SQL injection through appropriate ORM usage and safe query construction
Secure handling of environment variables and secrets
Appropriate CORS and allowed-host configuration where relevant
HTTPS in production
Least-privilege access to administrative and operational data
Audit logging for sensitive actions
Database backups and a tested restoration procedure
Dependency updates and secure production settings
Do not trust frontend validation as a security control.

Do not log passwords, OTPs, session tokens, or unnecessary personal data.

Apply emirate filtering to the backend as a marketplace scoping rule, but do not treat the selected emirate as a substitute for authentication or authorization.

17. Hosting and Budget
The initial hosting budget is approximately ₹15,000–₹20,000 per year, or around ₹1,250–₹1,667 per month.

Prioritize affordability during launch.

India-based hosting, potentially AWS Mumbai, is being considered to reduce costs. Evaluate response time for users in Abu Dhabi and Ras Al Khaimah before committing to the region.

Requirements:

Use a simple deployment architecture.
Avoid expensive managed services unless the budget supports them.
Evaluate a small VM for Django, PostgreSQL, Redis, and the background worker, while recognizing that running everything on a small instance can create memory and availability risks.
Consider the cost and reliability implications of keeping the database on the same machine.
Use appropriate object storage and backups for uploaded furniture images and database recovery.
Monitor CPU, memory, disk usage, database size, and application errors.
Plan an upgrade path when traffic or operational requirements grow.
Use environment-specific settings for development, testing, and production.
Keep the deployment reproducible.
The annual budget must be evaluated separately for:

Hosting and compute
Database and backups
Image/object storage
Domain and HTTPS requirements
WhatsApp OTP
SMS
Transactional email
Taxes and any provider fees
Do not promise that the complete platform can operate within ₹20,000 per year without estimating variable messaging and storage costs. Use current provider pricing when preparing a final deployment budget.

Before production deployment, review UAE privacy, e-commerce, licensing, and cross-border data-transfer requirements, as well as applicable Indian requirements for the hosting and operating arrangement. Do not treat a technical design as legal advice.

18. Explicitly Excluded from the Initial Release
Do not implement these features in the initial release:

Paid subscriptions
Paid verification or verified-seller packages
Paid listing boosts
Paid ranking or promoted listing products
Payment processing for furniture purchases
In-app logistics management
Delivery tracking
Staff/driver assignment
Online service scheduling
Service invoicing or payment collection
Service-payment reconciliation
Voice or video chat
Complex enterprise infrastructure
Keep the design extensible, but do not build future features prematurely. In particular, do not add subscription tables, subscription interfaces, paid verification workflows, or paid-ranking features in version one.

19. Implementation Plan
Work incrementally and keep the application runnable after every phase.

Phase 1 — Foundation
Create the Django project and Git repository.
Configure PostgreSQL.
Create the custom user model.
Configure environment variables and development settings.
Establish the initial database migrations.
Add a basic responsive layout.
Phase 2 — Emirate Selection and Accounts
Implement the emirate selection landing page.
Configure Abu Dhabi and Ras Al Khaimah.
Build emirate-aware routing and switching.
Add service-area management.
Implement WhatsApp OTP registration and login.
Validate user location and geographic relationships.
Phase 3 — Listings and Moderation
Implement furniture categories and listings.
Add image uploads.
Add admin approval before publication.
Build emirate-scoped browsing, search, filtering, and pagination.
Add listing detail pages.
Phase 4 — Chat
Implement conversations and persistent messages.
Configure Django Channels and WebSockets.
Add authentication, participant authorization, and message history.
Test unauthorized access and reconnect behaviour.
Phase 5 — Offers and Deals
Implement offers and counteroffers.
Implement seller acceptance of the final offer.
Add atomic listing reservation.
Implement cancellation requests, responses, and finalization.
Add deal history and notifications.
Phase 6 — Auctions
Implement auction creation and bidding.
Implement seller-controlled bid locking.
Implement bidder confirmation and deadlines.
Add automatic fallback to subsequent eligible bidders.
Add unsold outcomes and duplicate-award prevention.
Test concurrent bidding and background job retries.
Phase 7 — Optional Services and Operations Notifications
Implement area-specific service availability.
Record buyer-selected services against the deal.
Notify the operations team when the deal is accepted.
Add retry handling and notification status.
Keep all subsequent service coordination offline.
Phase 8 — Hardening and Deployment
Complete Django Admin configuration.
Add audit events and operational logging.
Add security and permission tests.
Configure production settings and HTTPS.
Configure backups and test restoration.
Test the application on desktop and mobile.
Estimate hosting, messaging, and storage costs.
Deploy and run end-to-end tests.
20. Testing Requirements
Write automated tests throughout development.

At minimum, test:

Accounts and locations
WhatsApp OTP verification
OTP expiry and rate limits
Duplicate verified phone numbers
Supported and unsupported registration areas
Invalid emirate/service-area combinations
Inactive emirates and service areas
Direct emirate URLs and emirate switching
Listings
Listing creation and ownership
Admin moderation
Visibility of approved versus unapproved listings
Emirate-scoped search and pagination
Invalid geographic associations
Image validation
Chat
Authorized and unauthorized conversations
WebSocket access controls
Message persistence
Cross-user data isolation
Deals
Offer and counteroffer transitions
Seller acceptance of the final offer
Concurrent acceptance attempts
Prevention of duplicate reservations
Cancellation requests and finalization
Listing reopening only after finalized cancellation
Services and notifications
Valid and invalid service selections
Area-specific service availability
Operations notification after accepted deal
Notification failure and retry
Duplicate notification prevention
Auctions
Valid and invalid bids
Bid tie-breaking
Seller bid locking
Rejection of bids after locking
Confirmation before deadline
Decline and timeout fallback
Correct fallback order
No bidder offered the auction twice
No eligible bidders remaining
Reserve-price rules
Concurrent deadline processing
Exactly one final auction award and deal
Correct final deal price
Security
Object-level authorization
Cross-emirate data leakage
Unauthorized API requests
Invalid state transitions
Administrative permission restrictions
21. Development Rules for ChatGPT
Act as a pragmatic senior Django engineer and technical guide.

I want to build this project properly while keeping it affordable and understandable.

Follow these rules:

Preserve the full specification. Do not silently remove previously defined requirements.
Explain architectural decisions briefly and clearly.
Work in small, testable implementation steps.
Before introducing a library, managed service, or additional component, explain why it is necessary and its approximate cost or operational impact.
Avoid unnecessary abstractions and overengineering.
Provide complete, runnable code for the files involved in each implementation step.
Clearly identify filenames and directories.
Include required imports, URL configuration, settings, migrations, and related changes where applicable.
Never assume a model, function, setting, or dependency already exists unless it has been established.
Explain required environment variables without exposing real credentials.
Include database migrations and tests when implementing new models or important business logic.
Use Django ORM safely and explain transaction boundaries for concurrency-sensitive workflows.
Do not implement critical business rules only in JavaScript.
Do not fabricate test results or claim that code has been executed when it has not.
When debugging, inspect the actual error and relevant code before proposing a fix.
Keep the application runnable at the end of each phase.
If a requirement is ambiguous, ask a focused question when the ambiguity could materially change the architecture or business workflow.
If a simpler, safer implementation satisfies the requirement, prefer it.
Do not add subscriptions, paid verification, paid listing boosts, or excluded logistics/payment features without explicit approval.
Maintain a clear record of completed phases, outstanding work, and important decisions.
22. First Task
Start by reviewing the specification and proposing the initial Django project structure, data model relationships, and phased implementation plan.

Explain the most important design decisions, especially:

How emirate selection and service-area validation work
How listings and searches are scoped to the selected emirate
How deals are accepted without duplicate reservations
How auctions handle seller locking, deadlines, and bidder fallback
How optional services trigger an operations notification without becoming a fulfilment workflow
How to keep the deployment affordable
Do not generate the entire application in one enormous response. Start with the architecture and foundational implementation, then proceed phase by phase, ensuring each stage is testable before moving on.