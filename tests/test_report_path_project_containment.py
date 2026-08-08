"""A run's `report_path` must stay inside that run's own `reports/<project>/`.

`reports.report_path_for` only ever *writes* there, and the supervisor stores
the reference through `reports.stored_report_path`. But the reference is a
plain string in SQLite (and, for imported v1.2 runs, a plain string in
`data/runs.jsonl`), so a hand-edited or imported record can propose any path it
likes. Two consumers turn such a string back into something a user sees:

* `legacy_import.import_legacy_runs` — copies the legacy string into the
  `report` table verbatim;
* `task_sync._apply_terminal_fields` — publishes the `report` row onto the
  Kanban task, whose `report_path` the UI resolves and renders.

Both now refuse anything that does not resolve under `reports/<project>/`,
which additionally rejects a reference aimed at *another* project's reports —
containment `resolve_report_path`'s root-wide check alone does not give.
"""

from __future__ import annotations

from command_center.runtime import db, legacy_import, reports, task_sync


# --------------------------------------------------------------------------
# reports.resolve_report_path(..., project=...) — the shared guard
# --------------------------------------------------------------------------


def test_project_scoped_resolution_accepts_a_report_in_its_own_project_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "REPORTS_ROOT", tmp_path)
    resolved = reports.resolve_report_path("reports/AIOS/20260101-000000_run-1.md", project="AIOS")
    assert resolved == (tmp_path / "AIOS" / "20260101-000000_run-1.md").resolve()


def test_project_scoped_resolution_matches_what_report_path_for_writes(tmp_path, monkeypatch):
    """The guard must never reject a path the writer itself produced — including
    for a project name the writer sanitizes ("My Proj" -> "My_Proj")."""
    monkeypatch.setattr(reports, "REPORTS_ROOT", tmp_path)
    run = {"id": "run-1", "project": "My Proj", "started_at": "2026-01-01T00:00:00"}
    path = reports.report_path_for(run)
    path.parent.mkdir(parents=True)
    path.write_text("report", encoding="utf-8")

    stored = reports.stored_report_path(path)
    assert reports.resolve_report_path(stored, project="My Proj") == path.resolve()


def test_project_scoped_resolution_rejects_traversal_out_of_the_project_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "REPORTS_ROOT", tmp_path / "reports")
    assert reports.resolve_report_path("reports/AIOS/../../../etc/passwd", project="AIOS") is None
    assert reports.resolve_report_path("reports/../../secrets.md", project="AIOS") is None
    assert reports.resolve_report_path("../../../etc/passwd", project="AIOS") is None


def test_project_scoped_resolution_rejects_absolute_paths_outside_the_project_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "REPORTS_ROOT", tmp_path / "reports")
    assert reports.resolve_report_path("/etc/passwd", project="AIOS") is None


def test_project_scoped_resolution_rejects_another_projects_report(tmp_path, monkeypatch):
    """Inside REPORTS_ROOT, so the root-wide check passes it — the project
    scope is what makes this a rejection."""
    monkeypatch.setattr(reports, "REPORTS_ROOT", tmp_path)
    stored = "reports/BANK/20260101-000000_run-1.md"
    assert reports.resolve_report_path(stored) is not None
    assert reports.resolve_report_path(stored, project="AIOS") is None


def test_project_scoped_resolution_rejects_the_project_dir_itself(tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "REPORTS_ROOT", tmp_path)
    assert reports.resolve_report_path("reports/AIOS", project="AIOS") is None


def test_project_scoped_resolution_rejects_symlink_escape(tmp_path, monkeypatch):
    reports_root = tmp_path / "reports"
    (reports_root / "AIOS").mkdir(parents=True)
    outside = tmp_path / "private.md"
    outside.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(reports, "REPORTS_ROOT", reports_root)

    (reports_root / "AIOS" / "escape.md").symlink_to(outside)
    assert reports.resolve_report_path("reports/AIOS/escape.md", project="AIOS") is None


def test_a_malicious_project_name_cannot_widen_the_scope(tmp_path, monkeypatch):
    """The scope is built from the *sanitized* component, so a run whose
    `project` is "../../etc" is confined to `reports/______etc/`, not to `/etc`."""
    monkeypatch.setattr(reports, "REPORTS_ROOT", tmp_path / "reports")
    assert reports.resolve_report_path("/etc/passwd", project="../../etc") is None
    assert reports.resolve_report_path("reports/../../etc/passwd", project="../../etc") is None
    assert reports.project_report_dir("../../etc").parent == (tmp_path / "reports")


def test_root_wide_resolution_is_unchanged_when_no_project_is_given(tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "REPORTS_ROOT", tmp_path / "reports")
    assert reports.resolve_report_path("reports/AIOS/x.md") is not None
    assert reports.resolve_report_path("reports/../../private.md") is None


# --------------------------------------------------------------------------
# task_sync — the guard at the point a report reference reaches a task
# --------------------------------------------------------------------------


def _completed_run(db_path, *, project: str = "AIOS") -> dict:
    task = db.create_task(db_path, project=project, title="t", task_type="implementation")
    session = db.create_session(db_path, task_id=task["id"], project=project, repository_path="/tmp/x")
    run = db.create_run(
        db_path,
        session_id=session["id"],
        task_id=task["id"],
        project=project,
        task_type="implementation",
        repository_path="/tmp/x",
        prompt="p",
        is_resume=False,
    )
    run = db.update_run_state(db_path, run["id"], expected_version=run["version"], new_state="QUEUED")
    run = db.update_run_state(db_path, run["id"], expected_version=run["version"], new_state="RUNNING")
    return db.update_run_state(
        db_path,
        run["id"],
        expected_version=run["version"],
        new_state="COMPLETED",
        fields={"completed_at": "2026-01-01T00:01:00"},
    )


def _kanban_task(project: str = "AIOS") -> dict:
    return {
        "id": "task-1",
        "project": project,
        "title": "Deploy P1",
        "launch_status": "Ready",
        "current_run_id": None,
        "progress": 0,
        "progress_mode": "auto",
        "timeline": [],
    }


def test_task_sync_publishes_a_report_path_inside_the_projects_dir(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    run = _completed_run(db_path)
    db.create_report(db_path, run["id"], "reports/AIOS/20260101-000000_run.md")
    task = _kanban_task()

    task_sync.sync_task_from_run(task, run, db_path=db_path)

    assert task["report_path"] == "reports/AIOS/20260101-000000_run.md"


def test_task_sync_drops_a_traversal_report_path(tmp_path):
    """The traversal case: a `report` row proposing `../../../etc/passwd` must
    not reach the task — the UI resolves and renders whatever lands there."""
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    run = _completed_run(db_path)
    db.create_report(db_path, run["id"], "reports/AIOS/../../../etc/passwd")
    task = _kanban_task()

    task_sync.sync_task_from_run(task, run, db_path=db_path)

    assert task.get("report_path") is None
    # the rest of the terminal sync still happens — the guard drops one field,
    # it does not abandon the run's bookkeeping
    assert task["last_run_at"] == "2026-01-01T00:01:00"


def test_task_sync_drops_an_absolute_report_path(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    run = _completed_run(db_path)
    db.create_report(db_path, run["id"], "/etc/passwd")
    task = _kanban_task()

    task_sync.sync_task_from_run(task, run, db_path=db_path)

    assert task.get("report_path") is None


def test_task_sync_drops_another_projects_report_path(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    run = _completed_run(db_path, project="AIOS")
    db.create_report(db_path, run["id"], "reports/BANK/20260101-000000_run.md")
    task = _kanban_task()

    task_sync.sync_task_from_run(task, run, db_path=db_path)

    assert task.get("report_path") is None


def test_task_sync_keeps_a_previously_stored_valid_path_when_a_bad_one_arrives(tmp_path):
    """Dropping means "do not overwrite with the escape", not "wipe the field"
    — but the bad reference must not be what the user ends up looking at."""
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    run = _completed_run(db_path)
    db.create_report(db_path, run["id"], "reports/AIOS/../../../etc/passwd")
    task = _kanban_task()
    task["report_path"] = "reports/AIOS/earlier.md"

    task_sync.sync_task_from_run(task, run, db_path=db_path)

    assert task["report_path"] == "reports/AIOS/earlier.md"


# --------------------------------------------------------------------------
# legacy_import — the untrusted ingress that makes the above reachable
# --------------------------------------------------------------------------


def _legacy_run(**overrides) -> dict:
    base = {
        "id": "legacy-run-1",
        "project": "AIOS",
        "task_id": "legacy-task-1",
        "task_type": "implementation",
        "repository_path": "/tmp/legacy-repo",
        "prompt": "do the legacy thing",
        "status": "completed",
        "started_at": "2026-01-01T10:00:00",
        "completed_at": "2026-01-01T10:05:00",
        "exit_code": 0,
        "report_path": "reports/AIOS/legacy-report.md",
    }
    base.update(overrides)
    return base


def test_legacy_import_skips_a_traversal_report_path(tmp_path):
    db_path = tmp_path / "runtime.db"
    created = legacy_import.import_legacy_runs(
        db_path, legacy_runs=[_legacy_run(report_path="reports/AIOS/../../../etc/passwd")]
    )

    assert len(created) == 1, "the run itself still imports"
    assert db.get_report(db_path, created[0]) is None


def test_legacy_import_skips_an_absolute_report_path(tmp_path):
    db_path = tmp_path / "runtime.db"
    created = legacy_import.import_legacy_runs(db_path, legacy_runs=[_legacy_run(report_path="/etc/passwd")])

    assert db.get_report(db_path, created[0]) is None


def test_legacy_import_skips_another_projects_report_path(tmp_path):
    db_path = tmp_path / "runtime.db"
    created = legacy_import.import_legacy_runs(
        db_path, legacy_runs=[_legacy_run(project="AIOS", report_path="reports/BANK/legacy-report.md")]
    )

    assert db.get_report(db_path, created[0]) is None


def test_legacy_import_still_keeps_a_well_formed_report_path(tmp_path):
    db_path = tmp_path / "runtime.db"
    created = legacy_import.import_legacy_runs(db_path, legacy_runs=[_legacy_run()])

    assert db.get_report(db_path, created[0])["path"] == "reports/AIOS/legacy-report.md"
