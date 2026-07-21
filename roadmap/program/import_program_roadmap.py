#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"File not found: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc


def normalize_existing(raw: Any) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if isinstance(raw, list):
        return raw, None
    if isinstance(raw, dict) and isinstance(raw.get("tasks"), list):
        return raw["tasks"], raw
    raise SystemExit("Unsupported tasks.json format: expected a list or an object with a tasks list.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the full program roadmap into AI Command Center.")
    parser.add_argument(
        "--repo",
        default=str(Path.home() / "Projects" / "ai-command-center"),
        help="AI Command Center repository path.",
    )
    parser.add_argument(
        "--roadmap",
        default=str(Path(__file__).with_name("program_roadmap.json")),
        help="Path to program_roadmap.json.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate and show changes without writing.")
    args = parser.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    roadmap_path = Path(args.roadmap).expanduser().resolve()
    tasks_path = repo / "data" / "tasks.json"

    roadmap = load_json(roadmap_path)
    incoming = roadmap.get("tasks")
    if not isinstance(incoming, list):
        raise SystemExit("Roadmap does not contain a tasks list.")

    raw_existing = load_json(tasks_path)
    existing, container = normalize_existing(raw_existing)

    existing_ids = {
        str(task.get("id") or task.get("task_key"))
        for task in existing
        if isinstance(task, dict)
    }

    duplicates = [task["id"] for task in incoming if task.get("id") in existing_ids]
    new_tasks = [task for task in incoming if task.get("id") not in existing_ids]

    all_ids = {task["id"] for task in incoming}
    broken_dependencies = {
        task["id"]: [dep for dep in task.get("depends_on", []) if dep not in all_ids and dep not in existing_ids]
        for task in incoming
    }
    broken_dependencies = {k: v for k, v in broken_dependencies.items() if v}
    if broken_dependencies:
        raise SystemExit(f"Broken dependencies: {broken_dependencies}")

    print(f"Existing tasks: {len(existing)}")
    print(f"Roadmap tasks: {len(incoming)}")
    print(f"Already present: {len(duplicates)}")
    print(f"To add: {len(new_tasks)}")

    if args.dry_run:
        for task in new_tasks:
            print(f"+ {task['id']} {task['title']}")
        return

    backup_dir = repo / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = backup_dir / f"tasks-before-program-roadmap-{stamp}.json"
    shutil.copy2(tasks_path, backup)

    existing.extend(new_tasks)
    output = existing if container is None else {**container, "tasks": existing}

    tmp_path = tasks_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(tasks_path)

    print(f"Imported {len(new_tasks)} tasks.")
    print(f"Backup: {backup}")
    print(f"Updated: {tasks_path}")


if __name__ == "__main__":
    main()
