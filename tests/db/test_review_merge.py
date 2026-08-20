"""review_once / merge_once (BO-S3b 2/3, 3/3) on live PostgreSQL: the store
side is real (READY_TO_REVIEW tasks with pr evidence), gh is faked in-process
by patching the module's _gh, and enqueue is a recording stub."""

from __future__ import annotations

import json


from command_center.orchestrator import review_merge
from command_center.orchestrator.review_merge import (
    merge_once, review_once,
)



def _ready(store, factory, task_id, pr):
    """A task in READY_TO_REVIEW with a pr evidence row — the state part 1
    leaves behind."""
    from tests.db.test_backlog_planner import _task
    assert store.upsert_task(_task(task_id, repo="repo-x", status="OPEN"))[0]
    with factory() as c, c.cursor() as cur:
        # walk OPEN -> IN_PROGRESS -> READY_TO_REVIEW via the real machine
        cur.execute("SELECT ok FROM backlog_transition(%s,'IN_PROGRESS','w')", (task_id,))
        cur.execute("SELECT backlog_record_evidence(%s,'pr',%s)", (task_id, pr))
        cur.execute("SELECT ok FROM backlog_transition(%s,'READY_TO_REVIEW','w')", (task_id,))
        c.commit()


def test_review_enqueues_one_run_per_ready_task(rig):
    app_factory, store, _ = rig
    _ready(store, app_factory, "VOYN-W0-R1", "https://github.com/x/y/pull/7")
    calls = []
    report = review_once(app_factory, lambda q, k, p: calls.append((q, k, p)))
    assert ("VOYN-W0-R1", "https://github.com/x/y/pull/7") in report.reviewed
    assert len(calls) == 1
    q, key, payload = calls[0]
    assert key == "review:VOYN-W0-R1"  # idempotency key
    assert payload["task_type"] == "review" and "pull/7" in payload["prompt"]


def test_merge_requires_accept_marker_and_green_checks(rig, monkeypatch):
    app_factory, store, _ = rig
    _ready(store, app_factory, "VOYN-W0-M1", "https://github.com/x/y/pull/8")
    head = "a" * 40

    def fake_gh(argv, repo):
        import subprocess
        if argv[:2] == ["pr", "view"]:
            body = json.dumps({
                "state": "OPEN", "headRefOid": head,
                "reviews": [{"body": f"ACCEPTANCE: ACCEPT {head}"}],
                "statusCheckRollup": [{"name": "CI", "conclusion": "SUCCESS"}],
            })
            return subprocess.CompletedProcess(argv, 0, body, "")
        if argv[:2] == ["pr", "merge"]:
            return subprocess.CompletedProcess(argv, 0, "merged", "")
        return subprocess.CompletedProcess(argv, 1, "", "?")

    monkeypatch.setattr(review_merge, "_gh", fake_gh)
    report = merge_once(app_factory, "/tmp")
    assert ("VOYN-W0-M1", head) in report.merged
    with app_factory() as c, c.cursor() as cur:
        cur.execute("SELECT status FROM backlog_task WHERE id=%s", ("VOYN-W0-M1",))
        assert cur.fetchone()[0] == "DONE"


def test_merge_skips_without_marker(rig, monkeypatch):
    app_factory, store, _ = rig
    _ready(store, app_factory, "VOYN-W0-M2", "https://github.com/x/y/pull/9")

    def fake_gh(argv, repo):
        import subprocess
        body = json.dumps({
            "state": "OPEN", "headRefOid": "b" * 40, "reviews": [],
            "statusCheckRollup": [{"name": "CI", "conclusion": "SUCCESS"}],
        })
        return subprocess.CompletedProcess(argv, 0, body, "")

    monkeypatch.setattr(review_merge, "_gh", fake_gh)
    report = merge_once(app_factory, "/tmp")
    assert ("VOYN-W0-M2", "no_accept_marker_on_head") in report.skipped
    with app_factory() as c, c.cursor() as cur:
        cur.execute("SELECT status FROM backlog_task WHERE id=%s", ("VOYN-W0-M2",))
        assert cur.fetchone()[0] == "READY_TO_REVIEW"  # untouched


def test_merge_skips_when_a_check_is_red(rig, monkeypatch):
    app_factory, store, _ = rig
    _ready(store, app_factory, "VOYN-W0-M3", "https://github.com/x/y/pull/10")
    head = "c" * 40

    def fake_gh(argv, repo):
        import subprocess
        body = json.dumps({
            "state": "OPEN", "headRefOid": head,
            "reviews": [{"body": f"ACCEPTANCE: ACCEPT {head}"}],
            "statusCheckRollup": [{"name": "CI", "conclusion": "FAILURE"}],
        })
        return subprocess.CompletedProcess(argv, 0, body, "")

    monkeypatch.setattr(review_merge, "_gh", fake_gh)
    report = merge_once(app_factory, "/tmp")
    assert any(t == "VOYN-W0-M3" and "checks_not_green" in r for t, r in report.skipped)
