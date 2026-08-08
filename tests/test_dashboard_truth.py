from __future__ import annotations

from command_center import dashboard_truth, read_model


def _counts(total: int = 6) -> read_model.TaskSnapshot:
    return read_model.TaskSnapshot(
        total=total,
        by_lane={lane: (total if lane == "Backlog" else 0) for lane in read_model.CANONICAL_LANES},
        other=0,
        done=0,
        blocked=0,
        active=total,
        attention=0,
    )


def _run(run_id: str, provenance: dict) -> dict:
    return {
        "id": run_id,
        "state": "COMPLETED",
        "started_at": f"2026-08-09T00:00:0{run_id[-1]}",
        "provenance": provenance,
    }


def test_dashboard_truth_distinguishes_every_delivery_and_runtime_state():
    sha = "a" * 40
    runs = [
        _run("run-1", {"head_sha": None, "pr": None, "ci": [], "accepted_sha": None, "deployed_sha": None, "evidence": []}),
        _run("run-2", {"head_sha": sha, "pr": {"number": 2}, "ci": [{"conclusion": "SUCCESS"}], "accepted_sha": None, "deployed_sha": None, "evidence": []}),
        _run("run-3", {"head_sha": sha, "pr": {"number": 3}, "ci": [{"conclusion": "SUCCESS"}], "accepted_sha": sha, "deployed_sha": None, "evidence": []}),
        _run("run-4", {"head_sha": sha, "accepted_sha": sha, "deployed_sha": sha, "evidence": []}),
        _run("run-5", {"head_sha": sha, "accepted_sha": sha, "deployed_sha": None, "evidence": []}),
        _run("run-6", {"head_sha": sha, "accepted_sha": sha, "deployed_sha": None, "evidence": [{"adapter": "runtime_probe", "status": "runtime_sha_mismatch", "candidate_sha": sha, "reported_sha": "b" * 40}]}),
    ]

    view = dashboard_truth.build_dashboard_truth(
        _counts(), runs=runs, total_run_count=42, stale_run_ids=frozenset({"run-5"}), run_window_limit=200
    )

    assert view.task_metric == dashboard_truth.SourceMetric(6, "tasks", "canonical TaskSnapshot")
    assert view.run_metric == dashboard_truth.SourceMetric(42, "runs", "runtime.db count_runs")
    assert view.run_window_label == "Последние 6 из 42 запусков (лимит окна: 200)"
    assert [item.state for item in view.deliveries] == [
        dashboard_truth.RUNTIME_MISMATCH,
        dashboard_truth.STALE_RUNTIME,
        dashboard_truth.DEPLOYED,
        dashboard_truth.ACCEPTED_UNDEPLOYED,
        dashboard_truth.UNACCEPTED,
        dashboard_truth.UNKNOWN,
    ]
    assert all(item.label and item.description and item.semantic_role == "status" for item in view.deliveries)


def test_dashboard_truth_does_not_promote_green_ci_or_health_to_acceptance_or_deployment():
    sha = "c" * 40
    run = _run(
        "run-1",
        {
            "head_sha": sha,
            "pr": {"number": 1, "head_sha": sha},
            "ci": [{"status": "COMPLETED", "conclusion": "SUCCESS"}],
            "accepted_sha": None,
            "deployed_sha": None,
            "evidence": [
                {
                    "adapter": "runtime_probe",
                    "status": "runtime_observed",
                    "candidate_sha": sha,
                    "reported_sha": None,
                }
            ],
        },
    )

    item = dashboard_truth.build_dashboard_truth(
        _counts(1), runs=[run], total_run_count=1
    ).deliveries[0]

    assert item.state == dashboard_truth.UNACCEPTED
    assert "не принят" in item.label.lower()
    assert "deploy" not in item.label.lower()


def test_runtime_mismatch_wins_over_stale_without_leaking_payload_fields():
    sha = "d" * 40
    run = _run(
        "run-1",
        {
            "head_sha": sha,
            "accepted_sha": sha,
            "deployed_sha": None,
            "evidence": [
                {
                    "adapter": "runtime_probe",
                    "status": "runtime_sha_mismatch",
                    "candidate_sha": sha,
                    "reported_sha": "e" * 40,
                    "native_payload": {"prompt": "secret"},
                }
            ],
        },
    )

    item = dashboard_truth.build_dashboard_truth(
        _counts(1), runs=[run], total_run_count=1, stale_run_ids=frozenset({"run-1"})
    ).deliveries[0]

    assert item.state == dashboard_truth.RUNTIME_MISMATCH
    assert "secret" not in repr(item)


def test_latest_runtime_evidence_replaces_an_older_mismatch_without_rewriting_history():
    sha = "f" * 40
    run = _run(
        "run-1",
        {
            "head_sha": sha,
            "accepted_sha": sha,
            "deployed_sha": sha,
            "evidence": [
                {"adapter": "runtime_probe", "status": "runtime_sha_mismatch"},
                {"adapter": "runtime_probe", "status": "runtime_sha_match"},
            ],
        },
    )

    item = dashboard_truth.build_dashboard_truth(
        _counts(1), runs=[run], total_run_count=1
    ).deliveries[0]

    assert item.state == dashboard_truth.DEPLOYED
