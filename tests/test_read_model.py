"""Canonical read-model: one source for the task counts every surface shows.

The audit found the dashboard, Kanban and Execution Center each aggregate the
task list inline, so the same fact renders as different numbers side by side
(tasks 174 vs 88 vs 116; Backlog 31 vs 61; Blocked 71 vs 86 vs absent). These
tests pin the single invariant that makes those surfaces reconcilable: every
task lands in exactly one lane, so the lane counts always sum to the total and
no task can silently vanish (audit D2/D5).
"""

from __future__ import annotations

from command_center import read_model


def test_lane_counts_plus_other_equal_total_nothing_vanishes():
    tasks = [{"status": "Backlog"}, {"status": "Blocked"}, {"status": "Done"}, {"status": "Weird"}]
    snap = read_model.task_snapshot(tasks)
    assert snap.total == 4
    assert sum(snap.by_lane.values()) + snap.other == snap.total


def test_blocked_tasks_are_visible_in_their_own_lane():
    tasks = (
        [{"status": "Blocked"} for _ in range(86)]
        + [{"status": "Done"} for _ in range(57)]
        + [{"status": "Backlog"} for _ in range(31)]
    )
    snap = read_model.task_snapshot(tasks)
    assert snap.total == 174
    assert snap.by_lane["Blocked"] == 86
    assert snap.blocked == 86
    # The exact defect: the board dropped Blocked, showing 88 of 174. Here the
    # canonical lanes account for every task.
    assert sum(snap.by_lane.values()) == 174


def test_unknown_status_is_bucketed_not_dropped():
    snap = read_model.task_snapshot([{"status": "Superseded"}, {"status": None}, {}])
    assert snap.other == 3
    assert snap.total == 3
    assert sum(snap.by_lane.values()) + snap.other == snap.total


def test_done_blocked_and_active_are_derived_consistently():
    tasks = [{"status": "Done"}, {"status": "Backlog"}, {"status": "Blocked"}, {"status": "In Progress"}]
    snap = read_model.task_snapshot(tasks)
    assert snap.done == 1
    assert snap.blocked == 1
    # "active" = in-flight/backlog work: not Done, not Blocked, not Other.
    assert snap.active == 2  # Backlog + In Progress


def test_attention_counts_requires_attention_and_regressed_after_done():
    tasks = [
        {"status": "Backlog", "launch_status": "Requires Attention"},
        {"status": "Done", "regressed_after_done": True},
        {"status": "Backlog", "launch_status": "Ready"},
    ]
    snap = read_model.task_snapshot(tasks)
    assert snap.attention == 2


def test_empty_task_list_is_all_zeros():
    snap = read_model.task_snapshot([])
    assert snap.total == 0
    assert snap.done == snap.blocked == snap.active == snap.attention == snap.other == 0
    assert sum(snap.by_lane.values()) == 0


# --- Canonical RUN-level snapshot (audit D5) ---------------------------------
# The dashboard banner, the AI-Supervisor caption and the top-bar glyph each
# counted "runs needing attention" from a different run set, so the same screen
# showed 24 / 141 / 109 under the word "внимания". run_snapshot() is the single
# definition (same run.state buckets the Execution Strip established); callers
# pass the non-superseded runs so the count is "needs attention now", not a
# cumulative historical tally.


def test_run_snapshot_buckets_by_state():
    runs = [
        {"state": "RUNNING"},
        {"state": "QUEUED"}, {"state": "PREPARED"},
        {"state": "FAILED"}, {"state": "INTERRUPTED"}, {"state": "UNKNOWN"},
        {"state": "COMPLETED"}, {"state": "CANCELLED"},
    ]
    snap = read_model.run_snapshot(runs)
    assert snap.total == 8
    assert snap.running == 1
    assert snap.queued == 2
    assert snap.attention == 3  # FAILED + INTERRUPTED + UNKNOWN


def test_run_snapshot_ignores_terminal_success_and_cancelled():
    snap = read_model.run_snapshot([{"state": "COMPLETED"}, {"state": "CANCELLED"}])
    assert snap.running == snap.queued == snap.attention == 0
    assert snap.total == 2


def test_run_snapshot_empty_is_zeros():
    snap = read_model.run_snapshot([])
    assert snap.total == snap.running == snap.queued == snap.attention == 0


def test_run_attention_states_match_execution_strip():
    """Guard against drift: the canonical run buckets must stay identical to the
    Execution Strip's, so the strip banner and the Supervisor caption can never
    diverge again."""
    from command_center.ui import execution_strip

    assert read_model.RUN_ATTENTION_STATES == execution_strip._ATTENTION_STATES
    assert read_model.RUN_LIVE_STATES == execution_strip._LIVE_STATES
    assert read_model.RUN_WAITING_STATES == execution_strip._WAITING_STATES
