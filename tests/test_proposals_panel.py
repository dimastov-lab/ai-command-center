"""UI coverage for the agent proposals inbox
(`command_center.ui.proposals_panel`, task bb9f6ab3).

The autonomy layer (create/assess/approve/reject) is covered in
`test_autonomy_service.py`; here we prove the inbox renders a pending proposal
and that its buttons drive the real API — Принять approves it, Отклонить archives
it (REJECTED) — so a human decision on an agent's suggestion actually lands.
"""
from __future__ import annotations

from streamlit.testing.v1 import AppTest

from command_center.runtime.api import ExecutionCenterAPI
from command_center.runtime.autonomy import AutonomyPolicy, ProposalKind, ProposalState
from command_center.runtime.autonomy_service import observe


def _human_gate_policy() -> AutonomyPolicy:
    # Kind allowed, nothing auto-approved -> assess routes to AWAITING_APPROVAL.
    return AutonomyPolicy(enabled=True, allowed_kinds=frozenset({ProposalKind.TASK_CREATION}))


def _make_awaiting(api: ExecutionCenterAPI, title: str = "Add missing tests for module X") -> str:
    proposal = api.create_proposal(
        kind=ProposalKind.TASK_CREATION,
        project="AICC",
        title=title,
        rationale="Coverage gap detected in module X",
        evidence=[observe("task_gap", "project_intelligence.compute", "no tests for module X", data={"module": "X"})],
        policy=_human_gate_policy(),
    )
    api.assess_proposal(proposal["id"])
    return proposal["id"]


def _panel_script() -> None:
    from command_center.runtime.api import ExecutionCenterAPI
    from command_center.ui import proposals_panel

    proposals_panel.render_proposals_inbox(ExecutionCenterAPI(), key_prefix="t")


def _run() -> AppTest:
    return AppTest.from_function(_panel_script, default_timeout=30).run()


def test_inbox_lists_a_pending_proposal(isolated_data_dir):
    _make_awaiting(ExecutionCenterAPI(), "Add missing tests for module X")
    at = _run()
    assert any("Add missing tests for module X" in m.value for m in at.markdown)


def test_accept_approves_the_proposal(isolated_data_dir):
    api = ExecutionCenterAPI()
    pid = _make_awaiting(api)
    at = _run()
    at.button(key=f"t_accept_{pid}").click().run()
    assert api.get_proposal(pid)["state"] == ProposalState.APPROVED


def test_reject_archives_the_proposal(isolated_data_dir):
    api = ExecutionCenterAPI()
    pid = _make_awaiting(api)
    at = _run()
    at.button(key=f"t_reject_{pid}").click().run()
    assert api.get_proposal(pid)["state"] == ProposalState.REJECTED


def test_empty_inbox_reports_no_proposals(isolated_data_dir):
    at = _run()
    assert any("Новых предложений нет" in s.value for s in at.success)
