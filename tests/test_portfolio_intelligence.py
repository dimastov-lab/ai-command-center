"""Tests for the Portfolio Intelligence read-model.

Cards are built on disk and loaded through the real
`portfolio_models.load_portfolio_tasks`, so these exercise the full
parse -> load -> compute path rather than hand-constructing `PortfolioTask`
records, matching how the Portfolio panel actually feeds the read-model.
"""

from __future__ import annotations

import random
import time
from pathlib import Path

from command_center.portfolio_intelligence import (
    HEALTH_AT_RISK,
    HEALTH_GOOD,
    READINESS_BLOCKED,
    READINESS_GATED,
    READINESS_LAUNCHED,
    READINESS_NOT_READY_LANE,
    READINESS_READY,
    READINESS_UNMAPPED,
    REC_BREAK_CYCLE,
    REC_LAUNCH_NOW,
    REC_MAP_REPOSITORY,
    REC_RESOLVE_BLOCKER,
    REC_REVIEW_CONFLICT,
    compute_portfolio_overview,
)
from command_center.portfolio_models import (
    PortfolioLoadResult,
    PortfolioTask,
    load_portfolio_tasks,
)


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


# --------------------------------------------------------------------------
# Founder Gate remediation: graph scalability, deep-chain safety, determinism.
#
# These use direct `PortfolioTask`/`PortfolioLoadResult` construction rather
# than on-disk cards so a 5,000-node graph does not mean 5,000 file writes —
# the read-model under test consumes a `PortfolioLoadResult`, exactly what the
# loader hands it. Each test below fails on commit ba3c182:
#   - deep chains -> RecursionError (BLOCKER-1);
#   - diamonds    -> exponential wall-time (MAJOR-1);
#   - long chains -> critical path silently capped at 201 (MAJOR-2);
#   - duplicate `requires` -> duplicate edges (MINOR-2);
#   - blocked-lane card -> project reported healthy (MINOR-1).
# --------------------------------------------------------------------------

_compute = compute_portfolio_overview
_PATHS = {"AICC": "/repo", "AIOS": "/repo2"}


def _task(task_id: str, requires=None, *, project: str = "AICC", lane: str = "ready") -> PortfolioTask:
    frontmatter = {
        "task_id": task_id,
        "project": project,
        "title": f"T {task_id}",
        "repository": "~/r",
        "base_branch": "main",
        "status": lane,
        "requires": list(requires or []),
    }
    return PortfolioTask(
        lane=lane, source_path=Path(f"/x/{task_id}.md"), frontmatter=frontmatter, body="", raw_text=""
    )


def _result(tasks: list[PortfolioTask]) -> PortfolioLoadResult:
    return PortfolioLoadResult(portfolio_root=Path("/p"), missing=False, tasks=tasks, card_issues=[])


def _tid(i: int) -> str:
    return f"T{i:05d}"


def test_forward_chain_3000_exact_and_consistent():
    tasks = [_task(_tid(i), [_tid(i - 1)] if i else []) for i in range(3000)]
    ov = _compute(_result(tasks), repository_paths=_PATHS)
    assert len(ov.graph.waves) == 3000
    assert len(ov.graph.critical_path) == 3000  # not silently capped at 201
    assert ov.tasks_by_id()[_tid(2999)].wave == 2999


def test_reverse_ordered_input_matches_forward():
    tasks = [_task(_tid(i), [_tid(i - 1)] if i else []) for i in range(3000)]
    forward = _compute(_result(tasks), repository_paths=_PATHS)
    reverse = _compute(_result(list(reversed(tasks))), repository_paths=_PATHS)
    assert forward.graph.waves == reverse.graph.waves
    assert forward.graph.critical_path == reverse.graph.critical_path


def test_deep_chain_dependency_points_to_larger_id_no_recursion_error():
    # On ba3c182 this raised RecursionError: sorted order is reverse-topological.
    tasks = [_task(_tid(i), [_tid(i + 1)] if i < 4999 else []) for i in range(5000)]
    ov = _compute(_result(tasks), repository_paths=_PATHS)
    assert len(ov.graph.waves) == 5000
    assert len(ov.graph.critical_path) == 5000
    assert ov.tasks_by_id()[_tid(0)].wave == 4999


def test_five_thousand_node_chain_completes():
    tasks = [_task(_tid(i), [_tid(i - 1)] if i else []) for i in range(5000)]
    ov = _compute(_result(tasks), repository_paths=_PATHS)
    assert ov.total_tasks == 5000
    assert len(ov.graph.critical_path) == 5000


def _diamond(layers: int) -> list[PortfolioTask]:
    tasks = [_task("H000")]
    for i in range(layers):
        hub, nxt = f"H{i:03d}", f"H{i + 1:03d}"
        tasks += [_task(f"A{i:03d}", [hub]), _task(f"B{i:03d}", [hub]), _task(nxt, [f"A{i:03d}", f"B{i:03d}"])]
    return tasks


def test_diamond_fork_join_is_not_exponential():
    # 24 diamond layers => ~2^24 simple paths. Exponential on ba3c182 (minutes);
    # linear here. Generous 10s bound catches exponential without CI flakiness.
    tasks = _diamond(24)
    start = time.perf_counter()
    ov = _compute(_result(tasks), repository_paths=_PATHS)
    elapsed = time.perf_counter() - start
    assert elapsed < 10.0, f"took {elapsed:.1f}s — exponential regression"
    assert len(ov.graph.critical_path) == 2 * 24 + 1  # exact longest chain through the hubs


def test_equal_length_paths_deterministic_tie_break():
    # Two length-3 chains: H1->A->H0 and H1->B->H0. Deterministic rule picks the
    # smallest task_id at each descent step.
    tasks = [_task("H0"), _task("A", ["H0"]), _task("B", ["H0"]), _task("H1", ["A", "B"])]
    ov = _compute(_result(tasks), repository_paths=_PATHS)
    assert ov.graph.critical_path == ["H1", "A", "H0"]


def test_shuffle_determinism_100_permutations():
    base = _diamond(6) + [_task("ISO"), _task("XP", ["A000"], project="AIOS")]

    def snapshot(ov):
        return (
            tuple((t.task_id, t.wave, t.readiness) for t in ov.tasks),
            tuple(tuple(w) for w in ov.graph.waves),
            tuple(ov.graph.critical_path),
            tuple(sorted(ov.graph.cyclic_task_ids)),
            tuple((e.from_id, e.to_id, e.cross_project, e.resolved) for e in ov.graph.edges),
            tuple((r.kind, tuple(r.task_ids)) for r in ov.recommendations),
        )

    reference = snapshot(_compute(_result(base), repository_paths=_PATHS))
    for seed in range(100):
        shuffled = base[:]
        random.Random(seed).shuffle(shuffled)
        assert snapshot(_compute(_result(shuffled), repository_paths=_PATHS)) == reference


def test_duplicate_requires_yields_single_edge_and_no_indegree_effect():
    ov = _compute(_result([_task("A"), _task("B", ["A", "A", "A"])]), repository_paths=_PATHS)
    b_to_a = [(e.from_id, e.to_id) for e in ov.graph.edges if e.from_id == "B"]
    assert b_to_a == [("B", "A")]  # exactly one edge despite three declarations
    assert ov.tasks_by_id()["B"].wave == 1  # in-degree/wave unaffected
    assert ov.graph.waves == [["A"], ["B"]]


def test_self_loop_is_cyclic_not_infinite():
    ov = _compute(_result([_task("S", ["S"])]), repository_paths=_PATHS)
    assert ov.graph.cyclic_task_ids == ["S"]
    assert ov.tasks_by_id()["S"].wave is None
    assert ov.graph.critical_path == []  # no path fabricated through the cycle


def test_cycle_downstream_is_cyclic_and_critical_path_uses_acyclic_portion():
    # A<->B cycle, C depends on A (downstream). D->E->F is a clean acyclic chain.
    tasks = [
        _task("A", ["B"]),
        _task("B", ["A"]),
        _task("C", ["A"]),
        _task("D"),
        _task("E", ["D"]),
        _task("F", ["E"]),
    ]
    ov = _compute(_result(tasks), repository_paths=_PATHS)
    assert ov.graph.cyclic_task_ids == ["A", "B", "C"]  # C is downstream of the cycle
    assert ov.graph.critical_path == ["F", "E", "D"]  # exact chain over the acyclic portion
    assert REC_BREAK_CYCLE in {r.kind for r in ov.recommendations}


def test_blocked_lane_card_surfaces_in_project_health():
    # MINOR-1: a card parked in the `blocked` lane with satisfied deps and no
    # gate must not let the project report as healthy.
    tasks = [_task("M", lane="blocked")]
    ov = _compute(_result(tasks), repository_paths=_PATHS)
    project = next(p for p in ov.projects if p.project == "AICC")
    assert project.health != HEALTH_GOOD
    assert project.blocked == 1
    assert any("blocked" in e for e in project.evidence)


def test_blocked_lane_card_does_not_change_launch_readiness():
    # Readiness itself is unchanged — the card is still not launchable because
    # it is not in the `ready` lane, not because of a dependency.
    ov = _compute(_result([_task("M", lane="blocked")]), repository_paths=_PATHS)
    assert ov.tasks_by_id()["M"].readiness == READINESS_NOT_READY_LANE
    assert ov.blocked == []  # top-level dependency-blocked set is unaffected
