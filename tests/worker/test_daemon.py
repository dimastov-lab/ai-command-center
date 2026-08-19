"""The daemon's loop, driven by a fake store — no database, no threads slept.

What these tests deliberately do NOT cover: the SQL protocol itself, which is
already proven by tests/db/test_queue_claim.py against real PostgreSQL, and
the store wrapper's SQL, which the integration test covers. Here the store is
a script of answers, so each test pins one piece of loop behaviour and fails
for exactly one reason.
"""

from __future__ import annotations


from command_center.db.work_queue_store import ClaimedWork, QueueRefusal
from command_center.worker.daemon import (
    HandlerOutcome,
    WorkerConfig,
    WorkerDaemon,
)


def _work(payload: dict, attempt_id: int = 1) -> ClaimedWork:
    return ClaimedWork(
        work_item_id=10,
        attempt_id=attempt_id,
        attempt_no=1,
        visible_until="2026-01-01T00:00:00+00:00",
        payload=payload,
        claim_token="token-plain",
    )


class ScriptedStore:
    """Answers claims from a script; records every protocol call."""

    def __init__(self, answers: list) -> None:
        self.answers = list(answers)
        self.calls: list[tuple] = []
        self.heartbeat_alive = True

    def claim(self, queue, *, visibility_seconds):
        self.calls.append(("claim", queue, visibility_seconds))
        if not self.answers:
            return QueueRefusal(reason="no_work")
        return self.answers.pop(0)

    def heartbeat(self, work):
        self.calls.append(("heartbeat", work.attempt_id))
        return self.heartbeat_alive

    def complete(self, work, result):
        self.calls.append(("complete", work.attempt_id, result))
        return True

    def fail(self, work, *, reason, retryable):
        self.calls.append(("fail", work.attempt_id, reason, retryable))
        return True


def _run_until_idle(daemon: WorkerDaemon, store: ScriptedStore) -> None:
    """Run the loop until the script is exhausted, then stop it via the
    injected sleep — the daemon idles only when there is no work, so the
    first idle sleep is the natural end of a scripted run."""
    # sleep is called with the idle backoff; use it as the stop trigger
    daemon._sleep = lambda _t: daemon.request_stop()  # type: ignore[method-assign]
    daemon.run_forever()


def test_a_claimed_item_is_dispatched_and_completed() -> None:
    store = ScriptedStore([_work({"kind": "echo", "x": 1})])
    outcomes = []

    def echo(payload, lease_lost):
        outcomes.append(payload)
        return HandlerOutcome(ok=True, result={"echoed": payload["x"]})

    daemon = WorkerDaemon(store, {"echo": echo}, WorkerConfig(visibility_seconds=3))
    _run_until_idle(daemon, store)

    assert outcomes == [{"kind": "echo", "x": 1}]
    assert ("complete", 1, {"echoed": 1}) in store.calls


def test_a_failing_handler_reports_fail_not_complete() -> None:
    store = ScriptedStore([_work({"kind": "boom"})])

    def boom(payload, lease_lost):
        return HandlerOutcome(ok=False, reason="did not work", retryable=True)

    daemon = WorkerDaemon(store, {"boom": boom}, WorkerConfig(visibility_seconds=3))
    _run_until_idle(daemon, store)

    assert ("fail", 1, "did not work", True) in store.calls
    assert not any(c[0] == "complete" for c in store.calls)


def test_a_raising_handler_is_a_retryable_failure() -> None:
    store = ScriptedStore([_work({"kind": "raise"})])

    def raiser(payload, lease_lost):
        raise RuntimeError("crashed")

    daemon = WorkerDaemon(store, {"raise": raiser}, WorkerConfig(visibility_seconds=3))
    _run_until_idle(daemon, store)

    fails = [c for c in store.calls if c[0] == "fail"]
    assert len(fails) == 1 and fails[0][3] is True  # retryable


def test_an_unknown_payload_kind_is_a_non_retryable_failure() -> None:
    """A payload nobody can execute will not become executable on retry;
    retrying it burns the attempt budget on the way to the same dead letter."""
    store = ScriptedStore([_work({"kind": "martian"})])
    daemon = WorkerDaemon(store, {}, WorkerConfig(visibility_seconds=3))
    _run_until_idle(daemon, store)

    fails = [c for c in store.calls if c[0] == "fail"]
    assert len(fails) == 1
    assert fails[0][3] is False  # not retryable
    assert "martian" in fails[0][2]


def test_a_lost_lease_discards_the_outcome() -> None:
    """After the database has given the attempt to someone else, reporting a
    result would be exactly the lost-update the protocol exists to prevent —
    so the daemon must report NOTHING."""
    store = ScriptedStore([_work({"kind": "slow"})])
    store.heartbeat_alive = False  # first beat discovers the lease is gone

    def slow(payload, lease_lost):
        # Wait until the heartbeat thread notices; then finish "successfully".
        assert lease_lost.wait(timeout=10), "heartbeat never signalled loss"
        return HandlerOutcome(ok=True, result={"too": "late"})

    daemon = WorkerDaemon(store, {"slow": slow}, WorkerConfig(visibility_seconds=3))
    _run_until_idle(daemon, store)

    assert not any(c[0] == "complete" for c in store.calls)
    assert not any(c[0] == "fail" for c in store.calls)


def test_sigterm_finishes_the_item_in_hand_and_claims_no_more() -> None:
    store = ScriptedStore([_work({"kind": "echo"}), _work({"kind": "echo"}, attempt_id=2)])
    seen = []

    def echo(payload, lease_lost):
        seen.append(payload)
        return HandlerOutcome(ok=True, result={})

    daemon = WorkerDaemon(store, {"echo": echo}, WorkerConfig(visibility_seconds=3))

    original_execute = daemon._execute

    def execute_then_stop(work):
        original_execute(work)
        daemon.request_stop()  # the signal arrives while item 1 is in hand

    daemon._execute = execute_then_stop  # type: ignore[method-assign]
    daemon.run_forever()

    assert len(seen) == 1, "the second item must not be claimed after stop"
    assert ("complete", 1, {}) in store.calls


def test_idle_backoff_grows_and_resets_on_work(monkeypatch) -> None:
    store = ScriptedStore([QueueRefusal("no_work"), QueueRefusal("no_work"),
                           _work({"kind": "echo"})])
    sleeps: list[float] = []
    daemon = WorkerDaemon(
        store,
        {"echo": lambda p, e: HandlerOutcome(ok=True)},
        WorkerConfig(visibility_seconds=3, idle_min_seconds=1.0, idle_max_seconds=8.0),
    )
    monkeypatch.setattr("command_center.worker.daemon.random",
                        type("R", (), {"uniform": staticmethod(lambda a, b: 0.0)}))

    def fake_sleep(t):
        sleeps.append(t)
        if len(sleeps) >= 4:  # two idles, work, then the post-script idle
            daemon.request_stop()

    daemon._sleep = fake_sleep  # type: ignore[method-assign]
    daemon.run_forever()

    assert sleeps[0] == 1.0 and sleeps[1] == 2.0, "backoff must grow while idle"
    # after real work the next idle starts from the floor again
    assert sleeps[2] == 1.0
