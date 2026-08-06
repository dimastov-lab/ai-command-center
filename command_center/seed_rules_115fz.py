"""Seed script: standard 115-ФЗ / FATF AML rule pack.

Run once (or idempotently on fresh DB):
    python -m command_center.seed_rules_115fz [--db path/to/rules.db]
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from command_center import rule_engine

# FATF high-risk jurisdictions (blacklist + greylisted at publication cutoff)
# Defined per FATF June 2024 list + 115-ФЗ Appendix (ЦБ РФ список)
_FATF_HIGH_RISK = [
    "AF",  # Afghanistan
    "BY",  # Belarus
    "MM",  # Myanmar
    "CF",  # Central African Republic
    "CD",  # DR Congo
    "CU",  # Cuba
    "GN",  # Guinea
    "GW",  # Guinea-Bissau
    "HT",  # Haiti
    "IR",  # Iran
    "IQ",  # Iraq
    "LA",  # Laos
    "LY",  # Libya
    "ML",  # Mali
    "MZ",  # Mozambique
    "NI",  # Nicaragua
    "KP",  # North Korea
    "SS",  # South Sudan
    "SD",  # Sudan
    "SY",  # Syria
    "TZ",  # Tanzania
    "UG",  # Uganda
    "VU",  # Vanuatu
    "YE",  # Yemen
    "ZW",  # Zimbabwe
]

# OFAC primary sanctions targets (US list used as international reference)
_OFAC_SANCTIONED = [
    "CU",  # Cuba
    "IR",  # Iran
    "KP",  # North Korea
    "RU",  # Russia
    "SY",  # Syria
    "BY",  # Belarus
    "VE",  # Venezuela
]

_RULES: list[dict] = [
    # -----------------------------------------------------------------------
    # 115-ФЗ Art. 6 — Mandatory control: cash transactions >= 600,000 RUB
    # -----------------------------------------------------------------------
    {
        "name": "115-ФЗ Ст.6: наличные ≥ 600 000 руб (CTR)",
        "description": (
            "Обязательный контроль операций с наличными на сумму от 600 тыс. руб. "
            "(ст. 6 Федерального закона 115-ФЗ). Подлежит направлению в Росфинмониторинг."
        ),
        "condition_op": "amount_gte",
        "threshold": 600_000.0,
        "value_list": None,
        "alert_type": "ctr",
        "priority_weight": "high",
        "jurisdiction": "RU",
    },
    # -----------------------------------------------------------------------
    # 115-ФЗ Art. 7 — Suspicious transaction report (STR) thresholds
    # -----------------------------------------------------------------------
    {
        "name": "115-ФЗ Ст.7: подозрительная операция ≥ 100 000 руб (STR)",
        "description": (
            "Операции, вызывающие подозрение, на сумму от 100 тыс. руб. подлежат "
            "направлению в Росфинмониторинг по ст. 7 115-ФЗ (подозрительные операции)."
        ),
        "condition_op": "amount_gte",
        "threshold": 100_000.0,
        "value_list": None,
        "alert_type": "str",
        "priority_weight": "medium",
        "jurisdiction": "RU",
    },
    # -----------------------------------------------------------------------
    # High-frequency: structuring detection
    # -----------------------------------------------------------------------
    {
        "name": "Структурирование: > 5 операций в период",
        "description": (
            "Дробление крупной суммы путём проведения множественных операций — "
            "признак структурирования (typology FATF). "
            "Порог: более 5 транзакций за наблюдаемый период."
        ),
        "condition_op": "frequency_gt",
        "threshold": 5.0,
        "value_list": None,
        "alert_type": "structuring",
        "priority_weight": "high",
        "jurisdiction": "global",
    },
    # -----------------------------------------------------------------------
    # PEP flag
    # -----------------------------------------------------------------------
    {
        "name": "PEP: политически значимое лицо",
        "description": (
            "Клиент или связанная сторона идентифицированы как PEP "
            "(politically exposed person). Требует усиленной проверки EDD/CDD."
        ),
        "condition_op": "pep_flag",
        "threshold": None,
        "value_list": None,
        "alert_type": "pep_related",
        "priority_weight": "critical",
        "jurisdiction": "global",
    },
    # -----------------------------------------------------------------------
    # Adverse media
    # -----------------------------------------------------------------------
    {
        "name": "Негативные медиа по клиенту",
        "description": (
            "Зафиксировано упоминание клиента в негативных СМИ, связанных с "
            "отмыванием, коррупцией или преступностью."
        ),
        "condition_op": "adverse_media",
        "threshold": None,
        "value_list": None,
        "alert_type": "str",
        "priority_weight": "high",
        "jurisdiction": "global",
    },
    # -----------------------------------------------------------------------
    # FATF blacklist / greylisted countries
    # -----------------------------------------------------------------------
    {
        "name": "FATF: высокорисковая юрисдикция",
        "description": (
            "Страна контрагента или клиента входит в список высокорисковых "
            "юрисдикций FATF (чёрный/серый список). Требует EDD."
        ),
        "condition_op": "country_in",
        "threshold": None,
        "value_list": _FATF_HIGH_RISK,
        "alert_type": "high_risk_country",
        "priority_weight": "high",
        "jurisdiction": "global",
    },
    # -----------------------------------------------------------------------
    # OFAC sanctioned countries
    # -----------------------------------------------------------------------
    {
        "name": "OFAC: санкционная юрисдикция",
        "description": (
            "Страна под первичными санкциями OFAC/ООН. "
            "Операции, как правило, запрещены без специального разрешения OFAC."
        ),
        "condition_op": "country_in",
        "threshold": None,
        "value_list": _OFAC_SANCTIONED,
        "alert_type": "sanctions",
        "priority_weight": "critical",
        "jurisdiction": "global",
    },
    # -----------------------------------------------------------------------
    # High-risk industries per 115-ФЗ and FATF guidance
    # -----------------------------------------------------------------------
    {
        "name": "Высокорисковая отрасль (115-ФЗ / FATF)",
        "description": (
            "Клиент работает в отрасли с повышенным риском ОД/ФТ: казино, "
            "криптовалюта, торговля оружием, ювелирные изделия и т.д."
        ),
        "condition_op": "industry_in",
        "threshold": None,
        "value_list": ["gambling", "crypto", "arms", "jewelry", "cash_intensive", "money_services"],
        "alert_type": "risk_escalation",
        "priority_weight": "medium",
        "jurisdiction": "global",
    },
    # -----------------------------------------------------------------------
    # High-risk products
    # -----------------------------------------------------------------------
    {
        "name": "Высокорисковый продукт (анонимный счёт, корр. банкинг)",
        "description": (
            "Клиент использует продукты с повышенным риском ОД: анонимные счета, "
            "корреспондентский банкинг, private banking, bearer instruments."
        ),
        "condition_op": "product_in",
        "threshold": None,
        "value_list": ["anonymous_account", "correspondent_banking", "private_banking", "bearer_instrument"],
        "alert_type": "risk_escalation",
        "priority_weight": "high",
        "jurisdiction": "global",
    },
    # -----------------------------------------------------------------------
    # Large non-cash: 115-ФЗ Art. 6.1 — immovable property / securities ≥ 5M RUB
    # -----------------------------------------------------------------------
    {
        "name": "115-ФЗ Ст.6.1: операции с недвижимостью / ЦБ ≥ 5 000 000 руб",
        "description": (
            "Операции с недвижимостью или ценными бумагами на сумму от 5 млн руб. — "
            "расширенный обязательный контроль по ст. 6.1 115-ФЗ."
        ),
        "condition_op": "amount_gte",
        "threshold": 5_000_000.0,
        "value_list": None,
        "alert_type": "ctr",
        "priority_weight": "critical",
        "jurisdiction": "RU",
    },
    # -----------------------------------------------------------------------
    # Medium risk tier escalation
    # -----------------------------------------------------------------------
    {
        "name": "Клиент с риск-тиром high или pep",
        "description": (
            "Событие связано с клиентом высокого риска или PEP — "
            "все транзакции подлежат усиленному мониторингу."
        ),
        "condition_op": "risk_tier_in",
        "threshold": None,
        "value_list": ["high", "pep"],
        "alert_type": "risk_escalation",
        "priority_weight": "high",
        "jurisdiction": "global",
    },
]


def _rule_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT id FROM rules WHERE name=?", (name,)).fetchone()
    return row is not None


def seed(db_path: Path) -> int:
    """Seed 115-ФЗ rules into db_path. Skips rules that already exist by name.

    Returns the number of rules created.
    """
    rule_engine.init_db(db_path)
    created = 0
    # Use a plain connection just to check existence; create_rule uses its own _db()
    check_conn = sqlite3.connect(db_path, timeout=5)
    try:
        existing = {
            row[0]
            for row in check_conn.execute("SELECT name FROM rules").fetchall()
        }
    finally:
        check_conn.close()

    for spec in _RULES:
        if spec["name"] in existing:
            continue
        rule_engine.create_rule(
            db_path,
            name=spec["name"],
            description=spec.get("description"),
            condition_op=spec["condition_op"],
            threshold=spec.get("threshold"),
            value_list=spec.get("value_list"),
            alert_type=spec["alert_type"],
            priority_weight=spec["priority_weight"],
            jurisdiction=spec.get("jurisdiction", "global"),
        )
        created += 1

    return created


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed 115-ФЗ AML rules")
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()
    db_path = args.db or rule_engine.resolve_db_path()
    n = seed(db_path)
    print(f"Seeded {n} rules into {db_path}")


if __name__ == "__main__":
    main()
