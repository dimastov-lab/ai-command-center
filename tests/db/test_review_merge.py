"""review_once / merge_once (BO-S3b 2/3, 3/3) on live PostgreSQL: the store
side is real (READY_TO_REVIEW tasks with pr evidence), gh is faked in-process
by patching the module's _gh, and enqueue is a recording stub."""

from __future__ import annotations

import json


from tests.db.test_backlog_planner import _test_repo_routes, rig  # noqa: F401 — pytest fixtures
from command_center.orchestrator import review_merge
from command_center.orchestrator.review_merge import (
    merge_once, publish_review_verdicts, review_once,
)


def _complete_review(app_factory, worker, task_id, result_text):
    """Enqueue + claim + complete a review-class work item exactly the way
    review_once/the real daemon would, so publish_review_verdicts reads a
    result shaped like production, not a hand-built row."""
    from command_center.db.work_queue_store import WorkQueueStore

    store = WorkQueueStore(app_factory)
    payload = {
        "kind": "agent_run", "v": 1, "project_id": task_id,
        "repository_path": "", "task_type": "review",
        "prompt": "review it", "timeout_seconds": 900, "untrusted": False,
    }
    store.enqueue(
        "execution", idempotency_key=f"review:{task_id}", payload=payload, task_id=task_id
    )
    claimed = worker.claim("execution", visibility_seconds=60)
    assert worker.complete(claimed, {"status": "completed", "result_text": result_text})


def _ready(store, factory, task_id, pr):
    """A task in READY_TO_REVIEW with a pr evidence row — the state part 1
    leaves behind."""
    from tests.db.test_backlog_planner import _task
    assert store.upsert_task(_task(task_id, repo="repo-x", status="OPEN"))[0]
    with factory() as c, c.cursor() as cur:
        # walk OPEN -> IN_PROGRESS -> READY_TO_REVIEW via the real machine;
        # transition's third arg is the bigint revision, re-read each step.
        def _rev():
            cur.execute("SELECT revision FROM backlog_task WHERE task_id=%s", (task_id,))
            return cur.fetchone()[0]
        cur.execute("SELECT ok FROM backlog_transition(%s,'IN_PROGRESS',%s)", (task_id, _rev()))
        cur.execute("SELECT backlog_record_evidence(%s,'pr',%s)", (task_id, pr))
        cur.execute("SELECT ok FROM backlog_transition(%s,'READY_TO_REVIEW',%s)", (task_id, _rev()))
        c.commit()


def test_review_enqueues_one_run_per_ready_task(rig):  # noqa: F811

    app_factory, store, _ = rig
    _ready(store, app_factory, "VOYN-W0-R1", "https://github.com/x/y/pull/7")
    calls = []
    report = review_once(app_factory, lambda q, k, p, tid: calls.append((q, k, p, tid)))
    assert ("VOYN-W0-R1", "https://github.com/x/y/pull/7") in report.reviewed
    assert len(calls) == 1
    q, key, payload, task_id = calls[0]
    assert key == "review:VOYN-W0-R1"  # idempotency key
    assert task_id == "VOYN-W0-R1"
    assert payload["task_type"] == "review" and "pull/7" in payload["prompt"]


def test_merge_requires_accept_marker_and_green_checks(rig, monkeypatch):  # noqa: F811

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
        cur.execute("SELECT status FROM backlog_task WHERE task_id=%s", ("VOYN-W0-M1",))
        assert cur.fetchone()[0] == "DONE"


def test_merge_skips_without_marker(rig, monkeypatch):  # noqa: F811

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
        cur.execute("SELECT status FROM backlog_task WHERE task_id=%s", ("VOYN-W0-M2",))
        assert cur.fetchone()[0] == "READY_TO_REVIEW"  # untouched


def test_publish_verdict_posts_the_marker_gh_pr_review_reads(rig, monkeypatch):  # noqa: F811
    """VOYN-W0-AICC-MISSING-MARKER-PUBLISHER: the agent's own ACCEPT verdict
    must reach GitHub as the exact `ACCEPTANCE: ACCEPT <sha>` comment-review
    body merge_once's _pr_is_mergeable scans for -- proven end to end here,
    not just that some gh call happened."""
    app_factory, store, worker = rig
    head = "d" * 40
    _ready(store, app_factory, "VOYN-W0-P1", "https://github.com/x/y/pull/11")
    _complete_review(
        app_factory, worker, "VOYN-W0-P1",
        f"Reviewed the diff, found nothing wrong.\nVERDICT: ACCEPT\nHEAD_SHA: {head}\n",
    )

    posted = []

    def fake_gh(argv, repo):
        import subprocess
        if argv[:2] == ["pr", "view"]:
            body = json.dumps({"headRefOid": head, "reviews": []})
            return subprocess.CompletedProcess(argv, 0, body, "")
        if argv[:2] == ["pr", "review"]:
            posted.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 1, "", "?")

    monkeypatch.setattr(review_merge, "_gh", fake_gh)
    report = publish_review_verdicts(app_factory, "/tmp")
    assert ("VOYN-W0-P1", "https://github.com/x/y/pull/11") in report.reviewed
    assert len(posted) == 1
    assert posted[0][:3] == ["pr", "review", "https://github.com/x/y/pull/11"]
    assert "--comment" in posted[0]
    body_index = posted[0].index("--body") + 1
    assert posted[0][body_index] == f"ACCEPTANCE: ACCEPT {head}"


def test_publish_verdict_skips_a_reject(rig, monkeypatch):  # noqa: F811
    app_factory, store, worker = rig
    _ready(store, app_factory, "VOYN-W0-P2", "https://github.com/x/y/pull/12")
    _complete_review(
        app_factory, worker, "VOYN-W0-P2",
        "Found a real defect.\nVERDICT: REJECT\nHEAD_SHA: " + "e" * 40 + "\n",
    )

    posted = []
    monkeypatch.setattr(
        review_merge, "_gh",
        lambda argv, repo: posted.append(argv) or __import__("subprocess").CompletedProcess(argv, 0, "{}", ""),
    )
    report = publish_review_verdicts(app_factory, "/tmp")
    assert ("VOYN-W0-P2", "review_verdict_reject") in report.skipped
    assert not any(a[:2] == ["pr", "review"] for a in posted)


def test_publish_verdict_skips_without_a_completed_review_yet(rig):  # noqa: F811
    app_factory, store, _worker = rig
    _ready(store, app_factory, "VOYN-W0-P3", "https://github.com/x/y/pull/13")
    report = publish_review_verdicts(app_factory, "/tmp")
    assert ("VOYN-W0-P3", "no_review_result_yet") in report.skipped


def test_publish_verdict_skips_when_already_posted(rig, monkeypatch):  # noqa: F811
    app_factory, store, worker = rig
    head = "f" * 40
    _ready(store, app_factory, "VOYN-W0-P4", "https://github.com/x/y/pull/14")
    _complete_review(
        app_factory, worker, "VOYN-W0-P4",
        f"Looks fine.\nVERDICT: ACCEPT\nHEAD_SHA: {head}\n",
    )

    posted = []

    def fake_gh(argv, repo):
        import subprocess
        if argv[:2] == ["pr", "view"]:
            body = json.dumps({
                "headRefOid": head,
                "reviews": [{"body": f"ACCEPTANCE: ACCEPT {head}"}],
            })
            return subprocess.CompletedProcess(argv, 0, body, "")
        posted.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(review_merge, "_gh", fake_gh)
    report = publish_review_verdicts(app_factory, "/tmp")
    assert ("VOYN-W0-P4", "marker_already_posted") in report.skipped
    assert not posted


def test_publish_verdict_skips_a_stale_review_after_a_new_push(rig, monkeypatch):  # noqa: F811
    """VOYN-OPS-EVIDENCE-MEASURED-ON-A-STATE-THAT-NO-LONGER-EXISTS, same
    class at a new site: the review ran against a sha that is no longer the
    PR's head (a push landed after the review was dispatched). Posting the
    old sha's marker would satisfy merge_once's string match against a
    branch state nobody re-reviewed."""
    app_factory, store, worker = rig
    reviewed_sha = "1" * 40
    new_head = "2" * 40
    _ready(store, app_factory, "VOYN-W0-P5", "https://github.com/x/y/pull/15")
    _complete_review(
        app_factory, worker, "VOYN-W0-P5",
        f"Looks fine.\nVERDICT: ACCEPT\nHEAD_SHA: {reviewed_sha}\n",
    )

    posted = []

    def fake_gh(argv, repo):
        import subprocess
        if argv[:2] == ["pr", "view"]:
            body = json.dumps({"headRefOid": new_head, "reviews": []})
            return subprocess.CompletedProcess(argv, 0, body, "")
        posted.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(review_merge, "_gh", fake_gh)
    report = publish_review_verdicts(app_factory, "/tmp")
    assert any(t == "VOYN-W0-P5" and r.startswith("stale_review") for t, r in report.skipped)
    assert not posted


def test_merge_skips_when_a_check_is_red(rig, monkeypatch):  # noqa: F811

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