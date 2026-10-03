# Order Tracking

Reads vendor order emails from Gmail, uses Gemini to pull out what happened (confirmed, shipped, delayed,
delivered...), and updates the matching rows in a Google Sheet: status, dates, estimated arrival, tracking
and notes. Built for tracking furniture, lighting and fixture orders on a renovation project, but nothing
is specific to that.

**Scope:** it tracks orders *after* they're placed. You record the vendor's **Order No** and **Item SKU** on
a row when you order; vendor emails then update that row. Approvals, quotes and budgets stay with people.

```
Vendor email → Gmail → this app → Gemini (reads the email) → match to a row by Order No / SKU → Google Sheet
                                                             ↘ anything unclear → "Needs Review" tab
```

The model only interprets text. Everything that touches your sheet (which row, what may change, what may never
change) is plain, unit-tested code. When it isn't sure, it changes nothing and asks you.

## What it does to your sheet

For the row an email belongs to:

| Column | Behavior |
|---|---|
| Status | Moves **forward only** (Ordered, In Production, Shipped, Received). Never backward. |
| Ordered / Shipped / Received | The event's date, only if the cell is blank. |
| Estimate Arrival, Lead Time | Updated when the email gives a different value. |
| Tracking (optional column) | Set if blank; otherwise it goes in Notes. |
| Order No | Filled in if blank, when the row was matched by SKU. |
| Notes | A dated line is appended, e.g. `[10/20] Vendor: Shipped | order SO-4412 | UPS 1Z999`. |
| Everything else | **Never touched**: quantities, budgets, owner, priority, approvals, links. |

Delays, backorders and cancellations are also listed on the **Needs Review** tab. Emails that can't be placed
(unknown order number, several rows that can't be told apart) go there too, with a link to the email, and are
retried for 3 days. Details: [requirements.md](requirements.md) and [design.md](design.md).

## What you need

You supply your own accounts. Nothing from the original author's setup is included.

- A **Google account** (Gmail + Google Sheets): the mailbox to watch and the sheet to update.
- A **Google Cloud project** (free; no billing needed to run locally) with the **Gmail API** and **Google Sheets API**
  enabled and an **OAuth client of type "Desktop app"**. Set the OAuth consent screen to **In production**, or the
  login expires after 7 days. Gmail's API only works for apps registered in a project; the project holds no email.
- A **Gemini API key** from [Google AI Studio](https://aistudio.google.com/apikey), with billing enabled (the free tier
  is about 20 requests a day and may use your content for training).
- **Python 3.10+**.
- A **Google Sheet** with this layout (column order doesn't matter, names are matched ignoring case and punctuation):
  - a tab (default name **Procurement Detail**) whose header row contains at least:
    `Room`, `Item`, `Order No`, `Item SKU`, `Status`, `Ordered`, `Shipped`, `Received`, `Estimate Arrival`,
    `Lead Time`, `Notes` (optional: `Tracking`). Title rows above the header are fine.
  - a tab named **Needs Review** (the app writes its headers). An optional tab named **Log** gets one line per update.
  - `Status` is expected to use these values: Not Started, Quote Requested, Client Review, Approved, Ordered,
    In Production, Shipped, Received, Installed, Hold.

## Quick start (local)

```bash
git clone https://github.com/Jefdeng/order-tracking && cd order-tracking
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

cp .env.example .env          # then set GEMINI_API_KEY and SHEET_ID (the long ID in the sheet's URL)
# save your OAuth client file as credentials.json in this folder

python auth.py                # opens a browser, signs in, creates token.json
python check_setup.py         # every line should say OK
python -m pytest -q           # optional: run the unit tests

python reprocess.py "newer_than:7d" --limit 5     # dry-run on recent mail: shows what it WOULD change
uvicorn main:app --port 8000                      # run it; polls Gmail every 15 s
```

`DRY_RUN=true` is the default in `.env.example`: it logs exactly what it would write (with values) and writes
nothing. Set `DRY_RUN=false` only once the log looks right. Only mail that arrives after the first run is processed;
use `reprocess.py` for older mail.

## Configuration

Settings are environment variables (read from `.env` locally). The common ones:

| Variable | Purpose | Default |
|---|---|---|
| `GEMINI_API_KEY` | Gemini key | required |
| `GEMINI_MODEL` | Model name | `gemini-3.8-flash` (availability varies; `check_setup.py` verifies it) |
| `SHEET_ID` | Spreadsheet ID or full URL | required |
| `WORKSHEET_NAME` | Tab with the table | first tab |
| `DRY_RUN` | Log intended changes, write nothing | `true` |
| `INGEST_MODE` | `poll` (local loop) or `pubsub` (cloud, driven by requests) | `poll` |
| `POLL_INTERVAL_SECONDS` | Local polling interval | `15` |
| `STATE_BACKEND` | `sqlite` (local file) or `firestore` (cloud) | `sqlite` |
| `REVIEW_TAB`, `LOG_TAB` | Tab names | `Needs Review`, `Log` |
| `PENDING_DAYS` | How long unplaced updates are retried | `3` |

## Run it in the cloud (laptop off)

[DEPLOY.md](DEPLOY.md) has the exact commands for Google Cloud: a private **Cloud Run** service, **Firestore** for state,
**Secret Manager** for your keys, and **Cloud Scheduler** polling every minute. At low volume it should stay inside the
free tiers; set a budget alert. Gmail *push* (Pub/Sub, near-instant) is also supported by the code, but Gmail requires the
Pub/Sub topic to be in the same project as the OAuth client, and an organization that restricts sharing to its own
domain will block Google's publishing account. A personal `@gmail.com` account doesn't have that restriction.

## Project layout

| File | Role |
|---|---|
| `main.py` | FastAPI app and endpoints (`/health`, `/tasks/poll`, `/api/webhook/gmail`, `/tasks/renew-watch`) |
| `pipeline.py` | Orchestration: new mail → extract → match → update; retries, quota handling, pending retry |
| `gmail_client.py` | Gmail access and email parsing |
| `extractor.py`, `models.py` | Gemini prompt and the structured-output schema |
| `matching.py` | Which sheet rows an update belongs to (pure logic) |
| `sheets.py` | Table detection, update rules, Needs Review / Log tabs |
| `state.py` | SQLite and Firestore state |
| `auth.py`, `check_setup.py`, `reprocess.py` | Sign-in, preflight check, dry-run/backfill tool |
| `tests/` | Unit tests |

Spec documents: [requirements.md](requirements.md) · [design.md](design.md) · [tasks.md](tasks.md).

## Security notes

- Never commit `.env`, `credentials.json` or `token.json` (they're git-ignored). `token.json` gives full read access to the
  mailbox and edit access to your sheets.
- The cloud service is deployed private; only Cloud Scheduler and Pub/Sub identities can call it.
- Email content is treated as untrusted. The model is told to extract only, and the sheet rules are enforced in code, so
  text in an email can't widen what gets written. Values that could run as spreadsheet formulas are neutralised.

## Known limits

- Attachments (PDF confirmations) aren't read yet; only the email text is.
- Earlier messages in a thread aren't given to the model.
- Quality depends on the model; availability can spike or hit quota (the app retries and holds messages).
- It never creates rows: unmatched items go to the Needs Review tab.
