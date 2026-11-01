#!/usr/bin/env bash
# Deploy the committed code to the existing Cloud Run service.
#
# Ships code only: environment variables (DRY_RUN, SHEET_ID, ...), secrets, service account and
# scaling stay exactly as they are on the live service. Change those with
#   gcloud run services update tracking-app --region us-west1 --update-env-vars KEY=value
# never with --env-vars-file, which replaces every variable (and would e.g. turn DRY_RUN back on).
#
# Usage: scripts/deploy.sh            (project from TRACKING_PROJECT, else `gcloud config`)
#        ALLOW_DIRTY=1 scripts/deploy.sh   to deploy uncommitted changes or a branch other than main
set -euo pipefail
cd "$(dirname "$0")/.."

SERVICE=tracking-app
REGION=${REGION:-us-west1}
PROJECT=${TRACKING_PROJECT:-$(gcloud config get-value project 2>/dev/null)}
[ -n "$PROJECT" ] || { echo "No project: set TRACKING_PROJECT or run 'gcloud config set project ...'" >&2; exit 1; }

branch=$(git rev-parse --abbrev-ref HEAD)
if [ "${ALLOW_DIRTY:-}" != "1" ]; then
  [ -z "$(git status --porcelain)" ] || { echo "Uncommitted changes; commit first (or ALLOW_DIRTY=1)." >&2; exit 1; }
  [ "$branch" = "main" ] || { echo "On branch '$branch'; deploy from main (or ALLOW_DIRTY=1)." >&2; exit 1; }
fi
sha=$(git rev-parse --short HEAD)

echo "==> Unit tests"
.venv/bin/python -m pytest -q

echo "==> Deploying $sha ($branch) to $SERVICE in $PROJECT/$REGION"
gcloud run deploy "$SERVICE" --source . --project "$PROJECT" --region "$REGION" \
  --update-labels "git-sha=$sha" --quiet

echo
echo "Deployed. Watch the next few polls:"
echo "  gcloud run services logs read $SERVICE --project $PROJECT --region $REGION --limit 30"
