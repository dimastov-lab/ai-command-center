#!/usr/bin/env bash
set -euo pipefail

# Commit dirty working trees that block AICC task launches.
# Run this directly in the terminal — Claude Code Auto Mode cannot do it.

echo "=== Fix dirty working trees ==="

# 1. ai-command-center — 4 modified docs/config files
AICC_REPO="/Users/dmitrijcernikov/Projects/ai-command-center"
if [ -d "$AICC_REPO" ]; then
  echo ""
  echo "-- $AICC_REPO"
  git -C "$AICC_REPO" status --short
  if ! git -C "$AICC_REPO" diff --quiet || ! git -C "$AICC_REPO" diff --cached --quiet; then
    git -C "$AICC_REPO" add -A
    git -C "$AICC_REPO" commit -m "chore: commit audit docs and state update"
    echo "✓ committed"
  else
    echo "✓ already clean"
  fi
fi

# 2. aios — untracked files (.aios/, docker-compose.override.yml, docs/superpowers/, specs/)
AIOS_REPO="/Users/dmitrijcernikov/Projects/aios"
if [ -d "$AIOS_REPO" ]; then
  echo ""
  echo "-- $AIOS_REPO (adding untracked to .gitignore)"
  GITIGNORE="$AIOS_REPO/.gitignore"
  for entry in ".aios/" "docker-compose.override.yml" "docs/superpowers/" "specs/"; do
    if ! grep -qxF "$entry" "$GITIGNORE" 2>/dev/null; then
      echo "$entry" >> "$GITIGNORE"
      echo "  added to .gitignore: $entry"
    fi
  done
  if ! git -C "$AIOS_REPO" diff --quiet -- .gitignore || git -C "$AIOS_REPO" status --short | grep -q ".gitignore"; then
    git -C "$AIOS_REPO" add .gitignore
    git -C "$AIOS_REPO" commit -m "chore: ignore local runtime files"
    echo "✓ .gitignore committed"
  else
    echo "✓ .gitignore already up to date"
  fi
fi

# 3. AML-Governance-Platform — untracked .claude/
AML_REPO="/Users/dmitrijcernikov/Desktop/AML-Governance-Platform"
if [ -d "$AML_REPO" ]; then
  echo ""
  echo "-- $AML_REPO (adding .claude/ to .gitignore)"
  GITIGNORE="$AML_REPO/.gitignore"
  if ! grep -qxF ".claude/" "$GITIGNORE" 2>/dev/null; then
    echo ".claude/" >> "$GITIGNORE"
    git -C "$AML_REPO" add .gitignore
    git -C "$AML_REPO" commit -m "chore: ignore .claude/ local config"
    echo "✓ .gitignore committed"
  else
    echo "✓ .gitignore already up to date"
  fi
fi

echo ""
echo "=== Done. Reload AICC to pick up clean state. ==="
