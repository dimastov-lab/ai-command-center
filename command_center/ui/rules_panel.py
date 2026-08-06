"""AML Rules Panel — Streamlit UI for rule management and evaluation."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from command_center import rule_engine
from command_center.rule_engine import (
    ALERT_TYPES,
    CONDITION_OPERATORS,
    JURISDICTIONS,
    InvalidCondition,
    RuleEngineError,
    RuleNotFound,
)


def render(rules_db: Path | None = None) -> None:
    if rules_db is None:
        rules_db = rule_engine.resolve_db_path()
    rule_engine.init_db(rules_db)

    st.header("Правила AML")

    tab_list, tab_create, tab_test = st.tabs(["Список правил", "Добавить правило", "Тест оценки"])

    with tab_list:
        _render_list(rules_db)

    with tab_create:
        _render_create(rules_db)

    with tab_test:
        _render_test(rules_db)


# ---------------------------------------------------------------------------
# Tab: Rule list
# ---------------------------------------------------------------------------

_PRIORITY_BADGE = {
    "low": "🟢",
    "medium": "🟡",
    "high": "🔴",
    "critical": "🚨",
}


def _render_list(rules_db: Path) -> None:
    col1, col2 = st.columns(2)
    with col1:
        enabled_only = st.checkbox("Только активные", value=False)
    with col2:
        juris_filter = st.selectbox("Юрисдикция", ["— все —"] + list(JURISDICTIONS))

    jf = juris_filter if juris_filter != "— все —" else None
    rules = rule_engine.list_rules(rules_db, enabled_only=enabled_only, jurisdiction=jf)

    if not rules:
        st.info("Правила не найдены.")
        return

    st.caption(f"Найдено: {len(rules)}")
    for r in rules:
        badge = _PRIORITY_BADGE.get(r["priority_weight"], "")
        label = f"{badge} [{r['jurisdiction']}] {r['name']} — {r['condition_op']}"
        with st.expander(label, expanded=False):
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Тип алерта", r["alert_type"])
            col_b.metric("Порог/список", _fmt_condition(r))
            col_c.metric("Статус", "✅ Активно" if r["enabled"] else "⏸ Отключено")

            if r.get("description"):
                st.caption(r["description"])

            toggle_label = "Отключить" if r["enabled"] else "Включить"
            if st.button(toggle_label, key=f"toggle_{r['id']}"):
                try:
                    rule_engine.toggle_rule(rules_db, r["id"], enabled=not r["enabled"])
                    st.rerun()
                except RuleNotFound as e:
                    st.error(str(e))


def _fmt_condition(r: dict) -> str:
    if r.get("threshold") is not None:
        return str(r["threshold"])
    vl = r.get("value_list")
    if isinstance(vl, str):
        vl = json.loads(vl)
    if vl:
        return ", ".join(str(v) for v in vl[:3]) + ("…" if len(vl) > 3 else "")
    return "—"


# ---------------------------------------------------------------------------
# Tab: Create rule
# ---------------------------------------------------------------------------


def _render_create(rules_db: Path) -> None:
    with st.form("create_rule"):
        name = st.text_input("Название *")
        description = st.text_area("Описание", height=60)

        col1, col2 = st.columns(2)
        with col1:
            condition_op = st.selectbox("Оператор условия *", sorted(CONDITION_OPERATORS))
        with col2:
            alert_type = st.selectbox("Тип алерта *", ALERT_TYPES)

        col3, col4 = st.columns(2)
        with col3:
            threshold_str = st.text_input("Порог (для amount_gt/gte, frequency_gt)")
        with col4:
            value_list_str = st.text_input("Список значений (через запятую, для *_in)")

        col5, col6 = st.columns(2)
        with col5:
            priority_weight = st.selectbox("Приоритет *", ["low", "medium", "high", "critical"])
        with col6:
            jurisdiction = st.selectbox("Юрисдикция *", list(JURISDICTIONS))

        submitted = st.form_submit_button("Создать правило")
        if submitted:
            if not name.strip():
                st.error("Укажите название.")
                return
            threshold = float(threshold_str) if threshold_str.strip() else None
            value_list = (
                [v.strip() for v in value_list_str.split(",") if v.strip()]
                if value_list_str.strip()
                else None
            )
            try:
                rule = rule_engine.create_rule(
                    rules_db,
                    name=name.strip(),
                    description=description.strip() or None,
                    condition_op=condition_op,
                    threshold=threshold,
                    value_list=value_list,
                    alert_type=alert_type,
                    priority_weight=priority_weight,
                    jurisdiction=jurisdiction,
                )
                st.success(f"Правило создано: {rule['id'][:8]} — {rule['name']}")
            except (InvalidCondition, RuleEngineError) as e:
                st.error(str(e))


# ---------------------------------------------------------------------------
# Tab: Test evaluate
# ---------------------------------------------------------------------------


def _render_test(rules_db: Path) -> None:
    st.subheader("Проверить событие против правил")

    col1, col2 = st.columns(2)
    with col1:
        amount = st.number_input("Сумма", min_value=0.0, value=0.0)
        country = st.text_input("Страна (ISO2)", value="")
        risk_tier = st.selectbox("Риск-тир клиента", ["", "low", "medium", "high", "pep"])
    with col2:
        frequency = st.number_input("Частота транзакций (за период)", min_value=0, value=0)
        industry = st.text_input("Отрасль клиента", value="")
        product = st.text_input("Продукт", value="")

    col3, col4 = st.columns(2)
    with col3:
        pep_flag = st.checkbox("PEP флаг")
    with col4:
        adverse_media_flag = st.checkbox("Негативные медиа")

    juris = st.selectbox("Фильтр по юрисдикции", ["— все —"] + list(JURISDICTIONS))
    event_ref = st.text_input("Event ref (опционально)")

    if st.button("Запустить оценку"):
        event = {
            "amount": amount,
            "frequency": frequency,
            "country": country.upper() if country else "",
            "risk_tier": risk_tier,
            "industry": industry,
            "product": product,
            "pep_flag": pep_flag,
            "adverse_media_flag": adverse_media_flag,
        }
        jf = juris if juris != "— все —" else None
        triggered = rule_engine.evaluate(
            rules_db,
            event,
            event_ref=event_ref or None,
            jurisdiction=jf,
        )
        if triggered:
            st.warning(f"Сработало правил: {len(triggered)}")
            for r in triggered:
                badge = _PRIORITY_BADGE.get(r["priority_weight"], "")
                st.write(f"**{badge} {r['name']}** — тип: `{r['alert_type']}`, приоритет: `{r['priority_weight']}`")
        else:
            st.success("Ни одно правило не сработало.")
