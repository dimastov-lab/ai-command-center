"""Execution-provider contracts for the shared Session Supervisor.

Providers own only CLI-specific concerns: discovery/probing, fixed argv and
environment construction, prompt transport, stream parsing, failure
classification, readiness, cancellation metadata, and audit-safe metadata.
The Supervisor remains the sole owner of processes, state, persistence,
timeouts, cancellation, and restart reconciliation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from command_center import agent_runner
from command_center.runtime import stream_parser

CLAUDE_ID = "claude_code"
CODEX_ID = "codex"


@dataclass(frozen=True)
class ProviderAvailability:
    provider_id: str
    available: bool
    code: str
    message: str
    executable: str | None = None
    version: str | None = None


@dataclass(frozen=True)
class LaunchSpec:
    argv: tuple[str, ...]
    environment: dict[str, str]
    stdin_text: str | None
    audit_metadata: dict[str, object]


class ExecutionProvider(Protocol):
    id: str
    label: str
    supports_resume: bool
    requires_dedicated_worktree: bool

    def availability(self) -> ProviderAvailability: ...

    def build_launch(
        self,
        *,
        repository_path: Path,
        session_id: str,
        prompt: str,
        task_type: str,
        is_resume: bool,
        model: str | None,
    ) -> LaunchSpec: ...

    def parse_stdout_line(self, line: str) -> dict | None: ...

    def sanitize_stderr(self, line: str) -> str: ...

    def classify_failure(self, *, exit_code: int, diagnostic_lines: list[str]) -> str | None: ...


def _prompt_audit(prompt: str, transport: str) -> dict[str, object]:
    encoded = prompt.encode("utf-8")
    return {
        "prompt_transport": transport,
        "prompt_sha256": hashlib.sha256(encoded).hexdigest(),
        "prompt_bytes": len(encoded),
    }


def _probe(executable: str, args: list[str], *, provider_id: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [executable, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{provider_id} version/interface probe failed: {type(exc).__name__}"
    output = (result.stdout or "").strip() or (result.stderr or "").strip()
    if result.returncode != 0:
        return False, f"{provider_id} version/interface probe failed (exit {result.returncode})"
    return True, output


class ClaudeProvider:
    id = CLAUDE_ID
    label = "Claude Code"
    supports_resume = True
    requires_dedicated_worktree = False

    def availability(self) -> ProviderAvailability:
        # Preserve the pre-provider behavior: Claude launch availability was
        # a configured capability, with expected failures handled by Popen.
        # Do not introduce a new real-CLI probe into existing tests/UI paths.
        binary = os.environ.get("AICC_CLAUDE_BINARY") or "claude"
        executable = shutil.which(binary) or binary
        return ProviderAvailability(self.id, True, "usable", "Claude Code CLI is configured.", executable)

    def build_launch(
        self,
        *,
        repository_path: Path,
        session_id: str,
        prompt: str,
        task_type: str,
        is_resume: bool,
        model: str | None,
    ) -> LaunchSpec:
        # Imported lazily to keep the historical command builder as the one
        # compatibility surface for Claude tests and callers.
        from command_center.runtime import supervisor

        argv = supervisor.build_claude_command(
            session_id=session_id,
            prompt=prompt,
            task_type=task_type,
            is_resume=is_resume,
            model=model,
        )
        return LaunchSpec(
            argv=tuple(argv),
            environment=dict(os.environ),
            stdin_text=None,
            audit_metadata={"provider_id": self.id, **_prompt_audit(prompt, "argv")},
        )

    def parse_stdout_line(self, line: str) -> dict | None:
        return stream_parser.parse_stream_line(line)

    def sanitize_stderr(self, line: str) -> str:
        return line

    def classify_failure(self, *, exit_code: int, diagnostic_lines: list[str]) -> str | None:
        return None


class CodexProvider:
    id = CODEX_ID
    label = "Codex CLI"
    supports_resume = False
    requires_dedicated_worktree = True

    def _executable(self) -> str | None:
        configured = os.environ.get("AICC_CODEX_BINARY")
        if configured:
            path = Path(configured).expanduser()
            return str(path) if path.is_file() and os.access(path, os.X_OK) else None
        return shutil.which("codex")

    def availability(self) -> ProviderAvailability:
        executable = self._executable()
        if executable is None:
            return ProviderAvailability(
                self.id, False, "executable_missing", "Codex CLI not found; install it or configure AICC_CODEX_BINARY."
            )
        version_ok, version = _probe(executable, ["--version"], provider_id=self.id)
        if not version_ok:
            return ProviderAvailability(self.id, False, "version_probe_failed", version, executable=executable)
        help_ok, help_output = _probe(executable, ["exec", "--help"], provider_id=self.id)
        required = ("Run Codex non-interactively", "--json", "--cd <DIR>", "read from stdin")
        if not help_ok or any(marker not in help_output for marker in required):
            return ProviderAvailability(
                self.id,
                False,
                "unsupported_interface",
                "Installed Codex CLI does not expose the required non-interactive stdin/JSON interface.",
                executable,
                version,
            )
        return ProviderAvailability(self.id, True, "usable", "Codex CLI is available.", executable, version)

    def build_launch(
        self,
        *,
        repository_path: Path,
        session_id: str,
        prompt: str,
        task_type: str,
        is_resume: bool,
        model: str | None,
    ) -> LaunchSpec:
        if is_resume:
            raise ValueError("Codex CLI resume is not supported by this provider increment.")
        availability = self.availability()
        if not availability.available or not availability.executable:
            raise RuntimeError(availability.message)
        sandbox = "read-only" if task_type in agent_runner.READ_ONLY_TASK_TYPES else "workspace-write"
        argv = [
            availability.executable,
            "exec",
            "--json",
            "--color",
            "never",
            "--sandbox",
            sandbox,
            "--cd",
            str(repository_path),
        ]
        if model:
            argv.extend(["--model", model])
        argv.append("-")
        environment = dict(os.environ)
        audit = {
            "provider_id": self.id,
            "provider_version": availability.version,
            "non_interactive": True,
            "sandbox": sandbox,
            "readiness": "first_output",
            "cancellation": "process_group_sigterm_then_sigkill",
            "environment_keys": sorted(environment),
            **_prompt_audit(prompt, "stdin"),
        }
        return LaunchSpec(tuple(argv), environment, prompt, audit)

    def parse_stdout_line(self, line: str) -> dict | None:
        parsed = stream_parser.parse_stream_line(line)
        if parsed is None or parsed["event_type"] == "malformed":
            return parsed
        payload = parsed["payload"]
        msg_type = payload.get("type")
        if msg_type == "item.completed":
            item = payload.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                return {"event_type": "assistant_message", "payload": payload}
        if msg_type == "turn.completed":
            return {"event_type": "result", "payload": payload}
        if msg_type in {"thread.started", "turn.started"}:
            return {"event_type": "lifecycle", "payload": payload}
        return parsed

    def sanitize_stderr(self, line: str) -> str:
        # Provider diagnostics are useful, but bearer/API tokens never are.
        redacted = re.sub(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+", r"\1 [REDACTED]", line)
        redacted = re.sub(
            r"(?i)\b(api[_ -]?key|access[_ -]?token|auth[_ -]?token)\s*[:=]\s*\S+",
            r"\1=[REDACTED]",
            redacted,
        )
        redacted = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", redacted)
        return redacted

    def classify_failure(self, *, exit_code: int, diagnostic_lines: list[str]) -> str | None:
        text = "\n".join(diagnostic_lines).lower()
        if any(token in text for token in ("quota", "usage limit", "spend limit", "rate limit")):
            return "quota_limit"
        if any(token in text for token in ("not logged in", "authentication", "unauthorized", "api key")):
            return "authentication_failed"
        return "provider_exit_nonzero" if exit_code else None


_PROVIDERS: dict[str, ExecutionProvider] = {
    CLAUDE_ID: ClaudeProvider(),
    CODEX_ID: CodexProvider(),
}


def get_provider(provider_id: str) -> ExecutionProvider:
    try:
        return _PROVIDERS[provider_id]
    except KeyError as exc:
        raise ValueError(f"Unknown execution provider: {provider_id!r}") from exc


def provider_ids() -> tuple[str, ...]:
    return tuple(_PROVIDERS)


def audit_json(metadata: dict[str, object]) -> str:
    return json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
