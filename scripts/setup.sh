#!/usr/bin/env bash
# One-time local setup: Python 3.10+ virtualenv, dependencies, .env from the template, unit tests.
# Safe to re-run. Usage: scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PY_VERSION=$(cat .python-version)

if command -v uv >/dev/null 2>&1 || [ -x "$HOME/.local/bin/uv" ]; then
  UV=$(command -v uv || echo "$HOME/.local/bin/uv")
  echo "==> Creating .venv with Python $PY_VERSION (uv)"
  "$UV" venv --allow-existing --python "$PY_VERSION" .venv
  "$UV" pip install --quiet --python .venv/bin/python -r requirements.txt -r requirements-dev.txt
else
  PY=""
  for c in "python$PY_VERSION" python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
      PY=$c; break
    fi
  done
  if [ -z "$PY" ]; then
    echo "Python 3.10+ not found. Install uv (no admin rights needed), then re-run this script:" >&2
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
  fi
  echo "==> Creating .venv with $($PY --version)"
  "$PY" -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt -r requirements-dev.txt
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> Created .env from .env.example (DRY_RUN=true). Fill in GEMINI_API_KEY and SHEET_ID."
fi

echo "==> Running unit tests"
.venv/bin/python -m pytest -q

missing=""
[ -f credentials.json ] || missing="$missing credentials.json"
[ -f token.json ] || missing="$missing token.json"
echo
echo "Setup done. Activate with: source .venv/bin/activate"
if [ -n "$missing" ]; then
  echo "Still needed:$missing (see CONTRIBUTING.md, 'Local setup'), then: python check_setup.py"
else
  echo "Next: python check_setup.py"
fi
