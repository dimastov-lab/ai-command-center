"""Regression coverage for Founder Gate Major-1: the lost-update race in
`data/portfolio_launches.json` and the fix (`_registry_lock` /
`_persist_registry_entry` in `command_center.portfolio_launch`).

Every test here is deterministic — concurrency is forced with
`threading.Barrier`/`multiprocessing.Barrier`, never a bare `sleep`, so a
race either always reproduces or always doesn't.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import threading
from pathlib import Path

import pytest

from command_center import portfolio_launch


def _entry(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "project": "AICC",
        "branch": f"task/{task_id.lower()}",
        "worktree": f"/tmp/{task_id.lower()}",
    }


# --------------------------------------------------------------------------
# The bug this module guards against: two concurrent read-modify-write
# cycles on the registry losing one entry when neither is guarded by a lock
# spanning the whole cycle. `_unsafe_add_entry` deliberately reproduces the
# pre-fix `launch_portfolio_task` pattern (`load_registry` -> mutate ->
# `save_registry`, no lock around the pair) — it is not production code, it
# exists to prove the race is real and that the fix below closes it.
# --------------------------------------------------------------------------


def _unsafe_add_entry(root: Path, task_id: str, barrier: threading.Barrier) -> None:
    registry = portfolio_launch.load_registry(root)
    barrier.wait()  # force both threads to have read the same pre-write state
    registry[task_id] = _entry(task_id)
    portfolio_launch.save_registry(root, registry)


def test_unsafe_read_modify_write_pattern_loses_an_update(tmp_path):
    """Logically reproduces the Founder Gate Major-1 lost-update scenario:
    two concurrent callers that both read the registry before either writes
    will, without a lock covering the whole read-modify-write cycle,
    silently overwrite each other's entry. This is the exact bug
    `_persist_registry_entry` (tested below) closes."""
    barrier = threading.Barrier(2)
    threads = [
        threading.Thread(target=_unsafe_add_entry, args=(tmp_path, "AICC-UNSAFE-A", barrier)),
        threading.Thread(target=_unsafe_add_entry, args=(tmp_path, "AICC-UNSAFE-B", barrier)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    registry = portfolio_launch.load_registry(tmp_path)
    assert len(registry) == 1, "expected the unguarded read-modify-write to lose one entry"


# --------------------------------------------------------------------------
# The fix: `_persist_registry_entry`, backed by `_registry_lock`
# --------------------------------------------------------------------------


def test_persist_registry_entry_two_different_tasks_both_survive_concurrent_writes(tmp_path):
    start = threading.Barrier(2)
    errors: list[Exception] = []

    def run(task_id: str) -> None:
        start.wait()
        try:
            result = portfolio_launch._persist_registry_entry(tmp_path, task_id, _entry(task_id))
            assert result is None, result
        except Exception as exc:  # noqa: BLE001 - surfaced via `errors`
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(t,)) for t in ("AICC-SAFE-A", "AICC-SAFE-B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, errors
    registry = portfolio_launch.load_registry(tmp_path)
    assert set(registry.keys()) == {"AICC-SAFE-A", "AICC-SAFE-B"}


def test_persist_registry_entry_same_task_id_only_one_entry_survives(tmp_path):
    """Two concurrent attempts to register the *same* task_id (e.g. a retry
    racing the original launch) must not corrupt the registry: exactly one
    write wins and the surviving entry is well-formed, never merged or
    interleaved between the two callers."""
    start = threading.Barrier(2)

    def run(variant: str) -> None:
        start.wait()
        entry = _entry("AICC-SAME")
        entry["run_id"] = variant
        portfolio_launch._persist_registry_entry(tmp_path, "AICC-SAME", entry)

    threads = [threading.Thread(target=run, args=(v,)) for v in ("first", "second")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    registry = portfolio_launch.load_registry(tmp_path)
    assert list(registry.keys()) == ["AICC-SAME"]
    assert registry["AICC-SAME"]["run_id"] in ("first", "second")


def test_registry_lock_is_released_after_exception_in_critical_section(tmp_path):
    with pytest.raises(RuntimeError):
        with portfolio_launch._registry_lock(tmp_path, timeout=2.0):
            raise RuntimeError("boom")

    # Must be immediately re-acquirable — a short timeout here would fail if
    # the exception above had left the lock held.
    with portfolio_launch._registry_lock(tmp_path, timeout=2.0):
        pass


def test_registry_stays_valid_json_after_many_concurrent_writes(tmp_path):
    task_ids = [f"AICC-BULK-{i}" for i in range(8)]
    start = threading.Barrier(len(task_ids))

    def run(task_id: str) -> None:
        start.wait()
        portfolio_launch._persist_registry_entry(tmp_path, task_id, _entry(task_id))

    threads = [threading.Thread(target=run, args=(t,)) for t in task_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    registry_path = portfolio_launch._registry_file(tmp_path)
    data = json.loads(registry_path.read_text(encoding="utf-8"))  # raises if partially written
    assert isinstance(data, dict)
    assert set(data.keys()) == set(task_ids)


def test_no_leftover_temp_files_after_concurrent_writes(tmp_path):
    task_ids = [f"AICC-TMP-{i}" for i in range(5)]
    start = threading.Barrier(len(task_ids))

    def run(task_id: str) -> None:
        start.wait()
        portfolio_launch._persist_registry_entry(tmp_path, task_id, _entry(task_id))

    threads = [threading.Thread(target=run, args=(t,)) for t in task_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    data_dir = portfolio_launch._registry_file(tmp_path).parent
    assert list(data_dir.glob("*.tmp")) == []


def test_corrupted_registry_fails_closed_instead_of_being_silently_overwritten(tmp_path):
    registry_path = portfolio_launch._registry_file(tmp_path)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(portfolio_launch.PortfolioLaunchError):
        portfolio_launch._persist_registry_entry(tmp_path, "AICC-NEW", _entry("AICC-NEW"))

    # The corrupted content must survive untouched — never silently replaced
    # with a fresh registry containing only the new entry.
    assert registry_path.read_text(encoding="utf-8") == "{not valid json"


def test_load_registry_strict_rejects_non_dict_json(tmp_path):
    registry_path = portfolio_launch._registry_file(tmp_path)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(portfolio_launch.PortfolioLaunchError):
        portfolio_launch._load_registry_strict(tmp_path)


def test_claim_lock_still_serializes_same_task_id_under_real_thread_contention(tmp_path):
    """`_claim` (the per-task_id exclusive-create lock, unchanged by this
    fix) must still admit exactly one concurrent claimant for the same
    task_id under genuine thread contention, not just the sequential-call
    check the existing unit test already covers."""
    start = threading.Barrier(2)
    outcomes: list[bool] = []
    lock = threading.Lock()

    def run() -> None:
        start.wait()
        claimed = portfolio_launch._claim(tmp_path, "AICC-CONTENDED")
        with lock:
            outcomes.append(claimed)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert sorted(outcomes) == [False, True]
    portfolio_launch._release(tmp_path, "AICC-CONTENDED")


# --------------------------------------------------------------------------
# Cross-process exclusion — the reason this uses an OS advisory file lock
# (`fcntl.flock` / `msvcrt.locking`) rather than `threading.Lock`, which
# would only serialize threads inside one interpreter and do nothing for two
# separate `streamlit run app.py` processes (or two Portfolio launches
# triggered from different terminals) sharing the same
# `data/portfolio_launches.json`.
# --------------------------------------------------------------------------


def _mp_persist_worker(data_dir: str, task_id: str, entry: dict, barrier) -> None:
    os.environ["AICC_DATA_DIR"] = data_dir
    from command_center import portfolio_launch as pl  # re-imported fresh in this process

    barrier.wait()
    result = pl._persist_registry_entry(Path(data_dir), task_id, entry)
    assert result is None, result


def test_registry_lock_provides_real_cross_process_exclusion(tmp_path):
    """Same guarantee as the thread-based tests above, but across two real
    OS processes (`multiprocessing`, spawn context — no shared Python
    interpreter, no GIL) — proving the lock is not merely a `threading.Lock`
    in disguise."""
    data_dir = os.environ["AICC_DATA_DIR"]
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    task_entries = {"AICC-MP-A": _entry("AICC-MP-A"), "AICC-MP-B": _entry("AICC-MP-B")}

    processes = [
        ctx.Process(target=_mp_persist_worker, args=(data_dir, task_id, entry, barrier))
        for task_id, entry in task_entries.items()
    ]
    for p in processes:
        p.start()
    for p in processes:
        p.join(timeout=30)
        assert p.exitcode == 0, f"worker process failed (exitcode={p.exitcode})"

    registry = portfolio_launch.load_registry(tmp_path)
    assert set(registry.keys()) == set(task_entries.keys())
