#!/usr/bin/env python3
"""One-shot migration: import all tasks from data/tasks.json into the AIOS Tasks API.

Usage:
    python scripts/migrate_tasks_to_aios.py [--dry-run] [--root PATH]

Environment variables required:
    AICC_AIOS_URL    — base URL of the AIOS API (e.g. https://aios.example.com)
    AICC_AIOS_TOKEN  — bearer token for authentication
    AICC_AIOS_TENANT_ID — tenant id (informational only; token encodes the tenant)

Idempotent: tasks whose AICC id is already in data/aios_task_map.json are skipped.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path when run as a script (flat layout, no install)
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Resolve project root (one level up from scripts/)
_DEFAULT_ROOT = _PROJECT_ROOT


def main(root: Path, *, dry_run: bool) -> int:
    url = os.environ.get("AICC_AIOS_URL")
    token = os.environ.get("AICC_AIOS_TOKEN")
    if not url or not token:
        logger.error("AICC_AIOS_URL and AICC_AIOS_TOKEN must be set")
        return 1

    from command_center import storage
    from command_center.application.aios_tasks import AIOSIdMap, build_aios_tasks_repository
    from command_center.tasks_repository import load_tasks

    tasks = load_tasks(root)
    logger.info("Found %d tasks in tasks.json", len(tasks))

    data_dir = storage.resolve_data_dir(root)
    id_map = AIOSIdMap(data_dir / "aios_task_map.json")

    skipped = 0
    migrated = 0
    failed = 0

    repo = None
    if not dry_run:
        repo = build_aios_tasks_repository(
            url=url,
            token=token,
            map_path=data_dir / "aios_task_map.json",
            id_map=id_map,
        )

    try:
        for task in tasks:
            aicc_id = task.get("id", "")
            if not aicc_id:
                logger.warning("Skipping task with no id: %r", task.get("title"))
                skipped += 1
                continue
            if id_map.get(aicc_id):
                logger.debug("Already migrated: %s", aicc_id)
                skipped += 1
                continue
            if dry_run:
                logger.info("[DRY-RUN] Would migrate: %s — %s", aicc_id, task.get("title"))
                migrated += 1
                continue
            try:
                created = repo.create(task)
                logger.info(
                    "Migrated %s → %s (%s)",
                    aicc_id,
                    created.get("aios_id"),
                    task.get("title"),
                )
                migrated += 1
            except Exception as exc:
                logger.error("Failed to migrate %s: %s", aicc_id, exc)
                failed += 1
    finally:
        if repo is not None:
            repo.close()

    logger.info("Done. migrated=%d  skipped=%d  failed=%d", migrated, skipped, failed)
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print what would happen, do nothing")
    parser.add_argument("--root", type=Path, default=_DEFAULT_ROOT, help="Project root directory")
    args = parser.parse_args()
    sys.exit(main(args.root, dry_run=args.dry_run))
