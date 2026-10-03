# Deploying to Google Cloud (Cloud Run + Pub/Sub + Firestore)

Replace `YOUR_PROJECT_ID` and `YOUR_SHEET_ID` below with your own values.

Result: Gmail pushes to Pub/Sub, Pub/Sub pushes to a private Cloud Run service, the service updates
your sheet. Nothing runs on your laptop. Cost should stay inside the free tiers; keep the $5 budget alert.

Gmail requires the Pub/Sub topic to be in the **same project as the OAuth client** (`credentials.json`),
so everything below uses project `YOUR_PROJECT_ID`.

## 0. Prerequisites (you)
- `brew install --cask google-cloud-sdk`, then `gcloud auth login`
- Project `YOUR_PROJECT_ID` has a billing account linked (Billing page).
- OAuth consent screen is **In production** (otherwise the Google login expires after 7 days).
- `python check_setup.py` passes locally and `token.json` exists.

## 1. Variables
```bash
export PROJECT=YOUR_PROJECT_ID REGION=us-west1
gcloud config set project $PROJECT
export PROJECT_NUMBER=$(gcloud projects describe $PROJECT --format='value(projectNumber)')
export RUNTIME=tracking-runtime@$PROJECT.iam.gserviceaccount.com
export INVOKER=tracking-invoker@$PROJECT.iam.gserviceaccount.com
```

## 2. APIs, database, service accounts
```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  pubsub.googleapis.com firestore.googleapis.com secretmanager.googleapis.com cloudscheduler.googleapis.com
gcloud firestore databases create --location=$REGION
gcloud iam service-accounts create tracking-runtime --display-name="TrackingApp runtime"
gcloud iam service-accounts create tracking-invoker --display-name="TrackingApp invoker (Pub/Sub + Scheduler)"
gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$RUNTIME --role=roles/datastore.user
```

## 3. Secrets
```bash
printf '%s' "PASTE_GEMINI_KEY" | gcloud secrets create gemini-api-key --data-file=-
gcloud secrets create google-token --data-file=token.json
for s in gemini-api-key google-token; do
  gcloud secrets add-iam-policy-binding $s --member=serviceAccount:$RUNTIME --role=roles/secretmanager.secretAccessor
done
```

## 4. Deploy (dry-run first)
Create `cloudrun.env.yaml` (not committed):
```yaml
INGEST_MODE: pubsub
STATE_BACKEND: firestore
DRY_RUN: "true"
SHEET_ID: YOUR_SHEET_ID
WORKSHEET_NAME: Procurement Detail
GEMINI_MODEL: gemini-3.8-flash
GOOGLE_CLOUD_PROJECT: YOUR_PROJECT_ID
PUBSUB_TOPIC: projects/YOUR_PROJECT_ID/topics/gmail-push
```
```bash
gcloud run deploy tracking-app --source . --region $REGION --service-account $RUNTIME \
  --no-allow-unauthenticated --max-instances 1 --memory 512Mi --timeout 300 \
  --env-vars-file cloudrun.env.yaml \
  --set-secrets GEMINI_API_KEY=gemini-api-key:latest,GOOGLE_TOKEN_JSON=google-token:latest
export URL=$(gcloud run services describe tracking-app --region $REGION --format='value(status.url)')
```

## 5. Pub/Sub
```bash
gcloud pubsub topics create gmail-push
gcloud pubsub topics add-iam-policy-binding gmail-push \
  --member=serviceAccount:gmail-api-push@system.gserviceaccount.com --role=roles/pubsub.publisher
gcloud run services add-iam-policy-binding tracking-app --region $REGION \
  --member=serviceAccount:$INVOKER --role=roles/run.invoker
gcloud iam service-accounts add-iam-policy-binding $INVOKER \
  --member=serviceAccount:service-$PROJECT_NUMBER@gcp-sa-pubsub.iam.gserviceaccount.com \
  --role=roles/iam.serviceAccountTokenCreator
gcloud pubsub subscriptions create gmail-push-sub --topic gmail-push --ack-deadline=120 \
  --push-endpoint=$URL/api/webhook/gmail --push-auth-service-account=$INVOKER --push-auth-token-audience=$URL
```

## 6. Scheduler (daily watch renewal + 5-minute backup check)
```bash
gcloud scheduler jobs create http renew-gmail-watch --location=$REGION --schedule="0 4 * * *" \
  --uri=$URL/tasks/renew-watch --http-method=POST --oidc-service-account-email=$INVOKER --oidc-token-audience=$URL
gcloud scheduler jobs create http backup-poll --location=$REGION --schedule="*/5 * * * *" \
  --uri=$URL/tasks/poll --http-method=POST --oidc-service-account-email=$INVOKER --oidc-token-audience=$URL
gcloud scheduler jobs run renew-gmail-watch --location=$REGION     # registers the Gmail watch now
```

## 7. Verify, then go live
```bash
gcloud run services logs read tracking-app --region $REGION --limit 50
# send a vendor-style test email; logs should show [DRY RUN] lines. Then:
gcloud run services update tracking-app --region $REGION --update-env-vars DRY_RUN=false
```
Stop everything: `gcloud run services delete tracking-app --region $REGION`, delete the two Scheduler jobs.
