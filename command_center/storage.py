"""Generic local JSON/JSONL persistence primitives.

Two storage shapes are used in this project, deliberately:

- **Whole-file JSON** (`data/tasks.json`, `data/chats.json`, `data/project_config.json`):
  read-modify-write documents where the current state is the entire file. Writes are
  atomic: a temp file is written in the same directory, fsynced, then swapped in with
  `os.replace` (a single filesystem rename), so a crash mid-write cannot corrupt the
  destination file.
- **Append-only JSON Lines** (`data/runs.jsonl`, `data/activity.jsonl`): every write is a
  single `open(..., "a")` + one line + `flush` + `fsync`, never a rewrite of prior
  content. This is used for run records and activity events because both are
  write-heavy logs (a run is written at queued/running/completed and can carry a large
  stdout blob) where rewriting the whole file on every update would be slower and would
  briefly risk the *entire* history during the rewrite window. JSONL reduces that risk to
  only the single new line being appended. The "current" state of a run is the last line
  seen for its id (last-write-wins fold), which the caller performs.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable


def resolve_data_dir(root: Path) -> Path:
    """`root / "data"`, unless the `AICC_DATA_DIR` environment variable overrides it.

    Every `command_center` module and `app.py` itself call this the same way, so a
    single environment variable redirects *all* runtime storage at once. This exists
    primarily so the test suite (Streamlit `AppTest`-driven page renders, in
    particular) can point every module at an isolated temp directory and never read
    or write the developer's real `data/*.json(l)` files — see `tests/conftest.py`.
    """
    override = os.environ.get("AICC_DATA_DIR")
    return Path(override) if override else root / "data"


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def ensure_seeded(path: Path, empty_default: Any) -> None:
    """Ensure `path` exists, initialized to `empty_default` if missing.

    Deliberately does **not** read the tracked `.example` sibling file: those files
    hold illustrative sample content for documentation purposes (see e.g.
    `data/runs.example.jsonl`) and must never be copied into a live runtime file —
    doing so would present fabricated data as if it were real, which the app must
    never do. Compare `app.py`'s `load_tasks()`, which seeds from
    `tasks.example.json` deliberately, because that example is `[]` (truly empty).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        atomic_write_json(path, empty_default)


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def ensure_seeded_jsonl(path: Path) -> None:
    """Ensure `path` exists as an empty log. See `ensure_seeded` for why the
    tracked `.example.jsonl` sibling is never copied into the live file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")


def fold_latest_by_id(records: Iterable[dict], id_key: str = "id") -> dict[str, dict]:
    """Fold an append-only sequence of snapshots into the latest record per id.

    Later records in `records` win over earlier ones with the same id, so callers
    should pass records in the order they were appended (oldest first).
    """
    latest: dict[str, dict] = {}
    for record in records:
        record_id = record.get(id_key)
        if record_id is None:
            continue
        latest[record_id] = record
    return latest
