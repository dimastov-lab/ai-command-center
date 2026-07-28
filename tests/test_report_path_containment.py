"""Report writes stay inside `reports/`, whatever a run's `project` says.

`report_path_for` (both the v1.2 `agent_runner` one and the v2
`runtime.reports` one) joins `run["project"]` onto `REPORTS_ROOT` as a path
component. That value is not always authored by this app: a Portfolio card's
`project` frontmatter field is externally-authored text that travels card ->
`portfolio_models.parse_card` -> `portfolio_launch._synthetic_task` ->
`execution_queue.launch_ready` -> `db.create_run(project=...)` -> the run
record both report writers consume, and nothing along that chain constrains it
the way `validate_task_id` constrains `task_id` (Portfolio project ids are
deliberately *not* checked against `models.PROJECT_IDS` — see
`portfolio_config`'s module docstring: `INFRA` is a legitimate id with no
Kanban counterpart, so an allowlist there would reject valid cards).

Containment — `storage.assert_within_root`, the same helper
`portfolio_launch` uses for worktree/lock paths — is therefore the gate on the
write side, mirroring `agent_runner.resolve_report_path` on the read side
(Founder Functional Audit MAJOR-9).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from command_center import agent_runner, report_parser, storage
from command_center.portfolio_models import parse_card
from command_center.runtime import db as runtime_db
from command_center.runtime import reports as runtime_reports

CARD_TEMPLATE = """---
schema_version: "1.0"
task_id: "{task_id}"
title: "Test task {task_id}"
project: "{project}"
type: "implementation"
capability: "none"
priority: "medium"
status: "ready"
repository: "~/Projects/ai-command-center"
base_branch: "main"
branch: null
worktree: null
agent: null
autonomy: "confirmed"
parallel_group: null
requires: []
blocks: []
conflicts_with: []
deliverables: ["a thing gets done"]
validation: ["true"]
stop_conditions: ["Stop once done."]
evidence: []
confidence: null
gated_by: []
---

# Test task {task_id}

## Objective

Do the test thing.
"""

# A card `project` that would resolve outside `reports/` if it were joined on
# unchecked — one level above the repo root that holds `reports/`, i.e. still
# inside `tmp_path`, so an escaped write is observable. The card file itself is
# written into a normal directory; only the *field* is hostile.
TRAVERSING_PROJECT = "../../pwned"


def _card_with_project(tmp_path: Path, project: str):
    """A real, parsed Portfolio card whose `project` is `project`."""
    lane_dir = tmp_path / "portfolio" / "tasks" / "ready"
    lane_dir.mkdir(parents=True, exist_ok=True)
    path = lane_dir / "AICC-TEST-001.md"
    path.write_text(CARD_TEMPLATE.format(task_id="AICC-TEST-001", project=project), encoding="utf-8")
    return parse_card(path, lane="ready")


def _files_outside_reports(root: Path, reports_root: Path) -> set[Path]:
    """Every file under `root` that is not inside `reports_root` — snapshotted
    before and after a report write so an escaped file of *any* name, in any
    directory, shows up as a difference."""
    contained = reports_root.resolve()
    return {p for p in root.rglob("*") if p.is_file() and contained not in p.resolve().parents}


@pytest.fixture
def reports_root(tmp_path, monkeypatch):
    """Point both report writers at an isolated `reports/` inside a stand-in
    repo root, so anything escaping it lands somewhere `tmp_path` can see."""
    root = tmp_path / "repo" / "reports"
    root.mkdir(parents=True)
    monkeypatch.setattr(agent_runner, "REPORTS_ROOT", root)
    monkeypatch.setattr(runtime_reports, "REPORTS_ROOT", root)
    return root


# --------------------------------------------------------------------------
# The end-to-end claim (audit MAJOR-9 Definition of Done): a card whose
# `project` contains `../` cannot cause a file to be written outside `reports/`.
# --------------------------------------------------------------------------


def test_portfolio_card_project_with_traversal_cannot_write_a_v2_report_outside_reports(
    tmp_path, reports_root
):
    task = _card_with_project(tmp_path, TRAVERSING_PROJECT)
    # The parser deliberately does not sanitize `project` — containment, not
    # normalization, is what stops this.
    assert task.project == TRAVERSING_PROJECT

    db_path = tmp_path / "runtime.db"
    runtime_db.migrate(db_path)
    db_task = runtime_db.create_task(
        db_path, project=task.project, title=task.title, task_type="implementation"
    )
    session = runtime_db.create_session(
        db_path, task_id=db_task["id"], project=task.project, repository_path=str(tmp_path)
    )
    # `db.create_run` stores the card's `project` verbatim, exactly as the
    # portfolio launch stack hands it over.
    run = runtime_db.create_run(
        db_path,
        session_id=session["id"],
        task_id=db_task["id"],
        project=task.project,
        task_type="implementation",
        repository_path=str(tmp_path),
        prompt="p",
        is_resume=False,
    )
    assert run["project"] == TRAVERSING_PROJECT

    before = _files_outside_reports(tmp_path, reports_root)
    with pytest.raises(storage.PathContainmentError):
        runtime_reports.save_report(run, [])

    assert _files_outside_reports(tmp_path, reports_root) == before


def test_portfolio_card_project_with_traversal_cannot_write_a_v1_report_outside_reports(
    tmp_path, reports_root
):
    task = _card_with_project(tmp_path, TRAVERSING_PROJECT)
    run = {
        "id": "r1",
        "project": task.project,
        "task_id": task.task_id,
        "agent": "claude_code",
        "task_type": "implementation",
        "repository_path": str(tmp_path),
        "prompt": "p",
        "status": "completed",
        "exit_code": 0,
        "started_at": "2026-01-01T00:00:00",
        "completed_at": "2026-01-01T00:05:00",
        "duration_seconds": 300.0,
        "stdout": "",
        "stderr": "",
        "pre_run": {},
        "post_run": {},
        "created_at": "2026-01-01T00:00:00",
    }

    before = _files_outside_reports(tmp_path, reports_root)
    with pytest.raises(storage.PathContainmentError):
        agent_runner.save_report(run, report_parser.empty_parsed_result())

    assert _files_outside_reports(tmp_path, reports_root) == before


# --------------------------------------------------------------------------
# The containment gate itself, per writer
# --------------------------------------------------------------------------


@pytest.fixture(params=["v1", "v2"])
def report_path_for(request):
    """Both `report_path_for` implementations, driven through one set of cases
    so neither writer can regress independently of the other."""
    if request.param == "v1":
        return lambda project: agent_runner.report_path_for(
            {"id": "r1", "project": project, "task_id": "t1", "agent": "claude_code",
             "started_at": "2026-03-04T10:20:30"}
        )
    return lambda project: runtime_reports.report_path_for(
        {"id": "r1", "project": project, "started_at": "2026-03-04T10:20:30"}
    )


@pytest.mark.parametrize(
    "hostile_project",
    ["../../../pwned", "..", "AICC/../../..", "/tmp/pwned", "AICC/../../etc"],
)
def test_report_path_for_rejects_a_project_that_escapes_reports_root(
    reports_root, report_path_for, hostile_project
):
    with pytest.raises(storage.PathContainmentError):
        report_path_for(hostile_project)


def test_report_path_for_rejects_a_project_directory_symlinked_out_of_the_tree(
    tmp_path, reports_root, report_path_for
):
    """The syntactically-innocent case: `project` is a plain component, but
    `reports/<project>/` is a symlink pointing outside the tree — which is why
    the check resolves both sides instead of just scanning for `..`."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (reports_root / "AICC").symlink_to(outside, target_is_directory=True)

    with pytest.raises(storage.PathContainmentError):
        report_path_for("AICC")


def test_report_path_for_accepts_a_portfolio_project_id_absent_from_the_kanban_registry(
    reports_root, report_path_for
):
    """`INFRA` is a real Portfolio project id with no `models.PROJECT_IDS`
    entry (see `portfolio_config`). Containment must not have quietly become a
    Kanban allowlist."""
    path = report_path_for("INFRA")
    assert path.parent == reports_root / "INFRA"


def test_report_path_for_accepts_an_ordinary_project(reports_root, report_path_for):
    path = report_path_for("AICC")
    assert path.parent == reports_root / "AICC"
    assert "20260304-102030" in path.name
