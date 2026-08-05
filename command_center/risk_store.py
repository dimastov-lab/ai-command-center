"""AML Risk scoring — customer risk assessment with explainable factors."""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from command_center import storage
from command_center.customer_store import NotFound, get_customer, init_db as _init_customers

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 1

# Risk factor weights (sum used to derive score 0–100)
_FACTOR_WEIGHTS: dict[str, int] = {
    "pep_flag": 35,
    "adverse_media_flag": 25,
    "high_risk_country": 20,
    "high_risk_industry": 10,
    "high_product_risk": 10,
}

# Countries with elevated AML risk (115-ФЗ / FATF high-risk list, illustrative)
HIGH_RISK_COUNTRIES: frozenset[str] = frozenset({
    "AF", "BY", "CU", "ER", "IR", "KP", "LY", "MM", "RU", "SD", "SO", "SS", "SY", "VE", "YE", "ZW",
})

# Industries with elevated AML risk
HIGH_RISK_INDUSTRIES: frozenset[str] = frozenset({
    "gambling", "crypto", "cash_intensive", "money_services", "arms", "precious_metals",
})

# Products/channels with elevated risk
HIGH_RISK_PRODUCTS: frozenset[str] = frozenset({
    "anonymous_account", "correspondent_banking", "private_banking", "shell_company",
})


def _score_to_tier(score: int) -> str:
    if score >= 60:
        return "high"
    if score >= 30:
        return "medium"
    return "low"


def resolve_db_path(root: Path | None = None) -> Path:
    return storage.resolve_data_dir(root or ROOT) / "aml_risk.db"


@contextmanager
def _db(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path) -> None:
    """Create risk assessment tables."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        conn.executescript(
            """
            BEGIN IMMEDIATE;

            CREATE TABLE IF NOT EXISTS risk_assessments (
                id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL,
                score INTEGER NOT NULL,
                tier TEXT NOT NULL CHECK(tier IN ('low','medium','high','pep')),
                factors TEXT NOT NULL,
                assessed_by TEXT NOT NULL,
                assessed_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL DEFAULT 1
            );

            PRAGMA user_version = 1;
            COMMIT;
            """
        )
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def compute_risk_score(
    customer_db_path: Path,
    risk_db_path: Path,
    customer_id: str,
    *,
    products: list[str] | None = None,
    actor: str,
) -> dict:
    """Compute risk score for a customer and persist the assessment.

    Returns dict with: customer_id, score (0-100), tier, factors (list of
    {factor, weight, triggered} dicts), assessment_id.
    """
    customer = get_customer(customer_db_path, customer_id)

    factors: list[dict] = []
    score = 0

    # PEP flag
    triggered = bool(customer.get("pep_flag"))
    w = _FACTOR_WEIGHTS["pep_flag"]
    factors.append({"factor": "pep_flag", "weight": w, "triggered": triggered})
    if triggered:
        score += w

    # Adverse media
    triggered = bool(customer.get("adverse_media_flag"))
    w = _FACTOR_WEIGHTS["adverse_media_flag"]
    factors.append({"factor": "adverse_media_flag", "weight": w, "triggered": triggered})
    if triggered:
        score += w

    # High-risk country
    country = customer.get("country") or ""
    triggered = country.upper() in HIGH_RISK_COUNTRIES
    w = _FACTOR_WEIGHTS["high_risk_country"]
    factors.append({"factor": "high_risk_country", "weight": w, "triggered": triggered, "value": country})
    if triggered:
        score += w

    # High-risk industry
    industry = customer.get("industry") or ""
    triggered = industry.lower() in HIGH_RISK_INDUSTRIES
    w = _FACTOR_WEIGHTS["high_risk_industry"]
    factors.append({"factor": "high_risk_industry", "weight": w, "triggered": triggered, "value": industry})
    if triggered:
        score += w

    # High-risk product
    prods = products or []
    triggered = any(p.lower() in HIGH_RISK_PRODUCTS for p in prods)
    w = _FACTOR_WEIGHTS["high_product_risk"]
    factors.append({"factor": "high_product_risk", "weight": w, "triggered": triggered, "value": prods})
    if triggered:
        score += w

    # PEP customers always get pep tier regardless of score
    tier = "pep" if customer.get("pep_flag") else _score_to_tier(score)

    import json
    assessment_id = str(uuid.uuid4())
    now = _utcnow()
    with _db(risk_db_path) as conn:
        conn.execute(
            """
            INSERT INTO risk_assessments(id, customer_id, score, tier, factors, assessed_by, assessed_at, schema_version)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (assessment_id, customer_id, score, tier, json.dumps(factors), actor, now, SCHEMA_VERSION),
        )

    return {
        "assessment_id": assessment_id,
        "customer_id": customer_id,
        "score": score,
        "tier": tier,
        "factors": factors,
        "assessed_at": now,
        "assessed_by": actor,
    }


def get_latest_assessment(risk_db_path: Path, customer_id: str) -> dict | None:
    """Return the most recent risk assessment for a customer, or None."""
    import json
    with _db(risk_db_path) as conn:
        row = conn.execute(
            "SELECT * FROM risk_assessments WHERE customer_id=? ORDER BY assessed_at DESC LIMIT 1",
            (customer_id,),
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["factors"] = json.loads(result["factors"])
    return result


def list_assessments(risk_db_path: Path, customer_id: str) -> list[dict]:
    """Return all assessments for a customer, newest first."""
    import json
    with _db(risk_db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM risk_assessments WHERE customer_id=? ORDER BY assessed_at DESC",
            (customer_id,),
        ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["factors"] = json.loads(d["factors"])
        results.append(d)
    return results
