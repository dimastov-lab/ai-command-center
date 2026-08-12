"""Hermetic tests for the `/api/v1/dispatch/*` controller.

The router delegates to `command_center.dispatch.service` / `.repository`,
referenced as module globals in `command_center.dispatch.api`, so every test
monkeypatches those before issuing a request — no real board, runtime.db or
policy file is ever touched, and the controller is proven to be a thin
pass-through (no business logic of its own).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import command_center.dispatch.api as apimod
from command_center.dispatch.models import (
    ASSIGNED,
    DispatchDecision,
    DispatchPlan,
    DispatchPolicy,
)
from command_center.webapi.app import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def _sample_plan() -> DispatchPlan:
    decision = DispatchDecision(
        task_id="t1", project="AICC", priority="High", reason=ASSIGNED,
        assigned_executor="ollama", estimated_cost_usd=0.0,
    )
    return DispatchPlan(
        decisions=(decision,), kill_switch_engaged=False,
        daily_spend_usd=0.0, max_daily_spend_usd=5.0, projected_spend_usd=0.0,
    )


def test_get_plan_returns_serialized_plan(monkeypatch):
    monkeypatch.setattr(apimod._service, "plan", lambda root: _sample_plan())

    body = _client().get("/api/v1/dispatch/plan").json()

    assert body["assignment_count"] == 1
    assert body["decisions"][0]["assigned_executor"] == "ollama"
    assert body["budget_remaining_usd"] == 5.0


def test_post_assign_forwards_confirmation(monkeypatch):
    captured: dict = {}

    def fake_assign(root, *, confirmed, actor=None):
        captured["confirmed"] = confirmed
        captured["actor"] = actor
        return {"applied": confirmed, "assigned_task_ids": ["t1"]}

    monkeypatch.setattr(apimod._service, "assign", fake_assign)

    r = _client().post("/api/v1/dispatch/assign", json={"confirmed": True, "actor": "op"})

    assert r.status_code == 200
    assert r.json()["applied"] is True
    assert captured == {"confirmed": True, "actor": "op"}


def test_post_assign_defaults_to_unconfirmed(monkeypatch):
    captured: dict = {}

    def fake_assign(root, *, confirmed, actor=None):
        captured["confirmed"] = confirmed
        return {"applied": False, "reason": "confirmation_required"}

    monkeypatch.setattr(apimod._service, "assign", fake_assign)

    r = _client().post("/api/v1/dispatch/assign", json={})

    assert r.status_code == 200
    assert captured["confirmed"] is False


def test_get_policy_returns_serialized_policy(monkeypatch):
    monkeypatch.setattr(
        apimod._policy_config,
        "load_policy",
        lambda root: DispatchPolicy(prefer_local=False, cost_matrix={"ollama": 0.0}),
    )

    body = _client().get("/api/v1/dispatch/policy").json()

    assert body["prefer_local"] is False
    assert body["cost_matrix"] == {"ollama": 0.0}


def test_put_policy_forwards_changes(monkeypatch):
    captured: dict = {}

    def fake_update(root, changes, *, actor=None):
        captured["changes"] = changes
        captured["actor"] = actor
        return DispatchPolicy(prefer_local=False)

    monkeypatch.setattr(apimod._policy_config, "update_policy", fake_update)

    r = _client().put(
        "/api/v1/dispatch/policy",
        json={"changes": {"prefer_local": False}, "actor": "editor"},
    )

    assert r.status_code == 200
    assert r.json()["prefer_local"] is False
    assert captured["changes"] == {"prefer_local": False}
    assert captured["actor"] == "editor"


def test_put_policy_accepts_bare_body_as_changes(monkeypatch):
    captured: dict = {}

    def fake_update(root, changes, *, actor=None):
        captured["changes"] = changes
        return DispatchPolicy()

    monkeypatch.setattr(apimod._policy_config, "update_policy", fake_update)

    _client().put("/api/v1/dispatch/policy", json={"prefer_local": True})

    assert captured["changes"] == {"prefer_local": True}
