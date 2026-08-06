"""Regression for SEC-D-02 (laundering): a task synthesized from an agent
report — the "create next task" widget and chat "convert message to task" — must
be able to carry the app-set `untrusted_import` provenance flag, exactly as
`backlog_proposals.apply_candidate` already stamps report-derived candidates. The
`app.create_task` wrapper previously had no `untrusted_import` parameter, so those
call sites *could not* propagate provenance and a follow-up derived from untrusted
report content launched as a trusted (Bash + bypassPermissions) run.
"""

from __future__ import annotations

import inspect

import app
from command_center import agent_runner


def test_create_task_wrapper_exposes_untrusted_import():
    assert "untrusted_import" in inspect.signature(app.create_task).parameters


def test_create_task_wrapper_stamps_untrusted(tmp_path, monkeypatch):
    monkeypatch.setenv("AICC_DATA_DIR", str(tmp_path))
    rec = app.create_task("AICC", "t", "remediation", "Backlog", goal="g", untrusted_import=True)
    assert rec.get("untrusted_import") is True
    assert agent_runner.is_untrusted_task(rec) is True


def test_create_task_wrapper_defaults_to_trusted(tmp_path, monkeypatch):
    monkeypatch.setenv("AICC_DATA_DIR", str(tmp_path))
    rec = app.create_task("AICC", "t", "implementation", "Backlog", goal="g")
    assert agent_runner.is_untrusted_task(rec) is False
