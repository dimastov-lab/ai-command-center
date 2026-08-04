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
from command_center.workspace_home import _ACTIVITY_ALLOWED_FIELDS, _RUN_ALLOWED_FIELDS


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
    # agents KPI aggregates each NON-sensitive project's own active_run_count
    # only: AICC's 4, excluding BANK's 3 (BANK is sensitive) — see
    # test_serialize_home_kpi_aggregates_exclude_sensitive_projects_raw_metrics
    # for the dedicated regression covering this. "running" sub-count only
    # counts non-sensitive runs actually in RUNNING state (1 of the 2
    # active_runs fixture rows, both of which happen to belong to AICC here).
    assert out["kpis"]["agents"] == {"value": 4, "meta_key": "running", "meta_n": 1}

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

    # STRICT redaction: the sensitive project (BANK) is absent from status[]
    # entirely — only the non-sensitive AICC row remains.
    assert [s["project"] for s in out["status"]] == ["AICC"]


def test_serialize_home_kpi_aggregates_exclude_sensitive_projects_raw_metrics():
    """Fix round 1 (code review finding): `kpis.agents.value`/`kpis.tasks.value`
    must not include a sensitive project's `active_run_count`/`task_count`,
    even indirectly via a workspace-wide sum. Every non-sensitive project's
    own `health` block is fully exposed in `projects[]`, so if the aggregate
    included LEGAL's contribution a client could recover LEGAL's raw totals
    by subtracting the (visible) non-sensitive project's values from the
    workspace-wide sum — a sensitive-derived raw metric reaching the API.
    """
    snap = {
        "projects": [
            {
                "id": "AICC",
                "display_name": "AI Command Center",
                "sensitive": False,
                "repository_state": "ok",
                "task_count": 5,
                "active_run_count": 2,
            },
            {
                "id": "LEGAL",
                "display_name": "Legal",
                "sensitive": True,
                "repository_state": "ok",
                "task_count": 100,
                "active_run_count": 50,
            },
        ],
        "active_runs": [
            {"run_id": "a1", "project": "AICC", "task_type": "implementation", "state": "RUNNING"},
            {"run_id": "l1", "project": "LEGAL", "task_type": "implementation", "state": "RUNNING"},
            {"run_id": "l2", "project": "LEGAL", "task_type": "review", "state": "QUEUED"},
        ],
        "recent_runs": [],
        "recent_activity": [],
        "artifacts": [],
        "reports": [],
    }

    out = serialize_home(snap)

    # Totals equal only AICC's (non-sensitive) values — LEGAL's 50/100
    # contribute nothing, despite being non-zero and despite LEGAL having
    # its own RUNNING run in `active_runs`.
    assert out["kpis"]["agents"]["value"] == 2
    assert out["kpis"]["agents"]["meta_n"] == 1  # only AICC's RUNNING run counted, not LEGAL's l1
    assert out["kpis"]["tasks"]["value"] == 5

    # LEGAL's own project entry is still fully redacted, as before this fix.
    legal = next(p for p in out["projects"] if p["id"] == "LEGAL")
    assert legal["redacted"] is True
    assert "health" not in legal
    assert "task_count" not in legal
    assert "active_run_count" not in legal

    # STRICT redaction (mixed run set): LEGAL's two runs (l1/l2) are dropped
    # from queue[] entirely; only AICC's non-sensitive run survives.
    assert [q["project"] for q in out["queue"]] == ["AICC"]


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


def test_serialize_home_excludes_sensitive_run_from_queue_status_and_activity():
    """STRICT REDACTION: a sensitive (BANK/LEGAL) project's run/activity/status
    rows are dropped from `queue`, `status` and `activity` entirely — not
    merely reduced to their upstream-allowlisted fields — and
    `overview.recent_activity_count` follows the filtered feed.

    The fixture still mirrors the real, already-sanitized shape a genuine
    BANK/LEGAL run/activity would have by the time it reaches this module
    (built from the actual allowlists, not guessed) to prove that even an
    already-allowlisted sensitive row is dropped whole here.
    """
    sensitive_run = {
        "run_id": "bank-r9",
        "source": "v2",
        "project": "BANK",
        "task_type": "implementation",
        "state": "RUNNING",
        "status": None,
        "created_at": "2026-07-28T11:00:00",
        "started_at": "2026-07-28T11:00:00",
        "completed_at": None,
        "exit_code": None,
        "duration_seconds": None,
        "failure_reason": None,
    }
    # Fixture uses ONLY fields the upstream allowlist ever lets through for a
    # sensitive project's run — no raw title/message/prompt/report-body field
    # is even representable here, exactly as `sanitize_workspace_project_entry`
    # guarantees for a real BANK/LEGAL run.
    assert set(sensitive_run) <= _RUN_ALLOWED_FIELDS

    sensitive_activity = {
        "project": "BANK",
        "event_type": "run_completed",
        "ts": "2026-07-28T11:05:00",
        "run_id": "bank-r9",
        "task_id": "bank-t9",
    }
    assert set(sensitive_activity) <= _ACTIVITY_ALLOWED_FIELDS

    snap = {
        "projects": [
            {
                "id": "BANK",
                "display_name": "Bank",
                "sensitive": True,
                "repository_state": "ok",
                "task_count": 40,
                "active_run_count": 7,
            },
        ],
        "active_runs": [sensitive_run],
        "recent_runs": [],
        "recent_activity": [sensitive_activity],
        "artifacts": [],
        "reports": [],
    }

    out = serialize_home(snap)

    # STRICT: the sole active run belongs to a sensitive project, so `queue`
    # is empty — the BANK run is dropped whole, not field-allowlisted.
    assert out["queue"] == []

    # STRICT: the sensitive project is omitted from `status` too, so its
    # `repository_state` ("ok" here) is never disclosed.
    assert out["status"] == []

    # STRICT: the BANK activity row is dropped whole (even though it is already
    # allowlisted, per the assertion above), and the count follows the feed.
    assert out["activity"] == []
    assert out["overview"]["recent_activity_count"] == 0

    # The project's own rollup entry stays fully redacted, as covered by the
    # existing tests above — asserted here too since this fixture is BANK-only.
    bank = next(p for p in out["projects"] if p["id"] == "BANK")
    assert bank["redacted"] is True
    assert "health" not in bank


def test_serialize_home_activity_feed_excludes_sensitive_project_rows():
    """STRICT REDACTION (mixed feed): activity rows for a sensitive project are
    dropped from `activity`, and `overview.recent_activity_count` counts only
    the surviving non-sensitive rows."""
    snap = _snapshot(
        recent_activity=[
            {"project": "AICC", "event_type": "run_completed", "ts": "t2", "run_id": "r0", "task_id": "t0"},
            {"project": "BANK", "event_type": "run_completed", "ts": "t1", "run_id": "b0", "task_id": "bt0"},
        ]
    )
    out = serialize_home(snap)

    assert [a["project"] for a in out["activity"]] == ["AICC"]
    assert out["overview"]["recent_activity_count"] == 1
