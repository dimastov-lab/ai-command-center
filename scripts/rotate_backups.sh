#!/usr/bin/env bash
# Rotate ad-hoc snapshots under data/backups (ops item: 1.3 GB, no rotation).
#
# Policy: keep every backup younger than KEEP_DAYS (default 30), and always
# keep the KEEP_MIN (default 5) newest entries regardless of age. Everything
# else is a deletion candidate.
#
# Dry-run by default — prints what would be deleted and the reclaimed size.
# Pass --apply to actually delete.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-$(cd "$(dirname "$0")/.." && pwd)/data/backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"
KEEP_MIN="${KEEP_MIN:-5}"
APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

[[ -d "$BACKUP_DIR" ]] || { echo "no backup dir: $BACKUP_DIR"; exit 0; }

# Newest first, one entry per line: "<mtime-epoch>\t<path>"
entries=$(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -exec stat -f '%m%t%N' {} + | sort -rn)
total=$(printf '%s\n' "$entries" | grep -c . || true)
cutoff=$(( $(date +%s) - KEEP_DAYS * 86400 ))

index=0
reclaimed=0
while IFS=$'\t' read -r mtime path; do
  [[ -n "$path" ]] || continue
  index=$((index + 1))
  if (( index <= KEEP_MIN )) || (( mtime >= cutoff )); then
    continue
  fi
  size=$(du -sk "$path" | cut -f1)
  reclaimed=$((reclaimed + size))
  if (( APPLY )); then
    rm -rf "$path"
    echo "deleted: $path ($((size / 1024)) MB)"
  else
    echo "would delete: $path ($((size / 1024)) MB)"
  fi
done <<< "$entries"

mode=$([[ $APPLY == 1 ]] && echo "reclaimed" || echo "would reclaim")
echo "---"
echo "$total entries scanned; $mode $((reclaimed / 1024)) MB (keep: ${KEEP_MIN} newest + <${KEEP_DAYS}d)"
(( APPLY )) || echo "dry-run only — rerun with --apply to delete"
