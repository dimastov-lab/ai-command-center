from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from command_center import aml_store
from command_center.ui import aml_panel

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


def _at_on_aml_page(**session_state) -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.session_state["nav_page"] = "aml"
    for key, value in session_state.items():
        at.session_state[key] = value
    return at.run()


def test_aml_page_renders_overview_and_all_workspace_views():
    at = _at_on_aml_page()
    assert not at.exception
    assert at.subheader[0].value == "AML Monitoring"
    assert any(metric.label == "Открытые алерты" for metric in at.metric)
    assert at.segmented_control[0].options == ["Обзор", "Очередь", "Клиенты", "Расследование", "Отчётность"]


def test_aml_queue_renders_filters_and_case_selection():
    at = _at_on_aml_page(aml_view="queue")
    assert not at.exception
    assert any(select.key == "aml_selected_case" for select in at.selectbox)
    assert any(button.label == "Перейти к расследованию" for button in at.button)


def test_aml_investigation_actions_drive_case_lifecycle():
    at = _at_on_aml_page(aml_view="investigation")
    assign = next(button for button in at.button if button.label == "Взять в работу")
    assert not assign.disabled

    at = assign.click().run()
    assert not at.exception
    assign = next(button for button in at.button if button.label == "Взять в работу")
    assert assign.disabled
    case = next(case for case in aml_store.list_cases() if case["id"] == "AML-2026-0418")
    assert case["status"] == "review"
    assert case["owner"] == "AML Analyst"
    assert aml_store.list_audit_events(case["id"])[0]["event"] == "Кейс взят в работу"


def test_aml_page_marks_persistent_local_prototype():
    at = _at_on_aml_page()
    assert any("постоянным локальным хранилищем" in info.value for info in at.info)


def test_mlro_role_enables_close_and_disables_analyst_actions():
    at = _at_on_aml_page(aml_view="investigation", aml_role="MLRO", aml_actor="Maria MLRO")

    close = next(button for button in at.button if button.label == "Закрыть без сообщения")
    assign = next(button for button in at.button if button.label == "Взять в работу")
    assert not close.disabled
    assert assign.disabled


def test_filter_cases_matches_risk_status_and_search():
    result = aml_panel.filter_cases(aml_panel.DEMO_CASES, risks=["high"], statuses=["waiting"], query="Baltic")
    assert [case["id"] for case in result] == ["AML-2026-0412"]


def test_aml_search_empty_state_is_safe():
    at = _at_on_aml_page(aml_view="queue", aml_query="не-существующий-клиент")
    assert not at.exception
    assert any("алерты не найдены" in warning.value for warning in at.warning)


def test_customer_and_reporting_windows_render():
    customers = _at_on_aml_page(aml_view="customers")
    assert not customers.exception
    assert any("Клиенты и KYC" in markdown.value for markdown in customers.markdown)

    reporting = _at_on_aml_page(aml_view="reporting")
    assert not reporting.exception
    assert len(reporting.tabs) == 2
    assert any("Журнал аудита" in markdown.value for markdown in reporting.markdown)


def test_mlro_reporting_exposes_sar_approval_flow():
    aml_store.seed_cases(aml_panel.DEMO_CASES)
    aml_store.create_sar_draft(
        "AML-2026-0418",
        filing_type="SAR / STR",
        rationale="Транзит средств требует сообщения",
        actor="AML Analyst",
        role="Analyst",
    )
    at = _at_on_aml_page(aml_view="reporting", aml_role="MLRO", aml_actor="Maria MLRO")

    assert not at.exception
    assert any(button.label == "Проверить и утвердить" for button in at.button)
