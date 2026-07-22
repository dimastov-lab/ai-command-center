"""Unit tests for `command_center.capabilities` — the executor-capability
profile model, override resolution, prompt-intent detection, and the
required-vs-granted preflight decision.

Regression anchor: AIOS-RECON-001 — a task typed read-only (`review`/
reconnaissance) whose prompt required editing files, adding regression tests,
running validation, and committing was launched with a read-only tool set. The
`decide()` tests below lock the exact behavior that catches that mismatch
before a subprocess is ever spawned.
"""

from __future__ import annotations

import pytest

from command_center import capabilities as c


# --------------------------------------------------------------------------
# Profile selection from task type (no override).
# --------------------------------------------------------------------------


@pytest.mark.parametrize("task_type", ["implementation", "remediation"])
def test_write_task_types_receive_workspace_write(task_type):
    decision = c.decide(task_type, "do the thing", None)
    assert decision.selected_profile == c.PROFILE_WORKSPACE_WRITE
    assert set(decision.granted_capabilities) >= {c.CAP_BASH, c.CAP_EDIT, c.CAP_WRITE}
    assert decision.ok


def test_reconciliation_task_receives_workspace_write():
    decision = c.decide("reconciliation", "reconcile the state", None)
    assert decision.selected_profile == c.PROFILE_WORKSPACE_WRITE
    assert decision.ok


@pytest.mark.parametrize("task_type", ["migration", "repair", "integration"])
def test_other_write_category_task_types_receive_workspace_write(task_type):
    assert c.decide(task_type, "x", None).selected_profile == c.PROFILE_WORKSPACE_WRITE


@pytest.mark.parametrize("task_type", ["review", "audit", "architecture_review", "final_gate"])
def test_read_only_task_types_receive_read_only(task_type):
    # A benign, non-write prompt keeps the read-only category read-only.
    decision = c.decide(task_type, "summarize the findings and report", None)
    assert decision.selected_profile == c.PROFILE_READ_ONLY
    assert set(decision.granted_capabilities) == {c.CAP_READ, c.CAP_GLOB, c.CAP_GREP}
    assert decision.ok


def test_unknown_task_type_defaults_to_workspace_write():
    # Matches the historical "everything else -> write-capable" branch.
    assert c.decide("some_future_task_type", "x", None).selected_profile == c.PROFILE_WORKSPACE_WRITE


# --------------------------------------------------------------------------
# Prompt-intent detection — a read-only-*typed* task whose prompt demands writes.
# --------------------------------------------------------------------------


def test_review_task_with_write_prompt_is_a_capability_mismatch():
    """The AIOS-RECON-001 shape: read-only task type, write-requiring prompt."""
    decision = c.decide(
        "review",
        "Inspect git history, edit the files, add regression tests, run validation, and commit.",
        None,
    )
    assert decision.selected_profile == c.PROFILE_READ_ONLY  # what it would be granted
    assert decision.required_profile == c.PROFILE_WORKSPACE_WRITE  # what it actually needs
    assert not decision.ok
    assert decision.missing_capabilities == [c.CAP_BASH, c.CAP_EDIT, c.CAP_WRITE]


def test_mismatch_reason_is_the_exact_specified_sentence():
    decision = c.decide("review", "please edit the files and commit", None)
    assert decision.reason == (
        "Executor capability mismatch: task requires Bash/Edit/Write; "
        "configured session provides only Read/Glob/Grep."
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "edit the files",
        "add regression tests",
        "run the validation suite",
        "commit your changes",
        "implement the feature",
        "generate the artifacts",
        "apply the patch",
    ],
)
def test_prompt_requires_write_detects_write_directives(prompt):
    assert c.prompt_requires_write(prompt) is True


@pytest.mark.parametrize(
    "prompt",
    [
        "review the code and report findings",
        "audit the architecture for risks",
        "summarize the module structure",
        "identify potential problems",
        "",
        None,
    ],
)
def test_prompt_requires_write_ignores_read_only_prose(prompt):
    assert c.prompt_requires_write(prompt) is False


def test_explicit_read_only_marker_suppresses_write_intent():
    # Even with a write verb present, an explicit read-only directive wins for
    # the prompt's contribution.
    assert c.prompt_requires_write("review only, do not modify or commit anything") is False


def test_implementation_prompt_marker_does_not_lower_category_requirement():
    # The read-only marker only affects the *prompt's* contribution; the
    # implementation task-type category still requires write.
    decision = c.decide("implementation", "implement X but do not commit yet", None)
    assert decision.selected_profile == c.PROFILE_WORKSPACE_WRITE
    assert decision.ok


# --------------------------------------------------------------------------
# Overrides.
# --------------------------------------------------------------------------


def test_explicit_read_only_override_is_respected_for_write_category_task():
    # A task may request read-only execution even though its category would
    # permit writing (brief point 4). Benign prompt -> respected, launches.
    decision = c.decide("implementation", "investigate the failing module", "read_only")
    assert decision.override == c.PROFILE_READ_ONLY
    assert decision.selected_profile == c.PROFILE_READ_ONLY
    assert decision.ok


def test_read_only_override_with_write_prompt_fails_closed():
    # Ambiguous: operator forced read-only, but the prompt plainly requires
    # writing. Fail closed rather than silently under-provision.
    decision = c.decide("implementation", "edit the files and commit the fix", "read_only")
    assert decision.selected_profile == c.PROFILE_READ_ONLY
    assert not decision.ok
    assert decision.missing_capabilities == [c.CAP_BASH, c.CAP_EDIT, c.CAP_WRITE]


def test_workspace_write_override_grants_write_on_read_only_task():
    decision = c.decide("review", "x", "workspace_write")
    assert decision.selected_profile == c.PROFILE_WORKSPACE_WRITE
    assert decision.ok


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("READ_ONLY", c.PROFILE_READ_ONLY),
        ("read_only", c.PROFILE_READ_ONLY),
        ("workspace_write", c.PROFILE_WORKSPACE_WRITE),
        ("trusted_development", c.PROFILE_WORKSPACE_WRITE),
        ("  Read_Only  ", c.PROFILE_READ_ONLY),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_normalize_override_accepts_canonical_and_aliases(raw, expected):
    assert c.normalize_override(raw) == expected


@pytest.mark.parametrize("bad", ["banana", "readwrite", "yolo", "read-write"])
def test_invalid_override_is_rejected(bad):
    with pytest.raises(c.InvalidCapabilityOverrideError):
        c.normalize_override(bad)


def test_decide_rejects_invalid_override():
    with pytest.raises(c.InvalidCapabilityOverrideError):
        c.decide("review", "x", "banana")


def test_non_string_override_is_rejected():
    with pytest.raises(c.InvalidCapabilityOverrideError):
        c.normalize_override(123)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Metadata / serialization for persistence.
# --------------------------------------------------------------------------


def test_as_metadata_is_flat_and_secret_free():
    decision = c.decide("review", "edit the files and commit", None)
    meta = decision.as_metadata()
    assert meta["capability_profile"] == c.PROFILE_READ_ONLY
    assert meta["required_capabilities"] == "Read,Glob,Grep,Bash,Edit,Write"
    assert meta["granted_capabilities"] == "Read,Glob,Grep"
    assert meta["capability_preflight"] == "mismatch"
    # command policy identifies the policy without leaking the prompt
    assert "edit the files" not in meta["command_policy"]
    assert meta["command_policy"].startswith(c.PROFILE_READ_ONLY)


def test_failure_reason_code_is_prefixed_and_machine_readable():
    decision = c.decide("review", "edit the files and commit", None)
    assert c.failure_reason_code(decision) == "capability_mismatch:Bash,Edit,Write"
    assert c.failure_reason_code(decision).startswith(c.FAILURE_REASON_PREFIX)


def test_command_policy_identity_excludes_prompt_and_names_profile():
    identity = c.command_policy_identity(c.PROFILE_WORKSPACE_WRITE)
    assert identity.startswith(c.PROFILE_WORKSPACE_WRITE)
    assert "permission-mode=acceptEdits" in identity
