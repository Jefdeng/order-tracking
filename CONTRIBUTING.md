# Working on this project

How to get access, run the app on your machine without touching the live sheet, and ship a change.
For what the app does, see [README.md](README.md); for how it's built, [design.md](design.md).

This repository is **public**. Never commit `.env`, `credentials.json`, `token.json`, `cloudrun.env.yaml`,
the sheet ID or the Google Cloud project ID. They're git-ignored; keep it that way.

## 1. Access you need

Ask the project owner for what your role needs:

| You want to | You need | The owner does |
|---|---|---|
| Change code, open PRs | Write access to the GitHub repo | Repo **Settings → Collaborators → Add people** |
| Run it locally against the real sheet | Edit access to the Google Sheet | Share the sheet with your Google account |
| | The OAuth client file `credentials.json` | Sends it privately (not by email or chat); or you download it from the Cloud project (**APIs & Services → Credentials**) |
| | A Gemini API key | You create your own at [Google AI Studio](https://aistudio.google.com/apikey), on a project with billing enabled |
| Read production logs | Google Cloud role **Logs Viewer** | `gcloud projects add-iam-policy-binding PROJECT --member=user:YOU --role=roles/logging.viewer` |
| Deploy | Roles **Cloud Run Source Developer** and **Service Account User** on the runtime account | `--role=roles/run.sourceDeveloper` on the project, plus `gcloud iam service-accounts add-iam-policy-binding tracking-runtime@PROJECT.iam.gserviceaccount.com --member=user:YOU --role=roles/iam.serviceAccountUser` |

If a deploy fails with a permission error naming a service account (`actAs`), the owner grants you
Service Account User on that account too; source builds may run as the project's default compute account.

If the OAuth consent screen is still in **Testing** mode, the owner must also add you under **Test users**,
and every login expires after 7 days. Setting it to **In production** fixes both.

## 2. Local setup

```bash
git clone https://github.com/Jefdeng/order-tracking && cd order-tracking
scripts/setup.sh              # Python 3.12 virtualenv, dependencies, .env from the template, unit tests
```

The script uses [uv](https://docs.astral.sh/uv/) if you have it, otherwise any Python 3.10+. If you have neither,
install uv (no admin rights needed): `curl -LsSf https://astral.sh/uv/install.sh | sh`.

Then:

1. Get the secrets. If you can read the Google Cloud project's secrets, run
   `gcloud auth login && gcloud config set project PROJECT && scripts/bootstrap_secrets.sh`. It writes
   `credentials.json` and `.env` (Gemini key included) without printing them. Otherwise, save the OAuth
   client file as `credentials.json` and put your own `GEMINI_API_KEY` in `.env`.
2. Check `.env`: `SHEET_ID` is set and `DRY_RUN=true`. Private project files (sheet links, project data)
   live in a separate private repository; clone it into `local/`, which git ignores.
3. `source .venv/bin/activate && python auth.py` signs in **your** Google account and writes `token.json`.
   The app reads that account's Gmail, so a dry run only sees vendor emails in your own mailbox.
4. `python check_setup.py` should print OK on every line.
5. `python reprocess.py --since 2026-10-01 --limit 10` shows what the app *would* change for recent mail.

## 3. Rules for local runs

The live service in Google Cloud already updates the sheet every minute. A local copy keeps its own state
(`state.db`), separate from production's, so it doesn't know what production has done.

- **Keep `DRY_RUN=true` locally.** `reprocess.py` is dry-run unless you pass `--apply`, whatever `.env` says.
- **Don't run `uvicorn main:app` with `DRY_RUN=false`** while the cloud service is live. Two writers would
  add duplicate Needs Review rows and Log lines.
- To test real writes, make a copy of the sheet (**File → Make a copy**), point `SHEET_ID` at the copy, and
  use `reprocess.py --apply`.

## 4. Making a change

1. Branch from `main`: `git checkout -b short-description`.
2. Change the code and add a unit test next to the existing ones in `tests/`. Matching and update rules
   ([matching.py](matching.py), `plan_update` in [sheets.py](sheets.py)) are pure functions and easy to test.
3. `python -m pytest -q`
4. Push and open a pull request. GitHub Actions runs the tests on every PR.
5. Merge to `main` once the tests pass and someone has reviewed it.

## 5. Deploying

Merging doesn't deploy. Anyone with deploy access runs this from an up-to-date `main`:

```bash
gcloud auth login && gcloud config set project PROJECT
git checkout main && git pull
scripts/deploy.sh
```

The script runs the tests, then ships the code only. The live settings stay as they are, including
`DRY_RUN`, `SHEET_ID`, the secrets and the scaling. Each revision is labelled with the commit it came from.

**Changing a live setting:** use `--update-env-vars`, which changes only the variables you name:

```bash
gcloud run services update tracking-app --region us-west1 --update-env-vars GEMINI_MODEL=...
```

Don't use `--env-vars-file` (as in DEPLOY.md's first-time setup) on the running service. It replaces
*every* variable, so an old file can silently switch production back to dry-run.

**Replacing the Google login** (after an expired token, or a new mailbox): the owner of the mailbox the
service reads runs `python auth.py`, then:

```bash
gcloud secrets versions add google-token --data-file=token.json
gcloud run services update tracking-app --region us-west1 --update-labels token-rotated=$(date +%Y%m%d)
```

The update starts a new revision, which picks up the new secret version.

**Checking on production:**

```bash
gcloud run services logs read tracking-app --region us-west1 --limit 50
gcloud scheduler jobs list --location us-west1
```
