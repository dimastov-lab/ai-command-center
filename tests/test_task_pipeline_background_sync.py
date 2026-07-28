"""Background sync daemon (audit MAJOR-8): an opt-in poller that runs `tick`
off any open page, so a headless/unattended host keeps the run->task Kanban
projection current instead of freezing until someone opens the board.

`tick` itself is monkeypatched to a fast fake in every test — these cover the
daemon's lifecycle (starts, is idempotent, survives a failing tick, re-reads the
project configs each pass, stops), not `tick`'s own (separately tested) work.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from command_center import task_pipeline as tp


@pytest.fixture(autouse=True)
def _stop_background_sync_after():
    # The daemon is a module-level singleton; make sure no test leaks a running
    # thread into the next one.
    yield
    tp.stop_background_sync()
    if tp._background_sync_thread is not None:
        tp._background_sync_thread.join(timeout=2.0)
    tp._background_sync_thread = None
    tp._background_sync_stop = None


def test_background_sync_runs_tick_until_stopped(monkeypatch):
    ran = threading.Event()
    calls: list = []

    def fake_tick(root, api, cfgs, **kwargs):
        calls.append((root, api, cfgs))
        ran.set()
        return None

    monkeypatch.setattr(tp, "tick", fake_tick)
    tp.start_background_sync(Path("/root"), "api-obj", lambda: {"AICC": {}}, interval_seconds=0.01)

    assert ran.wait(2.0), "background sync never ran a tick"
    assert calls[0][0] == Path("/root")
    assert calls[0][1] == "api-obj"
    assert calls[0][2] == {"AICC": {}}


def test_background_sync_is_idempotent(monkeypatch):
    monkeypatch.setattr(tp, "tick", lambda *a, **k: None)
    tp.start_background_sync(Path("/root"), None, lambda: {}, interval_seconds=5.0)
    first = tp._background_sync_thread
    tp.start_background_sync(Path("/root"), None, lambda: {}, interval_seconds=5.0)
    assert tp._background_sync_thread is first  # no second thread spawned


def test_background_sync_survives_a_failing_tick(monkeypatch):
    ran_again = threading.Event()
    count = {"n": 0}

    def flaky_tick(*a, **k):
        count["n"] += 1
        if count["n"] == 1:
            raise RuntimeError("transient tick failure")
        ran_again.set()
        return None

    monkeypatch.setattr(tp, "tick", flaky_tick)
    tp.start_background_sync(Path("/root"), None, lambda: {}, interval_seconds=0.01)
    assert ran_again.wait(2.0), "poller died after a single failing tick"


def test_background_sync_reads_project_configs_fresh_each_tick(monkeypatch):
    seen: list = []
    got_two = threading.Event()
    versions = iter([{"v": 1}, {"v": 2}])

    def fake_tick(root, api, cfgs, **kwargs):
        seen.append(cfgs)
        if len(seen) >= 2:
            got_two.set()
        return None

    def provider():
        return next(versions, {"v": 0})

    monkeypatch.setattr(tp, "tick", fake_tick)
    tp.start_background_sync(Path("/root"), None, provider, interval_seconds=0.01)
    assert got_two.wait(2.0)
    assert seen[0] != seen[1]  # provider re-invoked per tick, not captured once


def test_stop_is_a_noop_when_never_started():
    # Never started in this test (autouse fixture reset the globals) — stopping
    # must not raise.
    tp.stop_background_sync()
