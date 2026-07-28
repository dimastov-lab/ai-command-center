"""Executor abstraction: one common interface every task-launching backend
implements, so the Launch System and Task Card don't need to special-case
which agent a task runs on. This module is a **registry** — it owns each
executor's id, label, kind, terminal-launch capability, and live
availability probe. It no longer launches anything in-process: every real
launch goes through the PID-tracked v2 Session Supervisor
(`command_center.runtime`), so the executors' own `launch()` methods exist
only as fail-closed guards.

`claude_code`, `codex`, `copilot_cli`, and `ollama` are wired to real
availability probes, but their `launch()` fails closed (`_v2_only`) — the
legacy in-process launch path that ran `agent_runner` directly was retired
(audit MAJOR-3). `chatgpt`, `gemini`, `remote_agent` are declared so the
architecture is never hard-coded to one provider, but their `launch()`
raises `NotImplementedError` (integration is future work). `human` is a
valid executor value (a task can be explicitly assigned to a person) but is
not launchable through this module at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

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


def _not_implemented(executor_id: str, label: str) -> Callable[..., ExecutorResult]:
    def _launch(**_kwargs: object) -> ExecutorResult:
        raise NotImplementedError(
            f"Executor '{executor_id}' ({label}) is not wired up yet — the architecture "
            "reserves the slot, but integration is future work."
        )

    return _launch


def _v2_only(**_kwargs: object) -> ExecutorResult:
    raise RuntimeError(
        "This executor runs through the PID-tracked Execution Center (v2 Session "
        "Supervisor) only, never in-process — there is no synchronous launch path."
    )


EXECUTORS: dict[str, Executor] = {
    "claude_code": Executor(
        id="claude_code",
        label="Claude Code",
        kind="cli",
        supports_terminal_launch=True,
        availability_check=providers.get_provider("claude_code").availability,
        launch=_v2_only,
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
