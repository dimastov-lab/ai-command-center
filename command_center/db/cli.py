"""Operator entry point for the server database: `python -m command_center.db`.

Deliberately small and explicit. Migrations are not applied as a side effect of
the application starting, because that would make every replica in a rolling
deploy a potential migrator and would run schema changes under the application
credential. `bootstrap` runs once against a new database as a superuser; `upgrade` runs on
every deploy as the migrator. The application credential does neither.

    AICC_PG_USER=postgres       ... python -m command_center.db bootstrap
    AICC_PG_USER=aicc_migrator  ... python -m command_center.db upgrade
    AICC_PG_USER=aicc_app       ... python -m command_center.db status
"""

from __future__ import annotations

import argparse
import logging
import sys
from contextlib import nullcontext

from command_center.db import migrations, pool, roles
from command_center.db.config import ConfigError, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m command_center.db")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show the applied schema version.")
    sub.add_parser(
        "bootstrap",
        help="Create roles and set schema privileges (run once, as a superuser).",
    )
    sub.add_parser(
        "upgrade",
        help="Apply pending migrations and re-assert table grants (as the migrator).",
    )

    # The queue's recovery surface (SRV-06). These run as `aicc_app` — the
    # role the SQL protocol granted queue_reap/queue_redrive/work_dlq to —
    # which is why they live in the db CLI beside `status`, not in the worker.
    sub.add_parser(
        "queue-reap",
        help="Expire lapsed leases: requeue items with attempt budget left, "
        "dead-letter the exhausted (idempotent; run by aicc-queue-reaper.timer).",
    )
    dlq = sub.add_parser("queue-dlq", help="List dead-lettered work items.")
    dlq.add_argument("--queue", default=None, help="Restrict to one queue name.")
    dlq.add_argument("--limit", type=int, default=50, help="Rows to show (default 50).")
    redrive = sub.add_parser(
        "queue-redrive",
        help="Return one dead-lettered item to 'ready' with a raised attempt budget.",
    )
    redrive.add_argument("work_item_id", help="The wki_* id from queue-dlq.")
    redrive.add_argument(
        "--extra-attempts",
        type=int,
        default=1,
        help="Additional attempts to grant beyond those already burned (default 1).",
    )

    down = sub.add_parser("downgrade", help="Revert migrations down to a version.")
    down.add_argument(
        "--to",
        type=int,
        required=True,
        help="Target version to stop at (0 reverts everything).",
    )
    # A downgrade drops tables. Requiring the flag keeps a mistyped command from
    # destroying a production schema.
    down.add_argument(
        "--yes-i-understand-this-drops-data",
        action="store_true",
        dest="confirmed",
        help="Required acknowledgement that a downgrade is destructive.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    args = build_parser().parse_args(argv)

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    pool.open_pool(config)
    try:
        with pool.connection() as conn:
            if args.command == "status":
                print(f"target:  {config.redacted()}")
                print(f"applied: {list(migrations.applied_versions(conn))}")
                print(f"version: {migrations.current_version(conn)}")
                return 0

            if args.command == "bootstrap":
                count = roles.apply_bootstrap(conn)
                print(f"applied {count} role/schema statements")
                return 0

            if args.command == "upgrade":
                applied = migrations.upgrade(conn)
                print(f"applied: {list(applied)}" if applied else "already up to date")
                # Unconditionally, not only when something was applied: a table
                # created by a migration starts with no grants, and re-asserting
                # the matrix here is what keeps "migrated" and "reachable by the
                # app" the same state.
                count = roles.apply_table_grants(conn)
                print(f"re-asserted {count} table grants")
                return 0

            if args.command == "queue-reap":
                from command_center.db.work_queue_admin import WorkQueueAdmin

                reaped = WorkQueueAdmin(lambda: nullcontext(conn)).reap()
                print(f"reaped {reaped} lapsed attempt(s)")
                return 0

            if args.command == "queue-dlq":
                from command_center.db.work_queue_admin import WorkQueueAdmin

                letters = WorkQueueAdmin(lambda: nullcontext(conn)).dead_letters(
                    args.queue, limit=args.limit
                )
                if not letters:
                    print("dead-letter queue is empty")
                    return 0
                for letter in letters:
                    print(
                        f"{letter.work_item_id}  queue={letter.queue}  "
                        f"attempts={letter.attempt_count}/{letter.max_attempts}  "
                        f"dead_at={letter.dead_at}\n"
                        f"    dead_reason: {letter.dead_reason}\n"
                        f"    last_attempt: {letter.last_attempt_reason or '(none recorded)'}"
                    )
                return 0

            if args.command == "queue-redrive":
                from command_center.db.work_queue_admin import WorkQueueAdmin

                accepted = WorkQueueAdmin(lambda: nullcontext(conn)).redrive(
                    args.work_item_id, extra_attempts=args.extra_attempts
                )
                if accepted:
                    print(f"redriven: {args.work_item_id}")
                    return 0
                # The refusal is already audited server-side with its cause.
                print(
                    f"refused: {args.work_item_id} is unknown or not dead-lettered",
                    file=sys.stderr,
                )
                return 1

            if args.command == "downgrade":
                if not args.confirmed:
                    print(
                        "refusing to downgrade without "
                        "--yes-i-understand-this-drops-data",
                        file=sys.stderr,
                    )
                    return 2
                reverted = migrations.downgrade(conn, target=args.to)
                print(
                    f"reverted: {list(reverted)}" if reverted else "nothing to revert"
                )
                return 0
    finally:
        pool.close_pool()

    return 2  # pragma: no cover — argparse rejects unknown commands first


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
