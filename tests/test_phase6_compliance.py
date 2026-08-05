"""Тесты Phase 6: compliance_store — агрегация метрик."""

from __future__ import annotations

from pathlib import Path

import pytest

from command_center import (
    alert_store,
    case_store,
    compliance_store,
    customer_store,
    rule_engine,
    sar_store,
)
from command_center.seed_rules_115fz import seed as seed_rules


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def a_db(tmp_path: Path) -> Path:
    p = tmp_path / "alerts.db"
    alert_store.init_db(p)
    return p


@pytest.fixture()
def ca_db(tmp_path: Path) -> Path:
    p = tmp_path / "cases.db"
    case_store.init_db(p)
    return p


@pytest.fixture()
def s_db(tmp_path: Path) -> Path:
    p = tmp_path / "sars.db"
    sar_store.init_db(p)
    return p


@pytest.fixture()
def cu_db(tmp_path: Path) -> Path:
    p = tmp_path / "customers.db"
    customer_store.init_db(p)
    return p


@pytest.fixture()
def r_db(tmp_path: Path) -> Path:
    p = tmp_path / "rules.db"
    rule_engine.init_db(p)
    return p


def _alert(a_db: Path, **kw) -> dict:
    defaults = dict(
        source="manual", source_ref="r", subject_id="c1",
        subject_type="customer", trigger_desc="Test",
        priority="high", priority_rationale="test", due_at=None, actor="Analyst",
    )
    defaults.update(kw)
    return alert_store.create_alert(a_db, **defaults)


def _case(ca_db: Path, **kw) -> dict:
    defaults = dict(title="Test", priority="medium", created_by="ComplianceOfficer")
    defaults.update(kw)
    return case_store.create_case(ca_db, **defaults)


def _sar(s_db: Path, **kw) -> dict:
    defaults = dict(sar_type="str", created_by="ComplianceOfficer")
    defaults.update(kw)
    return sar_store.create_sar(s_db, **defaults)


def _customer(cu_db: Path, **kw) -> dict:
    defaults = dict(name="Тест", customer_type="legal", actor="Analyst")
    defaults.update(kw)
    return customer_store.create_customer(cu_db, **defaults)


# ---------------------------------------------------------------------------
# alert_stats
# ---------------------------------------------------------------------------


def test_alert_stats_empty(a_db: Path) -> None:
    s = compliance_store.alert_stats(a_db)
    assert s["total"] == 0
    assert s["open"] == 0
    assert s["overdue"] == 0
    assert s["by_state"] == {}


def test_alert_stats_counts(a_db: Path) -> None:
    _alert(a_db)
    _alert(a_db)
    s = compliance_store.alert_stats(a_db)
    assert s["total"] == 2
    assert s["open"] == 2
    assert s["by_state"].get("generated", 0) == 2


def test_alert_stats_nonexistent_db(tmp_path: Path) -> None:
    s = compliance_store.alert_stats(tmp_path / "missing.db")
    assert s["total"] == 0


def test_alert_stats_recent_30d(a_db: Path) -> None:
    _alert(a_db)
    s = compliance_store.alert_stats(a_db)
    assert s["recent_30d"] == 1


def test_alert_trend_returns_days(a_db: Path) -> None:
    _alert(a_db)
    _alert(a_db)
    trend = compliance_store.alert_trend(a_db, days=30)
    total = sum(r["cnt"] for r in trend)
    assert total == 2


# ---------------------------------------------------------------------------
# case_stats
# ---------------------------------------------------------------------------


def test_case_stats_empty(ca_db: Path) -> None:
    s = compliance_store.case_stats(ca_db)
    assert s["total"] == 0
    assert s["active"] == 0


def test_case_stats_counts(ca_db: Path) -> None:
    _case(ca_db)
    _case(ca_db, title="B")
    s = compliance_store.case_stats(ca_db)
    assert s["total"] == 2
    assert s["active"] == 2
    assert s["by_state"].get("open", 0) == 2


def test_case_stats_unassigned(ca_db: Path) -> None:
    _case(ca_db)
    _case(ca_db, title="B", assigned_to="Alice")
    s = compliance_store.case_stats(ca_db)
    assert s["unassigned"] == 1


def test_case_stats_by_investigator(ca_db: Path) -> None:
    _case(ca_db, title="1", assigned_to="Alice")
    _case(ca_db, title="2", assigned_to="Alice")
    _case(ca_db, title="3", assigned_to="Bob")
    s = compliance_store.case_stats(ca_db)
    investigators = {r["assigned_to"]: r["cnt"] for r in s["by_investigator"]}
    assert investigators["Alice"] == 2
    assert investigators["Bob"] == 1


def test_case_stats_escalated_to_sar(ca_db: Path) -> None:
    c = _case(ca_db, title="Esc")
    case_store.start_investigation(ca_db, c["id"], actor="Analyst")
    case_store.submit_for_review(ca_db, c["id"], actor="Analyst")
    case_store.escalate_to_sar(ca_db, c["id"], actor="MLRO", sar_ref="SAR-X")
    s = compliance_store.case_stats(ca_db)
    assert s["escalated_to_sar"] == 1
    assert s["active"] == 0


# ---------------------------------------------------------------------------
# sar_stats
# ---------------------------------------------------------------------------


def test_sar_stats_empty(s_db: Path) -> None:
    s = compliance_store.sar_stats(s_db)
    assert s["total"] == 0
    assert s["overdue"] == 0


def test_sar_stats_counts(s_db: Path) -> None:
    _sar(s_db, sar_type="str")
    _sar(s_db, sar_type="ctr")
    s = compliance_store.sar_stats(s_db)
    assert s["total"] == 2
    assert s["by_type"]["str"] == 1
    assert s["by_type"]["ctr"] == 1


def test_sar_stats_overdue(s_db: Path) -> None:
    import sqlite3
    sar = _sar(s_db)
    conn = sqlite3.connect(s_db)
    conn.execute(
        "UPDATE sars SET filing_deadline='2020-01-01T00:00:00+00:00' WHERE id=?",
        (sar["id"],),
    )
    conn.commit()
    conn.close()
    s = compliance_store.sar_stats(s_db)
    assert s["overdue"] == 1


def test_sar_stats_acknowledged_not_overdue(s_db: Path) -> None:
    import sqlite3
    sar = _sar(s_db, narrative="Text.")
    sar = sar_store.submit_for_review(s_db, sar["id"], actor="Analyst")
    sar = sar_store.approve_sar(s_db, sar["id"], actor="MLRO")
    sar = sar_store.submit_to_regulator(s_db, sar["id"], actor="MLRO", submission_ref="REF")
    sar_store.acknowledge_receipt(s_db, sar["id"], actor="MLRO", acknowledgement_ref="ACK")
    # Backdate deadline — should NOT appear as overdue because acknowledged
    conn = sqlite3.connect(s_db)
    conn.execute(
        "UPDATE sars SET filing_deadline='2020-01-01T00:00:00+00:00' WHERE id=?",
        (sar["id"],),
    )
    conn.commit()
    conn.close()
    s = compliance_store.sar_stats(s_db)
    assert s["overdue"] == 0


# ---------------------------------------------------------------------------
# customer_stats
# ---------------------------------------------------------------------------


def test_customer_stats_empty(cu_db: Path) -> None:
    s = compliance_store.customer_stats(cu_db)
    assert s["total"] == 0
    assert s["kyc_completion_rate"] == 0.0


def test_customer_stats_kyc_rate(cu_db: Path) -> None:
    c1 = _customer(cu_db, name="A")
    c2 = _customer(cu_db, name="B")
    customer_store.update_kyc_status(cu_db, c1["id"], kyc_status="verified", actor="Analyst")
    s = compliance_store.customer_stats(cu_db)
    assert s["total"] == 2
    assert s["kyc_verified"] == 1
    assert s["kyc_completion_rate"] == 50.0


def test_customer_stats_tier_distribution(cu_db: Path) -> None:
    c = _customer(cu_db)
    customer_store.update_risk_tier(cu_db, c["id"], risk_tier="high", actor="Analyst")
    s = compliance_store.customer_stats(cu_db)
    assert s["by_tier"].get("high", 0) == 1
    assert s["high_risk"] == 1


def test_customer_stats_pep(cu_db: Path) -> None:
    _customer(cu_db, pep_flag=True)
    _customer(cu_db, name="Normal")
    s = compliance_store.customer_stats(cu_db)
    assert s["pep"] == 1


# ---------------------------------------------------------------------------
# rule_stats
# ---------------------------------------------------------------------------


def test_rule_stats_empty(r_db: Path) -> None:
    s = compliance_store.rule_stats(r_db)
    assert s["total"] == 0
    assert s["enabled"] == 0


def test_rule_stats_with_seeded_rules(r_db: Path) -> None:
    seed_rules(r_db)
    s = compliance_store.rule_stats(r_db)
    assert s["total"] > 0
    assert s["enabled"] == s["total"]
    assert "ctr" in s["by_type"]
    assert "pep_related" in s["by_type"]


def test_rule_stats_hit_rate(r_db: Path) -> None:
    seed_rules(r_db)
    rule_engine.evaluate(r_db, {"amount": 700_000, "pep_flag": True})
    s = compliance_store.rule_stats(r_db)
    assert s["total_evaluations"] > 0
    assert s["total_hits"] > 0
    assert s["hit_rate"] > 0
    assert len(s["top_triggered"]) > 0


def test_rule_stats_nonexistent_db(tmp_path: Path) -> None:
    s = compliance_store.rule_stats(tmp_path / "missing.db")
    assert s["total"] == 0


# ---------------------------------------------------------------------------
# overdue_summary
# ---------------------------------------------------------------------------


def test_overdue_summary_all_zero(a_db, ca_db, s_db, cu_db) -> None:
    ov = compliance_store.overdue_summary(a_db, ca_db, s_db, cu_db)
    assert ov["total"] == 0
    assert ov["overdue_alerts"] == 0
    assert ov["overdue_sars"] == 0
    assert ov["customers_due_review"] == 0


def test_overdue_summary_counts_sars(a_db, ca_db, s_db, cu_db) -> None:
    import sqlite3
    sar = _sar(s_db)
    conn = sqlite3.connect(s_db)
    conn.execute(
        "UPDATE sars SET filing_deadline='2020-01-01T00:00:00+00:00' WHERE id=?",
        (sar["id"],),
    )
    conn.commit()
    conn.close()
    ov = compliance_store.overdue_summary(a_db, ca_db, s_db, cu_db)
    assert ov["overdue_sars"] == 1
    assert ov["total"] >= 1


# ---------------------------------------------------------------------------
# compliance_health
# ---------------------------------------------------------------------------


def test_health_score_zero_on_empty_dbs(a_db, ca_db, s_db, cu_db) -> None:
    h = compliance_store.compliance_health(a_db, ca_db, s_db, cu_db)
    assert 0 <= h["total"] <= 100
    # SAR pillar = 25 (no overdue), others = 0 because no data
    assert h["sar_score"] == 25.0
    assert h["kyc_score"] == 0.0


def test_health_score_satisfactory_with_good_data(a_db, ca_db, s_db, cu_db) -> None:
    # All customers KYC verified → kyc_score = 25
    for i in range(5):
        c = _customer(cu_db, name=f"C{i}")
        customer_store.update_kyc_status(cu_db, c["id"], kyc_status="verified", actor="A")
    # No overdue SARs → sar_score = 25
    h = compliance_store.compliance_health(a_db, ca_db, s_db, cu_db)
    assert h["kyc_score"] == 25.0
    assert h["sar_score"] == 25.0
    assert h["total"] >= 50.0


def test_health_score_penalises_overdue_sar(a_db, ca_db, s_db, cu_db) -> None:
    import sqlite3
    for _ in range(3):
        sar = _sar(s_db)
        conn = sqlite3.connect(s_db)
        conn.execute(
            "UPDATE sars SET filing_deadline='2020-01-01T00:00:00+00:00' WHERE id=?",
            (sar["id"],),
        )
        conn.commit()
        conn.close()
    h = compliance_store.compliance_health(a_db, ca_db, s_db, cu_db)
    # 3 overdue × 5 pts = 15 pts deducted → sar_score = max(0, 25-15) = 10
    assert h["sar_score"] == 10.0


def test_health_rating_strings(a_db, ca_db, s_db, cu_db) -> None:
    h = compliance_store.compliance_health(a_db, ca_db, s_db, cu_db)
    assert h["rating"] in ("SATISFACTORY", "NEEDS_IMPROVEMENT", "DEFICIENT", "CRITICAL")
