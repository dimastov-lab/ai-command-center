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

# Bind to localhost: the application has no authentication layer and runs
# privileged git/gh and agent subprocesses, so it must not be reachable from the
# local network. Passing an explicit --server.address still overrides this
# default — this script cannot stop that — but a non-loopback address is then
# refused by the application itself at first session
# (command_center/console_boundary.py, ADR 0010). The override exists to choose
# *which* loopback address, not whether to be on one.
if [[ $# -eq 0 ]] || ! grep -q -- "--server.address" <<<"$*"; then
  set -- --server.address localhost "$@"
fi

exec streamlit run "$ROOT_DIR/app.py" "$@"
