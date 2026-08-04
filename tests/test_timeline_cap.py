"""Bound the per-task timeline (audit — perf root cause).

The Kanban store (`tasks.json`) is read and JSON-parsed on every Streamlit rerun
(and by five 2-5s fragment pollers). Measured on live data it was 7.1 MB and
~18 ms/parse — and 151% of that file was the `timeline`: 26,504 events, up to
6,067 on a single task, because `append_timeline_event` grew it without bound.
Caching the parse does not help (a deepcopy measured *slower* than the parse, and
the read path mutates the loaded dicts). The fix is to stop the unbounded growth
and heal existing bloat, which shrinks the file and the parse ~5x. Authoritative
run history remains in `runtime.db` `run_event`; this only bounds the task's
UI timeline to its most-recent events.
"""

from __future__ import annotations

from command_center import models, tasks_repository


def test_max_timeline_events_is_a_sane_bound():
    assert isinstance(models.MAX_TIMELINE_EVENTS, int)
    assert 0 < models.MAX_TIMELINE_EVENTS <= 1000


def test_append_caps_timeline_and_keeps_newest():
    cap = models.MAX_TIMELINE_EVENTS
    task = {"timeline": [models.new_timeline_event("e", str(i)) for i in range(cap + 25)]}
    models.append_timeline_event(task, "new", "latest")
    assert len(task["timeline"]) == cap
    assert task["timeline"][-1]["message"] == "latest"  # the newest event is kept
    assert task["timeline"][0]["message"] != "0"  # the oldest events are dropped


def test_append_below_cap_is_unchanged():
    task: dict = {"timeline": []}
    models.append_timeline_event(task, "e", "m")
    models.append_timeline_event(task, "e", "m2")
    assert len(task["timeline"]) == 2


def test_normalize_task_heals_an_existing_bloated_timeline():
    cap = models.MAX_TIMELINE_EVENTS
    big = [{"id": str(i), "ts": "", "type": "e", "message": str(i)} for i in range(cap + 500)]
    task = {"id": "x", "project": "AICC", "title": "t", "goal": "g", "timeline": big}
    out = tasks_repository.normalize_task(task)
    assert len(out["timeline"]) == cap
    # newest events survive the heal
    assert out["timeline"][-1]["message"] == str(cap + 500 - 1)


def test_normalize_task_leaves_small_timeline_alone():
    task = {"id": "x", "project": "AICC", "title": "t", "goal": "g",
            "timeline": [{"id": "1", "ts": "", "type": "e", "message": "a"}]}
    out = tasks_repository.normalize_task(task)
    assert len(out["timeline"]) == 1
