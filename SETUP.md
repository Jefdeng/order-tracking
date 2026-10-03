# Setup checklist

Goal: inbound email -> Gemini extracts items -> "Test Procurement Sheet" sheet is updated.
Start with **poll mode** (steps 1-7, no Pub/Sub, no ngrok). Add **Pub/Sub mode** (step 8) later if you want push delivery.

## 1. Python

- `/usr/bin/python3` (3.9.6) is Apple's copy. Don't upgrade or modify it. Newer Google and Gemini SDK releases are dropping 3.9, so use a newer Python.
- Homebrew already has Python 3.14.8 at `/opt/homebrew/bin/python3`. A virtualenv built from it is in `.venv/`, and the unit tests pass on it.
- Activate it in every new terminal: `source .venv/bin/activate`
- In VS Code: Cmd+Shift+P -> "Python: Select Interpreter" -> `.venv`.
- If you'd rather use a more conservative version: `brew install python@3.12`, then `rm -rf .venv && python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt -r requirements-dev.txt`

## 2. Gemini API key

- Create a key at https://aistudio.google.com/apikey and put it in `.env` as `GEMINI_API_KEY`. This is separate from your Google Cloud OAuth `credentials.json`.
- Model: `GEMINI_MODEL` in `.env`, default `gemini-2.5-flash`. Change it to any current Gemini model that supports JSON-schema output. `python check_setup.py` verifies the name is valid.
- **Privacy:** these emails contain private content. On the Gemini API's free tier, Google's terms allow using submitted content to improve its products. The paid tier does not. Enable billing on the key's project before pointing this at your real inbox.
- Cost on a paid Flash-class model is a fraction of a cent per email. Check current pricing at https://ai.google.dev/pricing.

## 3. Google Cloud (you said this is mostly done; verify)

- [ ] **Gmail API** and **Google Sheets API** are enabled (APIs & Services -> Enabled APIs).
- [ ] **OAuth consent screen** is configured.
- [ ] **Refresh-token gotcha:** if the app's publishing status is **Testing**, Google expires refresh tokens after **7 days**, and the pipeline will silently stop. Fix: OAuth consent screen -> "Publish app" (In production). For personal use it can stay unverified, and you'll just see a warning screen at sign-in.
- [ ] **OAuth client ID** of type **Desktop app**. Download the JSON and save it as `credentials.json` in this folder.
- [ ] The Google account you sign in with can open the "Test Procurement Sheet" sheet. No sharing step is needed, because the app acts as you. Set `SHEET_ID` in `.env` to the ID from the sheet's URL (`docs.google.com/spreadsheets/d/<ID>/edit`). The app opens the sheet by ID, because looking it up by name would need extra Drive permission.

## 4. Configure

```bash
cp .env.example .env     # then edit: set GEMINI_API_KEY at minimum
```

Keep `DRY_RUN=true` for now. The app logs what it would write but doesn't touch the sheet.

## 5. Sign in to Google (once)

```bash
python auth.py
```

A browser opens. If you see "Google hasn't verified this app", choose Advanced -> Continue. Approve both permissions (read Gmail, edit Sheets). This writes `token.json`.

## 6. Preflight

```bash
python check_setup.py
```

All lines should say OK. It checks the files, the Gmail login, the sheet (headers and item count) and the API key.

## 7. Try it

Test on mail you already have:

```bash
python reprocess.py "newer_than:30d" --limit 10
```

Read the `[DRY RUN] ...` log lines. Each shows whether the email was skipped or which row it would update or append. Tune `extractor.py`'s `SYSTEM_PROMPT` if the LLM decisions look off. When it looks right:

1. Set `DRY_RUN=false` in `.env`.
2. Run the server: `uvicorn main:app --port 8000`

It checks Gmail every 2 minutes (`POLL_INTERVAL_SECONDS`). The first run only records a starting point, so only mail arriving after that is processed. Use `reprocess.py` to backfill older mail.

## 8. Optional: Pub/Sub push mode

Only worth it if 2-minute polling isn't fast enough. Poll mode needs none of this.

### 8a. Enable the API
Console -> APIs & Services -> Library -> "Cloud Pub/Sub API" -> Enable.

### 8b. Create the topic
1. Console -> Pub/Sub -> Topics -> **Create topic**.
2. Topic ID: `gmail-push`. **Uncheck** "Add a default subscription". Create.
3. Copy the full name `projects/YOUR_PROJECT_ID/topics/gmail-push` into `PUBSUB_TOPIC` in `.env`.

### 8c. Let Gmail publish to it (the step people miss)
1. Open the topic -> **Permissions** (click "Show info panel" if you don't see the tab).
2. **Add principal**: `gmail-api-push@system.gserviceaccount.com`
3. Role: **Pub/Sub Publisher**. Save.

Without this, the Gmail watch call fails with a "not authorized" error.

### 8d. Service account for authenticated pushes
This lets the app confirm each request really came from your Pub/Sub, since the ngrok URL is public.
1. IAM & Admin -> Service Accounts -> Create: name `pubsub-push`. No roles needed.
2. Copy its email into `PUBSUB_SERVICE_ACCOUNT` in `.env`.
3. If the console asks while you create the subscription (8f), grant Pub/Sub permission to create tokens. That means giving `service-PROJECT_NUMBER@gcp-sa-pubsub.iam.gserviceaccount.com` the **Service Account Token Creator** role on `pubsub-push`.

### 8e. ngrok with a stable URL
1. https://dashboard.ngrok.com -> Domains -> create a free static domain, e.g. `something.ngrok-free.dev`. The free plan includes one. Check the dashboard if your plan differs.
2. `ngrok config add-authtoken YOUR_TOKEN` (once).
3. Run: `ngrok http --url=something.ngrok-free.dev 8000` (older ngrok versions use `--domain=`).

### 8f. Create the push subscription
Pub/Sub -> Subscriptions -> **Create subscription**:
- Subscription ID: `gmail-push-sub`, topic: `gmail-push`
- Delivery type: **Push**
- Endpoint URL: `https://something.ngrok-free.dev/api/webhook/gmail`
- Tick **Enable authentication**, service account: `pubsub-push`, audience: `tracking-app-webhook`
- Leave the other defaults. The app returns 200 immediately, so the 10s ack deadline is fine.

### 8g. Configure and run
In `.env`:
```
INGEST_MODE=pubsub
PUBSUB_TOPIC=projects/YOUR_PROJECT_ID/topics/gmail-push
PUBSUB_AUDIENCE=tracking-app-webhook
PUBSUB_SERVICE_ACCOUNT=pubsub-push@YOUR_PROJECT_ID.iam.gserviceaccount.com
```
Then run ngrok (8e) in one terminal and `uvicorn main:app --port 8000` in another. On startup the app registers the Gmail watch (look for "Gmail watch active" in the log) and renews it every 24 hours. Send yourself a test email from another address and watch the log.

### If you can't use a static ngrok domain
The URL changes on every restart. `scripts/update_push_endpoint.sh <subscription-id> <service-account-email>` reads the current URL from ngrok's local API and updates the subscription. It needs the gcloud CLI (`brew install --cask google-cloud-sdk`, then `gcloud auth login` and `gcloud config set project YOUR_PROJECT_ID`).

### Troubleshooting
- Watch call returns "User not authorized" or an invalid topic error: redo 8c, and check `PUBSUB_TOPIC` has the exact project ID.
- Pub/Sub shows push failures with 401/403: the audience or service account in `.env` doesn't match the subscription.
- No push arrives: confirm ngrok is running, the subscription endpoint matches, and `/health` works through the ngrok URL.

## How it updates your sheet

Scope: **tracking orders after they're placed.** You add the vendor's **Order No** and **Item SKU** to a row when you order. When a vendor email arrives, the app matches it to rows by Order No first, then SKU, and updates only those rows:

| Column | What it does |
|---|---|
| Status | Moves forward only: Ordered, In Production, Shipped, Received. Never backward. Delays, backorders and cancellations don't change it (they go in Notes). |
| Ordered / Shipped / Received | The event's date is written, only if the cell is blank. |
| Estimate Arrival, Lead Time | Overwritten when the email gives a different value. |
| Tracking (if you add the column) | Set if blank, otherwise it goes in Notes. |
| Order No | Filled in if blank, when the row was matched by SKU. |
| Notes | A dated line is appended, e.g. `[10/20] Arhaus: Shipped | order SO-4412 | UPS 1Z999`. |
| Everything else | Never touched: Qty, Priority, budgets, Quoted / Actual, Owner, Flag, links. |

Emails that can't be placed (order number not on the sheet, several rows that can't be told apart) go to the **Needs Review** tab with a Gmail link and the reason. They're retried for 3 days, so adding the Order No to the sheet afterwards fixes them automatically. Delays, backorders and cancellations are also listed on Needs Review so a person sees them. Create a tab named **Log** if you want a line per applied update.

## Known limits

- Attachment contents (PDF quotes, etc.) are not read; only filenames are passed to the LLM.
- Only INBOX mail is considered.
- Emails over 12,000 characters are truncated (logged).
- Gemini sometimes returns 503 "high demand". Failed emails are retried with backoff for about 15 minutes.
