"""Provenance-based capability gating (audit D7).

The agent runs `claude` with `--permission-mode bypassPermissions` + Bash for any
non-read-only task type, on task text that can originate from an *imported task
package* (`task_import`) — untrusted input. So a malicious imported task could
drive arbitrary local shell. This gates capabilities on provenance: an untrusted
task defaults to the read-only profile (no Bash, no bypassPermissions) and only
an explicit operator elevation restores full development capability.
"""

from __future__ import annotations

from command_center import agent_runner as ar


def test_read_only_task_type_stays_read_only_regardless_of_provenance():
    # A read-only type has no dangerous capability to begin with.
    assert ar.profile_for_task("review", untrusted=True, operator_elevated=False) == ar.PROFILE_READ_ONLY
    assert ar.profile_for_task("review", untrusted=False, operator_elevated=True) == ar.PROFILE_READ_ONLY


def test_trusted_source_non_readonly_gets_full_development_profile():
    # An operator-authored (in-app) implementation task is unchanged.
    assert (
        ar.profile_for_task("implementation", untrusted=False, operator_elevated=False)
        == ar.PROFILE_TRUSTED_DEVELOPMENT
    )


def test_untrusted_non_readonly_downgrades_to_read_only():
    # The core fix: an imported implementation task is NOT auto-granted Bash +
    # bypassPermissions.
    assert (
        ar.profile_for_task("implementation", untrusted=True, operator_elevated=False)
        == ar.PROFILE_READ_ONLY
    )
    # Unknown/future types are also downgraded when untrusted (fail closed).
    assert ar.profile_for_task("something_new", untrusted=True, operator_elevated=False) == ar.PROFILE_READ_ONLY


def test_operator_elevation_restores_full_profile_for_untrusted_task():
    assert (
        ar.profile_for_task("implementation", untrusted=True, operator_elevated=True)
        == ar.PROFILE_TRUSTED_DEVELOPMENT
    )


def test_profile_for_task_matches_legacy_when_trusted():
    # Back-compat: with no provenance signal, the decision equals the old
    # task-type-only rule for every known type.
    for tt in ["review", "final_gate", "architecture_review", "implementation", "remediation", "weird"]:
        assert ar.profile_for_task(tt, untrusted=False, operator_elevated=False) == ar.profile_for_task_type(tt)


def test_build_command_untrusted_implementation_has_no_bypass_and_no_bash():
    from command_center.runtime import supervisor

    cmd = supervisor.build_claude_command(
        session_id="s", prompt="p", task_type="implementation", is_resume=False, untrusted=True
    )
    # Downgraded to the read-only profile: no bypassPermissions, and `--tools`
    # replaces the built-in set so Bash simply does not exist for the run.
    assert "bypassPermissions" not in cmd
    assert "acceptEdits" in cmd
    assert "--tools" in cmd
    assert "--disallowedTools" not in cmd


def test_build_command_trusted_implementation_keeps_full_development():
    from command_center.runtime import supervisor

    cmd = supervisor.build_claude_command(
        session_id="s", prompt="p", task_type="implementation", is_resume=False
    )
    assert "bypassPermissions" in cmd
    assert "--disallowedTools" in cmd
    assert "--tools" not in cmd


def test_build_command_untrusted_but_operator_elevated_keeps_full_development():
    from command_center.runtime import supervisor

    cmd = supervisor.build_claude_command(
        session_id="s", prompt="p", task_type="implementation", is_resume=False,
        untrusted=True, operator_elevated=True,
    )
    assert "bypassPermissions" in cmd


def test_claude_provider_build_launch_untrusted_drops_bypass_and_bash(tmp_path):
    from command_center.runtime import providers

    spec = providers.ClaudeProvider().build_launch(
        repository_path=tmp_path, session_id="s", prompt="p",
        task_type="implementation", is_resume=False, model=None, untrusted=True,
    )
    argv = list(spec.argv)
    assert "bypassPermissions" not in argv
    assert "--tools" in argv
    assert "--disallowedTools" not in argv


def test_claude_provider_build_launch_trusted_keeps_full_development(tmp_path):
    from command_center.runtime import providers

    spec = providers.ClaudeProvider().build_launch(
        repository_path=tmp_path, session_id="s", prompt="p",
        task_type="implementation", is_resume=False, model=None,
    )
    assert "bypassPermissions" in list(spec.argv)


def test_is_untrusted_source():
    # An imported task carries its package's provenance label; an in-app task
    # has no source.
    assert ar.is_untrusted_source("Founder Audit v2") is True
    assert ar.is_untrusted_source("Architecture Audit 2026-07-25") is True
    assert ar.is_untrusted_source("") is False
    assert ar.is_untrusted_source("   ") is False
    assert ar.is_untrusted_source(None) is False
