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

(cd web && [ -d node_modules ] || npm ci)
(cd web && npm run build)

exec python -m uvicorn "command_center.webapi.app:create_app" --factory \
  --host localhost --port 8791
