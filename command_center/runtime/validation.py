"""Configurable validation-plan execution for the completion pipeline.

"Claude exited 0" is not evidence a change is correct. Before the pipeline will
open or advance a pull request, it runs a project/task-configured validation
plan (e.g. `pytest`, `ruff check .`, `python -m compileall`, `mypy`) against the
run's own worktree and records structured, *bounded* results.

Safety (spec: "Do not execute arbitrary validation commands from untrusted
external input without validation"):
  * commands come only from project/task configuration, never from agent output;
  * each is parsed with `shlex.split` and run as a fixed argv list with
    `shell=False` — so shell metacharacters in an argument are passed literally
    and are inert (no pipes, expansion, or command substitution ever happen);
  * the leading executable must be in an allowlist of known validators, so a
    misconfigured plan cannot invoke an arbitrary *program* directly (e.g. `sh`,
    `rm`, `curl`). Note: interpreter-class entries in the allowlist (`python`,
    `python3`, `node`, `npx`, `make`, …) will execute whatever code the
    operator-configured arguments supply — the allowlist scopes the entry
    binary, not the semantics, so `validation_commands` is trusted operator
    configuration and must be reviewed like any other privileged config;
  * each command has an explicit timeout, and stdout/stderr are truncated to a
    bounded summary before they ever reach the database.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from command_center.models import iso_now

# Executables the pipeline is willing to run as validators. This scopes the
# *entry binary* a misconfigured (or hostile) `validation_commands` value may
# launch — it blocks direct invocation of arbitrary programs (`sh`, `rm`, …).
# Interpreter-class entries (`python`/`python3`/`node`/`npx`/`make`/`go`/…)
# will run whatever code the operator-supplied arguments specify, so
# `validation_commands` is trusted operator configuration: review it with the
# same care as any other privileged setting. See the module docstring.
SAFE_VALIDATOR_BINARIES: frozenset[str] = frozenset(
    {
        "python",
        "python3",
        "pytest",
        "ruff",
        "mypy",
        "pyright",
        "flake8",
        "black",
        "isort",
        "make",
        "node",
        "npm",
        "npx",
        "pnpm",
        "yarn",
        "go",
        "cargo",
        "uv",
        "tox",
    }
)

# Newlines are the one raw-string form we refuse outright — a validation
# "command" spanning multiple lines is a configuration mistake, not a command.
_FORBIDDEN_RAW_CHARS = frozenset("\n\r")

# Conservative safe default: byte-compile the repo's Python, skipping the usual
# non-source trees. `python3` (not `python`) because macOS ships only the
# former. Projects override with their real suite (pytest/ruff/...) via the
# `validation_commands` config key.
DEFAULT_VALIDATION_COMMANDS: tuple[str, ...] = (
    "python3 -m compileall -q -x (\\.venv|\\.git|node_modules|\\.ruff_cache|\\.pytest_cache) .",
)

_DEFAULT_TIMEOUT_SECONDS = 900
_SUMMARY_CHAR_LIMIT = 4000


class InvalidValidationCommand(Exception):
    """A configured validation command is unsafe or unparseable."""


@dataclass
class ValidationCommand:
    raw: str
    argv: list[str]

    @property
    def display(self) -> str:
        return self.raw


@dataclass
class CommandResult:
    command: str
    exit_code: int | None
    started_at: str
    finished_at: str
    stdout_summary: str
    stderr_summary: str

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


@dataclass
class ValidationOutcome:
    ran: bool
    results: list[CommandResult] = field(default_factory=list)
    error: str | None = None

    @property
    def passed(self) -> bool:
        """A plan passes only if it ran at least one command and every command
        exited 0. An empty plan that ran nothing does NOT pass (the caller must
        decide whether validation is required)."""
        return self.ran and bool(self.results) and all(r.passed for r in self.results)

    @property
    def failed_commands(self) -> list[str]:
        return [r.command for r in self.results if not r.passed]

    def summary(self) -> str:
        if not self.ran:
            return "validation not run"
        if not self.results:
            return "no validation commands configured"
        passed = sum(1 for r in self.results if r.passed)
        head = f"{passed}/{len(self.results)} commands passed"
        if self.failed_commands:
            head += "; failed: " + ", ".join(self.failed_commands)
        return head


def parse_command(raw: str) -> ValidationCommand:
    """Parse and validate one configured command string into a safe argv list.
    Raises `InvalidValidationCommand` for an empty/multi-line command, an
    unparseable one, or a leading executable outside `SAFE_VALIDATOR_BINARIES`.
    The argv is executed with `shell=False`, so no quoting/expansion happens."""
    if not raw or not raw.strip():
        raise InvalidValidationCommand("empty validation command")
    bad = _FORBIDDEN_RAW_CHARS & set(raw)
    if bad:
        raise InvalidValidationCommand(f"command must be a single line: {raw!r}")
    try:
        argv = shlex.split(raw)
    except ValueError as exc:
        raise InvalidValidationCommand(f"unparseable command {raw!r}: {exc}") from exc
    if not argv:
        raise InvalidValidationCommand(f"empty validation command: {raw!r}")
    binary = Path(argv[0]).name
    if binary not in SAFE_VALIDATOR_BINARIES:
        raise InvalidValidationCommand(
            f"validator {binary!r} is not in the allowlist (command {raw!r})"
        )
    return ValidationCommand(raw=raw, argv=argv)


def resolve_validation_plan(
    task: dict | None = None, project_cfg: dict | None = None
) -> list[ValidationCommand]:
    """Resolve the ordered validation plan for a task: task-level
    `validation_commands` override project-level, which override the safe
    default. Every command is validated; an invalid one raises rather than
    being silently dropped."""
    raw_commands: list[str] | None = None
    if task and isinstance(task.get("validation_commands"), list):
        raw_commands = [str(c) for c in task["validation_commands"]]
    elif project_cfg and isinstance(project_cfg.get("validation_commands"), list):
        raw_commands = [str(c) for c in project_cfg["validation_commands"]]
    if raw_commands is None:
        raw_commands = list(DEFAULT_VALIDATION_COMMANDS)
    return [parse_command(c) for c in raw_commands if c.strip()]


def _truncate(text: str | None, limit: int = _SUMMARY_CHAR_LIMIT) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    keep = limit - 40
    return f"[truncated {len(text) - keep} chars]\n" + text[-keep:]


def run_validation(
    repository_path: str,
    plan: list[ValidationCommand],
    *,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> ValidationOutcome:
    """Run each command in the plan against `repository_path`, capturing bounded
    results. Never raises for a failing command — a non-zero exit is recorded,
    not thrown; only a spawn failure surfaces as `error`."""
    repo = Path(repository_path)
    outcome = ValidationOutcome(ran=True)
    if not plan:
        return outcome
    # Keep the worktree clean: don't let validators write __pycache__ into it
    # (which would otherwise read as "uncommitted changes" downstream).
    env = dict(os.environ)
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    for command in plan:
        started = iso_now()
        try:
            proc = subprocess.run(
                command.argv,
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            result = CommandResult(
                command=command.raw,
                exit_code=proc.returncode,
                started_at=started,
                finished_at=iso_now(),
                stdout_summary=_truncate(proc.stdout),
                stderr_summary=_truncate(proc.stderr),
            )
        except subprocess.TimeoutExpired:
            result = CommandResult(
                command=command.raw,
                exit_code=None,
                started_at=started,
                finished_at=iso_now(),
                stdout_summary="",
                stderr_summary=f"timed out after {timeout_seconds}s",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            result = CommandResult(
                command=command.raw,
                exit_code=None,
                started_at=started,
                finished_at=iso_now(),
                stdout_summary="",
                stderr_summary=f"failed to run: {exc}",
            )
        outcome.results.append(result)
    return outcome
