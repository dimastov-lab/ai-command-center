#!/usr/bin/env bash
# Double-click this file on macOS to launch AI Command Center (native desktop).
# If the packaged .app exists — opens it. Otherwise falls back to dev mode via uv.

APP_BUNDLE="$HOME/Projects/ai-command-center/dist/macos/AI Command Center.app"
REPO="$HOME/Projects/ai-command-center"

if [[ -d "$APP_BUNDLE" ]]; then
  open "$APP_BUNDLE"
else
  cd "$REPO"
  uv run python -m command_center.desktop &
fi
