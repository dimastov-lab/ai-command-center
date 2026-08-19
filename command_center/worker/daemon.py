"""The claim-execute-report loop, built to be killed at any moment.

Design decisions, each traceable to the shipped substrate:

* **Identity is the connection.** The claimant is ``session_user`` by trigger,
  so the daemon carries no identity of its own — it is whatever per-host
  ``aicc_w_*`` role its DSN authenticates as. Enrolment is not this daemon's
  job: redeeming a ticket is an ``aicc_app`` privilege, deliberately denied to
  workers, so a worker host is enrolled by the operator or control plane
  before this service first starts.
* **The heartbeat runs beside the handler, not inside it.** A handler that
  blocks must not silence the heartbeat, and a lapsed lease must stop the
  work: the beat thread renews at a third of the visibility window and raises
  a stop flag the moment the database says ``attempt_superseded``.
* **Shutdown finishes the item in hand.** SIGTERM stops *claiming*; the
  current attempt runs to completion inside systemd's stop timeout. A second
  signal — or the timeout's SIGKILL — abandons it, and the lease expiry plus
  the control plane's reaper make that abandonment safe by construction.
* **Auth failure means stop, not retry.** A refused connection may mean the
  credential was rotated out from under us (`enroll_rotate_self` is
  first-writer-wins); hammering the server with a dead secret is
  indistinguishable from an attack, so the daemon exits non-zero and leaves
  restart pacing to systemd.
"""

from __future__ import annotations

import logging
import random
import signal
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from command_center.db.work_queue_store import (
    ClaimedWork,
    QueueRefusal,
    WorkQueueStore,
)

logger = logging.getLogger(__name__)

__all__ = ["Handler", "HandlerOutcome", "WorkerConfig", "WorkerDaemon"]


@dataclass(frozen=True, slots=True)
class HandlerOutcome:
    ok: bool
    result: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    retryable: bool = True


class Handler(Protocol):
    """One payload executor. It receives the payload and a ``lease_lost``
    event it must honour: when the event is set, the lease is gone and any
    further effect the handler produces is unaccountable."""

    def __call__(
        self, payload: dict[str, Any], lease_lost: threading.Event
    ) -> HandlerOutcome: ...


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    queue: str = "execution"
    visibility_seconds: int = 300
    # Idle polling: jittered exponential backoff. The floor keeps an idle
    # fleet from hammering the database; the cap keeps a newly enqueued item
    # from waiting long on a quiet host.
    idle_min_seconds: float = 1.0
    idle_max_seconds: float = 30.0


class WorkerDaemon:
    def __init__(
        self,
        store: WorkQueueStore,
        handlers: dict[str, Handler],
        config: WorkerConfig = WorkerConfig(),
        *,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._store = store
        self._handlers = dict(handlers)
        self._config = config
        self._stop = threading.Event()
        # Injectable so tests drive time instead of waiting through it.
        self._sleep = sleep if sleep is not None else self._stop.wait

    # -- lifecycle ------------------------------------------------------------

    def install_signal_handlers(self) -> None:
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, self._on_signal)

    def _on_signal(self, signum: int, _frame: Any) -> None:
        logger.info("signal %s: finishing the item in hand, claiming no more", signum)
        self._stop.set()

    def request_stop(self) -> None:
        self._stop.set()

    # -- the loop -------------------------------------------------------------

    def run_forever(self) -> None:
        idle = self._config.idle_min_seconds
        while not self._stop.is_set():
            claimed = self._store.claim(
                self._config.queue,
                visibility_seconds=self._config.visibility_seconds,
            )
            if isinstance(claimed, QueueRefusal):
                if claimed.reason == "no_work":
                    self._sleep(idle + random.uniform(0, idle))
                    idle = min(idle * 2, self._config.idle_max_seconds)
                    continue
                # Every other refusal is a protocol-level fact worth a log
                # line, and none of them is cured by asking again faster.
                logger.warning("claim refused: %s", claimed.reason)
                self._sleep(self._config.idle_max_seconds)
                continue
            idle = self._config.idle_min_seconds
            self._execute(claimed)

    def _execute(self, work: ClaimedWork) -> None:
        lease_lost = threading.Event()
        beat_stop = threading.Event()
        beat = threading.Thread(
            target=self._heartbeat_loop,
            args=(work, lease_lost, beat_stop),
            name=f"heartbeat-{work.attempt_id}",
            daemon=True,
        )
        beat.start()
        try:
            outcome = self._dispatch(work, lease_lost)
        finally:
            beat_stop.set()
            beat.join(timeout=5)

        if lease_lost.is_set():
            # The database already gave this attempt to someone else (or will,
            # at reap). Reporting anything would be refused as a stale owner —
            # and must be: writing a result after losing the lease is exactly
            # the lost-update this protocol exists to prevent.
            logger.warning(
                "attempt %s: lease lost mid-execution; outcome discarded",
                work.attempt_id,
            )
            return
        if outcome.ok:
            self._store.complete(work, outcome.result)
        else:
            self._store.fail(work, reason=outcome.reason, retryable=outcome.retryable)

    def _dispatch(
        self, work: ClaimedWork, lease_lost: threading.Event
    ) -> HandlerOutcome:
        kind = str(work.payload.get("kind", ""))
        handler = self._handlers.get(kind)
        if handler is None:
            # Non-retryable by design: a payload nobody can execute will not
            # become executable on the next attempt, and retrying it burns the
            # attempt budget on the way to the same dead letter.
            return HandlerOutcome(
                ok=False,
                reason=f"no handler for payload kind {kind!r}",
                retryable=False,
            )
        try:
            return handler(work.payload, lease_lost)
        except Exception as error:  # noqa: BLE001 -- the boundary of the daemon
            logger.exception("handler for %r raised", kind)
            return HandlerOutcome(ok=False, reason=repr(error), retryable=True)

    def _heartbeat_loop(
        self,
        work: ClaimedWork,
        lease_lost: threading.Event,
        beat_stop: threading.Event,
    ) -> None:
        # A third of the window: two consecutive beats may fail (a restarting
        # PostgreSQL, a network blip) before the lease actually lapses.
        interval = max(self._config.visibility_seconds / 3.0, 1.0)
        while not beat_stop.wait(interval):
            try:
                alive = self._store.heartbeat(work)
            except Exception:  # noqa: BLE001 -- transient DB errors must not kill the beat
                logger.exception("heartbeat error for attempt %s", work.attempt_id)
                continue
            if not alive:
                lease_lost.set()
                return
