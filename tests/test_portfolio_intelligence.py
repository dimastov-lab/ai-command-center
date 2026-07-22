"""Tests for the Portfolio Intelligence read-model.

Cards are built on disk and loaded through the real
`portfolio_models.load_portfolio_tasks`, so these exercise the full
parse -> load -> compute path rather than hand-constructing `PortfolioTask`
records, matching how the Portfolio panel actually feeds the read-model.
"""

from __future__ import annotations

from pathlib import Path

from command_center.portfolio_intelligence import (
    HEALTH_AT_RISK,
    HEALTH_GOOD,
    READINESS_BLOCKED,
    READINESS_GATED,
    READINESS_LAUNCHED,
    READINESS_READY,
    READINESS_UNMAPPED,
    REC_BREAK_CYCLE,
    REC_LAUNCH_NOW,
    REC_MAP_REPOSITORY,
    REC_RESOLVE_BLOCKER,
    REC_REVIEW_CONFLICT,
    compute_portfolio_overview,
)
from command_center.portfolio_models import load_portfolio_tasks


def _fmt_list(values: list[str]) -> str:
    if not values:
        return "[]"
    return "[" + ", ".join(f'"{v}"' for v in values) + "]"


def _card(
    task_id: str,
    *,
    project: str = "AICC",
    lane: str = "ready",
    status: str | None = None,
    priority: str = "medium",
    requires: list[str] | None = None,
    blocks: list[str] | None = None,
    conflicts_with: list[str] | None = None,
    gated_by: list[str] | None = None,
    worktree: str | None = None,
    agent: str | None = None,
    parallel_group: str | None = None,
) -> str:
    return f"""---
task_id: "{task_id}"
title: "Title of {task_id}"
project: "{project}"
type: "implementation"
priority: "{priority}"
status: "{status or lane}"
repository: "~/Projects/{project.lower()}"
base_branch: "main"
worktree: {f'"{worktree}"' if worktree else "null"}
agent: {f'"{agent}"' if agent else "null"}
parallel_group: {f'"{parallel_group}"' if parallel_group else "null"}
requires: {_fmt_list(requires or [])}
blocks: {_fmt_list(blocks or [])}
conflicts_with: {_fmt_list(conflicts_with or [])}
gated_by: {_fmt_list(gated_by or [])}
---

# {task_id}
"""


def _write(root: Path, lane: str, project: str, task_id: str, text: str) -> None:
    lane_dir = root / "tasks" / lane / project
    lane_dir.mkdir(parents=True, exist_ok=True)
    (lane_dir / f"{task_id}.md").write_text(text, encoding="utf-8")


def _build(root: Path, cards: list[tuple[str, str, str, str]]) -> None:
    """Each card is (lane, project, task_id, text)."""
    for lane, project, task_id, text in cards:
        _write(root, lane, project, task_id, text)


# --------------------------------------------------------------------------
# Missing / empty
# --------------------------------------------------------------------------


def test_missing_portfolio_yields_missing_overview(tmp_path):
    result = load_portfolio_tasks(tmp_path / "nope")
    overview = compute_portfolio_overview(result)
    assert overview.missing is True
    assert overview.total_tasks == 0
    assert overview.projects == []
    assert overview.recommendations == []


# --------------------------------------------------------------------------
# Readiness derivation
# --------------------------------------------------------------------------


def test_ready_task_with_mapped_repo_is_ready_and_recommended(tmp_path):
    _build(tmp_path, [("ready", "AICC", "AICC-1", _card("AICC-1"))])
    result = load_portfolio_tasks(tmp_path)
    overview = compute_portfolio_overview(
        result, repository_paths={"AICC": "/repos/aicc"}
    )
    row = overview.tasks_by_id()["AICC-1"]
    assert row.readiness == READINESS_READY
    assert row.repository_path == "/repos/aicc"
    assert row.wave == 0
    assert row.blockers == []
    assert row.evidence  # positive evidence present
    assert overview.ready_now == ["AICC-1"]
    kinds = {r.kind for r in overview.recommendations}
    assert REC_LAUNCH_NOW in kinds


def test_unmapped_repo_makes_task_unmapped(tmp_path):
    _build(tmp_path, [("ready", "AICC", "AICC-1", _card("AICC-1"))])
    result = load_portfolio_tasks(tmp_path)
    overview = compute_portfolio_overview(result, repository_paths={})
    row = overview.tasks_by_id()["AICC-1"]
    assert row.readiness == READINESS_UNMAPPED
    assert row.repository_mapped is False
    assert "AICC" in overview.capacity.unmapped_projects
    assert REC_MAP_REPOSITORY in {r.kind for r in overview.recommendations}


def test_gated_task_is_gated(tmp_path):
    _build(tmp_path, [("ready", "AICC", "AICC-1", _card("AICC-1", gated_by=["EXTERNAL_AUDIT"]))])
    result = load_portfolio_tasks(tmp_path)
    overview = compute_portfolio_overview(result, repository_paths={"AICC": "/r"})
    row = overview.tasks_by_id()["AICC-1"]
    assert row.readiness == READINESS_GATED
    assert any("EXTERNAL_AUDIT" in b for b in row.blockers)


def test_launched_task_links_to_run(tmp_path):
    _build(tmp_path, [("ready", "AICC", "AICC-1", _card("AICC-1"))])
    result = load_portfolio_tasks(tmp_path)
    overview = compute_portfolio_overview(
        result,
        repository_paths={"AICC": "/r"},
        registry={"AICC-1": {"run_id": "run-42"}},
    )
    row = overview.tasks_by_id()["AICC-1"]
    assert row.readiness == READINESS_LAUNCHED
    assert row.launched is True
    assert row.run_id == "run-42"
    # A launched task no longer counts as ready demand.
    assert overview.ready_now == []


def test_blocked_by_open_requirement(tmp_path):
    _build(
        tmp_path,
        [
            ("blocked", "AICC", "DEP", _card("DEP", lane="blocked")),
            ("ready", "AICC", "AICC-1", _card("AICC-1", requires=["DEP"])),
        ],
    )
    result = load_portfolio_tasks(tmp_path)
    overview = compute_portfolio_overview(result, repository_paths={"AICC": "/r"})
    row = overview.tasks_by_id()["AICC-1"]
    assert row.readiness == READINESS_BLOCKED
    assert row.unmet_requires == ["DEP"]
    assert row.wave == 1
    # Single missing dependency -> a resolve_blocker recommendation.
    rec = next(r for r in overview.recommendations if r.kind == REC_RESOLVE_BLOCKER)
    assert rec.task_ids == ["AICC-1", "DEP"]


# --------------------------------------------------------------------------
# Dependency graph: waves, critical path, cross-project, cycles, unresolved
# --------------------------------------------------------------------------


def test_waves_and_critical_path_follow_dependency_chain(tmp_path):
    _build(
        tmp_path,
        [
            ("ready", "AICC", "A", _card("A")),
            ("ready", "AICC", "B", _card("B", requires=["A"])),
            ("ready", "AICC", "C", _card("C", requires=["B"])),
        ],
    )
    result = load_portfolio_tasks(tmp_path)
    overview = compute_portfolio_overview(result, repository_paths={"AICC": "/r"})
    graph = overview.graph
    assert graph.waves == [["A"], ["B"], ["C"]]
    # Critical path is the full chain, dependent-first.
    assert graph.critical_path == ["C", "B", "A"]
    assert overview.tasks_by_id()["C"].wave == 2


def test_cross_project_dependency_edge_is_flagged(tmp_path):
    _build(
        tmp_path,
        [
            ("ready", "AIOS", "AIOS-1", _card("AIOS-1", project="AIOS")),
            ("ready", "AICC", "AICC-1", _card("AICC-1", project="AICC", requires=["AIOS-1"])),
        ],
    )
    result = load_portfolio_tasks(tmp_path)
    overview = compute_portfolio_overview(
        result, repository_paths={"AICC": "/a", "AIOS": "/b"}
    )
    edge = next(e for e in overview.graph.edges if e.from_id == "AICC-1")
    assert edge.to_id == "AIOS-1"
    assert edge.cross_project is True
    assert edge.resolved is True


def test_cycle_is_detected_not_infinite(tmp_path):
    _build(
        tmp_path,
        [
            ("ready", "AICC", "A", _card("A", requires=["B"])),
            ("ready", "AICC", "B", _card("B", requires=["A"])),
        ],
    )
    result = load_portfolio_tasks(tmp_path)
    overview = compute_portfolio_overview(result, repository_paths={"AICC": "/r"})
    assert overview.graph.cyclic_task_ids == ["A", "B"]
    assert overview.tasks_by_id()["A"].wave is None
    assert REC_BREAK_CYCLE in {r.kind for r in overview.recommendations}


def test_unresolved_requirement_is_surfaced_and_not_an_edge_constraint(tmp_path):
    # DONE-1 is not in any loaded lane -> treated as already completed.
    _build(tmp_path, [("ready", "AICC", "AICC-1", _card("AICC-1", requires=["DONE-1"]))])
    result = load_portfolio_tasks(tmp_path)
    overview = compute_portfolio_overview(result, repository_paths={"AICC": "/r"})
    assert ("AICC-1", "DONE-1") in overview.graph.unresolved_requires
    row = overview.tasks_by_id()["AICC-1"]
    assert row.unmet_requires == []  # DONE-1 assumed done
    assert row.readiness == READINESS_READY


# --------------------------------------------------------------------------
# Capacity / allocation
# --------------------------------------------------------------------------


def test_worktree_collision_between_two_ready_tasks(tmp_path):
    _build(
        tmp_path,
        [
            ("ready", "AICC", "A", _card("A", worktree="/wt/shared")),
            ("ready", "AICC", "B", _card("B", worktree="/wt/shared")),
        ],
    )
    result = load_portfolio_tasks(tmp_path)
    overview = compute_portfolio_overview(result, repository_paths={"AICC": "/r"})
    assert overview.capacity.worktree_collisions == [("/wt/shared", ["A", "B"])]


def test_conflict_pair_and_recommendation(tmp_path):
    _build(
        tmp_path,
        [
            ("ready", "AICC", "A", _card("A", conflicts_with=["B"])),
            ("ready", "AICC", "B", _card("B")),
        ],
    )
    result = load_portfolio_tasks(tmp_path)
    overview = compute_portfolio_overview(result, repository_paths={"AICC": "/r"})
    assert overview.capacity.conflict_pairs == [("A", "B")]
    assert REC_REVIEW_CONFLICT in {r.kind for r in overview.recommendations}


def test_repository_and_parallel_group_demand(tmp_path):
    _build(
        tmp_path,
        [
            ("ready", "AICC", "A", _card("A", parallel_group="wave-1")),
            ("ready", "AICC", "B", _card("B", parallel_group="wave-1")),
        ],
    )
    result = load_portfolio_tasks(tmp_path)
    overview = compute_portfolio_overview(result, repository_paths={"AICC": "/r"})
    assert overview.capacity.ready_count == 2
    assert overview.capacity.by_repository == [("/r", 2)]
    assert overview.capacity.by_parallel_group == [("wave-1", ["A", "B"])]


# --------------------------------------------------------------------------
# Health derivation
# --------------------------------------------------------------------------


def test_project_health_good_when_nothing_blocked(tmp_path):
    _build(tmp_path, [("ready", "AICC", "A", _card("A"))])
    result = load_portfolio_tasks(tmp_path)
    overview = compute_portfolio_overview(result, repository_paths={"AICC": "/r"})
    project = next(p for p in overview.projects if p.project == "AICC")
    assert project.health == HEALTH_GOOD


def test_project_at_risk_when_majority_blocked(tmp_path):
    # 2 of 3 blocked -> ratio 0.67 > 0.3 -> At Risk.
    _build(
        tmp_path,
        [
            ("blocked", "AICC", "DEP", _card("DEP", lane="blocked")),
            ("ready", "AICC", "OK", _card("OK")),
            ("ready", "AICC", "B1", _card("B1", requires=["DEP"])),
            ("ready", "AICC", "B2", _card("B2", requires=["DEP"])),
        ],
    )
    result = load_portfolio_tasks(tmp_path)
    overview = compute_portfolio_overview(result, repository_paths={"AICC": "/r"})
    project = next(p for p in overview.projects if p.project == "AICC")
    assert project.health == HEALTH_AT_RISK
    assert "AICC" in overview.at_risk_projects
    assert project.evidence  # evidence naming the blocked tasks


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_overview_is_deterministic(tmp_path):
    cards = [
        ("ready", "AICC", "A", _card("A")),
        ("ready", "AIOS", "X", _card("X", project="AIOS", requires=["A"])),
        ("blocked", "AICC", "B", _card("B", lane="blocked", requires=["A"])),
    ]
    _build(tmp_path, cards)
    result = load_portfolio_tasks(tmp_path)
    paths = {"AICC": "/a", "AIOS": "/b"}
    first = compute_portfolio_overview(result, repository_paths=paths)
    second = compute_portfolio_overview(load_portfolio_tasks(tmp_path), repository_paths=paths)
    assert [t.task_id for t in first.tasks] == [t.task_id for t in second.tasks]
    assert first.graph.waves == second.graph.waves
    assert first.graph.critical_path == second.graph.critical_path
    assert [r.kind for r in first.recommendations] == [r.kind for r in second.recommendations]
