"""Truthful, pure Dashboard projection over canonical read models.

The projection never probes GitHub or runtime and never treats a green check,
health response, or current main as acceptance/deployment evidence. Callers
provide the authoritative task snapshot, API-enriched run rows, and the exact
total returned by ``ExecutionCenterAPI.count_runs``.
"""

from __future__ import annotations

from dataclasses import dataclass

from command_center import read_model

UNKNOWN = "unknown"
UNACCEPTED = "unaccepted"
ACCEPTED_UNDEPLOYED = "accepted_undeployed"
DEPLOYED = "deployed"
STALE_RUNTIME = "stale_runtime"
RUNTIME_MISMATCH = "runtime_mismatch"


@dataclass(frozen=True)
class SourceMetric:
    value: int
    entity: str
    source: str


@dataclass(frozen=True)
class DeliveryTruth:
    run_id: str
    state: str
    label: str
    description: str
    accent: str
    semantic_role: str = "status"


@dataclass(frozen=True)
class DashboardTruth:
    task_metric: SourceMetric
    run_metric: SourceMetric
    run_window_label: str
    deliveries: tuple[DeliveryTruth, ...]


_LABELS = {
    UNKNOWN: ("Доказательства неизвестны", "Нет подтверждённых PR, CI, acceptance или deploy данных.", "slate"),
    UNACCEPTED: ("Кандидат не принят", "PR или CI наблюдались, но accepted SHA не подтверждён.", "amber"),
    ACCEPTED_UNDEPLOYED: ("Принят, deploy неизвестен", "Accepted SHA подтверждён; verified deployed SHA отсутствует.", "blue"),
    DEPLOYED: ("Deploy подтверждён", "Accepted и verified deployed SHA совпадают.", "green"),
    STALE_RUNTIME: ("Runtime устарел", "Живой runtime не подтвердил свежий heartbeat.", "amber"),
    RUNTIME_MISMATCH: ("Runtime SHA не совпадает", "Наблюдаемый runtime SHA отличается от candidate SHA.", "red"),
}


def _runtime_mismatch(provenance: dict) -> bool:
    runtime_events = [
        item
        for item in provenance.get("evidence") or []
        if item.get("adapter") == "runtime_probe"
    ]
    return bool(runtime_events) and runtime_events[-1].get("status") == "runtime_sha_mismatch"


def _state_for(run: dict, stale_run_ids: frozenset[str]) -> str:
    provenance = run.get("provenance") or {}
    if _runtime_mismatch(provenance):
        return RUNTIME_MISMATCH
    if run.get("id") in stale_run_ids:
        return STALE_RUNTIME
    accepted = provenance.get("accepted_sha")
    deployed = provenance.get("deployed_sha")
    if accepted and deployed == accepted:
        return DEPLOYED
    if accepted:
        return ACCEPTED_UNDEPLOYED
    if provenance.get("pr") or provenance.get("ci"):
        return UNACCEPTED
    return UNKNOWN


def _delivery(run: dict, stale_run_ids: frozenset[str]) -> DeliveryTruth:
    state = _state_for(run, stale_run_ids)
    label, description, accent = _LABELS[state]
    return DeliveryTruth(
        run_id=str(run.get("id") or "unknown"),
        state=state,
        label=label,
        description=description,
        accent=accent,
    )


def build_dashboard_truth(
    counts: read_model.TaskSnapshot,
    *,
    runs: list[dict],
    total_run_count: int,
    stale_run_ids: frozenset[str] = frozenset(),
    run_window_limit: int = 200,
) -> DashboardTruth:
    if total_run_count < len(runs):
        raise ValueError("total_run_count cannot be smaller than the loaded run window")
    ordered = sorted(
        runs,
        key=lambda run: (run.get("started_at") or "", run.get("id") or ""),
        reverse=True,
    )
    return DashboardTruth(
        task_metric=SourceMetric(counts.total, "tasks", "canonical TaskSnapshot"),
        run_metric=SourceMetric(total_run_count, "runs", "runtime.db count_runs"),
        run_window_label=(
            f"Последние {len(runs)} из {total_run_count} запусков "
            f"(лимит окна: {run_window_limit})"
        ),
        deliveries=tuple(_delivery(run, stale_run_ids) for run in ordered),
    )
