#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f "$ROOT_DIR/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"
fi

if ! command -v streamlit >/dev/null 2>&1; then
  echo "Error: streamlit is not available on PATH." >&2
  echo "Create a virtual environment and run: pip install -r requirements.txt" >&2
  exit 1
fi

exec streamlit run "$ROOT_DIR/app.py" "$@"
