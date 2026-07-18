"""Launch orchestration as a plain-data service, fully decoupled from
Streamlit.

`app.py`'s `render_agent_launcher` collects widget input (project, prompt,
task type, timeout) and calls `execute_agent_launch`; nothing in this
module touches `st.*`, `session_state`, or any other Streamlit API. Per
`docs/desktop/ARCHITECTURE.md` §5 ("adapters... call existing functions
verbatim"), a future `command_center.application` adapter can call this
exact function from a `QRunnable` worker thread unchanged — it already
returns a plain dataclass, not a UI element, and every side effect
(subprocess execution, file writes) is delegated to already-existing,
already-tested `command_center/*` modules.

Routes execution through `command_center.executors` so the executor is a
parameter, not a hardcoded call to `agent_runner` — swapping in a future
ChatGPT/Codex/Gemini/remote executor requires no change here.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from command_center import activity_log, agent_runner, executors, launch, models, report_parser, workflow


@dataclass
class LaunchOutcome:
    run: dict
    parsed: dict
    result_status: str
    report_relpath: str


def execute_agent_launch(
    *,
    project: str,
    task_type: str,
    prompt: str,
    timeout_seconds: int,
    repository_path: Path,
    task: dict | None = None,
    executor_id: str = "claude_code",
    validation: launch.LaunchValidation | None = None,
    on_task_state_changed: Callable[[], None] | None = None,
) -> LaunchOutcome:
    """Runs `executor_id` synchronously against `repository_path`, parses
    the resulting report, and — if `task` is given — updates it in place
    with every derived field (progress/stage/timeline/PR/verdict/launch
    status/history). Returns the run record and outcome for the caller to
    persist and display.

    `on_task_state_changed`, if given, is invoked synchronously right after
    each in-place task mutation that happens *before* the (potentially
    multi-minute) blocking executor call — the caller's hook to persist
    that intermediate state so a crash mid-run doesn't lose the "Launching"/
    "Running" marker. A plain callback rather than a return value or a
    direct write, so this module stays storage-agnostic (Streamlit's
    `save_tasks`, a future desktop adapter's own persistence call, or a
    test's no-op are all equally valid callers)."""

    if task is not None:
        models.push_prompt_history(task, prompt)
        if validation is not None:
            launch.begin_launch(task, executor_id=executor_id, validation=validation)
        if on_task_state_changed is not None:
            on_task_state_changed()

    task_id = (task or {}).get("id")

    run = models.new_run_record(
        project=project,
        task_id=task_id,
        agent=executor_id,
        task_type=task_type,
        repository_path=str(repository_path),
        prompt=prompt,
        timeout_seconds=timeout_seconds,
    )
    run["pre_run"] = agent_runner.git_snapshot(repository_path)
    agent_runner.append_run(run)
    activity_log.log_event(
        "run_queued", project=project, task_id=task_id, run_id=run["id"],
        message=f"Запуск {task_type} поставлен в очередь",
    )

    run["status"] = "running"
    agent_runner.append_run(run)
    activity_log.log_event(
        "run_started", project=project, task_id=task_id, run_id=run["id"], message="Запуск начат",
    )
    if task is not None:
        task["launch_status"] = "Running"
        models.append_timeline_event(task, "executor_started", f"{executor_id} запущен.")
        if on_task_state_changed is not None:
            on_task_state_changed()

    executor = executors.get_executor(executor_id)
    result = executor.launch(
        repository_path=repository_path,
        prompt=prompt,
        task_type=task_type,
        timeout_seconds=timeout_seconds,
        model=agent_runner.default_model(),
    )

    run["status"] = result.status
    run["exit_code"] = result.exit_code
    run["stdout"] = result.stdout
    run["stderr"] = result.stderr
    run["started_at"] = result.started_at
    run["completed_at"] = result.completed_at
    run["duration_seconds"] = result.duration_seconds
    run["post_run"] = agent_runner.git_snapshot(repository_path)

    report_text = agent_runner.extract_result_text(result.stdout) if result.stdout else ""
    parsed = report_parser.parse_report(report_text)
    run["parsed"] = parsed

    report_path = agent_runner.save_report(run, parsed)
    run["report_path"] = os.path.relpath(report_path, agent_runner.ROOT)
    agent_runner.append_run(run)

    activity_log.log_event(
        "run_completed" if result.status == "completed" else "run_failed",
        project=project, task_id=task_id, run_id=run["id"],
        message=f"Статус: {result.status}, exit_code={result.exit_code}",
    )
    if parsed.get("verdict"):
        activity_log.log_event(
            "verdict_extracted", project=project, task_id=task_id, run_id=run["id"],
            message=f"Вердикт: {parsed['verdict']}",
        )
    activity_log.log_event(
        "report_saved", project=project, task_id=task_id, run_id=run["id"], message=report_path.name,
    )

    if task is not None:
        _apply_run_outcome_to_task(task, run=run, result_status=result.status, parsed=parsed, executor_id=executor_id)

    return LaunchOutcome(
        run=run, parsed=parsed, result_status=result.status, report_relpath=run["report_path"]
    )


def _apply_run_outcome_to_task(
    task: dict, *, run: dict, result_status: str, parsed: dict, executor_id: str
) -> None:
    task["current_run_id"] = run["id"]
    task["latest_verdict"] = parsed.get("verdict")
    task["report_path"] = run["report_path"]
    task["repository_path"] = run["repository_path"]
    task["branch"] = run["post_run"].get("branch")
    task["agent"] = executor_id
    task["executor"] = executor_id
    task["last_run_at"] = run["completed_at"]
    if parsed.get("pull_request_url"):
        task["pull_request_url"] = parsed["pull_request_url"]

    models.append_timeline_event(task, "validation_started", "Проверка результатов запуска.")

    if result_status == "completed":
        suggestion = workflow.suggest_next_task(run)
        task["workflow_stage"] = suggestion["workflow_stage"]
        if parsed.get("pull_request_url"):
            models.set_current_stage(task, "PR Ready")
            models.append_timeline_event(task, "pr_created", parsed["pull_request_url"])
        elif models.is_passing_verdict(parsed.get("verdict")):
            models.set_current_stage(task, "Validation")
            models.append_timeline_event(task, "tests_passed", f"Вердикт: {parsed['verdict']}")
        else:
            models.set_current_stage(task, "Coding Complete")
    else:
        task["workflow_stage"] = "Ready"

    launch.complete_launch(
        task,
        executor_id=executor_id,
        succeeded=result_status == "completed",
        workspace_path=run["repository_path"],
    )
    task["updated_at"] = models.iso_now()
