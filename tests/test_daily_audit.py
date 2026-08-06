import signal
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from command_center.daily_audit import (
    CampaignBackendError,
    CampaignResult,
    DailyAuditConfig,
    DailyAuditService,
    DailyAuditStore,
    utc_now,
)
from command_center.daily_audit_backend import ExecutionCenterCampaignBackend
from scripts.daily_audit_daemon import _request_shutdown


NOW = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)


class Backend:
    def __init__(self, result=None, error=None):
        self.result = result or CampaignResult("completed", "ok", target_verified=True)
        self.error = error
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.result


def config(tmp_path, **overrides):
    values = {
        "repository_path": tmp_path,
        "enabled": True,
        "interval": timedelta(days=1),
    }
    values.update(overrides)
    return DailyAuditConfig(**values)


def test_disabled_service_never_dispatches(tmp_path):
    backend = Backend()
    service = DailyAuditService(
        config(tmp_path, enabled=False), backend, db_path=tmp_path / "db.sqlite", clock=lambda: NOW
    )
    assert service.tick() is None
    assert backend.requests == []


def test_due_campaign_runs_once_and_schedules_next_day(tmp_path):
    backend = Backend()
    clock = [NOW]
    service = DailyAuditService(
        config(tmp_path), backend, db_path=tmp_path / "db.sqlite", clock=lambda: clock[0]
    )
    assert service.tick().target_verified
    assert service.tick() is None
    assert len(backend.requests) == 1
    assert backend.requests[0].lease_owner == service.owner
    clock[0] += timedelta(days=1, seconds=1)
    assert service.tick().target_verified
    assert len(backend.requests) == 2


def test_two_hosts_cannot_claim_same_due_campaign(tmp_path):
    backend = Backend()
    db_path = tmp_path / "db.sqlite"
    first = DailyAuditService(config(tmp_path), backend, db_path=db_path, owner="one", clock=lambda: NOW)
    second = DailyAuditService(config(tmp_path), backend, db_path=db_path, owner="two", clock=lambda: NOW)
    assert first.tick() is not None
    assert second.tick() is None


def test_backend_exception_is_persisted_and_retried_in_one_hour(tmp_path):
    clock = [NOW]
    failing = Backend(error=RuntimeError("network"))
    db_path = tmp_path / "db.sqlite"
    service = DailyAuditService(config(tmp_path), failing, db_path=db_path, clock=lambda: clock[0])
    with pytest.raises(RuntimeError, match="network"):
        service.tick()
    assert service.tick() is None
    clock[0] += timedelta(hours=1, seconds=1)
    healthy = Backend()
    resumed = DailyAuditService(config(tmp_path), healthy, db_path=db_path, clock=lambda: clock[0])
    assert resumed.tick().target_verified


def test_repeated_backend_failures_back_off_exponentially(tmp_path):
    clock = [NOW]
    db_path = tmp_path / "db.sqlite"
    service = DailyAuditService(
        config(tmp_path),
        Backend(error=RuntimeError("network")),
        db_path=db_path,
        clock=lambda: clock[0],
    )
    with pytest.raises(RuntimeError, match="network"):
        service.tick()
    first_retry = datetime.fromisoformat(service.store.status()["next_run_at"])
    assert first_retry == NOW + timedelta(hours=1)

    clock[0] = first_retry + timedelta(seconds=1)
    with pytest.raises(RuntimeError, match="network"):
        service.tick()
    status = service.store.status()
    assert status["consecutive_failures"] == 2
    assert datetime.fromisoformat(status["next_run_at"]) == clock[0] + timedelta(hours=2)


def test_provider_retry_time_is_preserved_and_delays_next_campaign(tmp_path):
    retry_at = NOW + timedelta(days=4)
    service = DailyAuditService(
        config(tmp_path),
        Backend(error=CampaignBackendError("provider_api_error: HTTP 429", retry_at=retry_at)),
        db_path=tmp_path / "db.sqlite",
        clock=lambda: NOW,
    )
    with pytest.raises(CampaignBackendError, match="429"):
        service.tick()
    status = service.store.status()
    assert datetime.fromisoformat(status["next_run_at"]) == retry_at
    campaign = service.store.list_campaigns(limit=1)[0]
    assert "provider_api_error" in campaign["summary"]
    assert "429" in campaign["summary"]


def test_completion_without_target_verification_is_rejected(tmp_path):
    backend = Backend(CampaignResult("completed", "claimed", target_verified=False))
    service = DailyAuditService(
        config(tmp_path), backend, db_path=tmp_path / "db.sqlite", clock=lambda: NOW
    )
    result = service.tick()
    assert result.status == "failed"
    assert "target-branch verification" in result.summary


def test_request_run_now_never_disturbs_active_campaign(tmp_path):
    backend = Backend()
    db_path = tmp_path / "db.sqlite"
    service = DailyAuditService(
        config(tmp_path), backend, db_path=db_path, clock=lambda: NOW
    )
    campaign_id = service.store.acquire_due(
        now=NOW, owner="host", lease_duration=timedelta(hours=1)
    )
    assert campaign_id
    assert service.store.request_run_now(now=NOW + timedelta(minutes=1)) is False
    status = service.store.status()
    assert status["active_campaign_id"] == campaign_id


def test_request_run_now_makes_idle_schedule_due(tmp_path):
    backend = Backend()
    db_path = tmp_path / "db.sqlite"
    service = DailyAuditService(
        config(tmp_path), backend, db_path=db_path, clock=lambda: NOW
    )
    service.store.ensure_schedule(NOW + timedelta(days=1))
    assert service.store.request_run_now(now=NOW)
    assert service.store.status()["next_run_at"] == "2026-07-28T08:00:00+00:00"


def test_store_closes_every_poll_connection(tmp_path, monkeypatch):
    opened = []
    real_connect = sqlite3.connect

    def tracked_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr("command_center.daily_audit.sqlite3.connect", tracked_connect)
    service = DailyAuditService(
        config(tmp_path), Backend(), db_path=tmp_path / "db.sqlite", clock=lambda: NOW
    )
    for _ in range(100):
        service.store.status()

    assert opened
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")


def test_lease_heartbeat_prevents_a_second_campaign_after_original_expiry(tmp_path):
    class BlockingBackend:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()

        def run(self, request):
            self.started.set()
            assert self.release.wait(timeout=2)
            return CampaignResult("completed", "ok", target_verified=True)

    backend = BlockingBackend()
    db_path = tmp_path / "db.sqlite"
    service = DailyAuditService(
        config(
            tmp_path,
            lease_duration=timedelta(milliseconds=120),
            lease_heartbeat_seconds=0.02,
        ),
        backend,
        db_path=db_path,
        owner="one",
        clock=utc_now,
    )
    result = []
    worker = threading.Thread(target=lambda: result.append(service.tick()))
    worker.start()
    assert backend.started.wait(timeout=1)
    original_until = datetime.fromisoformat(service.store.status()["lease_until"])
    deadline = time.monotonic() + 1
    while datetime.fromisoformat(service.store.status()["lease_until"]) <= original_until:
        assert time.monotonic() < deadline
        time.sleep(0.01)

    time.sleep(0.13)
    contender = DailyAuditStore(db_path)
    assert contender.acquire_due(
        now=utc_now(), owner="two", lease_duration=timedelta(seconds=1)
    ) is None
    backend.release.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert result[0].target_verified


def test_service_shutdown_cancels_active_agent_and_persists_interruption(tmp_path):
    class RunningAPI:
        def __init__(self):
            self.cancelled = []

        def get_run(self, run_id):
            return {"id": run_id, "state": "RUNNING", "failure_reason": None}

        def request_cancel(self, run_id, *, confirmed, grace_seconds=None):
            self.cancelled.append((run_id, confirmed, grace_seconds))
            return {"id": run_id, "state": "INTERRUPTED"}

    class ActiveAgentBackend:
        def __init__(self):
            self.api = RunningAPI()
            self.started = threading.Event()
            self.backend = ExecutionCenterCampaignBackend(
                api=self.api,
                poll_seconds=0.01,
            )

        def run(self, campaign_request):
            self.started.set()
            self.backend._wait_run(
                "active-run",
                timeout_seconds=30,
                request=campaign_request,
            )
            raise AssertionError("running agent unexpectedly completed")

    backend = ActiveAgentBackend()
    service = DailyAuditService(
        config(tmp_path, lease_heartbeat_seconds=0.02),
        backend,
        db_path=tmp_path / "db.sqlite",
        owner="daemon",
        clock=utc_now,
    )
    results = []
    worker = threading.Thread(target=lambda: results.append(service.tick()))
    worker.start()
    assert backend.started.wait(timeout=1)

    service.request_stop()
    worker.join(timeout=3)

    assert not worker.is_alive()
    assert results[0].status == "interrupted"
    assert backend.api.cancelled == [("active-run", True, 1.0)]
    assert service.store.status()["active_campaign_id"] is None
    assert service.store.list_campaigns(limit=1)[0]["status"] == "interrupted"


def test_daemon_signal_handler_sets_stop_and_requests_service_shutdown():
    class FakeService:
        def __init__(self):
            self.calls = 0

        def request_stop(self):
            self.calls += 1

    service = FakeService()
    stop_event = threading.Event()

    _request_shutdown(service, stop_event, signal.SIGTERM, None)

    assert stop_event.is_set()
    assert service.calls == 1


def test_expired_lease_recovers_and_terminalizes_abandoned_campaign(tmp_path):
    store = DailyAuditStore(tmp_path / "db.sqlite")
    abandoned = store.acquire_due(
        now=NOW, owner="old", lease_duration=timedelta(minutes=5)
    )
    assert (
        store.fail(
            abandoned,
            "late owner failed",
            owner="old",
            now=NOW + timedelta(minutes=6),
        )
        is None
    )
    campaigns = {row["id"]: row for row in store.list_campaigns(limit=10)}
    assert campaigns[abandoned]["status"] == "interrupted"
    assert store.status()["active_campaign_id"] == abandoned

    replacement = store.acquire_due(
        now=NOW + timedelta(minutes=6),
        owner="new",
        lease_duration=timedelta(hours=1),
    )
    assert replacement and replacement != abandoned
    campaigns = {row["id"]: row for row in store.list_campaigns(limit=10)}
    assert campaigns[abandoned]["status"] == "interrupted"
    assert campaigns[abandoned]["finished_at"] is not None
    assert store.status()["active_campaign_id"] == replacement

    assert (
        store.fail(
            abandoned,
            "late owner failed again",
            owner="old",
            now=NOW + timedelta(minutes=7),
        )
        is None
    )
    campaigns = {row["id"]: row for row in store.list_campaigns(limit=10)}
    assert campaigns[abandoned]["status"] == "interrupted"
    assert store.status()["active_campaign_id"] == replacement
