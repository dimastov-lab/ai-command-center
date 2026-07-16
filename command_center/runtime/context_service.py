"""Sensitive-project (BANK, LEGAL) context boundary, enforced in the service
layer.

v1.2 relied on the Streamlit UI to never auto-attach file contents/reports/
prompts/chats/diffs for BANK/LEGAL (see ARCHITECTURE.md §11.3). That boundary
lived only in `app.py`. The v2 Session Supervisor is a separate backend that
does not go through that UI, so this module re-implements the same rule as an
enforcement point every caller must pass through — a caller cannot get
sensitive content into a run's context by skipping a UI checkbox, because
there is no code path here that hands back sensitive content without an
explicit, per-item confirmation.
"""

from __future__ import annotations

import json

from command_center import project_config


class ConfirmationRequiredError(Exception):
    """Raised when a launch is attempted without explicit confirmation."""


def assemble_context(
    *,
    project_id: str,
    metadata: dict | None = None,
    candidate_content: dict[str, str] | None = None,
    confirmed_items: list[str] | None = None,
) -> dict:
    """Build the context bundle a run is allowed to receive.

    `metadata` (file names, counts, sizes, timestamps — never content) is
    always included, for every project, sensitive or not.

    `candidate_content` maps an item key (e.g. a file path, "report:<run_id>",
    "chat:<conversation_id>", "diff:<run_id>") to its actual text. For a
    non-sensitive project every candidate item passes through. For a
    sensitive project (`project_config.is_sensitive`), an item is included
    *only* if its key is also present in `confirmed_items` — the caller's
    proof that the user explicitly confirmed attaching that exact item.
    Anything sensitive and unconfirmed is silently excluded from `content`
    (surfaced instead in `excluded_pending_confirmation`, so a caller can
    still show "N items available, not attached" without ever receiving the
    text itself).
    """
    sensitive = project_config.is_sensitive(project_id)
    confirmed = set(confirmed_items or [])
    content: dict[str, str] = {}
    excluded: list[str] = []
    for key, value in (candidate_content or {}).items():
        if not sensitive or key in confirmed:
            content[key] = value
        else:
            excluded.append(key)
    return {
        "project": project_id,
        "sensitive": sensitive,
        "metadata": dict(metadata or {}),
        "content": content,
        "excluded_pending_confirmation": excluded,
    }


def build_prompt(instruction: str, context: dict) -> str:
    """Build the final provider-facing prompt from a user instruction plus an
    already-assembled `context` bundle (the return value of
    `assemble_context`). This is the *only* place a final prompt is
    constructed for an application-facing launch (see
    `ExecutionCenterAPI.start_run`) — a caller never gets to hand in a
    finished prompt string of their own for a real project launch, so
    sensitive content can only ever reach the outbound prompt by having
    already passed through `assemble_context`'s confirmation gate.

    Only `context["metadata"]` and `context["content"]` (both already
    filtered for sensitivity) are ever included — `context
    ["excluded_pending_confirmation"]` keys never appear in the returned
    text, by construction.
    """
    parts = [instruction.strip()]
    metadata = context.get("metadata") or {}
    if metadata:
        parts.append("## Context metadata\n" + json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True))
    content = context.get("content") or {}
    for key in sorted(content):
        parts.append(f"## Context: {key}\n\n{content[key]}")
    return "\n\n".join(parts)


def build_outbound_manifest(context: dict) -> dict:
    """A structured, auditable summary of exactly what left the machine in
    the assembled prompt: content *keys* and sizes (never the content text
    itself — that's already verbatim in the run's stored `prompt` field;
    this is a compact, purpose-built audit record meant to be readable at a
    glance via the events API without pulling the full prompt text)."""
    content = context.get("content") or {}
    return {
        "project": context.get("project"),
        "sensitive": context.get("sensitive"),
        "included_content_keys": sorted(content),
        "included_content_sizes": {key: len(value) for key, value in content.items()},
        "excluded_content_keys": sorted(context.get("excluded_pending_confirmation") or []),
        "metadata_keys": sorted((context.get("metadata") or {}).keys()),
    }


def require_launch_confirmation(confirmed: bool, *, what: str = "This launch") -> None:
    """Every launch requires explicit confirmation from the caller/UI — this is
    the one gate `supervisor.start_raw()` calls before a subprocess is ever
    spawned, regardless of what a calling UI layer does or doesn't check."""
    if not confirmed:
        raise ConfirmationRequiredError(f"{what} requires explicit confirmation before it may run.")
