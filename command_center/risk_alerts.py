"""Risk→Alert bridge — triggers an AML alert when a customer risk tier escalates."""

from __future__ import annotations

from pathlib import Path

from command_center import alert_store, customer_store, risk_store


def assess_and_alert(
    customer_db: Path,
    risk_db: Path,
    alert_db: Path,
    customer_id: str,
    *,
    products: list[str] | None = None,
    actor: str,
) -> dict:
    """Run risk assessment; create an alert if the resulting tier is high or pep.

    Returns dict with keys: assessment, alert (or None).
    """
    customer = customer_store.get_customer(customer_db, customer_id)
    old_tier = customer["risk_tier"]

    assessment = risk_store.compute_risk_score(
        customer_db, risk_db, customer_id, products=products, actor=actor
    )
    new_tier = assessment["tier"]

    # Update stored risk tier on the customer record
    customer_store.update_risk_tier(customer_db, customer_id, risk_tier=new_tier, actor=actor)

    alert: dict | None = None
    if new_tier in ("high", "pep") and old_tier not in ("high", "pep"):
        # Tier escalation to high/pep — create an alert
        triggered_factors = [
            f["factor"] for f in assessment["factors"] if f["triggered"]
        ]
        alert = alert_store.create_alert(
            alert_db,
            source="risk_assessment",
            source_ref=assessment["assessment_id"],
            subject_id=customer_id,
            subject_type="customer",
            trigger_desc=f"Risk tier escalated to {new_tier}: {', '.join(triggered_factors)}",
            priority="high" if new_tier == "pep" else "high",
            priority_rationale=f"Score {assessment['score']}/100; tier changed {old_tier}→{new_tier}",
            due_at=None,
            actor=actor,
        )

    return {"assessment": assessment, "alert": alert}
