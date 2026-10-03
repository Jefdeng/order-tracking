# Requirements: Vendor Order Tracking Automation

**Status:** MVP live (cloud, polling every minute). Gmail push pending an organization-policy change.
**Related:** [design.md](design.md) · [tasks.md](tasks.md) · [SETUP.md](SETUP.md) · [DEPLOY.md](DEPLOY.md)

## 1. Problem

A design studio tracks procurement for a house renovation in a Google Sheet ("Procurement Detail" tab,
one row per item). After an order is placed, vendors send emails: confirmation, in production, shipped,
delayed, delivered. Someone has to read each one and update the row by hand: status, dates, estimated
arrival, tracking, notes. This is repetitive and easy to miss.

## 2. Goal

Automatically apply vendor order emails to the matching sheet rows, so the sheet reflects order reality
without manual entry, while keeping people in control of decisions (approvals, budgets, ordering).

## 3. Scope

**In scope (MVP)**
- Tracking items **after** an order has been placed.
- The person places the order and records the vendor's **Order No** and **Item SKU** on the row.
- Incoming vendor emails update that row's status, date fields, estimated arrival, lead time, tracking and notes.
- Anything the system cannot place with confidence is surfaced for a human.

**Out of scope (explicitly)**
- Customer approval, client review, quote requests and quote comparison. These stay human.
- Placing orders or sending email.
- Creating new rows. A new row would land outside the sheet's TOTAL formulas.
- Crawling retailer websites for price, stock or shipping. Evaluated and dropped (see design.md, decision D8).
- Reading email attachments (PDF quotes and confirmations). Planned, not built.

## 4. Users and stories

| ID | Story |
|---|---|
| U1 | As a procurement lead, when a vendor emails that an order shipped, I want the row to show Shipped, the ship date, the carrier and tracking, so I don't have to copy them. |
| U2 | As a procurement lead, I want delays, backorders and cancellations called out so I can react. |
| U3 | As a procurement lead, I want emails the system can't place listed with a link to the email and the reason, so nothing is lost. |
| U4 | As a procurement lead, I want to try changes safely (dry-run) before the system edits my sheet. |
| U5 | As the owner, I want it to run without my laptop on, at near-zero cost. |

## 5. Functional requirements

Format: **WHEN** *trigger*, **THE SYSTEM SHALL** *behavior*.

### R1. Ingestion
- **R1.1** WHEN the schedule fires (every 60 s in the cloud; every 15 s when run locally), the system SHALL fetch inbox messages added since its stored Gmail history position and process each new message.
- **R1.2** WHEN a message has already been processed, the system SHALL NOT process it again (idempotent per Gmail message ID).
- **R1.3** WHEN processing a message fails, the system SHALL retry with backoff of 60 s × attempt number, up to 6 attempts, then stop retrying it.
- **R1.4** WHEN the Gemini quota is exhausted (HTTP 429), the system SHALL hold the current and remaining messages and retry every 5 minutes without counting an attempt.
- **R1.5** WHEN the system first runs (no stored position), it SHALL record the current position and process only mail arriving afterwards.
- **R1.6** WHEN Gmail reports the stored position as expired, the system SHALL fall back to inbox mail from the last day (max 50 messages).
- **R1.7** WHERE Gmail push is enabled, a Pub/Sub notification SHALL trigger the same processing as a scheduled poll, and the scheduled poll SHALL remain as a backup. *(Blocked: see tasks.md T4.)*

### R2. Extraction
- **R2.1** The system SHALL decide whether an email concerns an existing order (confirmation, production, shipping, delivery, delay, backorder, cancellation, invoice) and ignore marketing, newsletters, quotes for unordered items and personal mail.
- **R2.2** The system SHALL return one update per distinct (order number, event) in the email, each with its own line items and notes.
- **R2.3** Each update SHALL carry, when stated: order number, vendor, event, event date, estimated arrival (ISO date), lead time, tracking number, carrier, line items (SKU, name, quantity), notes.
- **R2.4** The system SHALL treat email content as untrusted data and SHALL NOT follow instructions found in it.
- **R2.5** The system SHALL NOT guess: fields not stated in the email are left empty.

### R3. Matching an update to rows
- **R3.1** The system SHALL match first on **Order No**, then on **SKU**, using normalised comparison (case, spaces, punctuation ignored; cells may hold several ids).
- **R3.2** WHEN the email names items by SKU, the system SHALL narrow an order's rows to those SKUs.
- **R3.3** WHEN several rows remain and the email names items only by name, the model MAY choose among those candidate rows only; it SHALL NOT select rows the order number or SKU did not already allow.
- **R3.4** WHEN the email names no items and the order number matches several rows, the update SHALL apply to every row on that order and note that it did.
- **R3.5** WHEN a row is matched by SKU and has no Order No, the system SHALL fill in the Order No.
- **R3.6** WHEN no row can be identified with confidence, the system SHALL NOT edit any row and SHALL list the email on the Needs Review tab with the reason.
- **R3.7** WHEN an update was unmatched, the system SHALL retry it for 3 days (at most once a minute), so adding the Order No/SKU to the sheet afterwards resolves it automatically.

### R4. Sheet updates
- **R4.1** **Status** SHALL only move forward along: Not Started → Quote Requested → Client Review → Approved → Ordered → In Production → Shipped → Received → Installed. It SHALL NOT move backward and SHALL NOT overwrite Hold or unknown values.
- **R4.2** Event → status: order confirmed → Ordered; in production → In Production; shipped → Shipped; delivered → Received. Delayed, backordered, cancelled and other events SHALL NOT change status.
- **R4.3** **Ordered / Shipped / Received** dates SHALL be written only when the cell is blank, using the event date from the email if stated, otherwise the email's date.
- **R4.4** **Estimate Arrival** and **Lead Time** SHALL be overwritten when the email gives a different value; a delivered event SHALL NOT change Estimate Arrival.
- **R4.5** **Tracking** SHALL be set when blank; if the sheet has no Tracking column, carrier and tracking SHALL go in Notes.
- **R4.6** **Notes** SHALL receive an appended dated line (`[m/d] Vendor: Event | order N | carrier tracking | detail`), skipped if already present.
- **R4.7** The system SHALL NEVER modify: Qty, Priority, Unit/Item Budget, Quoted/Actual, Owner, Flag, Approved, Client Review, links.
- **R4.8** Applying the same email twice SHALL produce no further change.
- **R4.9** Text written to cells SHALL be neutralised so it cannot execute as a spreadsheet formula.

### R5. Review and audit
- **R5.1** The Needs Review tab SHALL list: date, sender, subject, Gmail link, order number, items, event, reason, proposed update, a Resolved? column and a message key.
- **R5.2** Delays, backorders and cancellations SHALL ALSO be listed on Needs Review even when the row was updated.
- **R5.3** WHEN a retried update later matches, the Needs Review row SHALL be marked "auto-applied".
- **R5.4** WHERE a tab named Log exists, the system SHALL append one line per applied update.

### R6. Tolerance of sheet layout
- **R6.1** The system SHALL find columns by header text (case/punctuation-insensitive, with aliases), so column order and extra columns do not matter.
- **R6.2** The system SHALL locate the header row itself (first 30 rows, must contain Room and Item).
- **R6.3** A missing optional column SHALL cause that field to be skipped, not an error.
- **R6.4** The sheet SHALL be identified by ID (not name); the tab by configured name.

### R7. Safety and control
- **R7.1** A dry-run mode SHALL log intended changes (with values) and write nothing to the sheet.
- **R7.2** The cloud service SHALL be private; only the scheduler and Pub/Sub identities may invoke it.
- **R7.3** Credentials (Gemini key, Google login) SHALL be held in a secret store, never in the image or repository.
- **R7.4** The email body sent to the model SHALL be capped (12,000 characters; truncation logged).

## 6. Non-functional requirements

| ID | Requirement |
|---|---|
| N1 | **Latency:** an update appears within about 2 minutes of the email arriving (1-minute poll + processing). Push mode would cut this to seconds. |
| N2 | **Cost:** stay within free tiers at expected volume (a handful of emails a day); budget alert at $5/month. Gemini usage is billed per call. |
| N3 | **Availability:** runs with the owner's laptop off; no email is lost during outages (position-based catch-up + retries). |
| N4 | **Correctness over coverage:** when unsure, do nothing to the sheet and ask a person. |
| N5 | **Testability:** matching and update rules are pure functions with unit tests. |

## 7. Constraints and assumptions

- Gmail push requires the Pub/Sub topic to be in the **same Google Cloud project as the OAuth client**.
- The organization enforces "restrict sharing to our own domain", which blocks Google's Gmail publishing account from the topic until an org-level exception exists.
- The Google login (OAuth refresh token) must not expire: the consent screen must be **In production**, not Testing.
- Operators record Order No and SKU on the row when ordering. Orders confirmed by email before that happens are retried for 3 days.
- Vendors' emails contain the order number in text (not only in an attachment).
- Gemini free tier is 20 requests/day per model; the key's project has billing enabled.

## 8. Acceptance (MVP)

1. A "shipped" email for an order on the sheet sets Status=Shipped, fills Shipped, and appends a note, within 2 minutes, with the laptop off. ✔ verified live
2. A "delivered" email sets Received and the Received date. ✔ verified live
3. Re-processing the same email changes nothing. ✔ unit-tested; ✔ verified live
4. An email with an unknown order number appears on Needs Review. ✔ unit-tested; ⏳ not yet exercised live
5. Dry-run writes nothing. ✔ verified live (local and cloud)

## 9. Open questions

- Should attachments (PDF confirmations/quotes) be read? (Most valuable next step.)
- Add a Tracking column? Add a Log tab?
- Who is the organization admin able to grant the Organization Policy Administrator role (needed for push)?
