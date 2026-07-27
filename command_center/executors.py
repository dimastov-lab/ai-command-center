"""Executor abstraction: one common interface every task-launching backend
implements, so the Launch System and Task Card don't need to special-case
which agent a task runs on.

`claude_code` wraps the existing synchronous `command_center.agent_runner`
runner and is the only executor that actually runs anything today. The
remaining entries are declared so the rest of the architecture (the task
`executor` field, Task Card executor badge, launch validation, future
provider integrations) is never hard-coded to a single provider — but their
`launch()` deliberately raises `NotImplementedError`: integrating a real
ChatGPT/Codex/Gemini/remote-agent backend is future work, not simulated
here. `human` is a valid executor value (a task can be explicitly assigned
to a person) but is not launchable through this module at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from command_center import agent_runner
from command_center.runtime import providers


@dataclass(frozen=True)
class ExecutorResult:
    status: str  # matches agent_runner.RunResult.status / models.RUN_STATUSES
    exit_code: int | None
    stdout: str
    stderr: str
    started_at: str
    completed_at: str
    duration_seconds: float


@dataclass(frozen=True)
class Executor:
    id: str
    label: str
    kind: str  # "cli" | "chat" | "human" | "remote"
    supports_terminal_launch: bool
    availability_check: Callable[[], providers.ProviderAvailability] | None
    launch: Callable[..., ExecutorResult]

    @property
    def available(self) -> bool:
        if self.availability_check is None:
            return False
        return self.availability_check().available

    @property
    def availability(self) -> providers.ProviderAvailability | None:
        return self.availability_check() if self.availability_check is not None else None


def _launch_claude_code(
    *,
    repository_path: Path,
    prompt: str,
    task_type: str,
    timeout_seconds: int,
    model: str | None = None,
) -> ExecutorResult:
    result = agent_runner.run_claude_code(
        repository_path=repository_path,
        prompt=prompt,
        task_type=task_type,
        timeout_seconds=timeout_seconds,
        model=model,
    )
    return ExecutorResult(
        status=result.status,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        started_at=result.started_at,
        completed_at=result.completed_at,
        duration_seconds=result.duration_seconds,
    )


def _not_implemented(executor_id: str, label: str) -> Callable[..., ExecutorResult]:
    def _launch(**_kwargs: object) -> ExecutorResult:
        raise NotImplementedError(
            f"Executor '{executor_id}' ({label}) is not wired up yet — the architecture "
            "reserves the slot, but integration is future work."
        )

    return _launch


def _v2_only(**_kwargs: object) -> ExecutorResult:
    raise RuntimeError("Codex CLI is supported through the PID-tracked Execution Center launcher only.")


EXECUTORS: dict[str, Executor] = {
    "claude_code": Executor(
        id="claude_code",
        label="Claude Code",
        kind="cli",
        supports_terminal_launch=True,
        availability_check=providers.get_provider("claude_code").availability,
        launch=_launch_claude_code,
    ),
    "chatgpt": Executor(
        id="chatgpt",
        label="ChatGPT",
        kind="chat",
        supports_terminal_launch=False,
        availability_check=None,
        launch=_not_implemented("chatgpt", "ChatGPT"),
    ),
    "codex": Executor(
        id="codex",
        label="Codex CLI",
        kind="cli",
        supports_terminal_launch=True,
        availability_check=providers.get_provider("codex").availability,
        launch=_v2_only,
    ),
    "copilot_cli": Executor(
        id="copilot_cli",
        label="Copilot CLI",
        kind="cli",
        supports_terminal_launch=True,
        availability_check=providers.get_provider("copilot_cli").availability,
        launch=_v2_only,
    ),
    "ollama": Executor(
        id="ollama",
        label="Ollama (local)",
        kind="cli",
        supports_terminal_launch=True,
        availability_check=providers.get_provider("ollama").availability,
        launch=_v2_only,
    ),
    "gemini": Executor(
        id="gemini",
        label="Gemini",
        kind="cli",
        supports_terminal_launch=True,
        availability_check=None,
        launch=_not_implemented("gemini", "Gemini"),
    ),
    "human": Executor(
        id="human",
        label="Инженер (человек)",
        kind="human",
        supports_terminal_launch=False,
        availability_check=None,
        launch=_not_implemented("human", "Human Engineer"),
    ),
    "remote_agent": Executor(
        id="remote_agent",
        label="Удалённый агент",
        kind="remote",
        supports_terminal_launch=False,
        availability_check=None,
        launch=_not_implemented("remote_agent", "Remote Agent"),
    ),
}

EXECUTOR_IDS: list[str] = list(EXECUTORS.keys())


def get_executor(executor_id: str | None) -> Executor:
    resolved = executor_id or "claude_code"
    try:
        return EXECUTORS[resolved]
    except KeyError as exc:
        raise ValueError(f"Unknown executor: {resolved!r}") from exc


def available_executors() -> list[Executor]:
    return [executor for executor in EXECUTORS.values() if executor.available]
