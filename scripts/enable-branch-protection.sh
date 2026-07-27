#!/usr/bin/env bash
# Enable branch protection on `main` requiring the "Quality gates" status check
# before merging, plus standard rules (no force-push, no delete).
#
# Requires: gh CLI authenticated with admin:repo_hook / repo scope.
# Usage:   bash scripts/enable-branch-protection.sh
set -euo pipefail

BRANCH="main"
CONTEXT="Quality gates (whitespace · Ruff · compile · pytest)"

# `gh api` targets the current repo by default (from git remote).
echo "→ Enabling branch protection on '$BRANCH' for $(gh repo view --json nameWithOwner -q .nameWithOwner)"

gh api -X PUT "repos/{owner}/{repo}/branches/${BRANCH}/protection" \
  --input - <<JSON
{
  "required_status_checks": {
    "strict": false,
    "contexts": ["${CONTEXT}"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_linear_history": false
}
JSON

echo "✓ Branch '$BRANCH' is now protected."
echo "  - Required check: ${CONTEXT}"
echo "  - Force-push: denied"
echo "  - Deletion: denied"
echo "  - Admins enforced: yes"