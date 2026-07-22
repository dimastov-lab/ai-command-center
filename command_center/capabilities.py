"""Executor capability profiles — the single, testable source of truth for
"what tools may a Claude Code run actually invoke, and does that match what the
task actually needs?".

Background — the defect this module closes
------------------------------------------
Before this module existed, both command builders
(`agent_runner.build_command`, the v1 synchronous path, and
`runtime.supervisor.build_claude_command`, the v2 async path) resolved a run's
`task_type` to a tool set with one rule: read-only task types
(`review`/`final_gate`/`architecture_review`) got `--tools Read,Grep,Glob`
(a tool-set *replacement* — Bash/Edit/Write simply do not exist for that run),
everything else got the full tool set minus git-write Bash patterns.

That rule looks only at `task_type`. A task typed `review` (or a
reconnaissance/`architecture_review` task) whose *prompt* actually required
editing files, adding regression tests, running validation, and committing was
therefore launched with a read-only tool set. Claude received only Read/Glob/
Grep, correctly reported it could not proceed, and the process exited 0. The
capability shortfall was invisible until *after* the process exited (surfaced
only as a generic "Requires Attention"), because nothing ever compared the
capabilities the *task* needs against the capabilities the *session* was given
*before* spawning the subprocess. That is exactly the AIOS-RECON-001 incident.

The model
---------
A **capability** is one Claude Code tool name that gates what a run can touch.
We group them into two named **profiles**:

- ``PROFILE_READ_ONLY`` grants exactly ``READ_ONLY_CAPABILITIES``
  (``Read``/``Glob``/``Grep``) — no shell, no file mutation, ever. Enforced by
  ``--tools`` tool-set replacement (Bash and every shell-reachable mutation are
  absent, not merely denied — see `agent_runner`'s module docstring).
- ``PROFILE_WORKSPACE_WRITE`` grants ``WORKSPACE_WRITE_CAPABILITIES``
  (read/search **plus** ``Bash``/``Edit``/``Write``) — the profile a task that
  exists to modify a trusted local workspace needs. Enforced by leaving the
  full built-in tool set available (so tools beyond the six named here — e.g.
  ``MultiEdit``, ``NotebookEdit`` — stay available too) with only git-write
  Bash subcommands denied via ``--disallowedTools``.

`decide()` is the whole point: given `(task_type, prompt, override)` it computes
both the capabilities a run *needs* (`required_capabilities`) and the
capabilities it *will be granted* (`granted_capabilities`), and reports whether
the grant satisfies the need (`ok`). The launcher calls this *before*
`subprocess.Popen` and refuses to spawn when it does not (`preflight`).

Backward compatibility
-----------------------
The task-type → profile mapping is a strict superset of the previous
`READ_ONLY_TASK_TYPES` behavior: every task type that was read-only before is
read-only now, every task type that was write-capable before is write-capable
now, and an unrecognized/future task type still resolves to
``PROFILE_WORKSPACE_WRITE`` (the old "everything else" branch). `agent_runner`
re-exports its historical constant names from here so nothing downstream broke.

Pure functions only — no I/O, no subprocess, no database. Trivially unit
testable and safe to import from any layer (leaf module; imports nothing from
`command_center`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Capabilities (Claude Code tool names) and their canonical display order.
# --------------------------------------------------------------------------

CAP_READ = "Read"
CAP_GLOB = "Glob"
CAP_GREP = "Grep"
CAP_BASH = "Bash"
CAP_EDIT = "Edit"
CAP_WRITE = "Write"

# Stable order used everywhere a capability set is rendered (reason strings,
# persisted metadata, reports) so the same set always serializes identically.
CAPABILITY_ORDER: tuple[str, ...] = (CAP_READ, CAP_GLOB, CAP_GREP, CAP_BASH, CAP_EDIT, CAP_WRITE)
_ORDER_INDEX = {name: i for i, name in enumerate(CAPABILITY_ORDER)}

READ_ONLY_CAPABILITIES: frozenset[str] = frozenset({CAP_READ, CAP_GLOB, CAP_GREP})
# The capabilities a write-capable run is *guaranteed* to have. The real
# `PROFILE_WORKSPACE_WRITE` grant is a superset (the full built-in tool set),
# but these six are the ones preflight reasons about — they are exactly the
# capabilities a task can *require*.
WORKSPACE_WRITE_CAPABILITIES: frozenset[str] = READ_ONLY_CAPABILITIES | frozenset({CAP_BASH, CAP_EDIT, CAP_WRITE})

# --------------------------------------------------------------------------
# Profiles.
# --------------------------------------------------------------------------

PROFILE_READ_ONLY = "READ_ONLY"
PROFILE_WORKSPACE_WRITE = "WORKSPACE_WRITE"

PROFILE_CAPABILITIES: dict[str, frozenset[str]] = {
    PROFILE_READ_ONLY: READ_ONLY_CAPABILITIES,
    PROFILE_WORKSPACE_WRITE: WORKSPACE_WRITE_CAPABILITIES,
}

VALID_PROFILES: frozenset[str] = frozenset(PROFILE_CAPABILITIES)

# Human-facing one-liners for each profile (UI / reports).
PROFILE_LABELS: dict[str, str] = {
    PROFILE_READ_ONLY: "Read-only (Read/Glob/Grep)",
    PROFILE_WORKSPACE_WRITE: "Workspace-write (Read/Glob/Grep + Bash/Edit/Write)",
}

# --------------------------------------------------------------------------
# Task-type → profile category.
#
# Read-only task types are the explicit allow-list (a task type must be named
# here to be denied write access); everything else — including any
# unrecognized/future task type — is write-capable, preserving the historical
# "else -> trusted_development" branch. The extra names beyond this project's
# five canonical `artifacts.TASK_TYPES` (e.g. `audit`, `reconciliation`,
# `migration`) are recognized here so the mapping is stable if/when those task
# types are introduced, per the executor-capabilities brief.
# --------------------------------------------------------------------------

READ_ONLY_TASK_TYPES: frozenset[str] = frozenset(
    {
        "review",
        "audit",
        "architecture_review",
        # `final_gate` is read-only *by default* ("when explicitly
        # non-remediating"). A gate that must remediate opts into write access
        # through an explicit per-task override (see `normalize_override`),
        # never by silently reclassifying the task type.
        "final_gate",
    }
)

WRITE_TASK_TYPES: frozenset[str] = frozenset(
    {
        "implementation",
        "remediation",
        "reconciliation",
        "migration",
        "repair",
        "integration",
    }
)

# `--permission-mode` per profile. Both use `acceptEdits`: without an explicit
# permission mode the CLI's implicit default denies Write/Edit outright in
# headless `-p` mode while still exiting 0 (empirically confirmed — see
# `agent_runner`'s profile docstring). This is the value both command builders
# pass; it is defined here so the two paths can never diverge on it.
PERMISSION_MODE_BY_PROFILE: dict[str, str] = {
    PROFILE_READ_ONLY: "acceptEdits",
    PROFILE_WORKSPACE_WRITE: "acceptEdits",
}


class InvalidCapabilityOverrideError(ValueError):
    """Raised by `normalize_override` for an override value that is neither a
    recognized profile nor a recognized legacy alias — an ambiguous/invalid
    override must fail closed with a clear diagnostic rather than silently
    falling back to a default profile."""


# Legacy / alias spellings accepted for an explicit override, normalized to a
# canonical profile. Case-insensitive. `agent_runner`'s historical profile
# names (`read_only`, `trusted_development`) map here so an override persisted
# by older code still resolves.
_OVERRIDE_ALIASES: dict[str, str] = {
    "read_only": PROFILE_READ_ONLY,
    "readonly": PROFILE_READ_ONLY,
    "workspace_write": PROFILE_WORKSPACE_WRITE,
    "trusted_development": PROFILE_WORKSPACE_WRITE,
    "implementation": PROFILE_WORKSPACE_WRITE,
    "write": PROFILE_WORKSPACE_WRITE,
}


def normalize_override(override: str | None) -> str | None:
    """Return the canonical profile an explicit override requests, or ``None``
    when no override was given.

    ``None``/empty/whitespace means "no override" (fall back to the task-type
    category). A non-empty string that is neither a recognized profile nor a
    recognized alias raises `InvalidCapabilityOverrideError` (fail closed) —
    an invalid override is never silently ignored, because ignoring it would
    hand the run whatever the default happened to be while the operator
    believed they had constrained it."""
    if override is None:
        return None
    if not isinstance(override, str):
        raise InvalidCapabilityOverrideError(f"Capability override must be a string, got {type(override).__name__}.")
    key = override.strip()
    if not key:
        return None
    normalized = key.upper()
    if normalized in VALID_PROFILES:
        return normalized
    alias = _OVERRIDE_ALIASES.get(key.lower())
    if alias is not None:
        return alias
    raise InvalidCapabilityOverrideError(
        f"Unknown executor capability override {override!r}. "
        f"Valid values: {', '.join(sorted(VALID_PROFILES))} (or a recognized alias)."
    )


def profile_for_task_type(task_type: str) -> str:
    """The capability profile a `task_type` resolves to when there is no
    explicit override: ``PROFILE_READ_ONLY`` for exactly `READ_ONLY_TASK_TYPES`,
    ``PROFILE_WORKSPACE_WRITE`` for everything else (including any
    unrecognized/future task type — matching the historical "else" branch)."""
    return PROFILE_READ_ONLY if task_type in READ_ONLY_TASK_TYPES else PROFILE_WORKSPACE_WRITE


def minimal_profile_for(capabilities: frozenset[str]) -> str:
    """The least-privileged profile whose grant covers `capabilities`."""
    return PROFILE_READ_ONLY if capabilities <= READ_ONLY_CAPABILITIES else PROFILE_WORKSPACE_WRITE


def sort_capabilities(capabilities) -> list[str]:
    """`capabilities` in canonical `CAPABILITY_ORDER` (unknown names sort last,
    alphabetically, so nothing is ever dropped from a rendered set)."""
    return sorted(capabilities, key=lambda name: (_ORDER_INDEX.get(name, len(CAPABILITY_ORDER)), name))


def format_capabilities(capabilities) -> str:
    """Slash-joined canonical rendering, e.g. ``"Read/Glob/Grep"``."""
    return "/".join(sort_capabilities(capabilities)) or "(none)"


# --------------------------------------------------------------------------
# Prompt-intent detection — does the task's own prompt demand write access?
#
# A bounded, deterministic phrase list (no LLM call), deliberately narrow so
# ordinary read-only review prose is not misclassified. This is what catches a
# read-only-*typed* task whose prompt actually requires editing/tests/commits
# (the AIOS-RECON-001 shape): prompt intent can only ever *raise* the required
# capabilities to write, never lower them.
# --------------------------------------------------------------------------

# Explicit read-only directives. When present they suppress the write-intent
# signal (the operator has stated the run must not modify anything) — they only
# affect the *prompt's* contribution to required capabilities, never the
# task-type category, so an `implementation` task still requires write even if
# its prompt says "do not commit yet".
_READ_ONLY_MARKERS: list[re.Pattern[str]] = [
    re.compile(r"\bread[-\s]?only\b", re.I),
    re.compile(r"\bdo\s+not\s+(?:modify|edit|write|change|commit|create)\b", re.I),
    re.compile(r"\bdon'?t\s+(?:modify|edit|write|change|commit|create)\b", re.I),
    re.compile(r"\bwithout\s+(?:modifying|editing|writing|changing|committing)\b", re.I),
    re.compile(r"\bno\s+(?:code\s+)?(?:changes|modifications|edits|writes)\b", re.I),
]

_WRITE_INTENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bedit(?:ing|s)?\s+(?:the\s+|these\s+|those\s+)?files?\b", re.I),
    re.compile(r"\bedit(?:ing)?\s+(?:the\s+)?(?:code|source|module|function)\b", re.I),
    re.compile(r"\bmodify(?:ing)?\s+(?:the\s+|these\s+)?(?:files?|code|source)\b", re.I),
    re.compile(r"\bwrit(?:e|ing)\s+(?:the\s+|a\s+|new\s+)?(?:code|files?|tests?)\b", re.I),
    re.compile(r"\badd(?:ing)?\s+(?:a\s+|new\s+|the\s+|regression\s+)?tests?\b", re.I),
    re.compile(r"\bregression\s+tests?\b", re.I),
    re.compile(r"\brun(?:ning)?\s+(?:the\s+)?(?:validation|tests?|test\s+suite|lint(?:er)?|pytest|ruff|checks?)\b", re.I),
    re.compile(r"\bcommit(?:ting|s)?\b", re.I),
    re.compile(r"\bimplement(?:ing|ation)?\b", re.I),
    re.compile(r"\bapply(?:ing)?\s+(?:the\s+)?(?:patch|fix|change|diff)\b", re.I),
    re.compile(r"\bcreat(?:e|ing)\s+(?:a\s+|new\s+|the\s+)?(?:files?|module|artifacts?|branch)\b", re.I),
    re.compile(r"\bgenerat(?:e|ing)\s+(?:the\s+|a\s+)?(?:files?|code|artifacts?)\b", re.I),
    re.compile(r"\bfix(?:ing|es)?\s+(?:the\s+)?(?:bug|issue|defect|code|failing)\b", re.I),
    re.compile(r"\brefactor(?:ing)?\b", re.I),
]


def prompt_requires_write(prompt: str | None) -> bool:
    """`True` when `prompt` contains an explicit directive to modify the
    workspace (edit/write files, add tests, run validation, commit, generate
    artifacts, …) and no overriding read-only directive. `False` for an empty
    prompt or a prompt with no such directive."""
    if not prompt:
        return False
    for marker in _READ_ONLY_MARKERS:
        if marker.search(prompt):
            return False
    return any(pattern.search(prompt) for pattern in _WRITE_INTENT_PATTERNS)


# --------------------------------------------------------------------------
# The decision.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CapabilityDecision:
    """The full, self-describing result of resolving one run's capabilities.

    Every field is JSON-trivial (strings / sorted string lists / bools) so it
    can be persisted verbatim for diagnostics and rendered in the UI/report
    without re-deriving anything.
    """

    task_type: str
    override: str | None
    selected_profile: str  # the profile the run will actually be granted
    required_profile: str  # the least-privileged profile that would satisfy the need
    granted_capabilities: list[str]
    required_capabilities: list[str]
    missing_capabilities: list[str]
    prompt_requires_write: bool
    ok: bool
    reason: str | None  # human-facing mismatch sentence, or None when ok
    command_policy: str = field(default="")

    def as_metadata(self) -> dict:
        """Flat dict for persistence (run columns / run event payload). No
        prompt text, no secrets — only the policy identity and capability
        sets."""
        return {
            "capability_profile": self.selected_profile,
            "capability_override": self.override,
            "required_capabilities": ",".join(self.required_capabilities),
            "granted_capabilities": ",".join(self.granted_capabilities),
            "capability_required_profile": self.required_profile,
            "capability_prompt_requires_write": self.prompt_requires_write,
            "capability_preflight": "ok" if self.ok else "mismatch",
            "command_policy": self.command_policy,
            "capability_reason": self.reason,
        }


def command_policy_identity(profile: str, *, permission_mode: str | None = None) -> str:
    """A stable, secret-free identity for the tool-permission policy a command
    encodes — profile + permission mode + the concrete tool flag. Deliberately
    excludes the prompt (which may carry sensitive content). Used for
    diagnostics ("which policy did this run actually get?")."""
    mode = permission_mode or PERMISSION_MODE_BY_PROFILE.get(profile, "?")
    if profile == PROFILE_READ_ONLY:
        tool_flag = "tools=" + ",".join(sort_capabilities(READ_ONLY_CAPABILITIES))
    else:
        tool_flag = "disallowedTools=git-write"
    return f"{profile}|permission-mode={mode}|{tool_flag}"


def build_mismatch_reason(missing: frozenset[str], granted: frozenset[str]) -> str:
    """The exact user-facing sentence the brief specifies, e.g.::

        Executor capability mismatch: task requires Bash/Edit/Write;
        configured session provides only Read/Glob/Grep.
    """
    return (
        "Executor capability mismatch: "
        f"task requires {format_capabilities(missing)}; "
        f"configured session provides only {format_capabilities(granted)}."
    )


def decide(task_type: str, prompt: str | None, override: str | None = None) -> CapabilityDecision:
    """Resolve the capabilities a run needs vs. the capabilities it will be
    granted.

    `override` (if valid) sets the granted profile *and* the required baseline
    — an operator may run a normally-write-capable task read-only, and that is
    respected. The prompt-intent signal is applied *on top* of both the
    override/category baseline: a prompt that plainly requires writing raises
    the requirement to write regardless, so a read-only-configured run whose
    prompt demands edits/tests/commits still fails preflight (ambiguous ->
    fail closed). Raises `InvalidCapabilityOverrideError` for an invalid
    override.
    """
    normalized_override = normalize_override(override)
    selected_profile = normalized_override or profile_for_task_type(task_type)
    granted = PROFILE_CAPABILITIES[selected_profile]

    if normalized_override is not None:
        required = set(PROFILE_CAPABILITIES[normalized_override])
    else:
        required = set(PROFILE_CAPABILITIES[profile_for_task_type(task_type)])

    requires_write = prompt_requires_write(prompt)
    if requires_write:
        required |= WORKSPACE_WRITE_CAPABILITIES

    required_frozen = frozenset(required)
    missing = required_frozen - granted
    ok = not missing
    reason = None if ok else build_mismatch_reason(missing, granted)

    return CapabilityDecision(
        task_type=task_type,
        override=normalized_override,
        selected_profile=selected_profile,
        required_profile=minimal_profile_for(required_frozen),
        granted_capabilities=sort_capabilities(granted),
        required_capabilities=sort_capabilities(required_frozen),
        missing_capabilities=sort_capabilities(missing),
        prompt_requires_write=requires_write,
        ok=ok,
        reason=reason,
        command_policy=command_policy_identity(selected_profile),
    )


# Machine-readable `failure_reason` prefix for a preflight capability mismatch.
# Distinct from `runtime.outcome`'s `blocked:`/`incomplete:` prefixes so the
# UI can tell a *pre-spawn capability mismatch* apart from a user denial or a
# Claude runtime failure (see `runtime.session_view.derive_status`).
FAILURE_REASON_PREFIX = "capability_mismatch:"


def failure_reason_code(decision: CapabilityDecision) -> str:
    """The persisted `run.failure_reason` for a blocked launch — the prefix
    plus the missing capabilities in canonical order."""
    return FAILURE_REASON_PREFIX + ",".join(decision.missing_capabilities)
