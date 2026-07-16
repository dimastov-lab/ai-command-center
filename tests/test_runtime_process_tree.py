"""F4: process-group cancellation must terminate an entire process tree —
parent -> child -> grandchild — not just the direct child the Supervisor
`Popen`'d, and must never leave any level orphaned.
"""

from __future__ import annotations

import os
import time

from command_center.runtime import db, identity, supervisor


def _wait_for_tree_pids(pidfile_base: str, timeout: float = 10.0) -> dict[str, int]:
    deadline = time.monotonic() + timeout
    paths = {role: f"{pidfile_base}.{role}" for role in ("parent", "child", "grandchild")}

    while time.monotonic() < deadline:
        if all(os.path.exists(p) for p in paths.values()):
            pids = {}
            for role, path in paths.items():
                with open(path) as handle:
                    pids[role] = int(handle.read().strip())
            return pids
        time.sleep(0.05)
    raise TimeoutError(f"process tree pid files never all appeared: {pidfile_base}")


def test_grandchild_process_tree_terminates_on_process_group_cancellation(
    git_repo, configure_project_repo, fake_claude_tree
):
    env_overrides, pidfile_base = fake_claude_tree
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()

    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True
    )

    pids = _wait_for_tree_pids(pidfile_base)
    assert pids["parent"] == run["pid"], "the Supervisor-tracked pid must be the top-level process"
    assert identity.process_exists(pids["parent"])
    assert identity.process_exists(pids["child"])
    assert identity.process_exists(pids["grandchild"])

    result = sup.cancel(run["id"], confirmed=True, grace_seconds=5)

    assert result["state"] == "CANCELLED"
    assert identity.process_exists(pids["parent"]) is False, "parent must not survive cancellation"
    assert identity.process_exists(pids["child"]) is False, "child must not survive cancellation"
    assert identity.process_exists(pids["grandchild"]) is False, "grandchild must not survive cancellation (no orphan)"

    # Output received before cancellation (the two stream-json lines the
    # parent printed before settling into its long sleep) must be retained.
    events = db.list_run_events(sup.db_path, run["id"])
    result_events = [e for e in events if e["event_type"] == "result"]
    assert result_events and result_events[0]["payload"]["result"] == "tree started"


def test_grandchild_process_tree_terminates_even_when_parent_ignores_sigterm(
    git_repo, configure_project_repo, fake_claude_tree
):
    """Even when the direct child (the process the Supervisor itself
    launched) ignores SIGTERM, forcing escalation to SIGKILL, every level of
    the tree must still be gone afterward — proving `killpg` reaches the
    whole process group, not just whichever process responds to SIGTERM."""
    env_overrides, pidfile_base = fake_claude_tree
    env_overrides["FAKE_CLAUDE_TREE_IGNORE_SIGTERM"] = "1"
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()

    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True
    )
    pids = _wait_for_tree_pids(pidfile_base)

    started = time.monotonic()
    result = sup.cancel(run["id"], confirmed=True, grace_seconds=1)
    elapsed = time.monotonic() - started

    assert result["state"] == "CANCELLED"
    assert elapsed >= 1, "SIGKILL must not fire before the grace period elapses"

    events = db.list_run_events(sup.db_path, run["id"])
    lifecycles = [e["payload"].get("lifecycle") for e in events if e["event_type"] == "lifecycle"]
    assert "cancel_sigkill_sent" in lifecycles

    for role in ("parent", "child", "grandchild"):
        assert identity.process_exists(pids[role]) is False, f"{role} must not survive a forced (SIGKILL) cancellation"
