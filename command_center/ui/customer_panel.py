"""AML Customer Panel — Streamlit UI for customer profiles, KYC and risk."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from command_center import customer_store, risk_store
from command_center.customer_store import InvalidValue, NotFound
from command_center.risk_alerts import assess_and_alert

_TIER_ICONS: dict[str, str] = {
    "low": "🟢",
    "medium": "🟡",
    "high": "🔴",
    "pep": "⚠️",
}

_KYC_ICONS: dict[str, str] = {
    "verified": "✅",
    "in_progress": "🔄",
    "pending": "⏳",
    "expired": "⚠️",
    "rejected": "❌",
}


def render(
    customer_db: Path | None = None,
    risk_db: Path | None = None,
    alert_db: Path | None = None,
) -> None:
    """Render the full Customer panel."""
    if customer_db is None:
        customer_db = customer_store.resolve_db_path()
    if risk_db is None:
        risk_db = risk_store.resolve_db_path()
    if alert_db is None:
        from command_center import alert_store
        alert_db = alert_store.resolve_db_path()
        alert_store.init_db(alert_db)

    customer_store.init_db(customer_db)
    risk_store.init_db(risk_db)

    st.header("Клиенты AML")

    tab_list, tab_new, tab_detail, tab_due = st.tabs(
        ["Список", "Добавить", "Профиль", "К ревью"]
    )

    with tab_list:
        _render_list(customer_db)

    with tab_new:
        _render_new_customer(customer_db)

    with tab_detail:
        _render_detail(customer_db, risk_db, alert_db)

    with tab_due:
        _render_due_for_review(customer_db, risk_db, alert_db)


# ---------------------------------------------------------------------------
# Tab: List
# ---------------------------------------------------------------------------


def _render_list(customer_db: Path) -> None:
    col1, col2 = st.columns(2)
    with col1:
        tier_filter = st.multiselect("Риск-тир", ["low", "medium", "high", "pep"])
    with col2:
        kyc_filter = st.multiselect("KYC статус", list(customer_store.KYC_STATUSES))

    customers = customer_store.list_customers(customer_db)
    if tier_filter:
        customers = [c for c in customers if c["risk_tier"] in tier_filter]
    if kyc_filter:
        customers = [c for c in customers if c["kyc_status"] in kyc_filter]

    if not customers:
        st.info("Клиенты не найдены.")
        return

    rows = [
        {
            "ID": c["id"][:8],
            "Имя": c["name"],
            "Тип": c["customer_type"],
            "Риск": f"{_TIER_ICONS.get(c['risk_tier'], '')} {c['risk_tier']}",
            "KYC": f"{_KYC_ICONS.get(c['kyc_status'], '')} {c['kyc_status']}",
            "CDD": c["cdd_status"],
            "PEP": "Да" if c["pep_flag"] else "—",
            "Страна": c.get("country") or "—",
            "Последнее ревью": (c.get("last_review_at") or "Никогда")[:10],
        }
        for c in customers
    ]
    st.dataframe(rows, use_container_width=True)


# ---------------------------------------------------------------------------
# Tab: New customer
# ---------------------------------------------------------------------------


def _render_new_customer(customer_db: Path) -> None:
    with st.form("new_customer"):
        name = st.text_input("Наименование / ФИО *")
        customer_type = st.selectbox("Тип", ["individual", "legal"])
        country = st.text_input("Страна (ISO2, напр. RU, DE)")
        industry = st.text_input("Отрасль")
        pep_flag = st.checkbox("PEP (политически значимое лицо)")
        adverse_media = st.checkbox("Негативные медиа")
        actor = st.text_input("Создатель (актор)", value="Analyst")

        submitted = st.form_submit_button("Создать клиента")
        if submitted:
            if not name.strip():
                st.error("Укажите наименование.")
            else:
                try:
                    c = customer_store.create_customer(
                        customer_db,
                        name=name.strip(),
                        customer_type=customer_type,
                        country=country.upper() if country else None,
                        industry=industry or None,
                        pep_flag=pep_flag,
                        adverse_media_flag=adverse_media,
                        actor=actor,
                    )
                    st.success(f"Клиент создан: {c['id'][:8]} — {c['name']}")
                except InvalidValue as e:
                    st.error(str(e))


# ---------------------------------------------------------------------------
# Tab: Customer detail + risk assessment
# ---------------------------------------------------------------------------


def _render_detail(customer_db: Path, risk_db: Path, alert_db: Path) -> None:
    customer_id = st.text_input("ID клиента (полный)")
    if not customer_id:
        st.info("Введите ID клиента.")
        return

    try:
        profile = customer_store.get_customer_risk_profile(customer_db, customer_id)
    except NotFound:
        st.error(f"Клиент {customer_id!r} не найден.")
        return

    col1, col2, col3 = st.columns(3)
    col1.metric("Риск-тир", f"{_TIER_ICONS.get(profile['risk_tier'], '')} {profile['risk_tier']}")
    col2.metric("KYC", f"{_KYC_ICONS.get(profile['kyc_status'], '')} {profile['kyc_status']}")
    col3.metric("KYC проверок", f"{profile['kyc_checks_passed']}/{profile['kyc_checks_total']}")

    with st.expander("Полный профиль"):
        st.json({k: v for k, v in profile.items() if k != "kyc_latest"})

    st.subheader("Оценка риска")
    latest = risk_store.get_latest_assessment(risk_db, customer_id)
    if latest:
        st.write(f"**Последняя оценка:** score={latest['score']}, tier={latest['tier']}, {latest['assessed_at'][:10]}")
        st.dataframe(latest["factors"], use_container_width=True)
    else:
        st.info("Оценок риска нет.")

    actor = st.text_input("Актор для оценки", value="Analyst", key="risk_actor")
    products_str = st.text_input("Продукты (через запятую)", key="products")
    if st.button("Запустить оценку риска"):
        products = [p.strip() for p in products_str.split(",") if p.strip()]
        try:
            result = assess_and_alert(
                customer_db, risk_db, alert_db, customer_id,
                products=products or None, actor=actor
            )
            st.success(f"Score: {result['assessment']['score']}, Tier: {result['assessment']['tier']}")
            if result["alert"]:
                st.warning(f"⚠️ Создан алерт! ID: {result['alert']['id'][:8]}")
            st.rerun()
        except (NotFound, Exception) as e:
            st.error(str(e))

    st.subheader("KYC проверки")
    checks = customer_store.get_kyc_checks(customer_db, customer_id)
    if checks:
        st.dataframe(checks, use_container_width=True)
    else:
        st.info("KYC проверок нет.")

    with st.expander("Добавить KYC проверку"):
        with st.form("add_kyc"):
            check_type = st.text_input("Тип проверки (напр. identity, sanctions)")
            result_val = st.selectbox("Результат", ["pass", "fail", "pending", "inconclusive"])
            evidence_ref = st.text_input("Ссылка на документ")
            notes = st.text_area("Примечания")
            kyc_actor = st.text_input("Верификатор", value="Analyst")
            if st.form_submit_button("Добавить"):
                try:
                    customer_store.add_kyc_check(
                        customer_db,
                        customer_id=customer_id,
                        check_type=check_type,
                        result=result_val,
                        evidence_ref=evidence_ref or None,
                        notes=notes or None,
                        verified_by=kyc_actor,
                    )
                    st.success("KYC проверка добавлена.")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))


# ---------------------------------------------------------------------------
# Tab: Due for review
# ---------------------------------------------------------------------------


def _render_due_for_review(customer_db: Path, risk_db: Path, alert_db: Path) -> None:
    due = customer_store.get_customers_due_for_review(customer_db)
    if not due:
        st.success("Нет клиентов, требующих ревью.")
        return

    st.warning(f"{len(due)} клиент(ов) требуют CDD-ревью.")
    rows = [
        {
            "ID": c["id"][:8],
            "Имя": c["name"],
            "Риск-тир": f"{_TIER_ICONS.get(c['risk_tier'], '')} {c['risk_tier']}",
            "Последнее ревью": (c.get("last_review_at") or "Никогда")[:10],
        }
        for c in due
    ]
    st.dataframe(rows, use_container_width=True)

    customer_id = st.selectbox(
        "Провести ревью для",
        options=["— выберите —"] + [c["id"] for c in due],
        format_func=lambda x: x[:8] if x != "— выберите —" else x,
    )
    actor = st.text_input("Актор", value="Analyst", key="review_actor")

    if customer_id and customer_id != "— выберите —":
        if st.button("Отметить ревью выполненным"):
            customer_store.record_review(customer_db, customer_id, actor=actor)
            st.success("Ревью зафиксировано.")
            st.rerun()
