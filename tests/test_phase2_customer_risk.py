"""Tests for Phase 2: customer_store, risk_store, risk_alerts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from command_center import alert_store, customer_store, risk_store
from command_center.customer_store import InvalidValue, NotFound
from command_center.risk_alerts import assess_and_alert
from command_center.risk_store import HIGH_RISK_COUNTRIES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def c_db(tmp_path: Path) -> Path:
    p = tmp_path / "customers.db"
    customer_store.init_db(p)
    return p


@pytest.fixture()
def r_db(tmp_path: Path) -> Path:
    p = tmp_path / "risk.db"
    risk_store.init_db(p)
    return p


@pytest.fixture()
def a_db(tmp_path: Path) -> Path:
    p = tmp_path / "alerts.db"
    alert_store.init_db(p)
    return p


def _make_customer(c_db: Path, **kwargs) -> dict:
    defaults = dict(name="ООО Тест", customer_type="legal", actor="Analyst")
    defaults.update(kwargs)
    return customer_store.create_customer(c_db, **defaults)


# ---------------------------------------------------------------------------
# customer_store — CRUD
# ---------------------------------------------------------------------------


def test_create_customer_happy_path(c_db: Path) -> None:
    c = _make_customer(c_db)
    assert c["risk_tier"] == "medium"
    assert c["kyc_status"] == "pending"
    assert c["pep_flag"] == 0


def test_create_customer_invalid_type(c_db: Path) -> None:
    with pytest.raises(InvalidValue):
        _make_customer(c_db, customer_type="unknown")


def test_get_customer_not_found(c_db: Path) -> None:
    with pytest.raises(NotFound):
        customer_store.get_customer(c_db, "nonexistent")


def test_update_risk_tier(c_db: Path) -> None:
    c = _make_customer(c_db)
    updated = customer_store.update_risk_tier(c_db, c["id"], risk_tier="high", actor="Analyst")
    assert updated["risk_tier"] == "high"


def test_update_risk_tier_invalid(c_db: Path) -> None:
    c = _make_customer(c_db)
    with pytest.raises(InvalidValue):
        customer_store.update_risk_tier(c_db, c["id"], risk_tier="extreme", actor="Analyst")


def test_update_kyc_status(c_db: Path) -> None:
    c = _make_customer(c_db)
    updated = customer_store.update_kyc_status(c_db, c["id"], kyc_status="verified", actor="Analyst")
    assert updated["kyc_status"] == "verified"


def test_list_customers_filter(c_db: Path) -> None:
    _make_customer(c_db, name="A")
    b = _make_customer(c_db, name="B", pep_flag=True)
    customer_store.update_risk_tier(c_db, b["id"], risk_tier="pep", actor="Analyst")

    pep_list = customer_store.list_customers(c_db, risk_tier="pep")
    assert len(pep_list) == 1
    assert pep_list[0]["id"] == b["id"]


def test_record_review(c_db: Path) -> None:
    c = _make_customer(c_db)
    assert c["last_review_at"] is None
    updated = customer_store.record_review(c_db, c["id"], actor="Analyst")
    assert updated["last_review_at"] is not None


# ---------------------------------------------------------------------------
# KYC checks
# ---------------------------------------------------------------------------


def test_add_kyc_check(c_db: Path) -> None:
    c = _make_customer(c_db)
    check = customer_store.add_kyc_check(
        c_db,
        customer_id=c["id"],
        check_type="identity",
        result="pass",
        verified_by="Analyst",
    )
    assert check["result"] == "pass"


def test_get_kyc_checks_returns_latest_first(c_db: Path) -> None:
    c = _make_customer(c_db)
    customer_store.add_kyc_check(c_db, customer_id=c["id"], check_type="identity", result="pass", verified_by="A")
    customer_store.add_kyc_check(c_db, customer_id=c["id"], check_type="sanctions", result="fail", verified_by="A")
    checks = customer_store.get_kyc_checks(c_db, c["id"])
    assert len(checks) == 2
    assert checks[0]["check_type"] == "sanctions"


def test_get_customer_risk_profile(c_db: Path) -> None:
    c = _make_customer(c_db)
    customer_store.add_kyc_check(c_db, customer_id=c["id"], check_type="id", result="pass", verified_by="A")
    customer_store.add_kyc_check(c_db, customer_id=c["id"], check_type="aml", result="fail", verified_by="A")
    profile = customer_store.get_customer_risk_profile(c_db, c["id"])
    assert profile["kyc_checks_total"] == 2
    assert profile["kyc_checks_passed"] == 1
    assert profile["kyc_checks_failed"] == 1


# ---------------------------------------------------------------------------
# Periodic review
# ---------------------------------------------------------------------------


def test_customers_due_for_review_never_reviewed(c_db: Path) -> None:
    # Create a HIGH customer that was created > 90 days ago (mock created_at)
    c = _make_customer(c_db)
    customer_store.update_risk_tier(c_db, c["id"], risk_tier="high", actor="Analyst")
    # Manually backdate created_at
    import sqlite3
    conn = sqlite3.connect(c_db)
    old_date = (datetime.now(UTC) - timedelta(days=100)).isoformat()
    conn.execute("UPDATE customers SET created_at=? WHERE id=?", (old_date, c["id"]))
    conn.commit(); conn.close()

    due = customer_store.get_customers_due_for_review(c_db)
    assert any(d["id"] == c["id"] for d in due)


def test_customers_not_due_after_review(c_db: Path) -> None:
    c = _make_customer(c_db)
    customer_store.update_risk_tier(c_db, c["id"], risk_tier="high", actor="Analyst")
    customer_store.record_review(c_db, c["id"], actor="Analyst")

    due = customer_store.get_customers_due_for_review(c_db)
    assert not any(d["id"] == c["id"] for d in due)


# ---------------------------------------------------------------------------
# risk_store — scoring
# ---------------------------------------------------------------------------


def test_risk_score_pep_customer(c_db: Path, r_db: Path) -> None:
    c = _make_customer(c_db, pep_flag=True)
    result = risk_store.compute_risk_score(c_db, r_db, c["id"], actor="Analyst")
    assert result["tier"] == "pep"
    assert result["score"] >= 35


def test_risk_score_low_risk(c_db: Path, r_db: Path) -> None:
    c = _make_customer(c_db, country="DE")
    result = risk_store.compute_risk_score(c_db, r_db, c["id"], actor="Analyst")
    assert result["tier"] == "low"
    assert result["score"] == 0


def test_risk_score_high_risk_country(c_db: Path, r_db: Path) -> None:
    country = next(iter(HIGH_RISK_COUNTRIES))
    c = _make_customer(c_db, country=country)
    result = risk_store.compute_risk_score(c_db, r_db, c["id"], actor="Analyst")
    assert any(f["factor"] == "high_risk_country" and f["triggered"] for f in result["factors"])


def test_risk_score_high_risk_product(c_db: Path, r_db: Path) -> None:
    c = _make_customer(c_db)
    result = risk_store.compute_risk_score(c_db, r_db, c["id"], products=["private_banking"], actor="Analyst")
    assert any(f["factor"] == "high_product_risk" and f["triggered"] for f in result["factors"])


def test_get_latest_assessment(c_db: Path, r_db: Path) -> None:
    c = _make_customer(c_db)
    risk_store.compute_risk_score(c_db, r_db, c["id"], actor="Analyst")
    risk_store.compute_risk_score(c_db, r_db, c["id"], actor="Analyst")
    latest = risk_store.get_latest_assessment(r_db, c["id"])
    assert latest is not None
    assert len(risk_store.list_assessments(r_db, c["id"])) == 2


# ---------------------------------------------------------------------------
# Risk→Alert bridge
# ---------------------------------------------------------------------------


def test_risk_alert_triggered_on_escalation(c_db: Path, r_db: Path, a_db: Path) -> None:
    c = _make_customer(c_db, pep_flag=True, country="IR")
    result = assess_and_alert(c_db, r_db, a_db, c["id"], actor="system")
    assert result["alert"] is not None
    alerts = alert_store.list_alerts(a_db)
    assert len(alerts) == 1
    assert alerts[0]["source"] == "risk_assessment"
    assert alerts[0]["subject_id"] == c["id"]


def test_risk_alert_not_triggered_for_low_risk(c_db: Path, r_db: Path, a_db: Path) -> None:
    c = _make_customer(c_db, country="DE")
    result = assess_and_alert(c_db, r_db, a_db, c["id"], actor="system")
    assert result["alert"] is None
    assert alert_store.list_alerts(a_db) == []


def test_risk_alert_not_triggered_if_already_high(c_db: Path, r_db: Path, a_db: Path) -> None:
    c = _make_customer(c_db, pep_flag=True)
    # First assessment triggers alert
    assess_and_alert(c_db, r_db, a_db, c["id"], actor="system")
    # Second assessment — already high, no new alert
    assess_and_alert(c_db, r_db, a_db, c["id"], actor="system")
    assert len(alert_store.list_alerts(a_db)) == 1
