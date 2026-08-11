#!/usr/bin/env bash
# scripts/start-web.sh — build the web dashboard frontend and serve it
# together with the read-only API on http://localhost:${PORT:-8791} (single
# origin, no CORS needed in prod).
#
# Respects an existing AICC_DATA_DIR env var (resolved by
# command_center/storage.py); this script never sets or overrides it.
# Override the listen port with PORT (defaults to 8791); the host stays
# localhost — the API is deliberately not exposed off-box.
#
# Usage:
#   pip install -r requirements-web.txt
#   scripts/start-web.sh            # serves on :8791
#   PORT=9000 scripts/start-web.sh  # serves on :9000

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN=""

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  # AICC commonly uses Git worktrees. Reuse the primary checkout's virtual
  # environment when this worktree does not have its own .venv.
  GIT_COMMON_DIR="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  if [[ -n "$GIT_COMMON_DIR" ]]; then
    PRIMARY_ROOT="$(dirname "$GIT_COMMON_DIR")"
    if [[ -x "$PRIMARY_ROOT/.venv/bin/python" ]]; then
      PYTHON_BIN="$PRIMARY_ROOT/.venv/bin/python"
    fi
  fi
fi

if [[ -z "$PYTHON_BIN" ]] && command -v python >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python)"
fi

if [[ -z "$PYTHON_BIN" ]] && command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "Error: Python is not available." >&2
  echo "Create .venv in this checkout or in the primary Git worktree." >&2
  exit 1
fi

if ! "$PYTHON_BIN" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  echo "Error: FastAPI and Uvicorn are required." >&2
  echo "Run: $PYTHON_BIN -m pip install -r requirements-web.txt" >&2
  exit 1
fi

# Cached SPA: reuse web/dist and skip npm entirely when the built bundle is
# newer than every frontend source. AICC_WEB_REBUILD=1 forces a rebuild;
# AICC_WEB_REBUILD=0 forbids one (fails fast if the bundle is missing).
REBUILD="${AICC_WEB_REBUILD:-auto}"
DIST_MARKER="web/dist/index.html"

needs_build() {
  [[ ! -f "$DIST_MARKER" ]] && return 0
  # Any frontend file newer than the built bundle invalidates the cache.
  [[ -n "$(find web -path web/dist -prune -o -path web/node_modules -prune \
      -o -type f -newer "$DIST_MARKER" -print -quit)" ]]
}

if [[ "$REBUILD" == "1" ]] || { [[ "$REBUILD" != "0" ]] && needs_build; }; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "Error: npm is not available on PATH." >&2
    exit 1
  fi
  (cd web && npm ci)
  (cd web && npm run build)
elif [[ ! -f "$DIST_MARKER" ]]; then
  echo "Error: web/dist is missing and AICC_WEB_REBUILD=0 forbids building." >&2
  exit 1
else
  echo "Reusing cached SPA bundle in web/dist (AICC_WEB_REBUILD=$REBUILD)."
fi

exec "$PYTHON_BIN" -m uvicorn "command_center.webapi.app:create_app" --factory \
  --host localhost --port "${PORT:-8791}"
