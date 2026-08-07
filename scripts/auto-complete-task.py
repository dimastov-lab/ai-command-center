#!/usr/bin/env python3
"""Automate the PR → tests → merge lifecycle for a completed AICC task.

Usage:
    python scripts/auto-complete-task.py TASK-ID

Steps:
  1. Load task from tasks.json; resolve repository_path from project config.
  2. Create feature branch  aicc/{task-id-lower}  at current HEAD.
  3. Push branch to origin.
  4. Create GitHub PR  (gh pr create).
  5. Run full test suite  (uv run pytest tests/ -q).
  6. If green → merge PR (gh pr merge --squash --delete-branch) → set Done.
  7. If red  → create autofix task and exit 1 so the sequencer can retry.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from command_center import project_config, tasks_repository  # noqa: E402

MAX_AUTOFIX_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], cwd: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _die(msg: str) -> None:
    print(f"[ACT] ERROR: {msg}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def _log(msg: str) -> None:
    print(f"[ACT] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Task / project helpers
# ---------------------------------------------------------------------------

def _load_task(task_id: str) -> dict:
    tasks = tasks_repository.load_tasks(ROOT)
    t = next((x for x in tasks if x.get("id") == task_id), None)
    if t is None:
        _die(f"Task {task_id!r} not found in tasks.json")
    return t


def _repo_path(task: dict) -> str:
    repo = task.get("repository_path") or ""
    if repo:
        return repo
    proj = task.get("project") or ""
    if proj:
        cfgs = project_config.load_project_configs()
        cfg = cfgs.get(proj) or {}
        repo = cfg.get("repository_path") or ""
    if not repo:
        _die(
            f"Cannot determine repository_path for task {task['id']!r}. "
            "Set repository_path in the task or project config."
        )
    return repo


def _set_task_done(task_id: str, note: str) -> None:
    tasks_repository.set_manual_launch_status(ROOT, task_id, "Done", note)
    _log(f"Task {task_id} → Done")


def _create_autofix_task(task: dict, failure_output: str) -> str:
    from datetime import datetime
    parent_id = task["id"]
    attempt = int(task.get("autofix_count") or 0) + 1
    new_id = f"{parent_id}-FIX-{attempt:02d}"
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    fix_task: dict = {
        "id": new_id,
        "title": f"Auto-fix: {(task.get('title') or parent_id)[:60]} — attempt {attempt}",
        "description": (
            f"Auto-generated fix for {parent_id} (attempt {attempt}/{MAX_AUTOFIX_ATTEMPTS}).\n\n"
            "Tests failed — fix them without breaking passing tests:\n\n"
            f"```\n{failure_output[:3000]}\n```"
        ),
        "task_type": "implementation",
        "status": "Next",
        "launch_status": "Ready",
        "project": task.get("project"),
        "branch": task.get("branch"),
        "repository_path": task.get("repository_path"),
        "executor": task.get("executor"),
        "model": task.get("model"),
        "parent_task_id": parent_id,
        "created_at": now,
        "updated_at": now,
        "timeline": [],
        "tags": ["auto-fix"],
    }

    def _add(tasks: list[dict]) -> list[dict]:
        if not any(t["id"] == new_id for t in tasks):
            tasks.append(fix_task)
        return tasks

    tasks_repository.mutate_tasks(ROOT, _add)

    # Bump autofix_count on parent so we don't spin forever
    def _bump(tasks: list[dict]) -> list[dict]:
        for t in tasks:
            if t["id"] == parent_id:
                t["autofix_count"] = attempt
        return tasks

    tasks_repository.mutate_tasks(ROOT, _bump)
    return new_id


# ---------------------------------------------------------------------------
# Git / PR helpers
# ---------------------------------------------------------------------------

def _current_head(repo: str) -> str:
    r = _run(["git", "rev-parse", "HEAD"], cwd=repo)
    return r.stdout.strip() if r.returncode == 0 else ""


def _branch_exists_local(repo: str, branch: str) -> bool:
    r = _run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo)
    return r.returncode == 0


def _pr_url_for_branch(repo: str, branch: str) -> str | None:
    r = _run(["gh", "pr", "view", branch, "--json", "url", "--jq", ".url"], cwd=repo)
    url = r.stdout.strip()
    return url if r.returncode == 0 and url.startswith("http") else None


def _create_pr(task: dict, repo: str) -> str:
    """Create feature branch, push, open PR. Returns PR URL."""
    task_id = task["id"]
    branch = f"aicc/{task_id.lower().replace('_', '-')}"
    head_commit = _current_head(repo)

    # Create local branch if needed
    if not _branch_exists_local(repo, branch):
        _log(f"Creating branch {branch!r} at {head_commit[:7]}")
        r = _run(["git", "branch", branch, head_commit or "HEAD"], cwd=repo)
        if r.returncode != 0:
            _die(f"git branch failed: {r.stderr[:400]}")

    # Push branch
    _log(f"Pushing {branch!r} to origin…")
    r = _run(["git", "push", "origin", f"{branch}:{branch}"], cwd=repo)
    if r.returncode != 0:
        _die(f"git push failed:\n{r.stderr[:600]}")

    # Check for existing PR
    existing = _pr_url_for_branch(repo, branch)
    if existing:
        _log(f"PR already exists: {existing}")
        return existing

    # Open PR
    title = task.get("title") or task_id
    body = (
        f"Auto-created by AICC auto-complete-task.\n\n"
        f"**Task:** `{task_id}`\n"
        f"**Type:** {task.get('task_type') or 'implementation'}\n"
    )
    _log("Creating GitHub PR…")
    r = _run(
        ["gh", "pr", "create", "--base", "main", "--head", branch,
         "--title", title, "--body", body],
        cwd=repo,
    )
    if r.returncode != 0:
        _die(f"gh pr create failed:\n{r.stderr[:600]}")

    url = r.stdout.strip()
    _log(f"PR created: {url}")
    return url


def _run_tests(repo: str) -> tuple[bool, str]:
    """Run full pytest suite. Returns (passed, output)."""
    _log("Running tests: uv run pytest tests/ -q …")
    r = _run(
        ["uv", "run", "pytest", "tests/", "-q", "--tb=short"],
        cwd=repo,
        timeout=300,
    )
    output = (r.stdout + "\n" + r.stderr).strip()
    passed = r.returncode == 0
    status = "green ✅" if passed else "red ❌"
    _log(f"Tests {status}  (last line: {output.splitlines()[-1] if output else '–'})")
    return passed, output


def _merge_pr(pr_url: str, repo: str) -> None:
    """Squash-merge a GitHub PR."""
    _log(f"Merging PR {pr_url} …")
    r = _run(
        ["gh", "pr", "merge", pr_url, "--squash", "--delete-branch"],
        cwd=repo,
    )
    if r.returncode != 0:
        _die(f"gh pr merge failed:\n{r.stderr[:600]}")
    _log("PR merged.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(task_id: str) -> int:
    _log(f"=== auto-complete-task  {task_id} ===")

    task = _load_task(task_id)
    repo = _repo_path(task)

    _log(f"Repository: {repo}")
    _log(f"Task status: {task.get('launch_status')}")

    # Check autofix ceiling
    autofix_count = int(task.get("autofix_count") or 0)
    if autofix_count >= MAX_AUTOFIX_ATTEMPTS:
        _die(
            f"Autofix exhausted ({autofix_count}/{MAX_AUTOFIX_ATTEMPTS}). "
            "Review failing tests manually."
        )

    # Step 1: Create PR
    pr_url = _create_pr(task, repo)

    # Step 2: Run tests
    passed, test_output = _run_tests(repo)

    if not passed:
        fix_id = _create_autofix_task(task, test_output)
        _log(f"Tests failed — created autofix task {fix_id}. Exiting 1.")
        return 1

    # Step 3: Merge
    _merge_pr(pr_url, repo)

    # Step 4: Mark Done
    _set_task_done(task_id, f"Auto-merged PR {pr_url}")

    _log(f"=== {task_id} DONE ===")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: auto-complete-task.py TASK-ID", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(main(sys.argv[1]))
