import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from command_center.daily_audit import (
    CampaignResult,
    DailyAuditConfig,
    DailyAuditService,
)


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
