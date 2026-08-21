"""Regression tests for the audit-remediation hardening (P0/H1, L1).

Covers:
- the launched agent's environment is stripped of Git/GitHub push/merge
  credentials before spawn (H1);
- the GitHub CLI (`gh`) is denied for implementation/remediation agents, and
  read-only agents have no shell at all (H1);
- git_ops rejects every force-push spelling, including the `--force-with-lease=`
  equals-form the old exact-token tuple missed (L1).
"""

from __future__ import annotations

import pytest

from command_center import agent_runner
from command_center.runtime import git_ops


def test_scrub_vcs_credentials_removes_tokens_but_keeps_model_auth():
    env = {
        "GH_TOKEN": "ghp_secret",
        "GITHUB_TOKEN": "gho_secret",
        "GH_ENTERPRISE_TOKEN": "ent",
        "GIT_ASKPASS": "/usr/bin/askpass",
        "SSH_ASKPASS": "/usr/bin/ssh-askpass",
        "ANTHROPIC_API_KEY": "sk-ant-keep",
        "CLAUDE_CODE_MODEL": "sonnet",
        "PATH": "/usr/bin",
        "HOME": "/Users/x",
    }
    scrubbed = agent_runner.scrub_vcs_credentials(env)

    # every credential-bearing variable is gone
    for removed in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN",
                    "GIT_ASKPASS", "SSH_ASKPASS"):
        assert removed not in scrubbed
    # the agent's own model auth and essential process vars survive
    assert scrubbed["ANTHROPIC_API_KEY"] == "sk-ant-keep"
    assert scrubbed["CLAUDE_CODE_MODEL"] == "sonnet"
    assert scrubbed["PATH"] == "/usr/bin"
    assert scrubbed["HOME"] == "/Users/x"
    # the input mapping is not mutated in place
    assert "GH_TOKEN" in env


def test_gh_is_denied_for_implementation_and_remediation_agents():
    assert "Bash(gh:*)" in agent_runner.GIT_WRITE_DISALLOWED_TOOLS
    for task_type in ("implementation", "remediation"):
        cmd = agent_runner.build_command("do the task", task_type=task_type)
        assert "--disallowedTools" in cmd
        denied = cmd[cmd.index("--disallowedTools") + 1]
        assert "Bash(gh:*)" in denied
        assert "Bash(git push:*)" in denied


def test_read_only_agents_have_no_shell_tool_at_all():
    """Unrestricted Bash/git are unreachable via tool-set replacement; the
    two narrowly-scoped, read-only `gh pr view`/`gh pr diff` entries a review
    run needs to open the PR it was asked to assess are the sole exception
    (2026-08-21) -- everything else Bash-shaped, including plain `Bash` and
    any git subcommand, remains absent."""
    cmd = agent_runner.build_command("review it", task_type="review")
    assert "--tools" in cmd
    tools = cmd[cmd.index("--tools") + 1].split(",")
    assert "Bash" not in tools
    bash_entries = [t for t in tools if t.startswith("Bash")]
    assert set(bash_entries) <= {"Bash(gh pr view:*)", "Bash(gh pr diff:*)"}


@pytest.mark.parametrize(
    "args",
    [
        ["push", "origin", "--force", "main:main"],
        ["push", "origin", "-f", "main:main"],
        ["push", "origin", "+main:main"],
        ["push", "origin", "--force-with-lease", "main:main"],
        ["push", "origin", "--force-with-lease=refs/heads/main", "main:main"],
        ["push", "origin", "--force-if-includes", "main:main"],
    ],
)
def test_git_ops_rejects_every_force_push_spelling(args, tmp_path):
    # _assert_not_force runs before any subprocess, so no real git is invoked.
    with pytest.raises(git_ops.GitOpsError):
        git_ops.run_git_write(tmp_path, args)
