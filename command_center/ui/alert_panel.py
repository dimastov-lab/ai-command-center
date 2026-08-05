"""AML Alert Panel — Streamlit UI for alert triage and disposition (DATA-1)."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from command_center import alert_store
from command_center.alert_store import (
    AlertStoreError,
    InvalidTransition,
    LostUpdate,
    OutcomeRequired,
    PermissionDenied,
)

_OUTCOME_LABELS: dict[str, str] = {
    "true_positive": "True Positive",
    "false_positive": "False Positive",
    "escalated_to_case": "Escalated to Case",
    "closed_no_action": "Closed — No Action",
    "sar_filed": "SAR Filed",
}

_STATE_TRANSITIONS: dict[str, list[str]] = {
    "generated": ["assign"],
    "assigned": ["start_triage"],
    "in_triage": ["submit_for_review"],
    "pending_review": ["dispose", "escalate"],
    "closed": ["archive"],
}

_PRIORITY_COLOURS: dict[str, str] = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🟢",
}


def render(db_path: Path | None = None) -> None:
    """Render the full Alert panel."""
    if db_path is None:
        db_path = alert_store.resolve_db_path()

    alert_store.init_db(db_path)
    alert_store.mark_overdue(db_path)

    st.header("Алерты AML")

    tab_queue, tab_actions, tab_metrics = st.tabs(["Очередь", "Действия", "Метрики"])

    with tab_queue:
        _render_queue(db_path)

    with tab_actions:
        _render_actions(db_path)

    with tab_metrics:
        _render_metrics(db_path)


# ---------------------------------------------------------------------------
# Tab: Queue
# ---------------------------------------------------------------------------


def _render_queue(db_path: Path) -> None:
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        state_filter = st.multiselect(
            "Статус",
            ["generated", "assigned", "in_triage", "pending_review", "escalated", "closed", "archived"],
        )
    with col2:
        priority_filter = st.multiselect("Приоритет", ["critical", "high", "medium", "low"])
    with col3:
        owner_filter = st.text_input("Владелец")
    with col4:
        overdue_only = st.checkbox("Только просроченные")

    alerts = alert_store.list_alerts(db_path)

    if state_filter:
        alerts = [a for a in alerts if a["state"] in state_filter]
    if priority_filter:
        alerts = [a for a in alerts if a["priority"] in priority_filter]
    if owner_filter:
        alerts = [a for a in alerts if a.get("owner") == owner_filter]
    if overdue_only:
        alerts = [a for a in alerts if a["overdue"]]

    if not alerts:
        st.info("Алертов не найдено.")
        return

    rows = [
        {
            "ID": a["id"][:8],
            "Источник": a["source"],
            "Субъект": a["subject_id"],
            "Приоритет": f"{_PRIORITY_COLOURS.get(a['priority'], '')} {a['priority']}",
            "Статус": a["state"],
            "Владелец": a.get("owner") or "—",
            "Срок": (a.get("due_at") or "—")[:10],
            "⚠": "!" if a["overdue"] else "",
        }
        for a in alerts
    ]
    st.dataframe(rows, use_container_width=True)

    selected_id = st.selectbox(
        "Детали алерта",
        options=["— выберите —"] + [a["id"] for a in alerts],
        format_func=lambda x: x[:8] if x != "— выберите —" else x,
    )
    if selected_id and selected_id != "— выберите —":
        try:
            alert = alert_store.get_alert(db_path, selected_id)
        except KeyError:
            st.error("Алерт не найден.")
            return
        with st.expander("Детали алерта", expanded=True):
            st.json(alert)
            log = alert_store.get_audit_log(db_path, selected_id)
            if log:
                st.subheader("Журнал аудита")
                st.dataframe(log, use_container_width=True)


# ---------------------------------------------------------------------------
# Tab: Actions
# ---------------------------------------------------------------------------


def _render_actions(db_path: Path) -> None:
    alert_id = st.text_input("ID алерта (полный)")
    if not alert_id:
        st.info("Введите ID алерта для выполнения действия.")
        return

    try:
        alert = alert_store.get_alert(db_path, alert_id)
    except KeyError:
        st.error(f"Алерт {alert_id!r} не найден.")
        return

    st.write(f"**Статус:** `{alert['state']}` | **Приоритет:** `{alert['priority']}` | **Владелец:** `{alert.get('owner') or '—'}`")

    available = _STATE_TRANSITIONS.get(alert["state"], [])
    if not available:
        st.info("Нет доступных действий для этого статуса.")
        return

    action = st.selectbox("Действие", available)
    actor = st.text_input("Актор (имя пользователя)", value="Analyst")

    if action == "assign":
        owner = st.text_input("Назначить владельцу")
        if st.button("Назначить"):
            _safe_call(
                lambda: alert_store.assign_alert(db_path, alert_id, owner=owner, actor=actor),
                "Алерт назначен.",
            )

    elif action == "start_triage":
        if st.button("Начать триаж"):
            _safe_call(
                lambda: alert_store.start_triage(db_path, alert_id, actor=actor),
                "Триаж начат.",
            )

    elif action == "submit_for_review":
        rationale = st.text_area("Обоснование")
        if st.button("Отправить на ревью"):
            _safe_call(
                lambda: alert_store.submit_for_review(db_path, alert_id, actor=actor, rationale=rationale),
                "Отправлено на ревью.",
            )

    elif action == "dispose":
        outcome = st.selectbox(
            "Исход (DATA-1)",
            options=list(_OUTCOME_LABELS.keys()),
            format_func=lambda k: _OUTCOME_LABELS[k],
        )
        rationale = st.text_area("Обоснование (обязательно)")
        if st.button("Закрыть алерт"):
            _safe_call(
                lambda: alert_store.dispose_alert(
                    db_path, alert_id, actor=actor, outcome=outcome, rationale=rationale
                ),
                f"Алерт закрыт. Исход: {_OUTCOME_LABELS[outcome]}.",
            )

    elif action == "escalate":
        case_id = st.text_input("ID кейса")
        rationale = st.text_area("Обоснование эскалации")
        if st.button("Эскалировать в кейс"):
            _safe_call(
                lambda: alert_store.escalate_alert(
                    db_path, alert_id, actor=actor, case_id=case_id, rationale=rationale
                ),
                "Алерт эскалирован.",
            )

    elif action == "archive":
        rationale = st.text_area("Причина архивирования")
        if st.button("Архивировать"):
            _safe_call(
                lambda: alert_store.archive_alert(db_path, alert_id, actor=actor, rationale=rationale),
                "Алерт архивирован.",
            )


def _safe_call(fn, success_msg: str) -> None:
    try:
        fn()
        st.success(success_msg)
        st.rerun()
    except (OutcomeRequired, InvalidTransition, LostUpdate, PermissionDenied, AlertStoreError) as exc:
        st.error(str(exc))


# ---------------------------------------------------------------------------
# Tab: Metrics
# ---------------------------------------------------------------------------


def _render_metrics(db_path: Path) -> None:
    alerts = alert_store.list_alerts(db_path)
    if not alerts:
        st.info("Нет данных для отображения.")
        return

    overdue_count = sum(1 for a in alerts if a["overdue"])
    st.metric("Просроченные алерты", overdue_count)

    state_counts: dict[str, int] = {}
    for a in alerts:
        state_counts[a["state"]] = state_counts.get(a["state"], 0) + 1

    st.subheader("Алерты по статусу")
    st.bar_chart(state_counts)

    closed = [a for a in alerts if a["state"] == "closed" and a.get("outcome")]
    if closed:
        outcome_counts: dict[str, int] = {}
        for a in closed:
            outcome_counts[a["outcome"]] = outcome_counts.get(a["outcome"], 0) + 1
        st.subheader("Исходы закрытых алертов (DATA-1)")
        st.bar_chart(outcome_counts)
