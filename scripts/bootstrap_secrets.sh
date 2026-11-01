#!/usr/bin/env bash
# Fill in .env and credentials.json on a new computer from Google Secret Manager.
# Nothing secret is printed or committed. Safe to re-run.
#
# Needs: gcloud signed in with access to the project's secrets (`gcloud auth login`).
# Usage: scripts/bootstrap_secrets.sh            (project from TRACKING_PROJECT, else `gcloud config`)
#
# Secrets read: gemini-api-key -> GEMINI_API_KEY in .env, oauth-client -> credentials.json.
# Non-secret settings come from local/peninsula/app.env when present, else .env.example.
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT=${TRACKING_PROJECT:-$(gcloud config get-value project 2>/dev/null)}
[ -n "$PROJECT" ] || { echo "No project: set TRACKING_PROJECT or run 'gcloud config set project ...'" >&2; exit 1; }
secret() { gcloud secrets versions access latest --secret="$1" --project "$PROJECT" 2>/dev/null; }

if [ ! -f .env ]; then
  if [ -f local/peninsula/app.env ]; then cp local/peninsula/app.env .env; echo "==> .env created from local/peninsula/app.env"
  else cp .env.example .env; echo "==> .env created from .env.example (fill in SHEET_ID)"; fi
fi
chmod 600 .env

if key=$(secret gemini-api-key); then
  KEY="$key" python3 - <<'EOF'
import os, re
p, k = ".env", os.environ["KEY"].strip()
s = open(p).read()
s = re.sub(r"(?m)^GEMINI_API_KEY=.*$", "GEMINI_API_KEY=" + k, s) if re.search(r"(?m)^GEMINI_API_KEY=", s) else s.rstrip("\n") + "\nGEMINI_API_KEY=" + k + "\n"
open(p, "w").write(s)
EOF
  echo "==> GEMINI_API_KEY set in .env"
else
  echo "!!  Could not read secret 'gemini-api-key' in $PROJECT. Ask the owner for Secret Manager access." >&2
fi

if [ -f credentials.json ]; then
  echo "==> credentials.json already present"
elif json=$(secret oauth-client); then
  printf '%s' "$json" > credentials.json && chmod 600 credentials.json && echo "==> credentials.json written"
else
  echo "!!  No 'oauth-client' secret in $PROJECT. Download the Desktop OAuth client from"
  echo "    APIs & Services -> Credentials and save it as credentials.json." >&2
fi

echo
echo "Next: source .venv/bin/activate && python auth.py && python check_setup.py"
