from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from command_center import execution_queue, git_info, portfolio_launch
from command_center.portfolio_models import parse_card
from command_center.runtime import api as runtime_api

CARD_TEMPLATE = """---
schema_version: "1.0"
task_id: "{task_id}"
title: "Test task {task_id}"
project: "{project}"
type: "implementation"
capability: "none"
priority: "medium"
status: "{status}"
repository: "{repository}"
base_branch: "{base_branch}"
branch: {branch}
worktree: {worktree}
agent: null
autonomy: "confirmed"
parallel_group: null
requires: {requires}
blocks: []
conflicts_with: []
deliverables: ["a thing gets done"]
validation: ["true"]
stop_conditions: ["Stop once done."]
evidence: []
confidence: null
gated_by: []
---

# Test task {task_id}

## Objective

Do the test thing.
"""


def _current_branch(repo: Path) -> str:
    result = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _yaml_str_or_null(value: str | None) -> str:
    return "null" if value is None else f'"{value}"'


def _write_card(
    tmp_path,
    *,
    task_id: str = "AICC-TEST-001",
    project: str = "AICC",
    status: str = "ready",
    repository: str = "~/Projects/ai-command-center",
    base_branch: str = "main",
    requires: str = "[]",
    branch: str | None = None,
    worktree: str | None = None,
):
    lane = status if status in ("ready", "review", "blocked", "backlog") else "ready"
    lane_dir = tmp_path / "portfolio" / "tasks" / lane / project
    lane_dir.mkdir(parents=True, exist_ok=True)
    path = lane_dir / f"{task_id}.md"
    text = CARD_TEMPLATE.format(
        task_id=task_id,
        project=project,
        status=status,
        repository=repository,
        base_branch=base_branch,
        requires=requires,
        branch=_yaml_str_or_null(branch),
        worktree=_yaml_str_or_null(worktree),
    )
    path.write_text(text, encoding="utf-8")
    return parse_card(path, lane=lane)


def _add_worktree(repo: Path, *, path: Path, branch: str, base: str = "HEAD") -> None:
    subprocess.run(["git", "worktree", "add", "-b", branch, str(path), base], cwd=repo, check=True)


@pytest.fixture
def portfolio_worktrees_root(tmp_path, monkeypatch):
    root = tmp_path / "worktrees"
    monkeypatch.setenv(portfolio_launch.WORKTREES_ROOT_ENV, str(root))
    return root


# --------------------------------------------------------------------------
# Naming convention
# --------------------------------------------------------------------------


def test_branch_and_worktree_naming_is_deterministic_and_lowercased(portfolio_worktrees_root):
    assert portfolio_launch.branch_name_for("AICC-UI-001") == "task/aicc-ui-001"
    assert portfolio_launch.worktree_path_for("AICC-UI-001") == portfolio_worktrees_root / "aicc-ui-001"


# --------------------------------------------------------------------------
# Dry-run planning — pure, read-only
# --------------------------------------------------------------------------


def test_build_launch_plan_happy_path(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    task = _write_card(tmp_path, base_branch=base_branch, repository=str(git_repo))

    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})

    assert plan.launchable, plan.blockers
    assert plan.branch == "task/aicc-test-001"
    assert plan.worktree == str(portfolio_worktrees_root / "aicc-test-001")
    expected_sha = subprocess.run(
        ["git", "rev-parse", base_branch], cwd=git_repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert plan.base_sha == expected_sha
    assert plan.repository_root == str(git_repo.resolve())


def test_build_launch_plan_ignores_dirty_base_repository(git_repo, tmp_path, portfolio_worktrees_root):
    (git_repo / "dirty.txt").write_text("uncommitted", encoding="utf-8")
    base_branch = _current_branch(git_repo)
    task = _write_card(tmp_path, base_branch=base_branch, repository=str(git_repo))

    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})

    assert plan.launchable is True


def test_build_launch_plan_blocks_when_project_not_mapped(tmp_path, portfolio_worktrees_root):
    task = _write_card(tmp_path)
    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={})
    assert not plan.launchable
    assert any("не сопоставлен" in b for b in plan.blockers)


def test_build_launch_plan_blocks_when_repository_path_missing(tmp_path, portfolio_worktrees_root):
    task = _write_card(tmp_path)
    missing_path = str(tmp_path / "does-not-exist")
    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": missing_path})
    assert not plan.launchable
    assert any("не существует" in b for b in plan.blockers)


def test_build_launch_plan_blocks_when_path_is_not_a_git_repository(tmp_path, portfolio_worktrees_root):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    task = _write_card(tmp_path)
    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(not_a_repo)})
    assert not plan.launchable
    assert any("не является git-репозиторием" in b for b in plan.blockers)


def test_build_launch_plan_blocks_when_base_branch_ref_missing(git_repo, tmp_path, portfolio_worktrees_root):
    task = _write_card(tmp_path, base_branch="does-not-exist-branch", repository=str(git_repo))
    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})
    assert not plan.launchable
    assert any("не найдена" in b for b in plan.blockers)


def test_build_launch_plan_blocks_non_ready_lane(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    task = _write_card(tmp_path, status="blocked", base_branch=base_branch, repository=str(git_repo))
    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})
    assert not plan.launchable
    assert any("ready" in b for b in plan.blockers)


def test_build_launch_plan_blocks_unmet_requirements(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    blocker_task = _write_card(
        tmp_path, task_id="AICC-BLOCKER", status="blocked", base_branch=base_branch, repository=str(git_repo)
    )
    dependent = _write_card(
        tmp_path,
        task_id="AICC-DEPENDENT",
        base_branch=base_branch,
        repository=str(git_repo),
        requires='["AICC-BLOCKER"]',
    )
    tasks_by_id = {"AICC-BLOCKER": blocker_task, "AICC-DEPENDENT": dependent}

    plan = portfolio_launch.build_launch_plan(
        dependent, tasks_by_id=tasks_by_id, repository_paths={"AICC": str(git_repo)}
    )
    assert not plan.launchable
    assert any("AICC-BLOCKER" in b for b in plan.blockers)


def test_build_launch_plan_blocks_already_registered_task(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    task = _write_card(tmp_path, base_branch=base_branch, repository=str(git_repo))
    registry = {task.task_id: {"run_id": "r1"}}

    plan = portfolio_launch.build_launch_plan(
        task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)}, registry=registry
    )
    assert not plan.launchable
    assert any("уже была запущена" in b for b in plan.blockers)


def test_build_launch_plan_blocks_existing_branch(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    task = _write_card(tmp_path, base_branch=base_branch, repository=str(git_repo))
    subprocess.run(["git", "branch", "task/aicc-test-001"], cwd=git_repo, check=True)

    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})
    assert not plan.launchable
    assert any("ветка уже существует" in b for b in plan.blockers)


def test_build_launch_plan_blocks_existing_worktree_path_that_is_not_a_git_repo(
    git_repo, tmp_path, portfolio_worktrees_root
):
    """A bare directory sitting at the resolved (generated-default) worktree
    path is treated as "existing worktree mode" (never re-created via `git
    worktree add`), but fails validation because it isn't a git repository at
    all — a different, more specific blocker than the old blanket "path
    already exists"."""
    base_branch = _current_branch(git_repo)
    task = _write_card(tmp_path, base_branch=base_branch, repository=str(git_repo))
    portfolio_launch.worktree_path_for(task.task_id).mkdir(parents=True)

    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})
    assert not plan.launchable
    assert plan.worktree_mode == portfolio_launch.WORKTREE_MODE_EXISTING
    assert any("не является git-репозиторием" in b for b in plan.blockers)


def test_build_launch_plan_dry_run_makes_no_mutation(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    task = _write_card(tmp_path, base_branch=base_branch, repository=str(git_repo))
    branches_before = set(git_info.get_branches(git_repo))

    portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})

    assert set(git_info.get_branches(git_repo)) == branches_before
    assert not portfolio_launch.worktree_path_for(task.task_id).exists()
    assert execution_queue.load_queue(tmp_path) == []


# --------------------------------------------------------------------------
# Prompt generation
# --------------------------------------------------------------------------


def test_build_agent_prompt_contains_card_and_safety_instructions(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    task = _write_card(tmp_path, base_branch=base_branch, repository=str(git_repo))
    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})

    prompt = portfolio_launch.build_agent_prompt(task, plan)

    assert task.raw_text in prompt
    assert plan.branch in prompt
    assert plan.worktree in prompt
    assert plan.base_sha in prompt
    assert "git merge" in prompt
    assert "git push" in prompt
    assert "## Verdict" in prompt
    assert "## Findings" in prompt
    assert "Branch:" in prompt


# --------------------------------------------------------------------------
# Real launch — worktree/branch creation, queue/launcher reuse, rollback
# --------------------------------------------------------------------------


def test_launch_portfolio_task_requires_explicit_confirmation(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    task = _write_card(tmp_path, base_branch=base_branch, repository=str(git_repo))
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")

    with pytest.raises(portfolio_launch.PortfolioLaunchError):
        portfolio_launch.launch_portfolio_task(
            tmp_path, task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)},
            execution_center_api=api, confirmed=False,
        )


def test_launch_portfolio_task_creates_worktree_branch_and_launches(
    git_repo, tmp_path, fake_claude, portfolio_worktrees_root
):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    base_branch = _current_branch(git_repo)
    task = _write_card(tmp_path, base_branch=base_branch, repository=str(git_repo))
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")

    result = portfolio_launch.launch_portfolio_task(
        tmp_path, task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)},
        execution_center_api=api, confirmed=True,
    )

    try:
        assert result.launched is True, result.message
        assert result.run_id is not None

        worktree_path = Path(result.plan.worktree)
        assert worktree_path.is_dir()
        assert (worktree_path / ".git").exists()
        assert result.plan.branch in git_info.get_branches(git_repo)

        registry = portfolio_launch.load_registry(tmp_path)
        assert task.task_id in registry
        assert registry[task.task_id]["run_id"] == result.run_id
        assert registry[task.task_id]["branch"] == result.plan.branch

        entries = execution_queue.load_queue(tmp_path)
        entry = next(e for e in entries if e["task_id"] == task.task_id)
        assert entry["state"] == execution_queue.STATE_LAUNCHED
        assert entry["run_id"] == result.run_id
    finally:
        if result.run_id:
            api.request_cancel(result.run_id, confirmed=True)


def test_launch_portfolio_task_is_blocked_a_second_time_for_same_task(
    git_repo, tmp_path, fake_claude, portfolio_worktrees_root
):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    base_branch = _current_branch(git_repo)
    task = _write_card(tmp_path, base_branch=base_branch, repository=str(git_repo))
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")

    first = portfolio_launch.launch_portfolio_task(
        tmp_path, task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)},
        execution_center_api=api, confirmed=True,
    )
    assert first.launched is True

    # Re-parse the same card fresh (simulating a second click reading the
    # card again) rather than reusing `task`, to prove the guard is durable
    # state, not an artifact of Python object identity.
    task_again = parse_card(task.source_path, lane=task.lane)
    second = portfolio_launch.launch_portfolio_task(
        tmp_path, task_again, tasks_by_id={}, repository_paths={"AICC": str(git_repo)},
        execution_center_api=api, confirmed=True,
    )

    assert second.launched is False
    assert "уже была запущена" in second.message
    assert len(git_info.get_worktrees(git_repo)) == 2  # primary + the one worktree from `first`

    api.request_cancel(first.run_id, confirmed=True)


def test_claim_prevents_concurrent_double_launch(tmp_path):
    assert portfolio_launch._claim(tmp_path, "AICC-X") is True
    assert portfolio_launch._claim(tmp_path, "AICC-X") is False
    portfolio_launch._release(tmp_path, "AICC-X")
    assert portfolio_launch._claim(tmp_path, "AICC-X") is True
    portfolio_launch._release(tmp_path, "AICC-X")


def test_create_worktree_raises_portfolio_launch_error_on_git_failure(git_repo, tmp_path):
    with pytest.raises(portfolio_launch.PortfolioLaunchError):
        portfolio_launch.create_worktree(
            git_repo, branch="task/x", worktree_path=tmp_path / "wt", base_branch="does-not-exist-branch"
        )


def test_remove_worktree_and_delete_branch_are_best_effort_and_never_raise(git_repo, tmp_path):
    portfolio_launch.remove_worktree(git_repo, tmp_path / "nonexistent-worktree")
    portfolio_launch.delete_branch(git_repo, "no-such-branch")


def test_launch_portfolio_task_rolls_back_worktree_and_branch_on_launch_failure(
    git_repo, tmp_path, monkeypatch, portfolio_worktrees_root
):
    base_branch = _current_branch(git_repo)
    task = _write_card(tmp_path, base_branch=base_branch, repository=str(git_repo))
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")

    def failing_launch_ready(root, entries, tasks, tasks_by_id, project_configs, execution_center_api, *, entry_ids=None):
        updated = [dict(e) for e in entries]
        results = [
            execution_queue.LaunchAttemptResult(entry_ids[0], task.task_id, False, message="forced failure for test")
        ]
        return updated, results

    monkeypatch.setattr(portfolio_launch.execution_queue, "launch_ready", failing_launch_ready)

    result = portfolio_launch.launch_portfolio_task(
        tmp_path, task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)},
        execution_center_api=api, confirmed=True,
    )

    assert result.launched is False
    assert "forced failure" in result.message
    assert not Path(result.plan.worktree).exists()
    assert result.plan.branch not in git_info.get_branches(git_repo)
    assert task.task_id not in portfolio_launch.load_registry(tmp_path)
    assert execution_queue.load_queue(tmp_path) == []

    # The claim must have been released so a retry is possible.
    assert portfolio_launch._claim(tmp_path, task.task_id) is True
    portfolio_launch._release(tmp_path, task.task_id)


def test_concurrent_rollback_does_not_lose_a_parallel_successful_registration(
    git_repo, tmp_path, monkeypatch, fake_claude, portfolio_worktrees_root
):
    """Founder Gate Major-1 rollback/release-path regression: a launch that
    fails and rolls back must never be able to clobber a *different* task's
    registry entry being written concurrently. The rollback path itself
    never touches the registry (only a fully successful launch does) — this
    proves that holds under genuine concurrent execution of two different
    tasks, one failing and one succeeding, not just sequentially."""
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    base_branch = _current_branch(git_repo)
    ok_task = _write_card(tmp_path, task_id="AICC-RACE-OK", base_branch=base_branch, repository=str(git_repo))
    fail_task = _write_card(tmp_path, task_id="AICC-RACE-FAIL", base_branch=base_branch, repository=str(git_repo))
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")

    real_launch_ready = execution_queue.launch_ready

    def selective_launch_ready(root, entries, tasks, tasks_by_id, project_configs, execution_center_api, *, entry_ids=None):
        if any(t["id"] == "AICC-RACE-FAIL" for t in tasks):
            updated = [dict(e) for e in entries]
            results = [
                execution_queue.LaunchAttemptResult(
                    entry_ids[0], "AICC-RACE-FAIL", False, message="forced failure for test"
                )
            ]
            return updated, results
        return real_launch_ready(
            root, entries, tasks, tasks_by_id, project_configs, execution_center_api, entry_ids=entry_ids
        )

    monkeypatch.setattr(portfolio_launch.execution_queue, "launch_ready", selective_launch_ready)

    start = threading.Barrier(2)
    results: dict[str, portfolio_launch.PortfolioLaunchResult] = {}

    def run(task, key):
        start.wait()
        results[key] = portfolio_launch.launch_portfolio_task(
            tmp_path, task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)},
            execution_center_api=api, confirmed=True,
        )

    threads = [
        threading.Thread(target=run, args=(ok_task, "ok")),
        threading.Thread(target=run, args=(fail_task, "fail")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    try:
        assert results["ok"].launched is True, results["ok"].message
        assert results["fail"].launched is False

        registry = portfolio_launch.load_registry(tmp_path)
        assert "AICC-RACE-OK" in registry
        assert "AICC-RACE-FAIL" not in registry
        assert not Path(results["fail"].plan.worktree).exists()
        assert results["fail"].plan.branch not in git_info.get_branches(git_repo)
    finally:
        if results.get("ok") and results["ok"].run_id:
            api.request_cancel(results["ok"].run_id, confirmed=True)


def test_launch_batch_persists_entries_via_the_shared_registry_lock_mechanism(
    git_repo, tmp_path, fake_claude, portfolio_worktrees_root, monkeypatch
):
    """Founder Gate Major-1 requirement: batch launch must reuse the exact
    same atomic registry write path as a single launch (`_persist_registry_
    entry`, backed by `_registry_lock`), not a separate, potentially unsafe
    implementation."""
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    base_branch = _current_branch(git_repo)
    task_a = _write_card(tmp_path, task_id="AICC-BATCH-LOCK-A", base_branch=base_branch, repository=str(git_repo))
    task_b = _write_card(tmp_path, task_id="AICC-BATCH-LOCK-B", base_branch=base_branch, repository=str(git_repo))
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")

    calls: list[str] = []
    real_persist = portfolio_launch._persist_registry_entry

    def spy_persist(root, task_id, entry):
        calls.append(task_id)
        return real_persist(root, task_id, entry)

    monkeypatch.setattr(portfolio_launch, "_persist_registry_entry", spy_persist)

    results = portfolio_launch.launch_batch(
        tmp_path, [task_a, task_b], tasks_by_id={}, repository_paths={"AICC": str(git_repo)},
        execution_center_api=api, confirmed=True,
    )

    try:
        assert all(r.launched for r in results), [r.message for r in results]
        assert sorted(calls) == ["AICC-BATCH-LOCK-A", "AICC-BATCH-LOCK-B"]
        registry = portfolio_launch.load_registry(tmp_path)
        assert "AICC-BATCH-LOCK-A" in registry
        assert "AICC-BATCH-LOCK-B" in registry
    finally:
        for r in results:
            if r.run_id:
                api.request_cancel(r.run_id, confirmed=True)


# --------------------------------------------------------------------------
# Batch launch
# --------------------------------------------------------------------------


def test_launch_batch_with_valid_and_invalid_tasks(git_repo, tmp_path, fake_claude, portfolio_worktrees_root):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    base_branch = _current_branch(git_repo)
    valid = _write_card(tmp_path, task_id="AICC-VALID-1", base_branch=base_branch, repository=str(git_repo))
    invalid = _write_card(
        tmp_path, task_id="AICC-INVALID-1", status="blocked", base_branch=base_branch, repository=str(git_repo)
    )
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")

    results = portfolio_launch.launch_batch(
        tmp_path, [valid, invalid], tasks_by_id={}, repository_paths={"AICC": str(git_repo)},
        execution_center_api=api, confirmed=True,
    )

    assert results[0].launched is True
    assert results[1].launched is False
    assert any("ready" in b for b in results[1].plan.blockers)

    api.request_cancel(results[0].run_id, confirmed=True)


def test_launch_batch_respects_concurrency_limit(git_repo, tmp_path, fake_claude, portfolio_worktrees_root):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    base_branch = _current_branch(git_repo)
    task_a = _write_card(tmp_path, task_id="AICC-BATCH-A", base_branch=base_branch, repository=str(git_repo))
    task_b = _write_card(tmp_path, task_id="AICC-BATCH-B", base_branch=base_branch, repository=str(git_repo))
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")

    results = portfolio_launch.launch_batch(
        tmp_path, [task_a, task_b], tasks_by_id={}, repository_paths={"AICC": str(git_repo)},
        execution_center_api=api, confirmed=True, max_concurrent=1,
    )

    assert results[0].launched is True
    assert results[1].launched is False
    assert "лимит" in results[1].message

    api.request_cancel(results[0].run_id, confirmed=True)


# --------------------------------------------------------------------------
# Branch / worktree override resolution
# --------------------------------------------------------------------------


def test_explicit_branch_override_is_used(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    task = _write_card(
        tmp_path, base_branch=base_branch, repository=str(git_repo), branch="feature/my-explicit-branch"
    )

    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})

    assert plan.launchable, plan.blockers
    assert plan.branch == "feature/my-explicit-branch"
    assert plan.requested_branch == "feature/my-explicit-branch"
    assert plan.branch_source == portfolio_launch.SOURCE_CARD_OVERRIDE


def test_explicit_worktree_override_is_used(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    override_path = tmp_path / "my-explicit-worktree"
    task = _write_card(
        tmp_path, base_branch=base_branch, repository=str(git_repo), worktree=str(override_path)
    )

    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})

    assert plan.launchable, plan.blockers
    assert plan.worktree == str(override_path)
    assert plan.requested_worktree == str(override_path)
    assert plan.worktree_source == portfolio_launch.SOURCE_CARD_OVERRIDE
    assert plan.worktree_mode == portfolio_launch.WORKTREE_MODE_NEW


def test_absent_branch_falls_back_to_generated_default(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    task = _write_card(tmp_path, base_branch=base_branch, repository=str(git_repo))  # branch=None

    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})

    assert plan.branch == "task/aicc-test-001"
    assert plan.branch_source == portfolio_launch.SOURCE_GENERATED_DEFAULT
    assert plan.requested_branch is None


def test_absent_worktree_falls_back_to_generated_default(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    task = _write_card(tmp_path, base_branch=base_branch, repository=str(git_repo))  # worktree=None

    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})

    assert plan.worktree == str(portfolio_worktrees_root / "aicc-test-001")
    assert plan.worktree_source == portfolio_launch.SOURCE_GENERATED_DEFAULT
    assert plan.requested_worktree is None


def test_whitespace_only_branch_override_is_blocked_not_silently_defaulted(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    task = _write_card(tmp_path, base_branch=base_branch, repository=str(git_repo), branch="   ")

    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})

    assert not plan.launchable
    assert plan.branch == ""
    assert any("branch" in b for b in plan.blockers)


def test_whitespace_only_worktree_override_is_blocked_not_silently_defaulted(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    task = _write_card(tmp_path, base_branch=base_branch, repository=str(git_repo), worktree="   ")

    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})

    assert not plan.launchable
    assert plan.worktree == ""
    assert any("worktree" in b for b in plan.blockers)


def test_malformed_branch_override_is_blocked(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    task = _write_card(tmp_path, base_branch=base_branch, repository=str(git_repo), branch="bad..name")

    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})

    assert not plan.launchable
    assert any("недопустим" in b for b in plan.blockers)


def test_relative_worktree_override_is_blocked_as_ambiguous(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    task = _write_card(tmp_path, base_branch=base_branch, repository=str(git_repo), worktree="relative/path")

    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})

    assert not plan.launchable
    assert any("абсолютным" in b for b in plan.blockers)


def test_existing_valid_worktree_is_accepted_and_causes_no_worktree_add(
    git_repo, tmp_path, fake_claude, portfolio_worktrees_root
):
    existing_path = tmp_path / "existing-worktree"
    _add_worktree(git_repo, path=existing_path, branch="existing-branch")
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"

    base_branch = _current_branch(git_repo)
    task = _write_card(
        tmp_path, base_branch=base_branch, repository=str(git_repo),
        branch="existing-branch", worktree=str(existing_path),
    )

    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})
    assert plan.launchable, plan.blockers
    assert plan.worktree_mode == portfolio_launch.WORKTREE_MODE_EXISTING

    worktrees_before = git_info.get_worktrees(git_repo)
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    result = portfolio_launch.launch_portfolio_task(
        tmp_path, task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)},
        execution_center_api=api, confirmed=True,
    )

    try:
        assert result.launched is True, result.message
        # No new worktree was created — `git worktree add` was never called.
        assert git_info.get_worktrees(git_repo) == worktrees_before
    finally:
        if result.run_id:
            api.request_cancel(result.run_id, confirmed=True)


def test_existing_worktree_on_wrong_branch_is_blocked(git_repo, tmp_path, portfolio_worktrees_root):
    existing_path = tmp_path / "existing-worktree"
    _add_worktree(git_repo, path=existing_path, branch="some-other-branch")

    base_branch = _current_branch(git_repo)
    task = _write_card(
        tmp_path, base_branch=base_branch, repository=str(git_repo),
        branch="a-different-expected-branch", worktree=str(existing_path),
    )

    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})
    assert not plan.launchable
    assert any("сменил ветку" in b or "на ветке" in b for b in plan.blockers)


def test_existing_worktree_for_wrong_repository_is_blocked(git_repo, tmp_path, portfolio_worktrees_root):
    other_repo = tmp_path / "other-repo"
    other_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=other_repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=other_repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=other_repo, check=True)
    (other_repo / "f.txt").write_text("x")
    subprocess.run(["git", "add", "f.txt"], cwd=other_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=other_repo, check=True)

    existing_path = tmp_path / "existing-worktree-other-repo"
    _add_worktree(other_repo, path=existing_path, branch="some-branch")

    base_branch = _current_branch(git_repo)
    task = _write_card(
        tmp_path, base_branch=base_branch, repository=str(git_repo),
        branch="some-branch", worktree=str(existing_path),
    )

    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})
    assert not plan.launchable
    assert any("другому репозиторию" in b for b in plan.blockers)


def test_dirty_existing_worktree_is_blocked(git_repo, tmp_path, portfolio_worktrees_root):
    existing_path = tmp_path / "existing-worktree"
    _add_worktree(git_repo, path=existing_path, branch="existing-branch")
    (existing_path / "dirty.txt").write_text("uncommitted")

    base_branch = _current_branch(git_repo)
    task = _write_card(
        tmp_path, base_branch=base_branch, repository=str(git_repo),
        branch="existing-branch", worktree=str(existing_path),
    )

    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})
    assert not plan.launchable
    assert any("не чист" in b for b in plan.blockers)


def test_existing_worktree_is_never_removed_during_rollback(git_repo, tmp_path, monkeypatch, portfolio_worktrees_root):
    existing_path = tmp_path / "existing-worktree"
    _add_worktree(git_repo, path=existing_path, branch="existing-branch")

    base_branch = _current_branch(git_repo)
    task = _write_card(
        tmp_path, base_branch=base_branch, repository=str(git_repo),
        branch="existing-branch", worktree=str(existing_path),
    )
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")

    def failing_launch_ready(root, entries, tasks, tasks_by_id, project_configs, execution_center_api, *, entry_ids=None):
        updated = [dict(e) for e in entries]
        results = [
            execution_queue.LaunchAttemptResult(entry_ids[0], task.task_id, False, message="forced failure for test")
        ]
        return updated, results

    monkeypatch.setattr(portfolio_launch.execution_queue, "launch_ready", failing_launch_ready)

    result = portfolio_launch.launch_portfolio_task(
        tmp_path, task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)},
        execution_center_api=api, confirmed=True,
    )

    assert result.launched is False
    # The pre-existing worktree and its branch must both survive untouched.
    assert existing_path.is_dir()
    assert "existing-branch" in git_info.get_branches(git_repo)
    assert any(wt.get("path") == str(existing_path.resolve()) for wt in git_info.get_worktrees(git_repo))


def test_generated_worktree_and_branch_are_removed_during_rollback_on_failure(
    git_repo, tmp_path, monkeypatch, portfolio_worktrees_root
):
    base_branch = _current_branch(git_repo)
    task = _write_card(tmp_path, base_branch=base_branch, repository=str(git_repo))
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")

    def failing_launch_ready(root, entries, tasks, tasks_by_id, project_configs, execution_center_api, *, entry_ids=None):
        updated = [dict(e) for e in entries]
        results = [
            execution_queue.LaunchAttemptResult(entry_ids[0], task.task_id, False, message="forced failure for test")
        ]
        return updated, results

    monkeypatch.setattr(portfolio_launch.execution_queue, "launch_ready", failing_launch_ready)

    result = portfolio_launch.launch_portfolio_task(
        tmp_path, task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)},
        execution_center_api=api, confirmed=True,
    )

    assert result.launched is False
    assert not Path(result.plan.worktree).exists()
    assert result.plan.branch not in git_info.get_branches(git_repo)


def test_attaching_existing_branch_to_new_worktree_does_not_delete_branch_on_rollback(
    git_repo, tmp_path, monkeypatch, portfolio_worktrees_root
):
    """A card explicitly names a branch that already exists (but has no
    worktree of its own yet) — this module attaches it to a brand-new
    worktree rather than treating the pre-existing branch as a conflict. On
    rollback, the newly created worktree is removed but the pre-existing
    branch itself must survive."""
    subprocess.run(["git", "branch", "pre-existing-branch"], cwd=git_repo, check=True)
    base_branch = _current_branch(git_repo)
    task = _write_card(
        tmp_path, base_branch=base_branch, repository=str(git_repo), branch="pre-existing-branch"
    )
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")

    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})
    assert plan.launchable, plan.blockers
    assert plan.worktree_mode == portfolio_launch.WORKTREE_MODE_NEW
    assert any("привязана" in w for w in plan.warnings)

    def failing_launch_ready(root, entries, tasks, tasks_by_id, project_configs, execution_center_api, *, entry_ids=None):
        updated = [dict(e) for e in entries]
        results = [
            execution_queue.LaunchAttemptResult(entry_ids[0], task.task_id, False, message="forced failure for test")
        ]
        return updated, results

    monkeypatch.setattr(portfolio_launch.execution_queue, "launch_ready", failing_launch_ready)

    result = portfolio_launch.launch_portfolio_task(
        tmp_path, task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)},
        execution_center_api=api, confirmed=True,
    )

    assert result.launched is False
    assert not Path(result.plan.worktree).exists()  # the new worktree was rolled back
    assert "pre-existing-branch" in git_info.get_branches(git_repo)  # the branch was not


def test_prompt_uses_resolved_branch_and_worktree(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    override_worktree = tmp_path / "explicit-worktree"
    task = _write_card(
        tmp_path, base_branch=base_branch, repository=str(git_repo),
        branch="feature/explicit", worktree=str(override_worktree),
    )
    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})

    prompt = portfolio_launch.build_agent_prompt(task, plan)

    assert "feature/explicit" in prompt
    assert str(override_worktree) in prompt


def test_prompt_identifies_existing_worktree_mode(git_repo, tmp_path, portfolio_worktrees_root):
    existing_path = tmp_path / "existing-worktree"
    _add_worktree(git_repo, path=existing_path, branch="existing-branch")
    base_branch = _current_branch(git_repo)
    task = _write_card(
        tmp_path, base_branch=base_branch, repository=str(git_repo),
        branch="existing-branch", worktree=str(existing_path),
    )
    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})
    assert plan.worktree_mode == portfolio_launch.WORKTREE_MODE_EXISTING

    prompt = portfolio_launch.build_agent_prompt(task, plan)

    assert "УЖЕ СУЩЕСТВОВАЛ" in prompt
    assert "НЕ переключай ветку" in prompt


def test_dry_run_exposes_resolution_source(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    task = _write_card(tmp_path, base_branch=base_branch, repository=str(git_repo), branch="feature/explicit")

    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})

    assert plan.branch_source == portfolio_launch.SOURCE_CARD_OVERRIDE
    assert plan.worktree_source == portfolio_launch.SOURCE_GENERATED_DEFAULT


def test_duplicate_conflict_detected_via_resolved_branch(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    registry = {
        "AICC-OTHER": {"branch": "feature/shared", "worktree": str(tmp_path / "somewhere-else")}
    }
    task = _write_card(tmp_path, base_branch=base_branch, repository=str(git_repo), branch="feature/shared")

    plan = portfolio_launch.build_launch_plan(
        task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)}, registry=registry
    )

    assert not plan.launchable
    assert any("AICC-OTHER" in b for b in plan.blockers)


def test_duplicate_conflict_detected_via_resolved_worktree(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    shared_worktree = tmp_path / "shared-worktree-path"
    registry = {
        "AICC-OTHER": {"branch": "task/aicc-other", "worktree": str(shared_worktree)}
    }
    task = _write_card(tmp_path, base_branch=base_branch, repository=str(git_repo), worktree=str(shared_worktree))

    plan = portfolio_launch.build_launch_plan(
        task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)}, registry=registry
    )

    assert not plan.launchable
    assert any("AICC-OTHER" in b for b in plan.blockers)


def test_batch_detects_two_cards_requesting_the_same_branch(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    task_a = _write_card(
        tmp_path, task_id="AICC-DUP-A", base_branch=base_branch, repository=str(git_repo), branch="feature/shared"
    )
    task_b = _write_card(
        tmp_path, task_id="AICC-DUP-B", base_branch=base_branch, repository=str(git_repo), branch="feature/shared"
    )

    plans = portfolio_launch.build_batch_plan(
        [task_a, task_b], tasks_by_id={}, repository_paths={"AICC": str(git_repo)}
    )
    conflicts = portfolio_launch._detect_batch_conflicts(plans)

    assert "AICC-DUP-A" in conflicts
    assert "AICC-DUP-B" in conflicts


def test_batch_detects_two_cards_requesting_the_same_worktree(git_repo, tmp_path, portfolio_worktrees_root):
    base_branch = _current_branch(git_repo)
    shared_worktree = tmp_path / "shared-worktree"
    task_a = _write_card(
        tmp_path, task_id="AICC-DUP-A", base_branch=base_branch, repository=str(git_repo),
        worktree=str(shared_worktree),
    )
    task_b = _write_card(
        tmp_path, task_id="AICC-DUP-B", base_branch=base_branch, repository=str(git_repo),
        worktree=str(shared_worktree),
    )

    plans = portfolio_launch.build_batch_plan(
        [task_a, task_b], tasks_by_id={}, repository_paths={"AICC": str(git_repo)}
    )
    conflicts = portfolio_launch._detect_batch_conflicts(plans)

    assert "AICC-DUP-A" in conflicts
    assert "AICC-DUP-B" in conflicts


def test_launch_batch_skips_tasks_with_conflicting_overrides(git_repo, tmp_path, fake_claude, portfolio_worktrees_root):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    base_branch = _current_branch(git_repo)
    shared_worktree = tmp_path / "shared-worktree"
    task_a = _write_card(
        tmp_path, task_id="AICC-DUP-A", base_branch=base_branch, repository=str(git_repo),
        worktree=str(shared_worktree),
    )
    task_b = _write_card(
        tmp_path, task_id="AICC-DUP-B", base_branch=base_branch, repository=str(git_repo),
        worktree=str(shared_worktree),
    )
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")

    results = portfolio_launch.launch_batch(
        tmp_path, [task_a, task_b], tasks_by_id={}, repository_paths={"AICC": str(git_repo)},
        execution_center_api=api, confirmed=True,
    )

    assert all(not r.launched for r in results)
    assert not shared_worktree.exists()


def test_real_aicc_ui_001_card_plans_according_to_declared_intent(tmp_path, portfolio_worktrees_root, git_repo):
    """The real `AICC-UI-001` card (the case that motivated this
    remediation) declares `worktree: "~/Projects/ai-command-center"` — the
    same path as its own `repository` field — expressing "do this work in
    the primary worktree, do not create a new one." With override support,
    planning against that exact declared intent must resolve to the
    project's own repository path in `existing` mode, not a freshly
    generated `worktrees/aicc-ui-001` directory."""
    task = _write_card(
        tmp_path,
        task_id="AICC-UI-001",
        base_branch=_current_branch(git_repo),
        repository=str(git_repo),
        branch="fix/kanban-full-width-columns",
        worktree=str(git_repo),
    )

    plan = portfolio_launch.build_launch_plan(task, tasks_by_id={}, repository_paths={"AICC": str(git_repo)})

    assert plan.requested_worktree == str(git_repo)
    assert plan.worktree_source == portfolio_launch.SOURCE_CARD_OVERRIDE
    assert plan.worktree_mode == portfolio_launch.WORKTREE_MODE_EXISTING
    assert Path(plan.worktree) == git_repo.resolve()
    # Blocked here only because the primary worktree is still on its default
    # branch, not `fix/kanban-full-width-columns` — exactly the "existing
    # worktree on the wrong branch" guard doing its job for this real card;
    # an operator would create that branch (or check it out) before AICC
    # Command Center could safely launch against the primary worktree.
    assert not plan.launchable
    assert any("на ветке" in b or "сменил ветку" in b for b in plan.blockers)
