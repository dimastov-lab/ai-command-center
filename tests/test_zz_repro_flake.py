from __future__ import annotations
import json, threading, time
from pathlib import Path
import pytest
from command_center import task_import as ti, tasks_repository
from tests.test_task_import_concurrency import _package


def test_repro(tmp_path):
    task_id_batches = [[f"BULK-{i}-1", f"BULK-{i}-2"] for i in range(8)]
    start = threading.Barrier(len(task_id_batches))
    errors = []

    def run(task_ids):
        parsed, validation = _package(*task_ids)
        start.wait()
        try:
            ti.apply_task_package(tmp_path, parsed, validation)
        except BaseException as exc:
            errors.append(repr(exc))

    threads = [threading.Thread(target=run, args=(b,)) for b in task_id_batches]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    elapsed = time.monotonic() - t0
    alive = [t.name for t in threads if t.is_alive()]
    tasks_file = tasks_repository.tasks_file_path(tmp_path)
    data = json.loads(tasks_file.read_text(encoding="utf-8"))
    expected = {tid for b in task_id_batches for tid in b}
    got = {t["id"] for t in data}
    print(f"REPRO elapsed={elapsed:.2f}s alive={alive} errors={errors} missing={sorted(expected-got)} count={len(data)}")
    assert not alive, f"threads still running after join(15): {alive}"
    assert not errors, errors
    assert got == expected
