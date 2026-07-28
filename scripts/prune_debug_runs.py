#!/usr/bin/env python3
"""Delete run records that are artifacts of debugging, not real execution history.

Two classes qualify, and only these two — both identified by a terminal state
with **no `failure_reason` at all**, which is what distinguishes a run that was
lost from one that genuinely failed:

- `INTERRUPTED` with no reason — the supervising process exited while the child
  was still alive (an app restart, a `pkill`), so reconciliation could not say
  what became of it. Nothing was learned from these runs.
- `FAILED` with no reason — the provider CLI exited non-zero before doing any
  work. In this repository's history these are the runs that hit an expired
  OAuth session: zero tokens spent, no attempt made.

Everything else is kept, including every classified failure
(`blocked:permission_denied`, `timeout`, `blocked:final_response`): those record
something that actually happened and that a human may need to review.

Deleting run rows changes the retry budget a task has consumed, because the
scheduler counts terminal runs. That is the *point* here — attempts spent on an
expired session or a killed supervisor should not count against a task — but it
is a real consequence, so this script is deliberately not run automatically by
anything.

Usage:
    python scripts/prune_debug_runs.py --dry-run   # list what would go
    python scripts/prune_debug_runs.py --apply     # delete, after a backup

`--apply` refuses to run unless `--backup` names a file that does not yet
exist; the database is copied there first. There is no undo otherwise.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from command_center.runtime import db  # noqa: E402

# Child rows that reference a run. Ordered children-first so no statement can
# leave a row pointing at a deleted parent, whatever the schema's cascade rules.
_CHILD_TABLES: tuple[str, ...] = (
    "run_event",
    "report",
    "completion_event",
    "validation_result",
    "completion",
)


def debug_artifacts(db_path: Path) -> list[dict]:
    """Runs that are debugging residue. See the module docstring for why the
    test is "terminal state *and* no failure_reason": a classified failure is
    history, an unclassified one is a run that never reported anything."""
    return [
        run
        for run in db.list_runs(db_path)
        if run["state"] in ("INTERRUPTED", "FAILED") and not (run.get("failure_reason") or "")
    ]


def prune(db_path: Path, run_ids: list[str]) -> None:
    if not run_ids:
        return
    placeholders = ", ".join("?" for _ in run_ids)
    with db.connect(db_path) as conn:
        with db.transaction(conn):
            for table in _CHILD_TABLES:
                try:
                    conn.execute(f"DELETE FROM {table} WHERE run_id IN ({placeholders})", run_ids)
                except Exception as exc:  # noqa: BLE001 — a table may not exist on older schemas
                    print(f"  пропущено {table}: {type(exc).__name__}")
            conn.execute(f"DELETE FROM run WHERE id IN ({placeholders})", run_ids)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="только показать, ничего не менять")
    mode.add_argument("--apply", action="store_true", help="удалить (требует --backup)")
    parser.add_argument("--backup", type=Path, help="куда скопировать БД перед удалением")
    args = parser.parse_args(argv)

    db_path = db.resolve_db_path()
    doomed = debug_artifacts(db_path)
    total = len(db.list_runs(db_path))

    print(f"База: {db_path}")
    print(f"Всего прогонов: {total}")
    print(f"К удалению: {len(doomed)}  ·  Остаётся: {total - len(doomed)}")
    print()
    for run in doomed:
        why = "осиротел при перезапуске" if run["state"] == "INTERRUPTED" else "провайдер не начал работу"
        print(f"  {run['state']:<12} {(run.get('task_id') or '—')[:24]:<26} {run['id'][:8]}  {why}")

    if args.dry_run:
        print("\n(ничего не изменено)")
        return 0

    if args.backup is None:
        print("\nОШИБКА: --apply требует --backup — отката нет.", file=sys.stderr)
        return 2
    if args.backup.exists():
        print(f"\nОШИБКА: файл бэкапа уже существует: {args.backup}", file=sys.stderr)
        return 2

    args.backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db_path, args.backup)
    print(f"\nБэкап: {args.backup}")

    prune(db_path, [run["id"] for run in doomed])
    print(f"Удалено: {len(doomed)}  ·  Осталось: {len(db.list_runs(db_path))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
