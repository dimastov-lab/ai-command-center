"""AICC-AUTONOMY-002 — Concurrent Completion Advancer Hardening.

Deterministic regression tests for the guard-before-CAS race in
`db.update_completion` and for the idempotent, race-safe `_mark_attention` /
batch-isolated `advance_pending` behaviour that depends on it.

The defect: the structural transition guard was evaluated *before* the
compare-and-set version check, so when two advancers read the same row at
version V the winner could commit a legal move to V+1 while the stale loser had
its (now impossible) target measured against the winner's newer state — yielding
`InvalidCompletionTransitionError` instead of a benign `LostUpdateError`. That
misclassification let a loser (a) escalate a healthy row to REQUIRES_ATTENTION,
or (b) abort the whole `advance_pending` batch when the winner had reached a
terminal state (terminal -> REQUIRES_ATTENTION is itself illegal and re-raised).

These tests pin the corrected contract and are pure/deterministic (no threads,
no sleeps): the race is expressed as an explicit interleaving so the assertions
hold identically on every run.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from command_center import storage
from command_center.runtime import db as runtime_db
from command_center.runtime.completion import CompletionPolicy, CompletionState
from command_center.runtime.completion_service import (
    AdvanceResult,
    CompletionOrchestrator,
    EV_REQUIRES_ATTENTION,
    EV_STATE_CHANGED,
    MERGE_LOCK_FILE_NAME,
    MergeSlotBusyError,
)
from command_center.runtime.github import (
    STATE_OPEN,
    FakeGitHubClient,
    PullRequestState,
)

NOW = datetime(2026, 7, 22, 12, 0, 0)


@pytest.fixture
def db_path():
    p = runtime_db.resolve_db_path()
    runtime_db.migrate(p)
    return p


def _make_run(db_path):
    task = runtime_db.create_task(db_path, project="AICC", title="t", task_type="implementation")
    session = runtime_db.create_session(
        db_path, task_id=task["id"], project="AICC", repository_path="/tmp/x"
    )
    return runtime_db.create_run(
        db_path, session_id=session["id"], task_id=task["id"], project="AICC",
        task_type="implementation", repository_path="/tmp/x", prompt="p", is_resume=False,
    )


def _seed(db_path, run, state):
    return runtime_db.create_completion(
        db_path, run_id=run["id"], task_id=run["task_id"], project="AICC",
        repository_path="/tmp/x", completion_state=state,
    )


def _orch(db_path):
    return CompletionOrchestrator(db_path, github=FakeGitHubClient())


def _delete_completion(db_path, run_id):
    """Simulate a concurrent run/task cascade removing the completion row."""
    with runtime_db.connect(db_path) as conn:
        with runtime_db.transaction(conn):
            conn.execute("DELETE FROM completion WHERE run_id = ?", (run_id,))


def _events(db_path, run_id):
    return [e["event_type"] for e in runtime_db.list_completion_events(db_path, run_id)]


# ---------------------------------------------------------------------------
# db.update_completion — CAS is evaluated before the transition guard
# ---------------------------------------------------------------------------
def test_stale_loser_with_legal_target_gets_lost_update(db_path):
    """A stale caller whose target is otherwise legal loses on CAS."""
    run = _make_run(db_path)
    _seed(db_path, run, CompletionState.EXECUTION_FINISHED)  # v0
    runtime_db.update_completion(
        db_path, run["id"], expected_version=0,
        fields={"completion_state": CompletionState.VALIDATING_RESULT},  # winner -> v1
    )
    with pytest.raises(runtime_db.LostUpdateError):
        runtime_db.update_completion(
            db_path, run["id"], expected_version=0,  # stale
            fields={"completion_state": CompletionState.RESULT_VALID},
        )


def test_stale_loser_with_now_illegal_target_still_gets_lost_update(db_path):
    """Winner MERGED -> VERIFYING_TARGET_BRANCH; the stale loser's MERGED ->
    AWAITING_MERGE target is illegal against the winner's VERIFYING state, yet it
    must still lose with LostUpdateError (not InvalidCompletionTransitionError),
    and the winner's progress must be preserved untouched."""
    run = _make_run(db_path)
    _seed(db_path, run, CompletionState.MERGED)  # v0
    winner = runtime_db.update_completion(
        db_path, run["id"], expected_version=0,
        fields={"completion_state": CompletionState.VERIFYING_TARGET_BRANCH},
    )
    assert winner["completion_state"] == CompletionState.VERIFYING_TARGET_BRANCH
    assert winner["version"] == 1

    with pytest.raises(runtime_db.LostUpdateError):
        runtime_db.update_completion(
            db_path, run["id"], expected_version=0,  # stale
            fields={"completion_state": CompletionState.AWAITING_MERGE},
        )

    after = runtime_db.get_completion(db_path, run["id"])
    assert after["completion_state"] == CompletionState.VERIFYING_TARGET_BRANCH  # winner preserved
    assert after["version"] == winner["version"]  # loser wrote nothing at all


def test_stale_loser_against_terminal_winner_gets_lost_update(db_path):
    """Winner moves the row to a terminal state; the stale loser must receive
    LostUpdateError and leave the terminal winner untouched."""
    run = _make_run(db_path)
    _seed(db_path, run, CompletionState.VALIDATING_RESULT)  # v0
    winner = runtime_db.update_completion(
        db_path, run["id"], expected_version=0,
        fields={"completion_state": CompletionState.VALIDATION_FAILED},
    )
    with pytest.raises(runtime_db.LostUpdateError):
        runtime_db.update_completion(
            db_path, run["id"], expected_version=0,  # stale
            fields={"completion_state": CompletionState.RESULT_VALID},
        )
    after = runtime_db.get_completion(db_path, run["id"])
    assert after["completion_state"] == CompletionState.VALIDATION_FAILED  # winner preserved
    assert after["version"] == winner["version"]


def test_non_stale_illegal_transition_still_raises(db_path):
    """Legality is unchanged for a caller on the current version: an illegal
    move at the matching version still raises InvalidCompletionTransitionError."""
    run = _make_run(db_path)
    _seed(db_path, run, CompletionState.MERGED)  # v0
    with pytest.raises(runtime_db.InvalidCompletionTransitionError):
        runtime_db.update_completion(
            db_path, run["id"], expected_version=0,  # current
            fields={"completion_state": CompletionState.RESULT_VALID},
        )


def test_non_stale_same_state_metadata_update_still_works(db_path):
    """A same-state, current-version metadata write is still accepted."""
    run = _make_run(db_path)
    row = _seed(db_path, run, CompletionState.AWAITING_MERGE)
    updated = runtime_db.update_completion(
        db_path, run["id"], expected_version=row["version"],
        fields={"completion_state": CompletionState.AWAITING_MERGE, "retry_count": 4},
    )
    assert updated["completion_state"] == CompletionState.AWAITING_MERGE
    assert updated["retry_count"] == 4
    assert updated["version"] == row["version"] + 1


def test_global_merge_slot_refuses_overlapping_merge(db_path):
    orch = _orch(db_path)
    run = _make_run(db_path)
    row = _seed(db_path, run, CompletionState.AWAITING_MERGE)
    pr = PullRequestState(
        number=7,
        state=STATE_OPEN,
        mergeable="MERGEABLE",
    )
    lock_path = db_path.parent / MERGE_LOCK_FILE_NAME

    with storage.file_lock(lock_path):
        with pytest.raises(MergeSlotBusyError):
            orch._merge(
                row,
                pr,
                policy=CompletionPolicy(),
                now=NOW,
            )

    assert "MERGE_STARTED" not in _events(db_path, run["id"])


# ---------------------------------------------------------------------------
# _mark_attention — idempotent and race-safe
# ---------------------------------------------------------------------------
def test_mark_attention_on_missing_row_is_noop(db_path):
    """A row that vanished (concurrent cleanup) is a clean no-op, never a raise."""
    orch = _orch(db_path)
    run = _make_run(db_path)
    row = _seed(db_path, run, CompletionState.AWAITING_MERGE)
    with runtime_db.connect(db_path) as conn:
        with runtime_db.transaction(conn):
            conn.execute("DELETE FROM completion WHERE run_id = ?", (run["id"],))
    orch._mark_attention(row, now=NOW, reason_code="X", recommended="gone")  # must not raise
    assert runtime_db.get_completion(db_path, run["id"]) is None


def test_mark_attention_on_terminal_row_is_noop(db_path):
    """An already-terminal winner is never overwritten, and no misleading
    REQUIRES_ATTENTION / STATE_CHANGED event is appended."""
    orch = _orch(db_path)
    run = _make_run(db_path)
    row = _seed(db_path, run, CompletionState.MERGED)
    winner = runtime_db.update_completion(
        db_path, run["id"], expected_version=0,
        fields={"completion_state": CompletionState.COMPLETED},
    )
    events_before = _events(db_path, run["id"])

    orch._mark_attention(row, now=NOW, reason_code="X", recommended="stale")  # must not raise

    after = runtime_db.get_completion(db_path, run["id"])
    assert after["completion_state"] == CompletionState.COMPLETED  # winner preserved
    assert after["requires_human"] == 0
    assert after["version"] == winner["version"]  # no write happened
    assert _events(db_path, run["id"]) == events_before  # no false audit event
    assert EV_REQUIRES_ATTENTION not in _events(db_path, run["id"])


def test_mark_attention_with_stale_version_preserves_winner(db_path):
    """`_mark_attention` re-reads, so a caller holding a stale `row` still sees
    the winner's current (non-terminal) progress and does not clobber it back to
    an older state. It escalates the *fresh* row rather than the stale one."""
    orch = _orch(db_path)
    run = _make_run(db_path)
    stale_row = _seed(db_path, run, CompletionState.PULL_REQUEST_OPEN)  # v0, caller's snapshot
    # A winner advances the row forward before the loser marks attention.
    runtime_db.update_completion(
        db_path, run["id"], expected_version=0,
        fields={"completion_state": CompletionState.AWAITING_MERGE},  # -> v1
    )

    orch._mark_attention(stale_row, now=NOW, reason_code="X", recommended="stale")

    after = runtime_db.get_completion(db_path, run["id"])
    # Escalation applied to the fresh AWAITING_MERGE row (a legal target), not a
    # resurrection of the stale PULL_REQUEST_OPEN snapshot.
    assert after["completion_state"] == CompletionState.REQUIRES_ATTENTION
    assert after["requires_human"] == 1


def test_mark_attention_happy_path_escalates_and_logs(db_path):
    """Positive control: on a live non-terminal row, escalation still works and
    appends the REQUIRES_ATTENTION event exactly once."""
    orch = _orch(db_path)
    run = _make_run(db_path)
    row = _seed(db_path, run, CompletionState.AWAITING_MERGE)
    orch._mark_attention(row, now=NOW, reason_code="X", recommended="please look")
    after = runtime_db.get_completion(db_path, run["id"])
    assert after["completion_state"] == CompletionState.REQUIRES_ATTENTION
    assert after["requires_human"] == 1
    assert _events(db_path, run["id"]).count(EV_REQUIRES_ATTENTION) == 1


# ---------------------------------------------------------------------------
# advance_pending — one row's race never aborts the batch
# ---------------------------------------------------------------------------
def test_advance_pending_terminal_winner_does_not_abort_batch(db_path, monkeypatch):
    """Reproduces the "batch abort" defect. Row A's advancer loses to a winner
    that has driven A to a terminal state and then raises; the generic handler's
    `_mark_attention` must tolerate the terminal row (no raise) so the later due
    row B is still processed."""
    orch = _orch(db_path)
    run_a = _make_run(db_path)
    _seed(db_path, run_a, CompletionState.MERGED)
    run_b = _make_run(db_path)
    _seed(db_path, run_b, CompletionState.EXECUTION_FINISHED)

    seen: list[str] = []

    def fake_advance(run_id, *, now=None):
        seen.append(run_id)
        if run_id == run_a["id"]:
            winner = runtime_db.get_completion(db_path, run_id)
            runtime_db.update_completion(
                db_path, run_id, expected_version=winner["version"],
                fields={"completion_state": CompletionState.COMPLETED},  # terminal winner
            )
            raise RuntimeError("advancer raced against a terminal winner")
        return AdvanceResult(run_id, "EXECUTION_FINISHED", "EXECUTION_FINISHED", None, changed=False)

    monkeypatch.setattr(orch, "advance", fake_advance)

    results = orch.advance_pending(now=NOW)  # must NOT raise

    assert run_b["id"] in seen  # batch continued past the raced row
    assert len(results) == 1 and results[0].run_id == run_b["id"]
    a = runtime_db.get_completion(db_path, run_a["id"])
    assert a["completion_state"] == CompletionState.COMPLETED  # winner preserved
    assert a["requires_human"] == 0


def test_advance_pending_benign_cas_loser_leaves_no_trace(db_path, monkeypatch):
    """A LostUpdateError loser produces no state change, no requires_human, no
    retry-count bump, and — crucially — no misleading STATE_CHANGED or
    REQUIRES_ATTENTION audit event."""
    orch = _orch(db_path)
    run = _make_run(db_path)
    before = _seed(db_path, run, CompletionState.AWAITING_MERGE)
    events_before = _events(db_path, run["id"])

    def lose(run_id, *, now=None):
        raise runtime_db.LostUpdateError("benign concurrent advance")

    monkeypatch.setattr(orch, "advance", lose)
    results = orch.advance_pending(now=NOW)

    after = runtime_db.get_completion(db_path, run["id"])
    assert results == []
    assert after["completion_state"] == before["completion_state"]
    assert after["completion_state"] != CompletionState.REQUIRES_ATTENTION
    assert after["requires_human"] == 0
    assert after["retry_count"] == before["retry_count"]
    assert after["version"] == before["version"]
    assert _events(db_path, run["id"]) == events_before
    assert EV_STATE_CHANGED not in _events(db_path, run["id"])
    assert EV_REQUIRES_ATTENTION not in _events(db_path, run["id"])


def test_advance_pending_concurrent_invalid_transition_is_benign(db_path, monkeypatch):
    """Defense in depth: if an advancer surfaces InvalidCompletionTransitionError
    while a concurrent worker has already terminalized the row, `advance_pending`
    treats it as benign (no escalation) rather than clobbering the winner."""
    orch = _orch(db_path)
    run = _make_run(db_path)
    _seed(db_path, run, CompletionState.MERGED)

    def raise_invalid(run_id, *, now=None):
        winner = runtime_db.get_completion(db_path, run_id)
        runtime_db.update_completion(
            db_path, run_id, expected_version=winner["version"],
            fields={"completion_state": CompletionState.COMPLETED},  # terminal winner
        )
        raise runtime_db.InvalidCompletionTransitionError("stale target vs winner")

    monkeypatch.setattr(orch, "advance", raise_invalid)
    results = orch.advance_pending(now=NOW)  # must NOT raise

    after = runtime_db.get_completion(db_path, run["id"])
    assert results == []
    assert after["completion_state"] == CompletionState.COMPLETED  # winner preserved
    assert after["requires_human"] == 0
    assert EV_REQUIRES_ATTENTION not in _events(db_path, run["id"])


# ---------------------------------------------------------------------------
# M-1 — a row deleted *during* _mark_attention is a benign no-op (KeyError)
# ---------------------------------------------------------------------------
def test_mark_attention_row_deleted_between_read_and_cas_is_noop(db_path, monkeypatch):
    """`_mark_attention` re-reads the row, then writes via CAS. If the row is
    deleted in that window (run/task cascade), `update_completion` raises
    KeyError. That must be swallowed as benign concurrent cleanup — no raise,
    no resurrection of the row — not propagated (which would abort the batch).

    Fails on b8ae697 (KeyError escapes `_mark_attention`); passes after the fix."""
    orch = _orch(db_path)
    run = _make_run(db_path)
    row = _seed(db_path, run, CompletionState.AWAITING_MERGE)

    real_get = runtime_db.get_completion

    def deleting_get(db_path_, run_id):
        # `_mark_attention`'s single fresh read returns the live row, but the row
        # vanishes immediately afterwards — before `_transition`'s CAS write.
        fetched = real_get(db_path_, run_id)
        if fetched is not None:
            _delete_completion(db_path_, run_id)
        return fetched

    monkeypatch.setattr(runtime_db, "get_completion", deleting_get)
    orch._mark_attention(row, now=NOW, reason_code="X", recommended="deleted mid-escalation")
    monkeypatch.undo()

    # Row stayed deleted (never re-created as REQUIRES_ATTENTION), no exception.
    assert runtime_db.get_completion(db_path, run["id"]) is None


def test_advance_pending_row_deleted_mid_attention_does_not_abort_batch(db_path, monkeypatch):
    """End-to-end M-1: row A's advance fails, and A is deleted in the window
    inside `_mark_attention` (after its fresh read, before its CAS). The batch
    must not abort — the unrelated healthy row B is still processed and is never
    falsely escalated.

    Fails on b8ae697 (KeyError aborts `advance_pending`); passes after the fix."""
    orch = _orch(db_path)
    run_a = _make_run(db_path)
    _seed(db_path, run_a, CompletionState.AWAITING_MERGE)
    run_b = _make_run(db_path)
    _seed(db_path, run_b, CompletionState.EXECUTION_FINISHED)

    def fake_advance(run_id, *, now=None):
        if run_id == run_a["id"]:
            raise RuntimeError("advance blew up for A")
        return AdvanceResult(run_id, "EXECUTION_FINISHED", "EXECUTION_FINISHED", None, changed=False)

    monkeypatch.setattr(orch, "advance", fake_advance)

    real_get = runtime_db.get_completion
    a_reads = {"n": 0}

    def deleting_get(db_path_, run_id):
        fetched = real_get(db_path_, run_id)
        if run_id == run_a["id"] and fetched is not None:
            a_reads["n"] += 1
            # 1st read = the generic handler's `fresh`; 2nd read = inside
            # `_mark_attention`. Delete right after that second read, before CAS.
            if a_reads["n"] == 2:
                _delete_completion(db_path_, run_id)
        return fetched

    monkeypatch.setattr(runtime_db, "get_completion", deleting_get)
    results = orch.advance_pending(now=NOW)  # must NOT raise
    monkeypatch.undo()

    assert any(r.run_id == run_b["id"] for r in results)  # batch continued to B
    assert runtime_db.get_completion(db_path, run_a["id"]) is None  # A deleted, not resurrected
    b = runtime_db.get_completion(db_path, run_b["id"])
    assert b["completion_state"] == CompletionState.EXECUTION_FINISHED  # B untouched
    assert b["requires_human"] == 0  # no false attention on the unrelated row


# ---------------------------------------------------------------------------
# N-1 — a genuine (same-version) illegal transition still escalates, once
# ---------------------------------------------------------------------------
def test_advance_pending_genuine_invalid_transition_escalates_once(db_path, monkeypatch):
    """The `InvalidCompletionTransitionError` branch of `advance_pending` when
    the re-read row is still non-terminal: a genuine state-machine error (not a
    stale race, so the row is not concurrently advanced) is surfaced by escalating
    the row to REQUIRES_ATTENTION exactly once — audit event only after the CAS
    commits — while unrelated rows keep advancing. Genuine errors are not
    silently ignored."""
    orch = _orch(db_path)
    run_a = _make_run(db_path)
    _seed(db_path, run_a, CompletionState.AWAITING_MERGE)
    run_b = _make_run(db_path)
    _seed(db_path, run_b, CompletionState.EXECUTION_FINISHED)

    def fake_advance(run_id, *, now=None):
        if run_id == run_a["id"]:
            # No concurrent modification -> version still matches -> this is a
            # genuine illegal transition at the current version, not a race.
            raise runtime_db.InvalidCompletionTransitionError("genuine illegal move")
        return AdvanceResult(run_id, "EXECUTION_FINISHED", "EXECUTION_FINISHED", None, changed=False)

    monkeypatch.setattr(orch, "advance", fake_advance)
    results = orch.advance_pending(now=NOW)

    a = runtime_db.get_completion(db_path, run_a["id"])
    assert a["completion_state"] == CompletionState.REQUIRES_ATTENTION  # escalated, not ignored
    assert a["requires_human"] == 1
    events_a = _events(db_path, run_a["id"])
    assert events_a.count(EV_REQUIRES_ATTENTION) == 1  # exactly once
    assert EV_STATE_CHANGED in events_a  # persisted transition recorded
    # STATE_CHANGED (emitted inside _transition after the CAS commits) precedes
    # the REQUIRES_ATTENTION event, confirming the event follows persistence.
    assert events_a.index(EV_STATE_CHANGED) < events_a.index(EV_REQUIRES_ATTENTION)

    assert any(r.run_id == run_b["id"] for r in results)  # batch continued
    b = runtime_db.get_completion(db_path, run_b["id"])
    assert b["completion_state"] == CompletionState.EXECUTION_FINISHED  # unrelated row untouched
    assert b["requires_human"] == 0
