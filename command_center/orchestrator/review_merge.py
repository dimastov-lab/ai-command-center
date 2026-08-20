"""Server-side review and merge — the loop closes without a human (BO-S3b 2/3, 3/3).

Part 1 (publish.py) turns a finished run into a PR and ingest records the
pr/sha evidence, moving the task to READY_TO_REVIEW. This module is the rest:

- ``review_once``: for each READY_TO_REVIEW task carrying pr evidence and no
  verdict yet, enqueue one adversarial review run (read-only profile) whose
  prompt names the PR. The verdict lands in the work result like any outcome;
  the acceptance marker itself is published by the control-plane script
  (voyn-acceptance app), invoked here by path so the app key never enters
  this process.
- ``merge_once``: for each PR that carries an ACCEPT marker AND whose required
  checks are green, ``gh pr merge`` it and move the task READY_TO_REVIEW→DONE
  with the merged sha as evidence (via the existing backlog_transition gate).

Both are refusal-as-data, driven by oneshot timers, and idempotent: a task
already reviewed is skipped, an already-merged PR closes the task once.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any

__all__ = ["ReviewConfig", "LoopReport", "review_once", "merge_once"]


@dataclass(frozen=True, slots=True)
class ReviewConfig:
    reviewer: str = "server-reviewer"
    marker_tool: str = ""  # path to the acceptance-marker publisher; "" = skip
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


def review_once(factory: Any, enqueue: Any, cfg: ReviewConfig = ReviewConfig()) -> LoopReport:
    """Enqueue a review run for each READY_TO_REVIEW task with a pr and no
    review queued yet. ``enqueue(queue, key, payload)`` is the queue writer
    (control-plane privilege); passing it in keeps this composable and
    testable without a live queue."""
    report = LoopReport()
    # Idempotency is the queue's: enqueue keys on review:<task>, so a task
    # already under review returns the same work item, never a second run.
    tasks = _rows(
        factory,
        "SELECT DISTINCT t.id, e.value FROM backlog_task t "
        "JOIN backlog_evidence e ON e.task_id = t.id AND e.kind = 'pr' "
        "WHERE t.status = 'READY_TO_REVIEW' "
        "ORDER BY t.id LIMIT %s",
        (cfg.max_per_tick,),
    )
    for task_id, pr_url in tasks:
        payload = {
            "kind": "agent_run", "v": 1, "project_id": task_id,
            "repository_path": "", "task_type": "review",
            "prompt": _REVIEW_PROMPT.format(pr=pr_url, task=task_id),
            "timeout_seconds": cfg.review_timeout, "untrusted": False,
        }
        enqueue(cfg.queue, f"review:{task_id}", payload)
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


def merge_once(factory: Any, repo_path: str, cfg: ReviewConfig = ReviewConfig()) -> LoopReport:
    """Merge every READY_TO_REVIEW task whose PR carries an ACCEPT marker and
    green checks, then close it DONE with the merged sha as evidence."""
    report = LoopReport()
    tasks = _rows(
        factory,
        "SELECT t.id, e.value FROM backlog_task t "
        "JOIN backlog_evidence e ON e.task_id = t.id AND e.kind = 'pr' "
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
        with factory() as conn, conn.cursor() as cur:
            cur.execute("SELECT backlog_record_evidence(%s, 'sha', %s)", (task_id, head))
            cur.execute(
                "SELECT ok, reason FROM backlog_transition(%s, 'DONE', %s)",
                (task_id, cfg.reviewer),
            )
            ok, reason = cur.fetchone()
            conn.commit()
        report.merged.append((task_id, head)) if ok else report.skipped.append((task_id, f"transition:{reason}"))
    return report
