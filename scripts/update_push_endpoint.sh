#!/usr/bin/env bash
# Point a Pub/Sub push subscription at the current ngrok URL. Only needed if you do NOT
# use a reserved ngrok domain (free-tier random URLs change on every restart).
#
# Usage: scripts/update_push_endpoint.sh <subscription-id> <push-service-account-email> [audience]
# Requires: gcloud (logged in, project set) and ngrok running locally.
set -euo pipefail

SUB="${1:?usage: $0 <subscription-id> <push-service-account-email> [audience]}"
SA="${2:?usage: $0 <subscription-id> <push-service-account-email> [audience]}"
AUDIENCE="${3:-tracking-app-webhook}"   # must match PUBSUB_AUDIENCE in .env

BASE_URL=$(curl -s http://127.0.0.1:4040/api/tunnels | python3 -c '
import json, sys
tunnels = json.load(sys.stdin)["tunnels"]
print(next(t["public_url"] for t in tunnels if t["public_url"].startswith("https")))
')

echo "Setting push endpoint to ${BASE_URL}/api/webhook/gmail"
# modify-push-config replaces the whole push config, so the auth flags must be repeated.
gcloud pubsub subscriptions modify-push-config "$SUB" \
  --push-endpoint="${BASE_URL}/api/webhook/gmail" \
  --push-auth-service-account="$SA" \
  --push-auth-token-audience="$AUDIENCE"
