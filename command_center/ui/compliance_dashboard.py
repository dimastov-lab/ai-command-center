"""AML Compliance Dashboard — сводная панель для приёмки банком.

Агрегирует метрики всех AML-модулей: алерты, дела, SAR, клиенты, правила.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from command_center import (
    alert_store,
    case_store,
    compliance_store,
    customer_store,
    rule_engine,
    sar_store,
)

_HEALTH_COLOR: dict[str, str] = {
    "SATISFACTORY":      "🟢",
    "NEEDS_IMPROVEMENT": "🟡",
    "DEFICIENT":         "🔴",
    "CRITICAL":          "🚨",
}

_TIER_ICON: dict[str, str] = {
    "low": "🟢",
    "medium": "🟡",
    "high": "🔴",
    "pep": "⚠️",
}

_SAR_TYPE_LABEL: dict[str, str] = {
    "str": "STR",
    "ctr": "CTR",
    "pep": "PEP",
    "sanctions": "Санкции",
    "other": "Прочее",
}


def render(
    alert_db: Path | None = None,
    case_db: Path | None = None,
    sar_db: Path | None = None,
    customer_db: Path | None = None,
    rules_db: Path | None = None,
) -> None:
    # Resolve default paths and init DBs
    if alert_db is None:
        alert_db = alert_store.resolve_db_path()
        if alert_db.exists():
            alert_store.init_db(alert_db)
    if case_db is None:
        case_db = case_store.resolve_db_path()
        if case_db.exists():
            case_store.init_db(case_db)
    if sar_db is None:
        sar_db = sar_store.resolve_db_path()
        if sar_db.exists():
            sar_store.init_db(sar_db)
    if customer_db is None:
        customer_db = customer_store.resolve_db_path()
        if customer_db.exists():
            customer_store.init_db(customer_db)
    if rules_db is None:
        rules_db = rule_engine.resolve_db_path()
        if rules_db.exists():
            rule_engine.init_db(rules_db)

    # Load all stats once
    a = compliance_store.alert_stats(alert_db)
    ca = compliance_store.case_stats(case_db)
    s = compliance_store.sar_stats(sar_db)
    cu = compliance_store.customer_stats(customer_db)
    ru = compliance_store.rule_stats(rules_db)
    health = compliance_store.compliance_health(alert_db, case_db, sar_db, customer_db)
    overdue = compliance_store.overdue_summary(alert_db, case_db, sar_db, customer_db)

    st.header("Compliance Dashboard")

    # -----------------------------------------------------------------------
    # Health score banner
    # -----------------------------------------------------------------------
    _render_health_banner(health, overdue)

    # -----------------------------------------------------------------------
    # KPI tiles
    # -----------------------------------------------------------------------
    _render_kpi_row(a, ca, s, cu, overdue)

    st.divider()

    # -----------------------------------------------------------------------
    # Tabs
    # -----------------------------------------------------------------------
    tab_alerts, tab_cases, tab_sars, tab_customers, tab_rules = st.tabs([
        "📋 Алерты",
        "📁 Дела",
        "📤 SAR",
        "👥 Клиенты",
        "⚙️ Правила",
    ])

    with tab_alerts:
        _render_alerts_section(alert_db, a)

    with tab_cases:
        _render_cases_section(ca)

    with tab_sars:
        _render_sars_section(s)

    with tab_customers:
        _render_customers_section(cu)

    with tab_rules:
        _render_rules_section(ru)


# ---------------------------------------------------------------------------
# Health banner
# ---------------------------------------------------------------------------


def _render_health_banner(health: dict, overdue: dict) -> None:
    rating = health["rating"]
    icon = _HEALTH_COLOR.get(rating, "❓")
    score = health["total"]

    col_score, col_overdue = st.columns([2, 3])
    with col_score:
        if rating == "SATISFACTORY":
            st.success(f"{icon} **Compliance Health: {score}/100 — {rating}**")
        elif rating == "NEEDS_IMPROVEMENT":
            st.warning(f"{icon} **Compliance Health: {score}/100 — {rating}**")
        else:
            st.error(f"{icon} **Compliance Health: {score}/100 — {rating}**")

    with col_overdue:
        total_overdue = overdue["total"]
        if total_overdue == 0:
            st.success("✅ Просроченных элементов нет")
        else:
            parts = []
            if overdue["overdue_alerts"]:
                parts.append(f"алертов: {overdue['overdue_alerts']}")
            if overdue["overdue_sars"]:
                parts.append(f"SAR: {overdue['overdue_sars']}")
            if overdue["customers_due_review"]:
                parts.append(f"ревью клиентов: {overdue['customers_due_review']}")
            st.error(f"⚠️ **Просрочено — {total_overdue}** ({', '.join(parts)})")

    # Health pillars
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("KYC", f"{health['kyc_score']}/25")
    c2.metric("SAR-подача", f"{health['sar_score']}/25")
    c3.metric("Закрытие алертов", f"{health['alert_score']}/25")
    c4.metric("Закрытие дел", f"{health['case_score']}/25")


# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------


def _render_kpi_row(a: dict, ca: dict, s: dict, cu: dict, overdue: dict) -> None:
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Открытых алертов", a["open"],
              delta=f"-{a['overdue']} просрочено" if a["overdue"] else None,
              delta_color="inverse")
    c2.metric("Активных дел", ca["active"],
              delta=f"{ca['unassigned']} без следователя" if ca["unassigned"] else None,
              delta_color="inverse")
    c3.metric("Ожидают подачи SAR", s["pending_filing"],
              delta=f"-{s['overdue']} просрочено" if s["overdue"] else None,
              delta_color="inverse")
    c4.metric("SAR подтверждены", s["acknowledged"])
    c5.metric("Клиентов (всего)", cu["total"],
              delta=f"{cu['high_risk']} высокий риск" if cu["high_risk"] else None,
              delta_color="inverse")
    c6.metric("KYC завершено", f"{cu['kyc_completion_rate']}%")


# ---------------------------------------------------------------------------
# Alerts section
# ---------------------------------------------------------------------------


def _render_alerts_section(alert_db: Path, a: dict) -> None:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("По состоянию")
        by_state = a["by_state"]
        if by_state:
            st.bar_chart(by_state)
        else:
            st.info("Алертов нет.")

    with col2:
        st.subheader("По исходу")
        by_outcome = a["by_outcome"]
        if by_outcome:
            st.bar_chart(by_outcome)
        else:
            st.info("Исходов нет.")

    col3, col4, col5 = st.columns(3)
    col3.metric("True Positive", a["true_positive"])
    col4.metric("False Positive", a["false_positive"])
    col5.metric("Создано за 30 дней", a["recent_30d"])

    st.subheader("Тренд (30 дней)")
    trend = compliance_store.alert_trend(alert_db, days=30)
    if trend:
        trend_data = {r["day"]: r["cnt"] for r in trend}
        st.bar_chart(trend_data)
    else:
        st.info("Данных о трендах нет.")


# ---------------------------------------------------------------------------
# Cases section
# ---------------------------------------------------------------------------


def _render_cases_section(ca: dict) -> None:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("По состоянию")
        if ca["by_state"]:
            st.bar_chart(ca["by_state"])
        else:
            st.info("Дел нет.")

    with col2:
        st.subheader("По приоритету")
        if ca["by_priority"]:
            st.bar_chart(ca["by_priority"])
        else:
            st.info("Дел нет.")

    col3, col4, col5 = st.columns(3)
    col3.metric("Эскалировано в SAR", ca["escalated_to_sar"])
    col4.metric("Закрыто за 30 дней", ca["closed_30d"])
    col5.metric("Без следователя", ca["unassigned"],
                delta="требуют назначения" if ca["unassigned"] else None,
                delta_color="inverse")

    if ca["by_investigator"]:
        st.subheader("Нагрузка по следователям (активные дела)")
        inv_data = {r["assigned_to"]: r["cnt"] for r in ca["by_investigator"]}
        st.bar_chart(inv_data)


# ---------------------------------------------------------------------------
# SARs section
# ---------------------------------------------------------------------------


def _render_sars_section(s: dict) -> None:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("По состоянию")
        if s["by_state"]:
            st.bar_chart(s["by_state"])
        else:
            st.info("SAR нет.")

    with col2:
        st.subheader("По типу")
        if s["by_type"]:
            labeled = {_SAR_TYPE_LABEL.get(k, k): v for k, v in s["by_type"].items()}
            st.bar_chart(labeled)
        else:
            st.info("SAR нет.")

    col3, col4, col5 = st.columns(3)
    col3.metric("Всего SAR", s["total"])
    col4.metric("Подтверждено регулятором", s["acknowledged"])
    col5.metric("Подано за 30 дней", s["submitted_30d"])

    if s["overdue_items"]:
        st.error(f"🚨 Просрочено SAR: {s['overdue']} — нарушение сроков 115-ФЗ!")
        st.dataframe(
            [{"Номер": r["sar_number"], "Тип": r["sar_type"],
              "Статус": r["state"], "Дедлайн": r["filing_deadline"][:10]}
             for r in s["overdue_items"]],
            use_container_width=True,
        )
    else:
        st.success("✅ Все SAR поданы в срок.")


# ---------------------------------------------------------------------------
# Customers section
# ---------------------------------------------------------------------------


def _render_customers_section(cu: dict) -> None:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Распределение по риск-тирам")
        if cu["by_tier"]:
            labeled = {f"{_TIER_ICON.get(k, '')} {k}": v for k, v in cu["by_tier"].items()}
            st.bar_chart(labeled)
        else:
            st.info("Клиентов нет.")

    with col2:
        st.subheader("KYC-статусы")
        if cu["by_kyc_status"]:
            st.bar_chart(cu["by_kyc_status"])
        else:
            st.info("Клиентов нет.")

    col3, col4, col5, col6 = st.columns(4)
    col3.metric("PEP", cu["pep"],
                delta="требуют EDD" if cu["pep"] else None,
                delta_color="inverse")
    col4.metric("Негативные медиа", cu["adverse_media"])
    col5.metric("Высокий риск + PEP", cu["high_risk"])
    col6.metric("Требуют ревью", cu["due_for_review"],
                delta="просрочено CDD" if cu["due_for_review"] else None,
                delta_color="inverse")

    # KYC completion gauge
    rate = cu["kyc_completion_rate"]
    bar_len = int(rate / 100 * 20)
    gauge = "█" * bar_len + "░" * (20 - bar_len)
    if rate >= 80:
        st.success(f"KYC completion rate: **{rate}%** [{gauge}]")
    elif rate >= 60:
        st.warning(f"KYC completion rate: **{rate}%** [{gauge}]")
    else:
        st.error(f"KYC completion rate: **{rate}%** [{gauge}] — ниже допустимого порога")


# ---------------------------------------------------------------------------
# Rules section
# ---------------------------------------------------------------------------


def _render_rules_section(ru: dict) -> None:
    col1, col2, col3 = st.columns(3)
    col1.metric("Правил всего", ru["total"])
    col2.metric("Активных правил", ru["enabled"])
    col3.metric("Hit rate", f"{ru.get('hit_rate', 0)}%")

    col4, col5 = st.columns(2)
    col4.metric("Оценок выполнено", ru.get("total_evaluations", 0))
    col5.metric("Срабатываний всего", ru.get("total_hits", 0))

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("Правила по типу алерта")
        if ru.get("by_type"):
            st.bar_chart(ru["by_type"])
        else:
            st.info("Правил нет.")

    with col_b:
        st.subheader("Топ-10 правил по срабатываниям")
        top = ru.get("top_triggered", [])
        if top:
            st.dataframe(
                [{"Правило": r["name"],
                  "Тип": r["alert_type"],
                  "Приоритет": r["priority_weight"],
                  "Срабатываний": r["hit_count"]}
                 for r in top],
                use_container_width=True,
            )
        else:
            st.info("Нет данных о срабатываниях.")
