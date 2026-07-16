"""Append-only activity/event log — see `command_center.storage` module docstring for
why this is JSON Lines rather than a whole-file JSON array.

Never logs secrets or full sensitive document content: `models.new_activity_event`
truncates `message` to 500 characters, and callers here should always pass a short
summary (a title, an id, a verdict), never a full report or chat message body.
"""

from __future__ import annotations

from pathlib import Path

from command_center import models, storage

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = storage.resolve_data_dir(ROOT)
ACTIVITY_FILE = DATA_DIR / "activity.jsonl"
ACTIVITY_EXAMPLE_FILE = DATA_DIR / "activity.example.jsonl"


def log_event(
    event_type: str,
    *,
    project: str | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
    conversation_id: str | None = None,
    message: str = "",
) -> dict:
    event = models.new_activity_event(
        event_type,
        project=project,
        task_id=task_id,
        run_id=run_id,
        conversation_id=conversation_id,
        message=message,
    )
    storage.append_jsonl(ACTIVITY_FILE, event)
    return event


def load_activity(limit: int = 500) -> list[dict]:
    storage.ensure_seeded_jsonl(ACTIVITY_FILE)
    records = storage.read_jsonl(ACTIVITY_FILE)
    records.sort(key=lambda event: event.get("ts") or "", reverse=True)
    return records[:limit]
