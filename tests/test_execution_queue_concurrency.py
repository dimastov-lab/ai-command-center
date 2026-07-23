"""Cross-process locking coverage for `data/execution_queue.json`.

Same class of bug — and same fix shape — as
`tests/test_task_import_concurrency.py` and
`tests/test_portfolio_registry_concurrency.py`: `storage.atomic_write_json`'s
temp-file + `os.replace` makes a *single* queue write atomic but does nothing
to stop two concurrent read-modify-write *cycles* from losing one another's
update. `execution_queue` now runs every such cycle inside `queue_lock` (a thin
wrapper over the shared `storage.file_lock` OS advisory-lock primitive).

Structure mirrors those two files deliberately:

- `_unsafe_reevaluate` reproduces the *pre-fix* pattern (`load_queue` -> mutate
  -> `save_queue`, no lock spanning the pair) to prove the race is real.
- Thread tests prove the lock serializes callers inside one interpreter.
- The `multiprocessing` (spawn context) tests are the ones that actually prove
  *cross-process* exclusion — two genuinely separate OS processes, no shared
  interpreter, no GIL to accidentally save us — plus the crashed-holder case
  proving a dead lock holder never wedges a later caller.

Failure-injection tests live at the bottom: an exception mid-cycle must release
the lock, and a lock genuinely held elsewhere must surface a bounded
`LockTimeoutError` rather than hang.

Note on `root`: `storage.resolve_data_dir` honours `AICC_DATA_DIR` (set by the
test conftest), so every `root` argument in this session resolves to the same
isolated data dir regardless of the `tmp_path` passed — the queue file and its
lock file are shared across all callers here, which is exactly what a
cross-process test needs.
"""

from __future__ import annotations

import multiprocessing
import os
import threading
import time
from pathlib import Path

import pytest

from command_center import execution_queue, models, storage


def _task(task_id: str, **overrides) -> dict:
    task = {
        "id": task_id,
        "project": "AIOS",
        "title": f"Task {task_id}",
        "status": "Backlog",
        "priority": "Medium",
        "depends_on": [],
    }
    task.update(models.default_task_execution_fields())
    task.update(models.default_task_workflow_fields())
    task.update(overrides)
    return task


# --------------------------------------------------------------------------
# The bug this module guards against: two concurrent read-modify-write cycles
# on `execution_queue.json`, without a lock spanning the whole cycle, losing
# one enqueue. `_unsafe_reevaluate` deliberately bypasses the locked
# `enqueue_and_persist` and reproduces the pre-fix pattern — not production
# code, exists only to prove the race is real and that the fix closes it.
# --------------------------------------------------------------------------


def _unsafe_enqueue(root: Path, task: dict, barrier: threading.Barrier) -> None:
    tasks_by_id = {task["id"]: task}
    existing = execution_queue.load_queue(root)
    barrier.wait()  # force both threads to have read the same pre-write state
    updated = execution_queue.enqueue(existing, task, tasks_by_id)
    execution_queue.save_queue(root, updated)


def test_unsafe_read_modify_write_pattern_loses_entries(tmp_path):
    barrier = threading.Barrier(2)
    threads = [
        threading.Thread(target=_unsafe_enqueue, args=(tmp_path, _task("UNSAFE-A"), barrier)),
        threading.Thread(target=_unsafe_enqueue, args=(tmp_path, _task("UNSAFE-B"), barrier)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    stored = execution_queue.load_queue(tmp_path)
    assert len(stored) < 2, "expected the unguarded read-modify-write to lose one enqueue"


# --------------------------------------------------------------------------
# The fix: `enqueue_and_persist` / `reevaluate_and_persist` hold `queue_lock`
# across their whole load->save cycle.
# --------------------------------------------------------------------------


def test_two_concurrent_enqueues_both_survive_threaded(tmp_path):
    start = threading.Barrier(2)
    errors: list[Exception] = []

    def run(task_id: str) -> None:
        task = _task(task_id)
        start.wait()
        try:
            execution_queue.enqueue_and_persist(tmp_path, task, {task_id: task})
        except Exception as exc:  # noqa: BLE001 - surfaced via `errors`
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=("THREAD-A",)),
        threading.Thread(target=run, args=("THREAD-B",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not errors, errors
    stored = execution_queue.load_queue(tmp_path)
    ids = sorted(e["task_id"] for e in stored)
    assert ids == ["THREAD-A", "THREAD-B"], "an enqueue was lost despite the lock"


def test_many_concurrent_enqueues_all_survive_and_json_stays_valid(tmp_path):
    task_ids = [f"BULK-{i}" for i in range(12)]
    start = threading.Barrier(len(task_ids))

    def run(task_id: str) -> None:
        task = _task(task_id)
        start.wait()
        execution_queue.enqueue_and_persist(tmp_path, task, {task_id: task})

    threads = [threading.Thread(target=run, args=(tid,)) for tid in task_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    import json

    queue_file = execution_queue.queue_file_path(tmp_path)
    data = json.loads(queue_file.read_text(encoding="utf-8"))  # raises if partially written
    assert isinstance(data, list)
    assert sorted(e["task_id"] for e in data) == sorted(task_ids)
    assert len(data) == len(task_ids)


def test_reevaluate_does_not_clobber_a_concurrent_enqueue(tmp_path):
    """A `reevaluate_and_persist` running concurrently with an `enqueue_and_persist`
    must end with *both* the re-evaluated pre-existing entry and the newly
    enqueued one — the lock forces the two cycles to serialize instead of the
    reevaluate writing back its stale snapshot over the enqueue."""
    seed = _task("SEED", status="Backlog")
    execution_queue.enqueue_and_persist(tmp_path, seed, {"SEED": seed})

    tasks_by_id = {"SEED": seed}
    start = threading.Barrier(2)

    def reevaluate() -> None:
        start.wait()
        execution_queue.reevaluate_and_persist(tmp_path, tasks_by_id)

    def add() -> None:
        newcomer = _task("NEW")
        start.wait()
        execution_queue.enqueue_and_persist(tmp_path, newcomer, {"NEW": newcomer})

    threads = [threading.Thread(target=reevaluate), threading.Thread(target=add)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    stored = execution_queue.load_queue(tmp_path)
    assert sorted(e["task_id"] for e in stored) == ["NEW", "SEED"]


def test_no_leftover_temp_files_after_concurrent_writes(tmp_path):
    task_ids = [f"TMP-{i}" for i in range(6)]
    start = threading.Barrier(len(task_ids))

    def run(task_id: str) -> None:
        task = _task(task_id)
        start.wait()
        execution_queue.enqueue_and_persist(tmp_path, task, {task_id: task})

    threads = [threading.Thread(target=run, args=(tid,)) for tid in task_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    data_dir = execution_queue.queue_file_path(tmp_path).parent
    assert list(data_dir.glob(".execution_queue.json_*.tmp")) == []


# --------------------------------------------------------------------------
# Cross-process exclusion — the reason this uses an OS advisory file lock
# (`storage.file_lock`) rather than `threading.Lock`, which would only
# serialize threads inside one interpreter and do nothing for two separate
# `streamlit run app.py` processes (or the future Supervisor poller) sharing
# the same `data/execution_queue.json`.
# --------------------------------------------------------------------------


def _mp_enqueue_worker(data_dir: str, task_ids: list[str], barrier) -> None:
    os.environ["AICC_DATA_DIR"] = data_dir

    from command_center import execution_queue as _eq  # re-imported fresh in this process
    from command_center import models as _models

    barrier.wait()
    for tid in task_ids:
        task = {
            "id": tid,
            "project": "AIOS",
            "title": f"Task {tid}",
            "status": "Backlog",
            "priority": "Medium",
            "depends_on": [],
        }
        task.update(_models.default_task_execution_fields())
        task.update(_models.default_task_workflow_fields())
        _eq.enqueue_and_persist(Path(data_dir), task, {tid: task})


def test_enqueue_provides_real_cross_process_exclusion(tmp_path):
    """Two genuinely separate OS processes, each enqueuing a batch, started at
    the same instant via a real `multiprocessing.Barrier`. After both finish
    the queue holds the union of both batches, nothing is lost, the JSON is
    valid, and there are no duplicate ids — what a same-process thread test
    alone cannot prove (no shared interpreter, no GIL)."""
    data_dir = os.environ["AICC_DATA_DIR"]
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)

    batch_a = ["MP-A-1", "MP-A-2", "MP-A-3"]
    batch_b = ["MP-B-1", "MP-B-2", "MP-B-3"]

    processes = [
        ctx.Process(target=_mp_enqueue_worker, args=(data_dir, batch_a, barrier)),
        ctx.Process(target=_mp_enqueue_worker, args=(data_dir, batch_b, barrier)),
    ]
    for p in processes:
        p.start()
    for p in processes:
        p.join(timeout=30)
        assert p.exitcode == 0, f"worker process failed (exitcode={p.exitcode})"

    stored = execution_queue.load_queue(tmp_path)
    ids = [e["task_id"] for e in stored]

    assert set(ids) == set(batch_a) | set(batch_b), "a batch was lost across processes"
    assert len(ids) == len(set(ids)), "duplicate ids after concurrent cross-process enqueue"

    import json

    queue_file = execution_queue.queue_file_path(tmp_path)
    data = json.loads(queue_file.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 6


def _mp_hold_lock_then_die(data_dir: str, ready_event, lock_held_ack) -> None:
    """Acquires the queue lock and then hard-exits *without* releasing it
    explicitly — simulating a crash while holding the lock, to prove a
    subsequent caller is never permanently blocked by a dead holder."""
    os.environ["AICC_DATA_DIR"] = data_dir
    from pathlib import Path as _Path

    from command_center import execution_queue as _eq
    from command_center import storage as _storage

    lock_path = _eq.queue_lock_path(_Path(data_dir))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    if handle.seek(0, os.SEEK_END) == 0:
        handle.write(b"0")
        handle.flush()
    assert _storage._try_acquire_lock(handle)
    lock_held_ack.set()
    ready_event.wait(timeout=10)
    os._exit(1)  # hard exit — no atexit/finally handlers, no lock release call


def test_a_crashed_lock_holder_never_permanently_blocks_a_later_caller(tmp_path):
    """The queue lock is an OS advisory lock released by the kernel the instant
    the holding process's fd closes — including on a crash — so there is no
    "stale lock" state to clear. A real OS process crashes while holding it, and
    a subsequent `enqueue_and_persist` still succeeds promptly."""
    data_dir = os.environ["AICC_DATA_DIR"]
    ctx = multiprocessing.get_context("spawn")
    lock_held_ack = ctx.Event()
    ready_event = ctx.Event()

    holder = ctx.Process(target=_mp_hold_lock_then_die, args=(data_dir, ready_event, lock_held_ack))
    holder.start()
    assert lock_held_ack.wait(timeout=10), "lock-holding process never signalled it had the lock"

    ready_event.set()
    holder.join(timeout=10)
    assert holder.exitcode == 1

    task = _task("AFTER-CRASH")
    start = time.monotonic()
    execution_queue.enqueue_and_persist(tmp_path, task, {"AFTER-CRASH": task})
    elapsed = time.monotonic() - start
    assert [e["task_id"] for e in execution_queue.load_queue(tmp_path)] == ["AFTER-CRASH"]
    assert elapsed < 5.0, "enqueue after a crashed lock holder should not wait out the full timeout"


# --------------------------------------------------------------------------
# Failure injection
# --------------------------------------------------------------------------


def test_lock_is_released_after_exception_inside_the_critical_section(tmp_path, monkeypatch):
    """A failure mid-cycle must not leave the queue lock held forever."""
    seed = _task("SEED")
    execution_queue.enqueue_and_persist(tmp_path, seed, {"SEED": seed})

    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure mid-cycle")

    monkeypatch.setattr(execution_queue, "save_queue", boom)
    with pytest.raises(RuntimeError):
        execution_queue.reevaluate_and_persist(tmp_path, {"SEED": seed})
    monkeypatch.undo()

    # Must be immediately re-acquirable with a short timeout — would fail if the
    # exception above had left the lock held.
    task = _task("AFTER-EXC")
    with execution_queue.queue_lock(tmp_path, timeout=2.0):
        pass
    execution_queue.enqueue_and_persist(tmp_path, task, {"AFTER-EXC": task})
    assert sorted(e["task_id"] for e in execution_queue.load_queue(tmp_path)) == ["AFTER-EXC", "SEED"]


def test_queue_lock_times_out_with_a_clear_error_instead_of_hanging(tmp_path):
    """If the lock is genuinely held elsewhere, a caller gets a bounded,
    descriptive `LockTimeoutError` — never an indefinite hang. Proven with a
    tight explicit timeout so the assertion stays fast and meaningful."""
    lock_path = execution_queue.queue_lock_path(tmp_path)
    with storage.file_lock(lock_path, timeout=5.0):
        start = time.monotonic()
        with pytest.raises(storage.LockTimeoutError):
            with execution_queue.queue_lock(tmp_path, timeout=0.3):
                pass
        elapsed = time.monotonic() - start
        assert elapsed < 3.0, "queue_lock must not block far past its own timeout"

    # Once released, the lock is immediately re-acquirable — no manual recovery.
    task = _task("AFTER-TIMEOUT")
    execution_queue.enqueue_and_persist(tmp_path, task, {"AFTER-TIMEOUT": task})
    assert [e["task_id"] for e in execution_queue.load_queue(tmp_path)] == ["AFTER-TIMEOUT"]


def test_launch_ready_commit_merges_onto_a_concurrent_write(tmp_path, git_repo, fake_claude):
    """`launch_ready` re-reads and merges under the lock: an entry enqueued by
    another writer *after* the caller loaded its `entries` snapshot but before
    the launch commits must survive, alongside the freshly-launched entry."""
    from command_center.runtime import api as runtime_api

    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    task = _task("LAUNCH", workspace_path=str(git_repo))
    tasks_by_id = {"LAUNCH": task}
    entries = execution_queue.enqueue([], task, tasks_by_id)
    execution_queue.save_queue(tmp_path, entries)

    # Simulate a concurrent writer that landed a new entry on disk between the
    # caller's `load`/snapshot (`entries` above) and `launch_ready`'s commit.
    concurrent = _task("CONCURRENT")
    execution_queue.enqueue_and_persist(tmp_path, concurrent, {"CONCURRENT": concurrent})

    api = runtime_api.ExecutionCenterAPI(db_path=git_repo.parent / "runtime.db")
    updated, results = execution_queue.launch_ready(
        tmp_path, entries, [task], tasks_by_id, {"AIOS": {"repository_path": str(git_repo)}}, api
    )

    assert results[0].launched is True
    stored = {e["task_id"]: e for e in execution_queue.load_queue(tmp_path)}
    # The concurrent writer's entry was NOT clobbered by launch_ready's snapshot...
    assert "CONCURRENT" in stored
    # ...and the launched entry reflects its new state.
    assert stored["LAUNCH"]["state"] == execution_queue.STATE_LAUNCHED
    assert stored["LAUNCH"]["run_id"] == results[0].run_id

    api.request_cancel(results[0].run_id, confirmed=True)


def test_launch_commit_preserves_concurrent_deletion_of_unrelated_entry(tmp_path):
    launch_task = _task("LAUNCH")
    unrelated_task = _task("DELETE-ME")
    tasks = {"LAUNCH": launch_task, "DELETE-ME": unrelated_task}
    base = execution_queue.enqueue([], launch_task, tasks)
    base = execution_queue.enqueue(base, unrelated_task, tasks)
    execution_queue.save_queue(tmp_path, base)

    unrelated = next(e for e in base if e["task_id"] == "DELETE-ME")
    execution_queue.dequeue_and_persist(tmp_path, unrelated["id"])
    launched = next(e for e in base if e["task_id"] == "LAUNCH")

    merged = execution_queue._commit_launch_results(
        tmp_path,
        base,
        {launched["id"]: {"state": execution_queue.STATE_LAUNCHED, "run_id": "run-1"}},
    )

    assert [e["task_id"] for e in merged] == ["LAUNCH"]
    assert merged[0]["state"] == execution_queue.STATE_LAUNCHED


def test_launch_commit_restores_only_a_concurrently_deleted_launched_entry(tmp_path):
    task = _task("LAUNCH")
    base = execution_queue.enqueue([], task, {"LAUNCH": task})
    execution_queue.save_queue(tmp_path, base)
    entry = base[0]
    execution_queue.dequeue_and_persist(tmp_path, entry["id"])

    merged = execution_queue._commit_launch_results(
        tmp_path,
        base,
        {entry["id"]: {"state": execution_queue.STATE_LAUNCHED, "run_id": "run-1"}},
    )

    assert len(merged) == 1
    assert merged[0]["id"] == entry["id"]
    assert merged[0]["state"] == execution_queue.STATE_LAUNCHED
