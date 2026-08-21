"""Server-side review and merge — the loop closes without a human (BO-S3b 2/3, 3/3).

Part 1 (publish.py) turns a finished run into a PR and ingest records the
pr/sha evidence, moving the task to READY_TO_REVIEW. This module is the rest:

- ``review_once``: for each READY_TO_REVIEW task carrying pr evidence and no
  verdict yet, enqueue one adversarial review run (read-only profile) whose
  prompt names the PR. The verdict lands in the work result like any outcome.
- ``publish_review_verdicts``: for each READY_TO_REVIEW task whose review
  work item has a result but whose PR head has no marker yet, parse the
  agent's own ``VERDICT: ACCEPT|REJECT`` / ``HEAD_SHA: <sha>`` lines from the
  result text and, on ACCEPT, post the ``ACCEPTANCE: ACCEPT <sha>`` marker as
  a comment-type PR review (``gh pr review --comment``) -- the exact string
  ``merge_once`` scans for. The original design named a separate
  control-plane app (voyn-acceptance) for this, deliberately kept out of this
  process so its credential never entered the worker; that app's GitHub
  installation did not survive the 2026-08-20 org migration (installations
  are not carried over by a repository transfer) and was never reconnected.
  The field it would have used (``ReviewConfig.marker_tool``) was declared
  but never actually invoked anywhere -- this function had no implementation
  at all until VOYN-W0-AICC-MISSING-MARKER-PUBLISHER (2026-08-21), so no
  review verdict, however produced, ever reached GitHub and ``merge_once``
  could not merge a single PR autonomously start to finish. Posting as a
  *comment*-type review, not an approval, is deliberate: GitHub refuses
  self-approval for the PR's own author (this pipeline's author and merger
  are the same account), but a comment-type review from the author is
  permitted and still lands in the ``reviews[]`` array ``merge_once`` reads
  — verified live against voyn88/aios#273 before writing this.
- ``merge_once``: for each PR that carries an ACCEPT marker AND whose required
  checks are green, ``gh pr merge`` it and move the task READY_TO_REVIEW→DONE
  with the merged sha as evidence (via the existing backlog_transition gate).

All three are refusal-as-data, driven by oneshot timers, and idempotent: a
task already reviewed is skipped, a marker already posted is skipped, an
already-merged PR closes the task once.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "LoopReport",
    "ReviewConfig",
    "merge_once",
    "publish_review_verdicts",
    "review_once",
]


@dataclass(frozen=True, slots=True)
class ReviewConfig:
    reviewer: str = "server-reviewer"
    queue: str = "execution"
    review_timeout: int = 900
    max_per_tick: int = 8


@dataclass
class LoopReport:
    reviewed: list[tuple[str, str]] = field(default_factory=list)
    merged: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


def _gh(argv: list[str], repo_path: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", *argv], cwd=repo_path, capture_output=True, text=True,
        check=False, timeout=120,
    )


def _rows(factory: Any, sql: str, params: tuple = ()) -> list[tuple]:
    with factory() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


# -- Part 2: review -----------------------------------------------------------

_REVIEW_PROMPT = (
    "Adversarially review pull request {pr} for task {task}. Read the diff. "
    "Hunt for defects that make it wrong, unsafe, or a regression — a control "
    "that reads wider than it acts, a test that passes on broken code. State a "
    "verdict as the last line, exactly: VERDICT: ACCEPT or VERDICT: REJECT, "
    "then HEAD_SHA: <the PR head sha>."
)


def review_once(factory: Any, enqueue: Any, cfg: ReviewConfig | None = None) -> LoopReport:
    """Enqueue a review run for each READY_TO_REVIEW task with a pr and no
    review queued yet. ``enqueue(queue, key, payload, task_id)`` is the queue
    writer (control-plane privilege); passing it in keeps this composable and
    testable without a live queue. The task_id is passed through to the
    enqueue call (not just embedded in the payload/prompt) so
    publish_review_verdicts can look the result back up by
    ``work_item.task_id`` -- omitting it left that column NULL for every
    review item, which is what VOYN-W0-AICC-MISSING-MARKER-PUBLISHER's
    lookup exposed."""
    cfg = cfg or ReviewConfig()
    report = LoopReport()
    # Idempotency is the queue's: enqueue keys on review:<task>, so a task
    # already under review returns the same work item, never a second run.
    tasks = _rows(
        factory,
        "SELECT DISTINCT t.task_id, e.value FROM backlog_task t "
        "JOIN backlog_evidence e ON e.task_id = t.task_id AND e.kind = 'pr' "
        "WHERE t.status = 'READY_TO_REVIEW' "
        "ORDER BY t.task_id LIMIT %s",
        (cfg.max_per_tick,),
    )
    for task_id, pr_url in tasks:
        payload = {
            "kind": "agent_run", "v": 1, "project_id": task_id,
            "repository_path": "", "task_type": "review",
            "prompt": _REVIEW_PROMPT.format(pr=pr_url, task=task_id),
            "timeout_seconds": cfg.review_timeout, "untrusted": False,
        }
        enqueue(cfg.queue, f"review:{task_id}", payload, task_id)
        report.reviewed.append((task_id, pr_url))
    return report


# -- Part 2b: publish the verdict as the marker merge_once reads -------------

# Same shape as handlers.py's _HEAD_SHA_TRAILER (the labelled-trailer-only
# rule: a bare 40-hex string in a transcript is any object id at all, so only
# the explicit trailer counts). Verdict is equally strict -- the exact
# uppercase token the review prompt asks for, nothing inferred from prose.
_VERDICT = re.compile(r"^VERDICT:\s*(ACCEPT|REJECT)\s*$", re.MULTILINE)
_HEAD_SHA_TRAILER = re.compile(r"^HEAD_SHA:\s*([0-9a-f]{7,40})\s*$", re.MULTILINE)


def _latest_review_result(factory: Any, task_id: str) -> dict[str, Any] | None:
    """The most recent succeeded review-class work result for this task, or
    None if the review hasn't finished (or hasn't been dispatched) yet.
    Keyed on the same idempotency_key review_once enqueues with, so this
    reads exactly the run review_once started -- never a stale or unrelated
    result for the same task_id."""
    rows = _rows(
        factory,
        "SELECT wr.payload FROM work_item i "
        "JOIN work_result wr ON wr.result_id = i.result_id "
        "WHERE i.task_id = %s AND i.idempotency_key = %s AND i.state = 'succeeded' "
        "ORDER BY wr.created_at DESC LIMIT 1",
        (task_id, f"review:{task_id}"),
    )
    if not rows:
        return None
    payload = rows[0][0]
    return json.loads(payload) if isinstance(payload, str) else payload


def _has_accept_marker(repo_path: str, pr_url: str) -> tuple[bool, str]:
    """Whether an ACCEPT marker already stands on the PR's current head --
    read-only, no gh pr merge/checks concern (that's _pr_is_mergeable's
    job). Returns (has_marker, head_sha)."""
    view = _gh(["pr", "view", pr_url, "--json", "reviews,headRefOid"], repo_path)
    if view.returncode != 0:
        return False, ""
    data = json.loads(view.stdout or "{}")
    head = data.get("headRefOid", "")
    accept = any(
        f"ACCEPTANCE: ACCEPT {head}" in (r.get("body") or "")
        for r in data.get("reviews", [])
    )
    return accept, head


def publish_review_verdicts(factory: Any, repo_path: str, cfg: ReviewConfig | None = None) -> LoopReport:
    """For each READY_TO_REVIEW task whose review run has a result, publish
    the ACCEPT marker merge_once looks for. A REJECT verdict, a missing
    verdict/sha in the result text, or a marker already posted for the
    current head are all skips, not errors -- the task simply waits for its
    next state (a fresh review after a new push, or a human)."""
    cfg = cfg or ReviewConfig()
    report = LoopReport()
    tasks = _rows(
        factory,
        "SELECT t.task_id, e.value FROM backlog_task t "
        "JOIN backlog_evidence e ON e.task_id = t.task_id AND e.kind = 'pr' "
        "WHERE t.status = 'READY_TO_REVIEW' ORDER BY t.updated_at LIMIT %s",
        (cfg.max_per_tick,),
    )
    for task_id, pr_url in tasks:
        result = _latest_review_result(factory, task_id)
        if result is None:
            report.skipped.append((task_id, "no_review_result_yet"))
            continue
        text = result.get("result_text") or ""
        # The LAST match, not the first: the prompt asks for the verdict "as
        # the last line", but nothing enforces that, and an agent reasoning
        # aloud in free text can draft a tentative verdict, reconsider, and
        # correct it -- .search() would silently keep the earlier one.
        # Independent review caught this live (2026-08-21): a transcript
        # that says ACCEPT, keeps reading, finds a real defect, then says
        # REJECT would have posted ACCEPTANCE anyway under .search().
        verdict_matches = list(_VERDICT.finditer(text))
        sha_matches = list(_HEAD_SHA_TRAILER.finditer(text))
        if not verdict_matches or not sha_matches:
            report.skipped.append((task_id, "verdict_or_head_sha_missing_in_review_result"))
            continue
        if verdict_matches[-1].group(1) != "ACCEPT":
            report.skipped.append((task_id, "review_verdict_reject"))
            continue
        sha = sha_matches[-1].group(1)
        already, current_head = _has_accept_marker(repo_path, pr_url)
        if already:
            report.skipped.append((task_id, "marker_already_posted"))
            continue
        if current_head and current_head != sha:
            # The PR moved since this review ran (a new push). Posting a
            # marker for a sha that is no longer the head would satisfy
            # merge_once's string match against stale evidence -- exactly
            # the "evidence measured on a state that no longer exists" class
            # already on file. A fresh review_once tick will re-dispatch.
            report.skipped.append((task_id, f"stale_review: reviewed {sha} but head is {current_head}"))
            continue
        posted = _gh(
            ["pr", "review", pr_url, "--comment", "--body", f"ACCEPTANCE: ACCEPT {sha}"],
            repo_path,
        )
        if posted.returncode != 0:
            report.skipped.append((task_id, f"marker_post_failed: {posted.stderr.strip()[:120]}"))
            continue
        report.reviewed.append((task_id, pr_url))
    return report


# -- Part 3: merge ------------------------------------------------------------


def _pr_is_mergeable(repo_path: str, pr_url: str) -> tuple[bool, str]:
    """A PR is ready to merge iff its required checks are green and an ACCEPT
    marker stands on the head. `gh pr view` gives both in one call."""
    view = _gh(
        ["pr", "view", pr_url, "--json",
         "reviews,statusCheckRollup,mergeStateStatus,state,headRefOid"],
        repo_path,
    )
    if view.returncode != 0:
        return False, f"gh_view_failed: {view.stderr.strip()[:100]}"
    data = json.loads(view.stdout or "{}")
    if data.get("state") != "OPEN":
        return False, f"pr_{str(data.get('state')).lower()}"
    head = data.get("headRefOid", "")
    accept = any(
        f"ACCEPTANCE: ACCEPT {head}" in (r.get("body") or "")
        for r in data.get("reviews", [])
    )
    if not accept:
        return False, "no_accept_marker_on_head"
    rollup = data.get("statusCheckRollup") or []
    bad = [
        c.get("name", "?") for c in rollup
        if c.get("conclusion") not in (None, "SUCCESS", "NEUTRAL", "SKIPPED")
        and "cceptance" not in c.get("name", "")
    ]
    if bad:
        return False, f"checks_not_green: {bad[:3]}"
    return True, head


def merge_once(factory: Any, repo_path: str, cfg: ReviewConfig | None = None) -> LoopReport:
    """Merge every READY_TO_REVIEW task whose PR carries an ACCEPT marker and
    green checks, then close it DONE with the merged sha as evidence."""
    cfg = cfg or ReviewConfig()
    report = LoopReport()
    tasks = _rows(
        factory,
        "SELECT t.task_id, e.value FROM backlog_task t "
        "JOIN backlog_evidence e ON e.task_id = t.task_id AND e.kind = 'pr' "
        "WHERE t.status = 'READY_TO_REVIEW' ORDER BY t.updated_at LIMIT %s",
        (cfg.max_per_tick,),
    )
    for task_id, pr_url in tasks:
        ready, detail = _pr_is_mergeable(repo_path, pr_url)
        if not ready:
            report.skipped.append((task_id, detail))
            continue
        merged = _gh(["pr", "merge", pr_url, "--squash"], repo_path)
        if merged.returncode != 0:
            report.skipped.append((task_id, f"merge_failed: {merged.stderr.strip()[:100]}"))
            continue
        head = detail  # _pr_is_mergeable returned the head sha
        # Evidence and the DONE transition are one act: the sha row and the
        # status move commit together or not at all (an explicit transaction,
        # since the app factory is autocommit). backlog_transition's third
        # argument is the optimistic-lock revision (bigint), read here (a plain SELECT — the app role writes only through
        # functions, so no row lock is taken; the optimistic revision below is
        # the concurrency guard); the
        # actor is session_user inside the function, not an argument.
        with factory() as conn:
            conn.autocommit = False
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT revision FROM backlog_task WHERE task_id = %s",
                        (task_id,),
                    )
                    row = cur.fetchone()
                    if row is None:
                        conn.rollback()
                        report.skipped.append((task_id, "task_vanished"))
                        continue
                    revision = row[0]
                    cur.execute(
                        "SELECT backlog_record_evidence(%s, 'sha', %s)", (task_id, head)
                    )
                    cur.execute(
                        "SELECT ok, reason FROM backlog_transition(%s, 'DONE', %s)",
                        (task_id, revision),
                    )
                    ok, reason = cur.fetchone()
                if ok:
                    conn.commit()
                    report.merged.append((task_id, head))
                else:
                    conn.rollback()
                    report.skipped.append((task_id, f"transition:{reason}"))
            finally:
                conn.autocommit = True
    return report
