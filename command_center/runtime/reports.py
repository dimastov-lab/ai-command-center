"""Renders the immutable report artifact for a run, from its `RunEvent` stream.

One report file per run (`report_path_for` embeds the run id), written once,
at a terminal state, with the full text of every assistant/result event this
run produced — never truncated (mirrors `agent_runner.save_report`'s
guarantee for v1.2). `Report` rows are 0..1 per run and are never rewritten
once created: `save_report` is only ever called once per run by the
supervisor, and re-running it against the same run id would produce a new
file (the timestamp is fixed at first call, but nothing here overwrites an
existing path — `db.create_report` has a primary key on `run_id` and will
raise on a second insert for the same run).
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from command_center.models import iso_now

ROOT = Path(__file__).resolve().parent.parent.parent
REPORTS_ROOT = ROOT / "reports"


def report_path_for(run: dict) -> Path:
    project = run.get("project") or "UNKNOWN"
    started = run.get("started_at") or run.get("created_at") or iso_now()
    try:
        started_dt = datetime.fromisoformat(started)
    except (ValueError, TypeError):
        started_dt = datetime.now()
    timestamp = started_dt.strftime("%Y%m%d-%H%M%S")
    run_part = (run.get("id") or "unknown")[:12]
    return REPORTS_ROOT / project / f"{timestamp}_{run_part}.md"


def _assistant_text_blocks(events: list[dict]) -> list[str]:
    blocks: list[str] = []
    for event in events:
        if event["event_type"] != "assistant_message":
            continue
        message = (event["payload"].get("message") or {})
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                blocks.append(block["text"])
    return blocks


def _tool_activity_lines(events: list[dict]) -> list[str]:
    lines: list[str] = []
    for event in events:
        if event["event_type"] != "assistant_message":
            continue
        message = event["payload"].get("message") or {}
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                lines.append(f"- `{block.get('name', '?')}` {block.get('input', {})}")
    return lines


def result_text(events: list[dict]) -> str:
    """The run's final `result` event text, or a placeholder if none exists
    yet. Public — also used by `task_sync.py` to feed `report_parser.
    parse_report` the same text v1.2 parses (the agent's final message, not
    the whole rendered report markdown)."""
    for event in reversed(events):
        if event["event_type"] == "result":
            text = event["payload"].get("result")
            if text:
                return text
    return "_Не найдено в событиях запуска._"


def _stderr_text(events: list[dict]) -> str:
    lines = [event["payload"].get("line", "") for event in events if event["event_type"] == "stderr_line"]
    return "\n".join(lines)


def _malformed_count(events: list[dict]) -> int:
    return sum(1 for event in events if event["event_type"] == "malformed")


def render_report_markdown(run: dict, events: list[dict]) -> str:
    duration = None
    if run.get("started_at") and run.get("completed_at"):
        try:
            started = datetime.fromisoformat(run["started_at"])
            completed = datetime.fromisoformat(run["completed_at"])
            duration = (completed - started).total_seconds()
        except (ValueError, TypeError):
            duration = None
    duration_str = f"{duration:.1f} с" if isinstance(duration, (int, float)) else "—"

    assistant_text = "\n\n".join(_assistant_text_blocks(events)) or "_Нет текстового вывода ассистента._"
    tool_lines = _tool_activity_lines(events)
    tool_text = "\n".join(tool_lines) if tool_lines else "_Инструменты не вызывались._"
    result_text_value = result_text(events)
    stderr_text = _stderr_text(events)
    malformed = _malformed_count(events)

    return f"""# Отчёт агента (v2 Session Supervisor)

- Run ID: `{run.get('id', '—')}`
- Session ID: `{run.get('session_id', '—')}`
- Task ID: `{run.get('task_id') or '—'}`
- Project: {run.get('project', '—')}
- Task type: {run.get('task_type', '—')}
- Repository: `{run.get('repository_path', '—')}`
- Resume: {'да' if run.get('is_resume') else 'нет'} (sequence {run.get('sequence', '—')})
- Started: {run.get('started_at') or '—'}
- Completed: {run.get('completed_at') or '—'}
- Duration: {duration_str}
- Exit code: {run.get('exit_code')}
- State: {run.get('state', '—')}
- Malformed stream lines encountered: {malformed}

## Prompt

```
{run.get('prompt', '')}
```

## Assistant output (полный, без сокращений)

{assistant_text}

## Инструменты (вызовы)

{tool_text}

## Итоговый результат (result)

```
{result_text_value}
```

## Stderr (полный, без сокращений)

```
{stderr_text}
```

## Git status до запуска

```
{run.get('pre_run_git_status') or '—'}
```

## Git status после запуска

```
{run.get('post_run_git_status') or '—'}
```
"""


def save_report(run: dict, events: list[dict]) -> Path:
    path = report_path_for(run)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = render_report_markdown(run, events)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, path)
    return path
