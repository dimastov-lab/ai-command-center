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


def test_serialize_home_sensitive_run_queue_and_activity_expose_only_allowlisted_fields():
    """Pins the redaction intent documented in `serializers.py`: `queue`/`activity`
    carry no per-serializer filtering of their own for run/activity entries — they
    rely entirely on `workspace_home.sanitize_workspace_project_entry` having already
    reduced any *sensitive* project's run/activity dicts down to
    `_RUN_ALLOWED_FIELDS`/`_ACTIVITY_ALLOWED_FIELDS` before they ever reach the
    snapshot's `active_runs`/`recent_activity`. This fixture mirrors that real,
    already-sanitized shape (built from the actual allowlists, not guessed) — no
    `title`, no `message`, no prompt/report body — the way a genuine BANK/LEGAL run
    would look by the time it reaches this module.

    This test passes today; it exists to catch a regression where `queue`/`activity`
    start deriving fields from something other than the fixed, safe field set below
    (e.g. a future `r.get("message")`/`r.get("prompt")` fallback added to
    `_serialize_queue`), which would defeat the upstream allowlist for this surface.
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

    # queue[] entry: only the fixed, safe DTO fields — the fallback chain
    # (task_type, since no "title" key exists) resolves to a category label,
    # never anything resembling raw prompt/report content.
    assert len(out["queue"]) == 1
    queue_entry = out["queue"][0]
    assert set(queue_entry.keys()) == {"title", "project", "progress", "state"}
    assert queue_entry["title"] == "implementation"
    assert queue_entry["project"] == "BANK"
    assert queue_entry["state"] == "RUNNING"
    for forbidden in ("message", "prompt", "report", "body", "text", "summary"):
        assert forbidden not in queue_entry

    # activity[] is a direct passthrough of `recent_activity` in this module
    # (see serializers.py's `_overview`/`serialize_home` docs) — so it must
    # carry only what the upstream allowlist already produced.
    assert len(out["activity"]) == 1
    activity_entry = out["activity"][0]
    assert set(activity_entry.keys()) <= _ACTIVITY_ALLOWED_FIELDS
    for forbidden in ("message", "prompt", "report", "body", "text", "title"):
        assert forbidden not in activity_entry

    # The project's own rollup entry stays fully redacted, as covered by the
    # existing tests above — asserted here too since this fixture is BANK-only.
    bank = next(p for p in out["projects"] if p["id"] == "BANK")
    assert bank["redacted"] is True
    assert "health" not in bank
