"""Launches Claude Code non-interactively against a configured project repository.

Security model:

- `subprocess.run` is called exactly like the rest of this project already calls it
  (`app.py`'s `run_start_task_script`/`run_git_command`): a fixed argument list, never
  `shell=True`, `capture_output=True`, `text=True`, an explicit timeout. The task
  prompt is passed as a single argv element to the `claude` binary, never interpreted
  by a shell, so prompt content cannot inject shell commands.
- `validate_repository` refuses to run unless `repository_path` is *exactly* the path
  configured for that project in `command_center.project_config` (resolved, so
  symlink/`..` tricks can't escape it). Used directly by `chat_service` and
  `runtime.supervisor`, which have no per-task workspace concept — a run
  launched through either can never target a path outside project config.
  The task-launcher flow (`app.py`'s `render_agent_launcher`) instead resolves
  its path via `command_center.launch.resolve_workspace_path` — task
  `workspace_path` / project `default_workspace_path` / project
  `repository_path`, in that order — and validates *that* resolved path
  (same `expanduser().resolve()` symlink/`..` guard) rather than calling
  `validate_repository`; a task can still never be launched against a path
  that didn't come from that trusted precedence chain.
- This module itself never calls `git commit`/`push`/`merge`/`reset`/`rebase`/`clean` —
  the only git subprocess calls here are the read-only pre/post-run snapshot
  (`rev-parse`, `branch --show-current`, `status --porcelain`).

- **Read-only task types** (`review`, `final_gate`, `architecture_review`, see
  `READ_ONLY_TASK_TYPES`) receive genuine technical enforcement, not a prompt
  instruction: `build_command` passes `--tools` (which — per `claude --help` —
  replaces the entire *available* tool set, not merely a permission hint layered on
  top of it) set to exactly `READ_ONLY_ALLOWED_TOOLS` (`Read`, `Grep`, `Glob`). The
  `Bash` tool is not in that list, so it is not merely denied by pattern — it does
  not exist for that run. Nothing invocable through it (arbitrary shell redirection,
  `rm`/`mv`/`cp`/`sed -i`, `git add`/`apply`/`checkout`/`restore`/`switch`/`stash`/
  `commit`/`push`/`merge`/`reset`/`rebase`/`clean`, or anything else) is reachable,
  because there is no tool call that can reach a shell at all. `Edit`/`Write`/
  `NotebookEdit`/`MultiEdit` are likewise simply absent from the tool set. An earlier
  version of this module instead tried to deny specific `Bash(git ...)` patterns via
  `--disallowedTools` while leaving the general-purpose `Bash` tool itself available —
  an independent review found that left every *other* shell-reachable mutation
  (`git apply`, `git checkout`, `git stash`, plain file redirection, etc.)
  unrestricted for a task type documented as "must not modify any file." `--tools`
  closes that gap by removing Bash from what the run can invoke at all, rather than
  trying to enumerate everything Bash must not be allowed to do.
- **Implementation/remediation task types** keep `Bash` (they need it to run tests,
  linters, etc., per the `AGENT_ROLES` prompt rules in `scripts/start-task.sh`) but
  get `--disallowedTools` set to `GIT_WRITE_DISALLOWED_TOOLS`, which blocks the Bash
  patterns for every git operation `scripts/start-task.sh`'s own prompts already
  forbid these task types from performing (`add`, `apply`, `checkout`, `restore`,
  `switch`, `stash`, `commit`, `push`, `merge`, `reset`, `rebase`, `clean`, branch
  deletion). This is pattern-based denial, not tool removal — it does not, and does
  not claim to, prevent all possible repository mutation via Bash, only the git-write
  operations listed above. These task types are *expected* to edit files (that is
  their job); the boundary being enforced here is specifically "no git writes," not
  "no writes."
- **This application's own code** is the only place the actual git-write prohibition
  (never commit/push/merge/reset/rebase/delete/clean *automatically*) is absolute: it
  is simply never called by any code path in this codebase, verified by the absence
  of any such `subprocess`/`git` invocation outside the read-only snapshot calls
  listed above. That guarantee is unconditional and does not depend on what a spawned
  `claude` process chooses to do.

- Cancellation: Streamlit re-executes the whole script top-to-bottom on every
  interaction, so there is no supervisor process that could safely interrupt a
  `subprocess.run` call already in flight from a *previous* run. This module
  therefore implements a robust **synchronous** runner: the calling Streamlit page
  blocks (with a spinner) until the process exits or the timeout fires. "Cancelled" is
  a valid `RUN_STATUSES` value but is only ever reached if the process could not be
  started at all — there is no fake mid-flight cancel button. This is a known,
  documented limitation (see ARCHITECTURE.md / README "Known limitations").
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from command_center import models, project_config, storage

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = storage.resolve_data_dir(ROOT)
RUNS_FILE = DATA_DIR / "runs.jsonl"
RUNS_EXAMPLE_FILE = ROOT / "data" / "runs.example.jsonl"

CLAUDE_BINARY = "claude"

READ_ONLY_TASK_TYPES = {"review", "final_gate", "architecture_review"}

# The *complete* available tool set for read-only task types, passed via `--tools`
# (not `--allowedTools`/`--disallowedTools`). Per `claude --help`, `--tools` replaces
# the built-in tool set outright rather than layering a permission rule on top of it,
# so a tool simply absent here cannot be invoked by that run at all — in particular,
# `Bash` (and therefore every shell-reachable mutation: `rm`/`mv`/`cp`/`sed -i`,
# redirection, `git add`/`apply`/`checkout`/`restore`/`switch`/`stash`/`commit`/
# `push`/`merge`/`reset`/`rebase`/`clean`, or anything else) is not merely denied by
# pattern, it does not exist for that run. `Edit`/`Write`/`NotebookEdit`/`MultiEdit`
# are likewise simply not in this list. This app already captures the pre/post-run
# git snapshot itself (`git_snapshot`, below), so a read-only run does not need shell
# access to git to do its job.
READ_ONLY_ALLOWED_TOOLS: list[str] = ["Read", "Grep", "Glob"]

# Bash patterns blocked via `--disallowedTools` for implementation/remediation runs,
# which keep the `Bash` tool (they need it to run tests/linters/etc. per the
# `AGENT_ROLES` prompt rules in `scripts/start-task.sh`). This is pattern-based
# denial of specific git-write subcommands, not tool removal, and does not claim to
# block every possible repository mutation reachable through Bash — only the git
# operations these task types' own prompts already forbid them from performing.
GIT_WRITE_DISALLOWED_TOOLS: list[str] = [
    "Bash(git add:*)",
    "Bash(git apply:*)",
    "Bash(git checkout:*)",
    "Bash(git restore:*)",
    "Bash(git switch:*)",
    "Bash(git stash:*)",
    "Bash(git commit:*)",
    "Bash(git push:*)",
    "Bash(git merge:*)",
    "Bash(git reset:*)",
    "Bash(git rebase:*)",
    "Bash(git clean:*)",
    "Bash(git branch -d:*)",
    "Bash(git branch -D:*)",
]

DEFAULT_TIMEOUT_SECONDS = 900
MIN_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 3600


class RunnerError(Exception):
    """Raised when a run cannot even be attempted (validation failure)."""


def claude_cli_available() -> bool:
    return shutil.which(CLAUDE_BINARY) is not None


def validate_repository(project_id: str, repository_path: str) -> Path:
    """Raise RunnerError unless `repository_path` is the configured path for `project_id`."""
    if not repository_path:
        raise RunnerError("Путь к репозиторию не указан.")
    config = project_config.get_project_config(project_id)
    configured = config.get("repository_path")
    if not configured:
        raise RunnerError(
            f"Путь к репозиторию не настроен для проекта {project_id}. "
            "Настройте его в разделе «Проекты» → «Настройки репозитория»."
        )
    resolved_requested = Path(repository_path).expanduser().resolve()
    resolved_configured = Path(configured).expanduser().resolve()
    if resolved_requested != resolved_configured:
        raise RunnerError(
            "Указанный путь репозитория не совпадает с настроенным путём проекта. "
            "Запуск отклонён."
        )
    if not resolved_configured.is_dir():
        raise RunnerError(f"Настроенный путь репозитория не существует: {resolved_configured}")
    return resolved_configured


def _run_git(args: list[str], cwd: Path, timeout: int = 10) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def is_git_repository(repo_path: Path) -> bool:
    result = _run_git(["rev-parse", "--is-inside-work-tree"], cwd=repo_path)
    return bool(result and result.returncode == 0 and result.stdout.strip() == "true")


def git_snapshot(repo_path: Path) -> dict:
    """Read-only branch/HEAD/status snapshot of `repo_path`, used for pre/post-run records."""
    if not is_git_repository(repo_path):
        return {"is_git_repo": False, "branch": None, "head": None, "status_summary": None}

    branch = _run_git(["branch", "--show-current"], cwd=repo_path)
    head = _run_git(["rev-parse", "--short", "HEAD"], cwd=repo_path)
    status = _run_git(["status", "--porcelain"], cwd=repo_path)

    status_lines = [line for line in (status.stdout.splitlines() if status and status.returncode == 0 else []) if line]

    return {
        "is_git_repo": True,
        "branch": (branch.stdout.strip() if branch and branch.stdout.strip() else "(detached HEAD)"),
        "head": head.stdout.strip() if head and head.returncode == 0 else None,
        "status_summary": "\n".join(status_lines) if status_lines else "(чисто)",
    }


def build_command(
    prompt: str,
    *,
    task_type: str,
    model: str | None = None,
) -> list[str]:
    command = [
        CLAUDE_BINARY,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--permission-mode",
        "acceptEdits",
    ]

    if task_type in READ_ONLY_TASK_TYPES:
        # Tool-set replacement, not a permission-layer denial: Bash (and every
        # shell-reachable mutation) is not in this list, so it cannot be invoked by
        # this run at all. See the module docstring and READ_ONLY_ALLOWED_TOOLS.
        command += ["--tools", ",".join(READ_ONLY_ALLOWED_TOOLS)]
    else:
        # Bash stays available (implementation/remediation runs need it), but the
        # specific git-write subcommands their own prompts already forbid are denied.
        command += ["--disallowedTools", ",".join(GIT_WRITE_DISALLOWED_TOOLS)]

    if model:
        command += ["--model", model]
    return command


def extract_result_text(stdout: str) -> str:
    """Extract the assistant's final report text from `claude -p --output-format json` stdout.

    Falls back to the raw stdout verbatim if it isn't parseable JSON in a recognized
    shape — the caller always has the full original stdout available regardless, this
    is only used to find the best candidate text to run the report parser against.
    """
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return stdout
    if isinstance(data, list):
        for item in reversed(data):
            if isinstance(item, dict) and item.get("type") == "result":
                return item.get("result", stdout) or stdout
        return stdout
    if isinstance(data, dict):
        return data.get("result", stdout) or stdout
    return stdout


class RunResult:
    def __init__(
        self,
        *,
        status: str,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        duration_seconds: float,
        started_at: str,
        completed_at: str,
    ) -> None:
        self.status = status
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration_seconds = duration_seconds
        self.started_at = started_at
        self.completed_at = completed_at


def run_claude_code(
    *,
    repository_path: Path,
    prompt: str,
    task_type: str,
    timeout_seconds: int,
    model: str | None = None,
) -> RunResult:
    """Execute Claude Code synchronously. Never raises for expected failure modes."""
    command = build_command(prompt, task_type=task_type, model=model)
    started_at = models.iso_now()
    started_monotonic = time.monotonic()

    try:
        completed = subprocess.run(
            command,
            cwd=repository_path,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        duration = time.monotonic() - started_monotonic
        status = "completed" if completed.returncode == 0 else "failed"
        return RunResult(
            status=status,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_seconds=duration,
            started_at=started_at,
            completed_at=models.iso_now(),
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - started_monotonic
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return RunResult(
            status="timed_out",
            exit_code=None,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration,
            started_at=started_at,
            completed_at=models.iso_now(),
        )
    except OSError as exc:
        duration = time.monotonic() - started_monotonic
        return RunResult(
            status="failed",
            exit_code=None,
            stdout="",
            stderr=f"Не удалось запустить Claude Code: {exc}",
            duration_seconds=duration,
            started_at=started_at,
            completed_at=models.iso_now(),
        )


def resolve_timeout(requested: int | None) -> int:
    if not requested:
        return DEFAULT_TIMEOUT_SECONDS
    return max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, int(requested)))


def default_model() -> str | None:
    """Model override from environment, e.g. CLAUDE_CODE_MODEL=sonnet. Optional."""
    value = os.environ.get("CLAUDE_CODE_MODEL", "").strip()
    return value or None


# --------------------------------------------------------------------------
# Run persistence (append-only; see command_center.storage module docstring)
# --------------------------------------------------------------------------


def append_run(run: dict) -> dict:
    """Append a full snapshot of `run` as the new latest state for its id."""
    snapshot = dict(run)
    snapshot["updated_at"] = models.iso_now()
    storage.append_jsonl(RUNS_FILE, snapshot)
    return snapshot


def load_runs() -> list[dict]:
    """Load every run's *latest* snapshot, newest-created first."""
    storage.ensure_seeded_jsonl(RUNS_FILE)
    records = storage.read_jsonl(RUNS_FILE)
    latest = storage.fold_latest_by_id(records)
    return sorted(latest.values(), key=lambda run: run.get("created_at") or "", reverse=True)


def get_run(run_id: str) -> dict | None:
    for run in load_runs():
        if run.get("id") == run_id:
            return run
    return None


# --------------------------------------------------------------------------
# Full report storage (FEATURE 3) — never truncated
# --------------------------------------------------------------------------

REPORTS_ROOT = ROOT / "reports"


def report_path_for(run: dict) -> Path:
    project = run.get("project") or "UNKNOWN"
    started = run.get("started_at") or run.get("created_at") or models.iso_now()
    try:
        started_dt = datetime.fromisoformat(started)
    except ValueError:
        started_dt = datetime.now()
    timestamp = started_dt.strftime("%Y%m%d-%H%M%S")
    task_part = (run.get("task_id") or "adhoc")[:12]
    agent = run.get("agent") or "agent"
    filename = f"{timestamp}_{task_part}_{agent}.md"
    return REPORTS_ROOT / project / filename


def _format_findings_markdown(parsed: dict) -> str:
    findings = parsed.get("findings") or {}
    if not any(findings.values()):
        return "_Не найдено / не указано в отчёте._"
    lines = []
    for severity in models.SEVERITIES:
        items = findings.get(severity) or []
        if not items:
            continue
        lines.append(f"**{severity}:**")
        lines.extend(f"- {item}" for item in items)
    return "\n".join(lines)


def render_report_markdown(run: dict, parsed: dict) -> str:
    pre = run.get("pre_run") or {}
    post = run.get("post_run") or {}
    duration = run.get("duration_seconds")
    duration_str = f"{duration:.1f} с" if isinstance(duration, (int, float)) else "—"

    return f"""# Отчёт агента

- Run ID: `{run.get('id', '—')}`
- Task ID: `{run.get('task_id') or '—'}`
- Project: {run.get('project', '—')}
- Agent: {run.get('agent', '—')}
- Task type: {run.get('task_type', '—')}
- Repository: `{run.get('repository_path', '—')}`
- Branch before run: {pre.get('branch') or '—'}
- HEAD before run: {pre.get('head') or '—'}
- Branch after run: {post.get('branch') or '—'}
- HEAD after run: {post.get('head') or '—'}
- Started: {run.get('started_at') or '—'}
- Completed: {run.get('completed_at') or '—'}
- Duration: {duration_str}
- Exit code: {run.get('exit_code')}
- Status: {run.get('status', '—')}

## Prompt

```
{run.get('prompt', '')}
```

## Stdout (полный, без сокращений)

```
{run.get('stdout', '')}
```

## Stderr (полный, без сокращений)

```
{run.get('stderr', '')}
```

## Извлечённые данные (парсер)

- Verdict: {parsed.get('verdict') or 'не определено'}
- Confidence: {parsed.get('confidence', 'none')}
- Commit hash: {parsed.get('commit_hash') or '—'}
- Branch: {parsed.get('branch') or '—'}
- Pull Request: {parsed.get('pull_request_url') or '—'}
- Recommended next action: {parsed.get('recommended_next_action') or '—'}

### Findings

{_format_findings_markdown(parsed)}

## Git status до запуска

```
{pre.get('status_summary') or '—'}
```

## Git status после запуска

```
{post.get('status_summary') or '—'}
```
"""


def save_report(run: dict, parsed: dict) -> Path:
    path = report_path_for(run)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = render_report_markdown(run, parsed)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, path)
    return path


def resolve_report_path(run: dict) -> Path | None:
    """Resolve `run["report_path"]` (relative to ROOT) to an absolute path, but only
    if it stays under REPORTS_ROOT once resolved (symlinks/`..` included).

    `run["report_path"]` is always written by this module's own `save_report`, so it
    is not attacker-influenced through the shipped UI today — this is defense in
    depth against a corrupted or hand-edited `data/runs.jsonl` record referencing an
    arbitrary local file, which would otherwise be read and rendered verbatim by the
    Runs/Reports pages. Returns None (never raises) if `report_path` is missing or
    resolves outside REPORTS_ROOT.
    """
    report_path = run.get("report_path")
    if not report_path:
        return None
    candidate = (ROOT / report_path).resolve()
    reports_root = REPORTS_ROOT.resolve()
    if candidate != reports_root and reports_root not in candidate.parents:
        return None
    return candidate
