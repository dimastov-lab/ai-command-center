"""MAJOR-9: the runtime report *write* path must stay under REPORTS_ROOT.

`command_center.runtime.reports.report_path_for` is a second copy of the report
path builder -- the `agent_runner` one was contained under SEC-2, this one was
not. It builds ``REPORTS_ROOT / project / f"{ts}_{id[:12]}.md"`` from a run's
``project``/``id``; an imported or hand-authored ``project`` like "../../root"
walked the write out of REPORTS_ROOT. Each user-influenced component is now
sanitized to a separator-free charset (mirrors SEC-2's fix on the sibling).
"""

from __future__ import annotations

from command_center.runtime import reports


def test_malicious_project_cannot_escape_reports_root(tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "REPORTS_ROOT", tmp_path)
    run = {"id": "run-1", "project": "../../root", "started_at": "2026-07-29T00:00:00"}
    resolved = reports.report_path_for(run).resolve()
    assert tmp_path.resolve() in resolved.parents, f"escaped REPORTS_ROOT: {resolved}"


def test_malicious_run_id_cannot_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "REPORTS_ROOT", tmp_path)
    run = {"id": "../../../pwn", "project": "AICC", "started_at": "2026-07-29T00:00:00"}
    resolved = reports.report_path_for(run).resolve()
    assert tmp_path.resolve() in resolved.parents, f"escaped REPORTS_ROOT: {resolved}"
    # the sanitized id stays a single filename component under <project>/
    assert resolved.parent == (tmp_path / "AICC").resolve()


def test_legit_values_are_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "REPORTS_ROOT", tmp_path)
    run = {"id": "run-abc123", "project": "AICC-Proj", "started_at": "2026-07-29T12:00:00"}
    path = reports.report_path_for(run)
    assert path.parent == tmp_path / "AICC-Proj"
    assert path.name.startswith("20260729-120000_run-abc123")
    assert path.suffix == ".md"
