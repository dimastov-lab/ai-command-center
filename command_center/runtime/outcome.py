"""EvaluatingResult: decides what a process exit *means*, separately from
whether the process exited.

`Supervisor._supervise()` already knows the *process*-level facts the moment
`claude` exits: `exit_code`, whether cancellation/timeout fired, and whether
the working tree changed. Historically it collapsed straight from those
facts to `run.state` with one rule — `exit_code == 0` -> `COMPLETED` — which
is wrong: `exit_code == 0` only proves the `claude` CLI process itself did
not crash. It says nothing about whether the requested tool calls were
actually permitted to run (a `Write`/`Edit` call denied by permission mode
still leaves the CLI process exiting 0 — confirmed empirically against the
real CLI, see `agent_runner`'s profile docstring) or whether the agent's own
final message reports it could not proceed.

This module is the explicit `EvaluatingResult` stage: given the process-exit
facts plus the run's own structured result evidence (the final `result`
stream-json event, which the real `claude` CLI populates with a
`permission_denials` array — see `claude -p --output-format json`'s
documented `result` event shape), it decides whether an `exit_code == 0`
run is genuinely `OK` (the caller may record `COMPLETED`), was `BLOCKED`
(a tool call was denied, or the agent's own final message says it could
not continue), or is `INCOMPLETE` (the process finished cleanly and nothing
was blocked, but a task type that requires repository changes produced
none).

Prefers structured event evidence (`permission_denials`) over text
classification whenever both are available — text classification
(`detect_blocker_language`) is only consulted when the structured field is
empty, per the project's remediation brief ("prefer structured event
evidence over text classification when both exist").

Pure functions only: no I/O, no database access, easy to unit test and to
call from `Supervisor._supervise()` without adding a new failure surface.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Outcome vocabulary for this module's own return value. Distinct from (and
# narrower than) `runtime.session_view`'s display vocabulary or `runtime.db`'s
# persisted `RUN_STATES` — this module only ever needs to say "the process
# exited 0; was that actually a success?".
# --------------------------------------------------------------------------

OK = "ok"
BLOCKED = "blocked"
INCOMPLETE = "incomplete"

# Task types whose entire purpose is to modify the repository — see
# `agent_runner.PROFILE_TRUSTED_DEVELOPMENT`. A run of one of these task
# types that exits 0 with an unchanged working tree did not do its job,
# regardless of what it says in its final message.
REQUIRES_CHANGES_TASK_TYPES = frozenset({"implementation", "remediation"})


# --------------------------------------------------------------------------
# Final-response blocker classifier (Required fix 6) — a bounded,
# deterministic phrase list, not naive exact-string matching and not an LLM
# call. Only consulted when there is no structured `permission_denials`
# evidence to rely on instead. Patterns are intentionally narrow (word
# boundaries, specific phrasing) so ordinary report prose is never
# misclassified — e.g. a report that merely *mentions* the word "blocked" in
# a findings table is not, by itself, evidence the run itself was blocked;
# callers should treat this as one signal among the structured ones, not an
# infallible oracle.
# --------------------------------------------------------------------------

_BLOCKER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bcannot\s+(?:execute|continue|proceed|complete)\b", re.I),
    re.compile(r"\bunable\s+to\s+(?:execute|continue|proceed|complete)\b", re.I),
    re.compile(r"\bmissing\s+(?:the\s+)?(?:required\s+)?permissions?\b", re.I),
    re.compile(r"\bpermission\s+denied\b", re.I),
    re.compile(r"\bno\s+permission\s+to\b", re.I),
    re.compile(r"\bdo\s+not\s+have\s+(?:the\s+)?(?:necessary\s+|required\s+)?permission", re.I),
    re.compile(r"\btool\s+limitations?\b", re.I),
    re.compile(r"\bbash\s+(?:is\s+|was\s+)?unavailable\b", re.I),
    re.compile(r"\b(?:write|edit)\s+(?:tool\s+)?(?:is\s+|was\s+)?unavailable\b", re.I),
    re.compile(r"\bneed(?:s)?\s+access\b", re.I),
    re.compile(r"\bwaiting\s+for\s+approval\b", re.I),
    re.compile(r"\brequires?\s+approval\b", re.I),
    re.compile(r"\bblocked\s+(?:by|from|on)\b", re.I),
    re.compile(r"^\s*blocked\b", re.I),
]


def detect_blocker_language(text: str | None) -> str | None:
    """Returns the matched phrase (lowercased, for use in a `failure_reason`
    code) if `text` contains explicit blocker language, else `None`."""
    if not text:
        return None
    for pattern in _BLOCKER_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0).strip().lower()
    return None


# Git subcommands that a trusted implementation agent is *intentionally* denied
# (the completion pipeline owns commit/push/merge, never the agent — see
# `agent_runner.GIT_WRITE_DISALLOWED_TOOLS`). A denial of one of these is the
# system working as designed, not a run that "could not do its work": the agent
# poking at git it isn't allowed to touch, while its real work happened through
# Read/Edit/Write/other Bash. Such denials must NOT fail an otherwise-productive
# run. (`git stash` is included because the disallow pattern also catches the
# read-only `git stash list` the agent sometimes runs to inspect state.)
_EXPECTED_GIT_DENIAL_MARKERS: tuple[str, ...] = (
    "git add", "git apply", "git checkout", "git restore", "git switch",
    "git stash", "git commit", "git push", "git merge", "git reset",
    "git rebase", "git clean", "git branch -d", "git branch -D",
)


def _is_expected_git_denial(entry: dict) -> bool:
    """True when a denial is for one of the intentionally-blocked git-write
    commands — expected and benign, not evidence the run was blocked."""
    command = str((entry.get("tool_input") or {}).get("command") or "")
    return any(marker in command for marker in _EXPECTED_GIT_DENIAL_MARKERS)


def permission_denial_tool_names(permission_denials: list | None) -> list[str]:
    """Extracts the sorted, de-duplicated tool names from a `result` event's
    `permission_denials` array (each entry `{"tool_name": ..., ...}` per the
    real `claude -p --output-format json`/`stream-json` `result` event
    shape), **excluding intentionally-blocked git-write denials** (those are the
    system working as designed, not a run that could not do its work — a run that
    edited files and ran its tests but incidentally poked at `git` it is not
    allowed to touch is not "blocked"). Never raises — returns `[]`."""
    if not permission_denials:
        return []
    names = {
        str(entry["tool_name"])
        for entry in permission_denials
        if isinstance(entry, dict) and entry.get("tool_name") and not _is_expected_git_denial(entry)
    }
    return sorted(names)


def classify_process_result(
    *,
    task_type: str,
    result_text: str | None,
    permission_denials: list | None,
    working_tree_changed: bool,
) -> tuple[str, str | None]:
    """The `EvaluatingResult` decision for a run whose process already exited
    0, was not cancelled, and did not time out.

    Returns `(outcome, reason)`:

    - `(OK, None)`: the caller may record this run `COMPLETED`.
    - `(BLOCKED, reason)`: a tool call was denied (`reason` is
      `"permission_denied:<Tool1>,<Tool2>"`, structured evidence) or the
      agent's own final message explicitly reports it could not continue
      (`reason` is `"final_response:<matched phrase>"`, text evidence, only
      consulted when there were no permission denials to report instead).
    - `(INCOMPLETE, reason)`: nothing was blocked, but `task_type` requires
      repository changes and the working tree did not change
      (`reason == "working_tree_unchanged"`).

    `reason` is always a short, machine-readable code suitable for
    `run.failure_reason` (never `None` when `outcome != OK`) — this is what
    persists the structured blocker/failure reason the UI later displays
    (Required fix 5/7), rather than only being visible in a stream-json
    event a user would have to go dig up.
    """
    denied_tools = permission_denial_tool_names(permission_denials)
    if denied_tools:
        return BLOCKED, f"permission_denied:{','.join(denied_tools)}"

    blocker_phrase = detect_blocker_language(result_text)
    if blocker_phrase:
        return BLOCKED, f"final_response:{blocker_phrase}"

    if task_type in REQUIRES_CHANGES_TASK_TYPES and not working_tree_changed:
        return INCOMPLETE, "working_tree_unchanged"

    return OK, None
