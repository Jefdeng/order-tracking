# Tasks: Vendor Order Tracking Automation

**Implements:** [requirements.md](requirements.md) · **Design:** [design.md](design.md)
Legend: `[x]` done and verified · `[ ]` open · `(R…)` requirement covered · `(D…)` design decision.

## Phase 1: Foundation ✔
- [x] T1.1 Project scaffold: FastAPI app, config from environment, `.env.example`, `.gitignore`
- [x] T1.2 Google sign-in (`auth.py`) producing `token.json` with Gmail read-only + Sheets scopes
- [x] T1.3 Gmail client: history polling, message parsing (plain/HTML, attachment names), body cap (R1.1, R7.4)
- [x] T1.4 Sheet access by ID (not name); header-row detection; header aliases (R6, D9)
- [x] T1.5 Preflight `check_setup.py` (Gmail, sheet, Gemini live call)
- [x] T1.6 Switch LLM to Gemini; discover and fix model-availability and quota behavior

## Phase 2: Order-tracking logic ✔
- [x] T2.1 Extraction schema and prompt: one update per (order number, event); events; untrusted-content rule (R2)
- [x] T2.2 Matching by Order No → SKU → model choice among candidates (R3.1–R3.5, D1, D2)
- [x] T2.3 Update policy: forward-only status, blank-only dates, estimates, tracking, notes, protected columns (R4)
- [x] T2.4 Idempotency and formula neutralisation (R4.8, R4.9)
- [x] T2.5 Needs Review tab, attention events, optional Log tab (R5)
- [x] T2.6 Pending retry of unmatched updates for 3 days; auto-resolve (R3.7, R5.3)
- [x] T2.7 Reliability: backoff retries, quota deferral, history-expired fallback, first-run baseline (R1.2–R1.6)
- [x] T2.8 Dry-run mode with value logging (R7.1)
- [x] T2.9 Unit tests: matching, update policy, parsing, state (37 passing)
- [x] T2.10 Live local verification on real mail (row updated to Shipped, then Received)

## Phase 3: Cloud deployment ✔ (polling)
- [x] T3.1 State abstraction with Firestore backend (D4)
- [x] T3.2 Credentials from secret; read-only-disk safe token refresh
- [x] T3.3 Cloud endpoints: `/tasks/poll`, `/tasks/renew-watch`, `/api/webhook/gmail`; work inside the request (D6)
- [x] T3.4 Dockerfile, `.dockerignore`, `DEPLOY.md`
- [x] T3.5 Enable APIs; service accounts; Firestore; Secret Manager
- [x] T3.6 Fix build permissions on the default compute account; deploy Cloud Run (private, max 1 instance)
- [x] T3.7 Cloud Scheduler `backup-poll` every minute (D5)
- [x] T3.8 Budget alert $5/month
- [x] T3.9 Cloud dry-run verified, then switched live (`DRY_RUN=false`); real email updated the sheet

## Phase 4: Hardening and next value (open)

### Gmail push (blocked)
- [ ] T4.1 **Organization admin** grants an org-policy exception or the Organization Policy Administrator role (blocked: org policy `iam.allowedPolicyMemberDomains` rejects `gmail-api-push@system.gserviceaccount.com`)
- [ ] T4.2 Temporarily lift the restriction on the project, add the publisher binding to topic `gmail-push`, restore the policy
- [ ] T4.3 Unpause `renew-gmail-watch`; trigger once; confirm "Gmail watch active" in logs
- [ ] T4.4 Send a test email; confirm update within seconds via push (R1.7)
- [ ] T4.5 Relax `backup-poll` to every 5 minutes

### Prerequisite checks
- [ ] T5.1 Confirm the OAuth consent screen is **In production** (otherwise the Google login expires after 7 days) (constraint)
- [ ] T5.2 Confirm billing stays attached; calendar reminder if the free trial ends (~90 days)
- [ ] T5.3 Exercise an unmatched-order email live and confirm the Needs Review row, then add the Order No and watch it auto-resolve (R3.6, R3.7)

### Improvements (ordered by value)
- [ ] T6.1 **Read PDF attachments** (confirmations/quotes): Gemini accepts PDFs; pass attachment content with the email
- [ ] T6.2 **Thread context**: include earlier messages so "it shipped" replies resolve to an order
- [ ] T6.3 Add a **Tracking** column to the sheet (code already supports it) and a **Log** tab if wanted
- [ ] T6.4 Alerting: notify when runs fail repeatedly or the quota is hit (Cloud Monitoring on error logs)
- [ ] T6.5 Confidence/approval gate for unusual changes (e.g., large estimate shifts)
- [ ] T6.6 Evaluation set: 30–50 real vendor emails with expected updates, scored on each prompt/model change
- [ ] T6.7 Automated test for the Firestore backend (emulator) and an integration test of the poll endpoint
- [ ] T6.8 Optional: Cloud Tasks chain for 15-second polling, if one minute proves too slow (see design D5)

### Housekeeping
- [ ] T7.1 Delete `test.txt` and the local `state.db`; keep `reports/` or remove it (contains one screenshot with a public IP)
- [ ] T7.2 Rotate the Gemini key if it was ever shared
- [ ] T7.3 Initialise git and commit (`.env`, tokens and `cloudrun.env.yaml` are already ignored)

## Dropped (recorded for context)
- Website price/lead-time/shipping crawler and the Quoted Unit Cost / Forecast columns: 55% of linked pages block automated fetches and an automated browser was blocked outright (D8).
- Quote and approval handling: kept human by design (requirements §3).
- Pub/Sub + ngrok on a laptop: still needs the laptop running (D4).

## Definition of done for the MVP
All acceptance items in requirements §8 pass, T4 or the polling fallback is in place, and T5.1 is confirmed.
