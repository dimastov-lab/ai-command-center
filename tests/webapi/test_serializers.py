"""Unit tests for `command_center.webapi.serializers.serialize_home`.

The fixture below deliberately mirrors the REAL shape returned by
`command_center.workspace_home.build_workspace_home_snapshot` (verified by
reading that module directly, not guessed) — top-level `projects` /
`active_runs` / `recent_runs` / `recent_activity` / `artifacts` / `reports` /
`worktrees_by_project`, with per-project entries carrying `task_count` /
`active_run_count` / `repository_state` and run entries carrying
`run_id`/`source`/`project`/`task_type`/`state`/... rather than a `title`/
`progress` pair. See `command_center/webapi/serializers.py` for how each
output DTO field is derived from these real keys.
"""

from __future__ import annotations

from command_center.webapi.serializers import serialize_home


def _snapshot(**overrides):
    base: dict = {
        "projects": [
            {
                "id": "AICC",
                "display_name": "AI Command Center",
                "sensitive": False,
                "repository_path": "/repos/aicc",
                "repository_state": "ok",
                "task_count": 12,
                "active_run_count": 4,
            },
            {
                "id": "BANK",
                "display_name": "Bank",
                "sensitive": True,
                "repository_path": "/repos/bank",
                "repository_state": "ok",
                "task_count": 9,
                "active_run_count": 3,
            },
        ],
        "worktrees_by_project": {
            "AICC": {"state": "ok", "worktrees": []},
            "BANK": {"state": "ok", "worktrees": []},
        },
        "active_runs": [
            {
                "run_id": "r1",
                "source": "v2",
                "project": "AICC",
                "task_type": "implementation",
                "state": "RUNNING",
                "status": None,
                "created_at": "2026-07-28T10:00:00",
                "started_at": "2026-07-28T10:00:00",
                "completed_at": None,
            },
            {
                "run_id": "r2",
                "source": "v2",
                "project": "AICC",
                "task_type": "review",
                "state": "QUEUED",
                "status": None,
                "created_at": "2026-07-28T09:00:00",
                "started_at": None,
                "completed_at": None,
            },
        ],
        "recent_runs": [],
        "recent_activity": [
            {
                "project": "AICC",
                "event_type": "run_completed",
                "ts": "2026-07-28T08:00:00",
                "run_id": "r0",
                "task_id": "t0",
            },
        ],
        "artifacts": [],
        "reports": [
            {
                "run_id": "r1",
                "source": "v2",
                "project": "AICC",
                "verdict": "APPROVED_FOR_COMMIT",
                "severity_counts": {},
                "created_at": 123.0,
            },
            {
                "run_id": "r2",
                "source": "v2",
                "project": "BANK",
                "verdict": "NOT_APPROVED_FOR_COMMIT",
                "severity_counts": {},
                "created_at": 124.0,
            },
        ],
    }
    base.update(overrides)
    return base


def test_serialize_home_shapes_kpis_and_redacts_sensitive():
    snap = _snapshot()
    out = serialize_home(snap)

    # DTO top-level shape Task 4 (frontend) depends on.
    assert set(out.keys()) == {"projects", "kpis", "queue", "health", "activity", "overview", "status"}

    assert out["kpis"]["projects"]["value"] == 2
    # agents KPI aggregates each project's own (unfiltered upstream)
    # active_run_count: 4 (AICC) + 3 (BANK) = 7; "running" sub-count only
    # counts runs actually in RUNNING state (1 of the 2 active_runs fixture rows).
    assert out["kpis"]["agents"] == {"value": 7, "meta_key": "running", "meta_n": 1}

    # sensitive project is present but must carry no raw metrics.
    bank = next(p for p in out["projects"] if p["id"] == "BANK")
    assert bank["redacted"] is True
    assert "health" not in bank
    assert "task_count" not in bank
    assert "active_run_count" not in bank

    # non-sensitive project keeps its data.
    aicc = next(p for p in out["projects"] if p["id"] == "AICC")
    assert "redacted" not in aicc
    assert aicc["healthy"] is True
    assert aicc["health"]["task_count"] == 12

    assert out["queue"][0]["state"] == "RUNNING"


def test_serialize_home_reviews_kpi_counts_non_passing_verdicts_as_pending():
    snap = _snapshot()
    out = serialize_home(snap)

    # 2 reports total, 1 non-passing verdict (NOT_APPROVED_FOR_COMMIT) => pending.
    assert out["kpis"]["reviews"] == {"value": 2, "meta_key": "pending", "meta_n": 1}


def test_serialize_home_handles_empty_snapshot():
    out = serialize_home({})

    assert out["projects"] == []
    assert out["kpis"]["projects"] == {"value": 0, "meta_key": "all_healthy", "meta_n": 0}
    assert out["kpis"]["agents"] == {"value": 0, "meta_key": "running", "meta_n": 0}
    assert out["kpis"]["tasks"] == {"value": 0, "meta_key": "in_progress", "meta_n": 0}
    assert out["kpis"]["reviews"] == {"value": 0, "meta_key": "pending", "meta_n": 0}
    assert out["queue"] == []
    assert out["activity"] == []
    assert out["status"] == []
