"""Provider-specific contracts for the shared Session Supervisor.

Providers build commands and create one stateful runtime per launched process.
That runtime is the single pre-persistence boundary for stream sanitization,
event normalization, readiness/result evidence, and bounded error evidence.
The Supervisor remains the sole process/state/timeout/cancellation owner.
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

MAX_PERSISTED_EVENT_CHARS = 65_536
MAX_CODEX_PROMPT_CHARS = 100_000
MAX_REDACTION_SOURCE_CHARS = 16_384
MAX_PROMPT_PATTERNS = 96
MAX_CREDENTIAL_CHARS = 512
MAX_STREAM_CARRY_CHARS = 2_048
MIN_PROMPT_FRAGMENT_CHARS = 8
MAX_ENV_SECRET_VALUES = 64
MAX_ENV_SECRET_CHARS = 16_384
_ROLLING_HASH_MASK = (1 << 64) - 1
_ROLLING_HASH_BASE = 257


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


class ProviderRuntime(Protocol):
    requires_valid_result: bool
    requires_verified_identity: bool

    def feed_stdout(self, chunk: str) -> list[str]: ...

    def feed_stderr(self, chunk: str) -> list[str]: ...

    def flush_stdout(self) -> list[str]: ...

    def flush_stderr(self) -> list[str]: ...

    def parse_stdout_line(self, line: str) -> dict | None: ...

    def stdout_event_is_readiness(self, line: str, event: dict | None) -> bool: ...

    def stderr_line_is_readiness(self, line: str) -> bool: ...

    def event_is_valid_result(self, event: dict) -> bool: ...

    def event_is_provider_error(self, event: dict) -> bool: ...


class ExecutionProvider(Protocol):
    id: str
    label: str
    supports_resume: bool
    requires_dedicated_worktree: bool

    def availability(self) -> ProviderAvailability: ...

    def validate_prompt(self, prompt: str) -> None: ...

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

    def create_runtime(self, *, prompt: str, environment: dict[str, str]) -> ProviderRuntime: ...

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
    # Current Codex can emit a non-fatal warning on stderr while returning the
    # real version/help on stdout. Prefer stdout, but accept stderr-only output
    # from a successful probe.
    output = (result.stdout or "").strip() or (result.stderr or "").strip()
    if result.returncode != 0:
        return False, f"{provider_id} version/interface probe failed (exit {result.returncode})"
    return True, output


def _same_length_mask(value: str) -> str:
    """Mask a span without moving newline or JSON-string boundaries."""
    marker = "[REDACTED]"
    parts = re.split(r"(\r\n|\r|\n)", value)
    masked: list[str] = []
    for part in parts:
        if part in {"\r\n", "\r", "\n"}:
            masked.append(part)
        elif part:
            repeats = (len(part) + len(marker) - 1) // len(marker)
            masked.append((marker * repeats)[: len(part)])
    return "".join(masked)


_SENSITIVE_ENVIRONMENT_KEY = re.compile(
    r"(?i)(?:secret|token|api[_-]?key|password|credential|authorization|bearer)"
)


def _sensitive_environment_values(environment: dict[str, str]) -> tuple[str, ...]:
    values = tuple(
        dict.fromkeys(
            value
            for key, value in environment.items()
            if _SENSITIVE_ENVIRONMENT_KEY.search(key) and isinstance(value, str) and len(value) >= 4
        )
    )
    total_chars = sum(len(value) for value in values)
    if len(values) > MAX_ENV_SECRET_VALUES or total_chars > MAX_ENV_SECRET_CHARS:
        raise ValueError("Codex sensitive-environment redaction sources exceed the safety bound.")
    return values


class SensitiveValueRedactor:
    """Deterministic, bounded prompt/credential span redaction.

    Prompt-derived source values live only in this in-memory object. They are
    never included in audit metadata or persisted events.
    """

    _VALUE_PREFIX = r"(?:^|[^A-Za-z0-9]|\\[nrt])"
    _SK_TOKEN = re.compile(
        rf"(?i){_VALUE_PREFIX}(?P<secret>sk-(?:[A-Za-z0-9_-]|\r?\n)"
        rf"{{8,{MAX_CREDENTIAL_CHARS}}})"
    )
    _BEARER = re.compile(
        rf"(?i){_VALUE_PREFIX}bearer[ \t]+"
        rf"(?P<secret>[A-Za-z0-9._~+/=-](?:[A-Za-z0-9._~+/=-]|\r?\n)"
        rf"{{3,{MAX_CREDENTIAL_CHARS - 1}}})"
    )
    _ASSIGNMENT = re.compile(
        rf"(?i){_VALUE_PREFIX}(?:api[_ -]?key|access[_ -]?token|auth[_ -]?token)"
        rf"(?:\\?[\"'])*\s*(?::|=|\\\":)\s*(?:\\?[\"'])*\s*"
        rf"(?P<secret>[A-Za-z0-9._~+/=-](?:[A-Za-z0-9._~+/=-]|\r?\n)"
        rf"{{2,{MAX_CREDENTIAL_CHARS - 1}}})"
    )
    _PROMPT_TOKEN = re.compile(r"[A-Za-z0-9_./+=:@-]{8,}")

    def __init__(self, prompt: str, sensitive_values: tuple[str, ...] = ()) -> None:
        if len(prompt) > MAX_CODEX_PROMPT_CHARS:
            raise ValueError(
                f"Codex prompt exceeds the {MAX_CODEX_PROMPT_CHARS}-character safety limit."
            )
        patterns: list[str] = []

        def add(value: str) -> None:
            value = value.strip("\r\n")
            if len(value) < 4 or value in patterns:
                return
            current_size = sum(len(item) for item in patterns)
            if len(patterns) >= MAX_PROMPT_PATTERNS or current_size + len(value) > MAX_REDACTION_SOURCE_CHARS:
                return
            patterns.append(value)

        add(prompt)
        for sensitive_value in sensitive_values:
            add(sensitive_value)
            add(json.dumps(sensitive_value, ensure_ascii=False)[1:-1])
        for line in prompt.splitlines():
            stripped = line.strip()
            if 8 <= len(stripped) <= 512:
                add(stripped)
            for match in self._PROMPT_TOKEN.finditer(stripped):
                add(match.group(0)[:256])
        self._prompt_patterns = tuple(patterns)
        # Fixed-size rolling fingerprints cover arbitrary prompt fragments,
        # including fragments from JSON-escaped prompt echoes, without
        # persisting source text or retaining an unbounded pattern list.
        fragment_sources = (prompt, json.dumps(prompt, ensure_ascii=False)[1:-1], *sensitive_values)
        self._prompt_fragment_hashes: set[int] = set()
        for source in fragment_sources:
            self._prompt_fragment_hashes.update(self._window_hashes(source))
            self._prompt_fragment_hashes.update(
                self._window_hashes(json.dumps(source, ensure_ascii=False)[1:-1])
            )

    @staticmethod
    def _window_hashes(value: str) -> set[int]:
        width = MIN_PROMPT_FRAGMENT_CHARS
        if len(value) < width:
            return set()
        factor = pow(_ROLLING_HASH_BASE, width - 1, 1 << 64)
        current = 0
        for character in value[:width]:
            current = ((current * _ROLLING_HASH_BASE) + ord(character)) & _ROLLING_HASH_MASK
        hashes = {current}
        for index in range(width, len(value)):
            current = (
                (current - (ord(value[index - width]) * factor))
                * _ROLLING_HASH_BASE
                + ord(value[index])
            ) & _ROLLING_HASH_MASK
            hashes.add(current)
        return hashes

    def _prompt_fragment_spans(self, text: str) -> list[tuple[int, int]]:
        width = MIN_PROMPT_FRAGMENT_CHARS
        if len(text) < width or not self._prompt_fragment_hashes:
            return []
        factor = pow(_ROLLING_HASH_BASE, width - 1, 1 << 64)
        current = 0
        for character in text[:width]:
            current = ((current * _ROLLING_HASH_BASE) + ord(character)) & _ROLLING_HASH_MASK
        spans = [(0, width)] if current in self._prompt_fragment_hashes else []
        for index in range(width, len(text)):
            current = (
                (current - (ord(text[index - width]) * factor))
                * _ROLLING_HASH_BASE
                + ord(text[index])
            ) & _ROLLING_HASH_MASK
            if current in self._prompt_fragment_hashes:
                spans.append((index - width + 1, index + 1))
        return spans

    def spans(self, text: str) -> list[tuple[int, int]]:
        spans = self._prompt_fragment_spans(text)
        for value in self._prompt_patterns:
            start = 0
            while True:
                index = text.find(value, start)
                if index < 0:
                    break
                spans.append((index, index + len(value)))
                start = index + max(1, len(value))
        for pattern in (self._SK_TOKEN, self._BEARER, self._ASSIGNMENT):
            for match in pattern.finditer(text):
                spans.append(match.span("secret"))
        if not spans:
            return []
        spans.sort()
        merged = [spans[0]]
        for start, end in spans[1:]:
            previous_start, previous_end = merged[-1]
            if start <= previous_end:
                merged[-1] = (previous_start, max(previous_end, end))
            else:
                merged.append((start, end))
        return merged

    def redact(self, text: str, *, existing_mask: list[bool] | None = None) -> tuple[str, list[bool]]:
        mask = list(existing_mask or [False] * len(text))
        if len(mask) < len(text):
            mask.extend([False] * (len(text) - len(mask)))
        for start, end in self.spans(text):
            for index in range(max(0, start), min(len(text), end)):
                mask[index] = True
        output: list[str] = []
        index = 0
        while index < len(text):
            if not mask[index]:
                output.append(text[index])
                index += 1
                continue
            end = index + 1
            while end < len(text) and mask[end]:
                end += 1
            output.append(_same_length_mask(text[index:end]))
            index = end
        return "".join(output), mask


def _split_chunks(text: str) -> list[str]:
    chunks = text.splitlines(keepends=True)
    return chunks or ([text] if text else [])


class _BufferedSanitizedStream:
    """Bounded carry-over catches values split across chunks or lines."""

    def __init__(self, redactor: SensitiveValueRedactor) -> None:
        self._redactor = redactor
        self._pending = ""
        self._pending_mask: list[bool] = []

    def feed(self, chunk: str, *, force_flush: bool = False) -> list[str]:
        pieces = [
            chunk[offset : offset + MAX_PERSISTED_EVENT_CHARS]
            for offset in range(0, len(chunk), MAX_PERSISTED_EVENT_CHARS)
        ] or [""]
        emitted: list[str] = []
        for piece in pieces:
            self._pending += piece
            self._pending_mask.extend([False] * len(piece))
            sanitized, self._pending_mask = self._redactor.redact(
                self._pending, existing_mask=self._pending_mask
            )
            if force_flush:
                emitted.extend(_split_chunks(sanitized))
                self._pending = ""
                self._pending_mask = []
                continue
            if len(self._pending) <= MAX_STREAM_CARRY_CHARS:
                continue
            cut = len(self._pending) - MAX_STREAM_CARRY_CHARS
            # Preserve event boundaries whenever possible. An oversized
            # malformed line is still emitted in bounded pieces.
            newline_cut = self._pending.rfind("\n", 0, cut)
            if newline_cut >= 0:
                cut = newline_cut + 1
            emitted.extend(_split_chunks(sanitized[:cut]))
            self._pending = self._pending[cut:]
            self._pending_mask = self._pending_mask[cut:]
        return emitted

    def flush(self) -> list[str]:
        if not self._pending:
            return []
        sanitized, _ = self._redactor.redact(self._pending, existing_mask=self._pending_mask)
        self._pending = ""
        self._pending_mask = []
        return _split_chunks(sanitized)


class SanitizationBoundary:
    def __init__(self, prompt: str, sensitive_values: tuple[str, ...] = ()) -> None:
        redactor = SensitiveValueRedactor(prompt, sensitive_values)
        self._stdout = _BufferedSanitizedStream(redactor)
        self._stderr = _BufferedSanitizedStream(redactor)

    def feed_stdout(self, chunk: str) -> list[str]:
        # Complete JSON events are self-contained provider messages. Flush
        # them promptly so readiness remains observable during a live run.
        # Any preceding malformed carry is sanitized together with this line
        # before either part is released.
        complete_json_object = False
        if len(chunk) <= MAX_PERSISTED_EVENT_CHARS:
            try:
                complete_json_object = isinstance(json.loads(chunk), dict)
            except (json.JSONDecodeError, ValueError):
                pass
        return self._stdout.feed(chunk, force_flush=complete_json_object)

    def feed_stderr(self, chunk: str) -> list[str]:
        return self._stderr.feed(chunk)

    def flush_stdout(self) -> list[str]:
        return self._stdout.flush()

    def flush_stderr(self) -> list[str]:
        return self._stderr.flush()


class _PassthroughBoundary:
    @staticmethod
    def feed_stdout(chunk: str) -> list[str]:
        return [chunk]

    @staticmethod
    def feed_stderr(chunk: str) -> list[str]:
        return [chunk]

    @staticmethod
    def flush_stdout() -> list[str]:
        return []

    @staticmethod
    def flush_stderr() -> list[str]:
        return []


class ClaudeRuntime:
    requires_valid_result = False
    requires_verified_identity = False

    def __init__(self) -> None:
        self._boundary = _PassthroughBoundary()

    def feed_stdout(self, chunk: str) -> list[str]:
        return self._boundary.feed_stdout(chunk)

    def feed_stderr(self, chunk: str) -> list[str]:
        return self._boundary.feed_stderr(chunk)

    def flush_stdout(self) -> list[str]:
        return []

    def flush_stderr(self) -> list[str]:
        return []

    @staticmethod
    def parse_stdout_line(line: str) -> dict | None:
        return stream_parser.parse_stream_line(line)

    @staticmethod
    def stdout_event_is_readiness(line: str, event: dict | None) -> bool:
        return bool(line)

    @staticmethod
    def stderr_line_is_readiness(line: str) -> bool:
        return bool(line)

    @staticmethod
    def event_is_valid_result(event: dict) -> bool:
        return event.get("event_type") == "result"

    @staticmethod
    def event_is_provider_error(event: dict) -> bool:
        return False


class CodexRuntime:
    requires_valid_result = True
    requires_verified_identity = True

    def __init__(self, prompt: str, sensitive_values: tuple[str, ...] = ()) -> None:
        self._boundary = SanitizationBoundary(prompt, sensitive_values)
        self._last_assistant_text = ""

    def feed_stdout(self, chunk: str) -> list[str]:
        return self._boundary.feed_stdout(chunk)

    def feed_stderr(self, chunk: str) -> list[str]:
        return self._boundary.feed_stderr(chunk)

    def flush_stdout(self) -> list[str]:
        return self._boundary.flush_stdout()

    def flush_stderr(self) -> list[str]:
        return self._boundary.flush_stderr()

    def parse_stdout_line(self, line: str) -> dict | None:
        if len(line) > MAX_PERSISTED_EVENT_CHARS:
            return {
                "event_type": "malformed",
                "payload": {
                    "raw": line[:MAX_PERSISTED_EVENT_CHARS],
                    "error": "provider event exceeded persistence bound",
                },
            }
        parsed = stream_parser.parse_stream_line(line)
        if parsed is None or parsed["event_type"] == "malformed":
            return parsed
        payload = parsed["payload"]
        msg_type = payload.get("type")
        if msg_type in {"thread.started", "turn.started"}:
            normalized = {"provider_event": msg_type}
            if isinstance(payload.get("thread_id"), str):
                normalized["thread_id"] = payload["thread_id"][:256]
            return {"event_type": "lifecycle", "payload": normalized}
        if msg_type == "item.completed":
            item = payload.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text")
                if isinstance(text, str) and text:
                    self._last_assistant_text = text[:MAX_PERSISTED_EVENT_CHARS]
                    return {
                        "event_type": "assistant_message",
                        "payload": {
                            "provider": CODEX_ID,
                            "message": {"content": [{"type": "text", "text": self._last_assistant_text}]},
                        },
                    }
            if isinstance(item, dict) and item.get("type") == "error":
                return self._provider_error("item.completed", item)
        if msg_type == "turn.completed":
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else None
            return {
                "event_type": "result",
                "payload": {
                    "provider": CODEX_ID,
                    "provider_event": msg_type,
                    "provider_completion_valid": bool(self._last_assistant_text),
                    "result": self._last_assistant_text,
                    "usage": usage,
                },
            }
        if msg_type in {"error", "turn.failed"}:
            return self._provider_error(msg_type, payload)
        return parsed

    @staticmethod
    def _provider_error(event_name: str, payload: dict) -> dict:
        raw_error = payload.get("error")
        if isinstance(raw_error, dict):
            message = raw_error.get("message") or json.dumps(raw_error, ensure_ascii=False, sort_keys=True)
            code = raw_error.get("code")
        else:
            message = raw_error or payload.get("message") or payload.get("detail") or event_name
            code = payload.get("code")
        return {
            "event_type": "provider_error",
            "payload": {
                "provider": CODEX_ID,
                "provider_event": event_name,
                "code": str(code)[:128] if code is not None else None,
                "message": str(message)[:MAX_PERSISTED_EVENT_CHARS],
            },
        }

    @staticmethod
    def stdout_event_is_readiness(line: str, event: dict | None) -> bool:
        return bool(
            event
            and event.get("event_type") == "lifecycle"
            and (event.get("payload") or {}).get("provider_event") in {"thread.started", "turn.started"}
        )

    @staticmethod
    def stderr_line_is_readiness(line: str) -> bool:
        return False

    @staticmethod
    def event_is_valid_result(event: dict) -> bool:
        return bool(
            event.get("event_type") == "result"
            and (event.get("payload") or {}).get("provider_completion_valid")
        )

    @staticmethod
    def event_is_provider_error(event: dict) -> bool:
        return event.get("event_type") == "provider_error"


class ClaudeProvider:
    id = CLAUDE_ID
    label = "Claude Code"
    supports_resume = True
    requires_dedicated_worktree = False

    def availability(self) -> ProviderAvailability:
        binary = os.environ.get("AICC_CLAUDE_BINARY") or "claude"
        executable = shutil.which(binary) or binary
        return ProviderAvailability(self.id, True, "usable", "Claude Code CLI is configured.", executable)

    @staticmethod
    def validate_prompt(prompt: str) -> None:
        return None

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

    @staticmethod
    def create_runtime(*, prompt: str, environment: dict[str, str]) -> ProviderRuntime:
        return ClaudeRuntime()

    @staticmethod
    def parse_stdout_line(line: str) -> dict | None:
        return stream_parser.parse_stream_line(line)

    @staticmethod
    def sanitize_stderr(line: str) -> str:
        return line

    @staticmethod
    def classify_failure(*, exit_code: int, diagnostic_lines: list[str]) -> str | None:
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

    @staticmethod
    def validate_prompt(prompt: str) -> None:
        if len(prompt) > MAX_CODEX_PROMPT_CHARS:
            raise ValueError(
                f"Codex prompt exceeds the {MAX_CODEX_PROMPT_CHARS}-character safety limit."
            )
        _sensitive_environment_values(dict(os.environ))

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
        self.validate_prompt(prompt)
        environment = dict(os.environ)
        _sensitive_environment_values(environment)
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
        audit = {
            "provider_id": self.id,
            "provider_version": availability.version,
            "non_interactive": True,
            "sandbox": sandbox,
            "readiness": "recognized_codex_lifecycle_event",
            "result_evidence": "normalized_agent_message_then_turn_completed",
            "cancellation": "verified_process_group_sigterm_then_sigkill",
            **_prompt_audit(prompt, "stdin"),
        }
        return LaunchSpec(tuple(argv), environment, prompt, audit)

    @staticmethod
    def create_runtime(*, prompt: str, environment: dict[str, str]) -> ProviderRuntime:
        return CodexRuntime(prompt, _sensitive_environment_values(environment))

    def parse_stdout_line(self, line: str) -> dict | None:
        return CodexRuntime("").parse_stdout_line(line)

    @staticmethod
    def sanitize_stderr(line: str) -> str:
        runtime = CodexRuntime("")
        runtime.feed_stderr(line)
        return "".join(runtime.flush_stderr())

    @staticmethod
    def classify_failure(*, exit_code: int, diagnostic_lines: list[str]) -> str | None:
        # `diagnostic_lines` is already sanitized and bounded by Supervisor,
        # and contains only stderr/provider_error evidence.
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
