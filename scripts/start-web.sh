#!/usr/bin/env bash
# scripts/start-web.sh — build the web dashboard frontend and serve it
# together with the read-only API on http://localhost:8791 (single origin,
# no CORS needed in prod).
#
# Respects an existing AICC_DATA_DIR env var (resolved by
# command_center/storage.py); this script never sets or overrides it.
#
# Usage:
#   pip install -r requirements-web.txt
#   scripts/start-web.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f "$ROOT_DIR/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"
fi

if ! command -v python >/dev/null 2>&1; then
  echo "Error: python is not available on PATH." >&2
  echo "Create a virtual environment and install requirements-web.txt." >&2
  exit 1
fi

if ! python -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  echo "Error: FastAPI and Uvicorn are required." >&2
  echo "Run: python -m pip install -r requirements-web.txt" >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "Error: npm is not available on PATH." >&2
  exit 1
fi

(cd web && npm ci)
(cd web && npm run build)

exec python -m uvicorn "command_center.webapi.app:create_app" --factory \
  --host localhost --port 8791
