"""Portfolio Intelligence: a computed, read-only rollup over the Portfolio
task cards — unified per-project health, the cross-project dependency graph,
readiness waves, the critical path, capacity/allocation demand, and
deterministic, evidence-backed recommendations.

Nothing here is persisted and nothing is mutated. Every value is recomputed
on each call from three plain inputs the caller already has:

- a `PortfolioLoadResult` (from `portfolio_models.load_portfolio_tasks`),
- `repository_paths` (from `portfolio_config.load_repository_paths`) — the
  project-id -> local repository path mapping, so a task can be linked to its
  source repository and its execution readiness judged,
- `registry` (from `portfolio_launch.load_registry`) — the local launch
  registry, so a task can be linked to the execution session (`run_id`) it
  was already launched into.

This mirrors the boundary `command_center.project_intelligence` already
established for the AI Command Center's *own* Kanban tasks: a plain read
model, never a second store, never something the caller must remember to
update. It is deliberately kept separate because the two operate on different
id namespaces and data shapes — `project_intelligence` reads `tasks.json`
dicts (`id`/`depends_on`/`status`), this reads `PortfolioTask` cards
(`task_id`/`requires`/`lane`) from the separate Portfolio repository. This
module imports neither Streamlit, `portfolio_launch`, nor `portfolio_config`:
its inputs are plain data, so the domain logic stays independent of the UI
and of the launch/git stack.

Determinism and evidence: every derived status (a project's health, a task's
readiness, a recommendation) is produced by an explicit, documented rule and
carries a human-readable `evidence`/`blockers`/`reasons` list naming the
cards or fields it was derived from — never an opaque score. Ordering is
always stabilized (sorted by task_id / a fixed key) so the same portfolio
always yields the same overview, independent of dict/set iteration order.

Cycle safety: `requires` is card-authored, external, editable data and may
contain a dependency cycle. Wave and critical-path computation are both
guarded so a cycle degrades to "these ids are in a cycle" (reported
explicitly) plus a truncated path, never an infinite walk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from command_center.portfolio_models import (
    LAUNCHABLE_LANE,
    CardIssue,
    PortfolioLoadResult,
    PortfolioTask,
    unmet_requirements,
)

HEALTH_GOOD = "Good"
HEALTH_ATTENTION = "Attention"
HEALTH_AT_RISK = "At Risk"

# A project is "At Risk" once more than this share of its (all non-terminal —
# only ready/review/blocked/backlog cards are ever loaded) tasks are blocked.
# Same documented threshold as `project_intelligence.AT_RISK_BLOCKED_RATIO`,
# kept as its own constant so the two id spaces can be tuned independently.
AT_RISK_BLOCKED_RATIO = 0.3

# Depth bound for the critical-path walk — a backstop against a pathological
# `requires` chain, mirroring `project_intelligence._MAX_CRITICAL_PATH_DEPTH`.
_MAX_CRITICAL_PATH_DEPTH = 200

# Readiness states, in precedence order (the first that applies wins). A task
# is offered for launch only in state READY.
READINESS_LAUNCHED = "launched"        # already recorded in the launch registry
READINESS_GATED = "gated"              # has an unmet external `gated_by` gate
READINESS_BLOCKED = "blocked"          # `requires` still open in a loaded lane
READINESS_UNMAPPED = "unmapped"        # its project has no local repository path
READINESS_NOT_READY_LANE = "not_ready_lane"  # not in the launchable `ready` lane
READINESS_READY = "ready"              # launchable now


@dataclass(frozen=True)
class TaskReadiness:
    task_id: str
    project: str | None
    title: str | None
    lane: str
    status: str | None
    priority: str | None
    type: str | None
    repository_path: str | None
    repository_mapped: bool
    source_path: str
    run_id: str | None
    launched: bool
    wave: int | None
    requires: list[str]
    unmet_requires: list[str]
    blocks: list[str]
    conflicts_with: list[str]
    gated_by: list[str]
    agent: str | None
    worktree: str | None
    parallel_group: str | None
    readiness: str
    blockers: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProjectHealth:
    project: str
    repository_path: str | None
    repository_mapped: bool
    total: int
    by_lane: dict[str, int]
    ready: int
    blocked: int
    gated: int
    launched: int
    health: str
    health_reason: str
    evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DependencyEdge:
    # `from_id` requires `to_id` (edge points from dependent to dependency).
    from_id: str
    to_id: str
    cross_project: bool
    resolved: bool


@dataclass(frozen=True)
class DependencyGraph:
    edges: list[DependencyEdge]
    waves: list[list[str]]
    cyclic_task_ids: list[str]
    critical_path: list[str]
    critical_path_titles: list[str]
    # (task_id, missing_dep_id) for a `requires` entry not present in any
    # loaded lane — treated as already-completed per `unmet_requirements`'s
    # documented conservative assumption, surfaced here so it is visible
    # rather than silently swallowed.
    unresolved_requires: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class CapacityDemand:
    # Only READY tasks (launchable now) contribute demand — a blocked/gated
    # task makes no immediate claim on a repository, worktree, or agent.
    ready_count: int
    by_repository: list[tuple[str, int]]
    by_parallel_group: list[tuple[str, list[str]]]
    agents_requested: list[tuple[str, int]]
    worktree_collisions: list[tuple[str, list[str]]]
    conflict_pairs: list[tuple[str, str]]
    unmapped_projects: list[str]


@dataclass(frozen=True)
class Recommendation:
    kind: str
    summary: str
    task_ids: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


REC_LAUNCH_NOW = "launch_now"
REC_MAP_REPOSITORY = "map_repository"
REC_RESOLVE_BLOCKER = "resolve_blocker"
REC_REVIEW_CONFLICT = "review_conflict"
REC_BREAK_CYCLE = "break_cycle"


@dataclass(frozen=True)
class PortfolioOverview:
    portfolio_root: Path
    missing: bool
    card_issues: list[CardIssue]
    total_tasks: int
    projects: list[ProjectHealth]
    tasks: list[TaskReadiness]
    graph: DependencyGraph
    capacity: CapacityDemand
    recommendations: list[Recommendation]
    ready_now: list[str]
    blocked: list[str]
    at_risk_projects: list[str]

    def tasks_by_id(self) -> dict[str, TaskReadiness]:
        return {t.task_id: t for t in self.tasks}


# --------------------------------------------------------------------------
# Dependency-graph primitives (cycle-safe by construction)
# --------------------------------------------------------------------------


def _compute_waves(
    task_ids: list[str], deps: dict[str, list[str]]
) -> tuple[dict[str, int], set[str]]:
    """Topological readiness level of each task: wave 0 is a task whose
    `requires` are all already satisfied (no dependency present in the loaded
    set); wave N is `1 + max(wave(dep))`. Tasks caught in a `requires` cycle
    get no wave and are returned in the cyclic set instead of looping."""
    wave: dict[str, int] = {}
    on_stack: set[str] = set()
    cyclic: set[str] = set()

    def level(task_id: str) -> int | None:
        if task_id in wave:
            return wave[task_id]
        if task_id in cyclic:
            return None
        if task_id in on_stack:
            cyclic.add(task_id)
            return None
        on_stack.add(task_id)
        best = -1
        broken = False
        for dep_id in deps.get(task_id, []):
            dep_level = level(dep_id)
            if dep_level is None:
                broken = True
            else:
                best = max(best, dep_level)
        on_stack.discard(task_id)
        if broken:
            cyclic.add(task_id)
            return None
        wave[task_id] = best + 1
        return wave[task_id]

    for task_id in task_ids:
        level(task_id)
    return wave, cyclic


def _longest_chain(task_ids: list[str], deps: dict[str, list[str]]) -> list[str]:
    """Longest `requires` chain across the whole graph (the critical path),
    returned dependent-first (task -> its deepest dependency). Depth-bounded
    and revisit-guarded per branch so a cycle degrades to a truncated path
    instead of an infinite walk — same discipline as
    `project_intelligence._longest_blocked_chain`."""

    def walk(task_id: str, visited: list[str]) -> list[str]:
        if len(visited) > _MAX_CRITICAL_PATH_DEPTH:
            return visited
        longest = visited
        for dep_id in deps.get(task_id, []):
            if dep_id in visited:
                continue
            candidate = walk(dep_id, [*visited, dep_id])
            if len(candidate) > len(longest):
                longest = candidate
        return longest

    best: list[str] = []
    for task_id in task_ids:
        chain = walk(task_id, [task_id])
        if len(chain) > len(best):
            best = chain
    return best


# --------------------------------------------------------------------------
# Overview computation
# --------------------------------------------------------------------------


def _derive_readiness(
    task: PortfolioTask,
    *,
    unmet: list[str],
    repository_mapped: bool,
    run_id: str | None,
) -> tuple[str, list[str], list[str]]:
    """Deterministic readiness state + the blockers that produced it +
    positive evidence. Precedence: launched > gated > blocked > unmapped >
    wrong-lane > ready. The full blocker list is always returned (not just the
    winning one) so the UI can show every reason a task is not launchable."""
    blockers: list[str] = []
    evidence: list[str] = []

    if run_id:
        evidence.append(f"уже запущено в сессию `{run_id}`")
    if task.gated_by:
        blockers.append(f"внешний гейт: {', '.join(task.gated_by)}")
    if unmet:
        blockers.append(f"ожидает завершения: {', '.join(unmet)}")
    if not repository_mapped:
        blockers.append(f"репозиторий проекта `{task.project}` не сопоставлен с локальным путём")
    if task.lane != LAUNCHABLE_LANE:
        blockers.append(f"находится в дорожке `{task.lane}`, а не `{LAUNCHABLE_LANE}`")

    if run_id:
        readiness = READINESS_LAUNCHED
    elif task.gated_by:
        readiness = READINESS_GATED
    elif unmet:
        readiness = READINESS_BLOCKED
    elif not repository_mapped:
        readiness = READINESS_UNMAPPED
    elif task.lane != LAUNCHABLE_LANE:
        readiness = READINESS_NOT_READY_LANE
    else:
        readiness = READINESS_READY
        evidence.append("все зависимости выполнены, репозиторий сопоставлен, дорожка `ready`")

    return readiness, blockers, evidence


def _build_task_readiness(
    result: PortfolioLoadResult,
    *,
    repository_paths: dict[str, str | None],
    registry: dict[str, dict],
    waves: dict[str, int],
) -> list[TaskReadiness]:
    tasks_by_id = result.tasks_by_id()
    rows: list[TaskReadiness] = []
    for task in result.tasks:
        task_id = task.task_id
        if task_id is None:
            continue
        unmet = unmet_requirements(task, tasks_by_id)
        repo_path = repository_paths.get(task.project) if task.project else None
        repository_mapped = bool(repo_path)
        entry = registry.get(task_id) or {}
        run_id = entry.get("run_id")
        readiness, blockers, evidence = _derive_readiness(
            task, unmet=unmet, repository_mapped=repository_mapped, run_id=run_id
        )
        rows.append(
            TaskReadiness(
                task_id=task_id,
                project=task.project,
                title=task.title,
                lane=task.lane,
                status=task.status,
                priority=task.priority,
                type=task.type,
                repository_path=repo_path,
                repository_mapped=repository_mapped,
                source_path=str(task.source_path),
                run_id=run_id,
                launched=bool(run_id),
                wave=waves.get(task_id),
                requires=task.requires,
                unmet_requires=unmet,
                blocks=task.blocks,
                conflicts_with=task.conflicts_with,
                gated_by=task.gated_by,
                agent=task.agent,
                worktree=task.worktree,
                parallel_group=task.parallel_group,
                readiness=readiness,
                blockers=blockers,
                evidence=evidence,
            )
        )
    rows.sort(key=lambda r: r.task_id)
    return rows


def _build_project_health(rows: list[TaskReadiness]) -> list[ProjectHealth]:
    by_project: dict[str, list[TaskReadiness]] = {}
    for row in rows:
        by_project.setdefault(row.project or "(без проекта)", []).append(row)

    projects: list[ProjectHealth] = []
    for project, project_rows in sorted(by_project.items()):
        by_lane: dict[str, int] = {}
        for row in project_rows:
            by_lane[row.lane] = by_lane.get(row.lane, 0) + 1

        ready = sum(1 for r in project_rows if r.readiness == READINESS_READY)
        launched = sum(1 for r in project_rows if r.launched)
        gated = sum(1 for r in project_rows if r.readiness == READINESS_GATED)
        blocked_rows = [r for r in project_rows if r.readiness == READINESS_BLOCKED]
        blocked = len(blocked_rows)
        total = len(project_rows)

        repo_path = next((r.repository_path for r in project_rows if r.repository_path), None)
        repository_mapped = any(r.repository_mapped for r in project_rows)

        blocked_ratio = blocked / total if total else 0.0
        evidence: list[str] = []
        if blocked:
            evidence.append(
                f"{blocked} из {total} задач заблокированы зависимостями "
                f"({', '.join(r.task_id for r in blocked_rows)})"
            )
        if gated:
            evidence.append(f"{gated} задач под внешним гейтом")
        if not repository_mapped:
            evidence.append("репозиторий проекта не сопоставлен с локальным путём")
        if ready:
            evidence.append(f"{ready} задач готовы к запуску")

        if blocked == 0 and gated == 0:
            health = HEALTH_GOOD
            health_reason = "нет заблокированных или гейтованных задач"
        elif blocked_ratio > AT_RISK_BLOCKED_RATIO:
            health = HEALTH_AT_RISK
            health_reason = f"{blocked} из {total} задач заблокированы"
        else:
            health = HEALTH_ATTENTION
            reasons = []
            if blocked:
                reasons.append(f"{blocked} заблокированных")
            if gated:
                reasons.append(f"{gated} гейтованных")
            health_reason = ", ".join(reasons)

        projects.append(
            ProjectHealth(
                project=project,
                repository_path=repo_path,
                repository_mapped=repository_mapped,
                total=total,
                by_lane=by_lane,
                ready=ready,
                blocked=blocked,
                gated=gated,
                launched=launched,
                health=health,
                health_reason=health_reason,
                evidence=evidence,
            )
        )
    return projects


def _build_graph(result: PortfolioLoadResult) -> DependencyGraph:
    tasks_by_id = result.tasks_by_id()
    task_ids = sorted(tasks_by_id)
    # Only edges to a dependency present in a loaded lane constrain ordering;
    # a `requires` id absent from every loaded lane is treated as already
    # done (see `unmet_requirements`) and recorded as unresolved for
    # visibility rather than forming a graph edge.
    deps: dict[str, list[str]] = {}
    edges: list[DependencyEdge] = []
    unresolved: list[tuple[str, str]] = []
    for task_id in task_ids:
        task = tasks_by_id[task_id]
        present: list[str] = []
        for dep_id in task.requires:
            dep_task = tasks_by_id.get(dep_id)
            resolved = dep_task is not None
            cross_project = bool(dep_task and dep_task.project != task.project)
            edges.append(
                DependencyEdge(
                    from_id=task_id, to_id=dep_id, cross_project=cross_project, resolved=resolved
                )
            )
            if resolved:
                present.append(dep_id)
            else:
                unresolved.append((task_id, dep_id))
        deps[task_id] = present

    wave_map, cyclic = _compute_waves(task_ids, deps)
    max_wave = max(wave_map.values(), default=-1)
    waves: list[list[str]] = [[] for _ in range(max_wave + 1)]
    for task_id in task_ids:
        level = wave_map.get(task_id)
        if level is not None:
            waves[level].append(task_id)

    critical_path = _longest_chain(task_ids, deps)
    critical_path_titles = [
        (tasks_by_id[t].title or t) if t in tasks_by_id else t for t in critical_path
    ]

    return DependencyGraph(
        edges=edges,
        waves=waves,
        cyclic_task_ids=sorted(cyclic),
        critical_path=critical_path,
        critical_path_titles=critical_path_titles,
        unresolved_requires=sorted(unresolved),
    )


def _build_capacity(rows: list[TaskReadiness]) -> CapacityDemand:
    ready_rows = [r for r in rows if r.readiness == READINESS_READY]

    by_repository: dict[str, int] = {}
    agents: dict[str, int] = {}
    groups: dict[str, list[str]] = {}
    worktrees: dict[str, list[str]] = {}
    for row in ready_rows:
        if row.repository_path:
            by_repository[row.repository_path] = by_repository.get(row.repository_path, 0) + 1
        agent = row.agent or "(default)"
        agents[agent] = agents.get(agent, 0) + 1
        if row.parallel_group:
            groups.setdefault(row.parallel_group, []).append(row.task_id)
        if row.worktree:
            worktrees.setdefault(row.worktree, []).append(row.task_id)

    # Two ready tasks resolving to the same explicit worktree path cannot both
    # launch into it — a concrete capacity collision (a read-only preview of
    # what `portfolio_launch._detect_batch_conflicts` would reject at launch).
    worktree_collisions = sorted(
        (path, sorted(ids)) for path, ids in worktrees.items() if len(ids) > 1
    )

    # `conflicts_with` pairs where both sides are still open somewhere in the
    # loaded lanes — a declared mutual exclusion, surfaced once per pair.
    open_ids = {r.task_id for r in rows}
    conflict_pairs: set[tuple[str, str]] = set()
    for row in rows:
        for other in row.conflicts_with:
            if other in open_ids:
                lo, hi = sorted((row.task_id, other))
                conflict_pairs.add((lo, hi))

    unmapped = sorted(
        {r.project for r in rows if r.project and not r.repository_mapped}
    )

    return CapacityDemand(
        ready_count=len(ready_rows),
        by_repository=sorted(by_repository.items()),
        by_parallel_group=sorted((g, sorted(ids)) for g, ids in groups.items()),
        agents_requested=sorted(agents.items()),
        worktree_collisions=worktree_collisions,
        conflict_pairs=sorted(conflict_pairs),
        unmapped_projects=unmapped,
    )


def _build_recommendations(
    rows: list[TaskReadiness],
    graph: DependencyGraph,
    capacity: CapacityDemand,
) -> list[Recommendation]:
    """Deterministic, evidence-backed portfolio actions — never a guess.
    Ordered most-actionable first: launch what is ready, then unblock what is
    close, then resolve structural problems (unmapped repos, conflicts,
    cycles)."""
    recs: list[Recommendation] = []

    ready_ids = sorted(r.task_id for r in rows if r.readiness == READINESS_READY)
    if ready_ids:
        recs.append(
            Recommendation(
                kind=REC_LAUNCH_NOW,
                summary=f"{len(ready_ids)} задач готовы к запуску сейчас (волна с выполненными зависимостями).",
                task_ids=ready_ids,
                evidence=[f"`{tid}`: все зависимости выполнены, репозиторий сопоставлен" for tid in ready_ids],
            )
        )

    if capacity.unmapped_projects:
        recs.append(
            Recommendation(
                kind=REC_MAP_REPOSITORY,
                summary=(
                    f"Сопоставьте репозитории для {len(capacity.unmapped_projects)} проектов, "
                    "чтобы их задачи стали запускаемыми."
                ),
                task_ids=[],
                evidence=[f"проект `{p}` без локального пути" for p in capacity.unmapped_projects],
            )
        )

    # A blocked task whose only thing keeping it blocked is a single unmet
    # dependency is the highest-leverage unblock — finishing that one dep
    # releases it. Surfaced deterministically, sorted by task_id.
    for row in rows:
        if row.readiness == READINESS_BLOCKED and len(row.unmet_requires) == 1:
            dep = row.unmet_requires[0]
            recs.append(
                Recommendation(
                    kind=REC_RESOLVE_BLOCKER,
                    summary=f"Задача `{row.task_id}` разблокируется завершением одной зависимости `{dep}`.",
                    task_ids=[row.task_id, dep],
                    evidence=[f"`{row.task_id}` requires `{dep}` (единственная незакрытая зависимость)"],
                )
            )

    for a, b in capacity.conflict_pairs:
        recs.append(
            Recommendation(
                kind=REC_REVIEW_CONFLICT,
                summary=f"Задачи `{a}` и `{b}` объявлены взаимоисключающими — не запускайте параллельно.",
                task_ids=[a, b],
                evidence=[f"`{a}` conflicts_with `{b}`"],
            )
        )

    if graph.cyclic_task_ids:
        recs.append(
            Recommendation(
                kind=REC_BREAK_CYCLE,
                summary=f"Обнаружен цикл зависимостей среди {len(graph.cyclic_task_ids)} задач — его нужно разорвать.",
                task_ids=graph.cyclic_task_ids,
                evidence=[f"`{tid}` участвует в цикле requires" for tid in graph.cyclic_task_ids],
            )
        )

    return recs


def compute_portfolio_overview(
    result: PortfolioLoadResult,
    *,
    repository_paths: dict[str, str | None] | None = None,
    registry: dict[str, dict] | None = None,
) -> PortfolioOverview:
    """Build the full portfolio read-model from an already-loaded
    `PortfolioLoadResult`. `repository_paths`/`registry` default to empty
    (every project unmapped, nothing launched) so a caller that only has the
    cards can still get the dependency/wave/critical-path view. Never raises
    on portfolio content: a missing checkout or malformed cards are carried
    through from `result` (`missing`, `card_issues`) rather than aborting."""
    repository_paths = repository_paths or {}
    registry = registry or {}

    if result.missing:
        empty_graph = DependencyGraph(
            edges=[], waves=[], cyclic_task_ids=[], critical_path=[], critical_path_titles=[]
        )
        empty_capacity = CapacityDemand(
            ready_count=0,
            by_repository=[],
            by_parallel_group=[],
            agents_requested=[],
            worktree_collisions=[],
            conflict_pairs=[],
            unmapped_projects=[],
        )
        return PortfolioOverview(
            portfolio_root=result.portfolio_root,
            missing=True,
            card_issues=result.card_issues,
            total_tasks=0,
            projects=[],
            tasks=[],
            graph=empty_graph,
            capacity=empty_capacity,
            recommendations=[],
            ready_now=[],
            blocked=[],
            at_risk_projects=[],
        )

    graph = _build_graph(result)
    wave_map = {task_id: idx for idx, wave in enumerate(graph.waves) for task_id in wave}
    rows = _build_task_readiness(
        result, repository_paths=repository_paths, registry=registry, waves=wave_map
    )
    projects = _build_project_health(rows)
    capacity = _build_capacity(rows)
    recommendations = _build_recommendations(rows, graph, capacity)

    ready_now = sorted(r.task_id for r in rows if r.readiness == READINESS_READY)
    blocked = sorted(r.task_id for r in rows if r.readiness == READINESS_BLOCKED)
    at_risk_projects = [p.project for p in projects if p.health == HEALTH_AT_RISK]

    return PortfolioOverview(
        portfolio_root=result.portfolio_root,
        missing=False,
        card_issues=result.card_issues,
        total_tasks=len(rows),
        projects=projects,
        tasks=rows,
        graph=graph,
        capacity=capacity,
        recommendations=recommendations,
        ready_now=ready_now,
        blocked=blocked,
        at_risk_projects=at_risk_projects,
    )


__all__ = [
    "HEALTH_GOOD",
    "HEALTH_ATTENTION",
    "HEALTH_AT_RISK",
    "READINESS_LAUNCHED",
    "READINESS_GATED",
    "READINESS_BLOCKED",
    "READINESS_UNMAPPED",
    "READINESS_NOT_READY_LANE",
    "READINESS_READY",
    "REC_LAUNCH_NOW",
    "REC_MAP_REPOSITORY",
    "REC_RESOLVE_BLOCKER",
    "REC_REVIEW_CONFLICT",
    "REC_BREAK_CYCLE",
    "TaskReadiness",
    "ProjectHealth",
    "DependencyEdge",
    "DependencyGraph",
    "CapacityDemand",
    "Recommendation",
    "PortfolioOverview",
    "compute_portfolio_overview",
]
