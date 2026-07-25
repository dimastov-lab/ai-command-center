"""Agent proposals inbox — one place where agent suggestions land for a human
Принять / Отклонить decision (task bb9f6ab3).

Agents raise *proposals* (new functionality, a task reworked, work that looks
already done) through the autonomy layer (`runtime.autonomy_service`, tables
`proposal` / `proposal_evidence` / `proposal_event`). This panel is the human
decision surface over that layer:

- **Принять** → `approve_proposal` — records the operator's approval. Approval is
  the human gate; the actual dispatch/execution of an approved proposal crosses
  the policy-gated boundary owned by the autonomy engine (`dispatch` /
  `confirm_execution`), which this panel deliberately does not fake.
- **Отклонить** → `reject_proposal` (or `withdraw_proposal` where reject is not a
  legal transition) — terminal, i.e. archived.

Buttons are enabled only for the states where the underlying transition is legal
(`autonomy.PROPOSAL_TRANSITIONS`), and every call is guarded so a lost race just
surfaces as a message instead of a traceback.
"""
from __future__ import annotations

import streamlit as st

from command_center.runtime import autonomy
from command_center.runtime.autonomy_service import IllegalProposalActionError

_ACTOR = "operator"

# States still awaiting a human or the engine — what the inbox shows as open.
_OPEN_STATES = (
    autonomy.ProposalState.DRAFT,
    autonomy.ProposalState.PROPOSED,
    autonomy.ProposalState.ELIGIBLE,
    autonomy.ProposalState.AWAITING_APPROVAL,
    autonomy.ProposalState.BLOCKED,
)
# approve() accepts these directly (BLOCKED via an audited override inside approve()).
_APPROVABLE = (
    autonomy.ProposalState.ELIGIBLE,
    autonomy.ProposalState.AWAITING_APPROVAL,
    autonomy.ProposalState.BLOCKED,
)
# reject() is a legal transition only from these; elsewhere we archive via withdraw().
_REJECTABLE = (
    autonomy.ProposalState.AWAITING_APPROVAL,
    autonomy.ProposalState.BLOCKED,
)

_KIND_LABEL = {
    autonomy.ProposalKind.TASK_CREATION: "новая задача",
    autonomy.ProposalKind.TASK_EXECUTION: "запуск задачи",
    autonomy.ProposalKind.PRIORITY_CHANGE: "смена приоритета",
    autonomy.ProposalKind.DEPENDENCY_LINK: "связь-зависимость",
    autonomy.ProposalKind.MERGE: "merge",
}
_RISK_LABEL = {
    autonomy.RiskLevel.LOW: "низкий",
    autonomy.RiskLevel.MEDIUM: "средний",
    autonomy.RiskLevel.HIGH: "высокий",
    autonomy.RiskLevel.CRITICAL: "критический",
}
_STATE_LABEL = {
    autonomy.ProposalState.DRAFT: "черновик",
    autonomy.ProposalState.PROPOSED: "на оценке",
    autonomy.ProposalState.ELIGIBLE: "готово к решению",
    autonomy.ProposalState.AWAITING_APPROVAL: "ждёт решения",
    autonomy.ProposalState.BLOCKED: "заблокировано",
    autonomy.ProposalState.APPROVED: "принято",
    autonomy.ProposalState.REJECTED: "отклонено",
    autonomy.ProposalState.DISPATCHED: "передано в работу",
    autonomy.ProposalState.EXECUTED: "выполнено",
    autonomy.ProposalState.WITHDRAWN: "отозвано",
}


def _archive(api, proposal: dict, reason: str) -> None:
    """Reject where that is a legal transition, otherwise withdraw — both are
    terminal 'archived' outcomes, so 'Отклонить' always has an effect."""
    if proposal["state"] in _REJECTABLE:
        api.reject_proposal(proposal["id"], actor=_ACTOR, reason=reason or "отклонено оператором")
    else:
        api.withdraw_proposal(proposal["id"], actor=_ACTOR, reason=reason or "отклонено оператором")


def render_proposals_inbox(api, *, project: str | None = None, key_prefix: str = "proposals") -> None:
    """Render the agent-proposals inbox for `project` (None / 'Все' = all)."""
    scope = None if project in (None, "Все", "All") else project
    proposals = api.list_proposals(project=scope)
    open_props = [p for p in proposals if p["state"] in _OPEN_STATES]
    decided = [p for p in proposals if p["state"] not in _OPEN_STATES]

    st.markdown("#### 📥 Предложения агентов")
    st.caption(
        "Единое место для предложений агентов — новый функционал, переработка задач, "
        "удаление уже сделанного. «Принять» — одобрить, «Отклонить» — архивировать."
    )

    if not open_props:
        st.success("Новых предложений нет.")
    for proposal in open_props:
        pid = proposal["id"]
        with st.container(border=True):
            head, meta = st.columns([0.72, 0.28])
            head.markdown(f"**{proposal['title']}**")
            head.caption(
                f"{_KIND_LABEL.get(proposal['kind'], proposal['kind'])} · "
                f"риск {_RISK_LABEL.get(proposal['risk_level'], proposal['risk_level'])} · "
                f"{_STATE_LABEL.get(proposal['state'], proposal['state'])}"
            )
            meta.caption(f"проект: {proposal['project']}")
            if proposal.get("rationale"):
                st.caption(proposal["rationale"])

            evidence = api.get_proposal_evidence(pid)
            if evidence:
                with st.expander(f"Доказательства ({len(evidence)})", expanded=False):
                    for item in evidence:
                        blocker = " ⛔" if item.get("is_blocker") else ""
                        st.caption(f"— [{item.get('kind')}] {item.get('summary') or item.get('source')}{blocker}")

            accept_col, reject_col, reason_col = st.columns([0.22, 0.22, 0.56])
            reason = reason_col.text_input(
                "Причина отклонения",
                key=f"{key_prefix}_reason_{pid}",
                label_visibility="collapsed",
                placeholder="Причина (для отклонения)",
            )
            accepted = accept_col.button(
                "Принять",
                key=f"{key_prefix}_accept_{pid}",
                type="primary",
                use_container_width=True,
                disabled=proposal["state"] not in _APPROVABLE,
                help=None if proposal["state"] in _APPROVABLE else "Ещё не оценено — решение недоступно.",
            )
            rejected = reject_col.button(
                "Отклонить",
                key=f"{key_prefix}_reject_{pid}",
                use_container_width=True,
            )
            if accepted:
                try:
                    api.approve_proposal(pid, actor=_ACTOR)
                    st.toast(f"Принято: {proposal['title']}")
                    st.rerun()
                except IllegalProposalActionError as exc:
                    st.error(f"Нельзя принять: {exc}")
            if rejected:
                try:
                    _archive(api, proposal, reason)
                    st.toast(f"Отклонено: {proposal['title']}")
                    st.rerun()
                except IllegalProposalActionError as exc:
                    st.error(f"Нельзя отклонить: {exc}")

    if decided:
        with st.expander(f"Решённые ({len(decided)})", expanded=False):
            for proposal in decided:
                st.caption(
                    f"{_STATE_LABEL.get(proposal['state'], proposal['state'])} · "
                    f"{proposal['title']} — {proposal.get('decision_reason') or ''}"
                )
