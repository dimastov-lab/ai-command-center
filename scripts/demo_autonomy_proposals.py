#!/usr/bin/env python3
"""Demonstrate the autonomy proposal foundation (AICC-AUTONOMY-002) end to end.

Runs four scenarios against a throwaway SQLite store, printing the state
transitions, the eligibility verdict, and the audit trail for each:

  1. Autonomy disabled (default policy)      -> BLOCKED, nothing proposed.
  2. Enabled, low-risk, human gate           -> AWAITING_APPROVAL -> APPROVED,
     but dispatch REFUSED (allow_execution_dispatch is off).
  3. Enabled + dispatch allowed              -> APPROVED -> DISPATCHED ->
     EXECUTED (the caller performs the action; the engine only records it).
  4. MERGE proposal                          -> CRITICAL risk, never auto,
     always a human gate.

Nothing here launches a run, creates a task behind your back, or touches a real
repository — dispatch returns a plan the caller must execute explicitly.

Usage:
    python3 scripts/demo_autonomy_proposals.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Make the repo importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from command_center.runtime import db as runtime_db  # noqa: E402
from command_center.runtime.autonomy import (  # noqa: E402
    ALL_KINDS,
    AutonomyPolicy,
    ProposalKind,
    RiskLevel,
)
from command_center.runtime.autonomy_service import (  # noqa: E402
    AutonomyEngine,
    DispatchNotPermittedError,
    observe,
)


def _engine(tmp: Path) -> AutonomyEngine:
    return AutonomyEngine(tmp / "runtime.db")


def _print_events(engine: AutonomyEngine, proposal_id: str) -> None:
    for e in engine.events(proposal_id):
        arrow = f"{e['from_state'] or '-'} -> {e['to_state'] or '-'}"
        actor = f" [{e['actor']}]" if e["actor"] else ""
        reason = f" ({e['reason_code']})" if e["reason_code"] else ""
        print(f"    {e['seq']:>2}. {e['event_type']:<11} {arrow}{reason}{actor}")


def scenario_disabled(engine: AutonomyEngine) -> None:
    print("\n=== 1. Autonomy disabled (default policy) ===")
    p = engine.create_proposal(
        kind=ProposalKind.TASK_CREATION, project="AICC", title="Add missing tests for module X",
        rationale="project_intelligence reported a coverage gap in module X",
        evidence=[observe("task_gap", "project_intelligence.compute", "no tests for X", data={"module": "X"})],
        policy=AutonomyPolicy(),  # closed by default
    )
    p = engine.assess(p["id"])
    print(f"  final state: {p['state']}  reason: {p['last_reason_code']}")
    _print_events(engine, p["id"])


def scenario_human_gate(engine: AutonomyEngine) -> None:
    print("\n=== 2. Enabled, low-risk, human approval gate ===")
    policy = AutonomyPolicy(enabled=True, allowed_kinds=frozenset({ProposalKind.TASK_CREATION}))
    p = engine.create_proposal(
        kind=ProposalKind.TASK_CREATION, project="AICC", title="Add missing tests",
        rationale="coverage gap detected", policy=policy,
        evidence=[observe("task_gap", "project_intelligence.compute", "no tests for X")],
    )
    p = engine.assess(p["id"])
    print(f"  after assess: {p['state']} (requires_human={bool(p['requires_human'])})")
    p = engine.approve(p["id"], actor="dima@me.com", reason="worthwhile")
    print(f"  after approve: {p['state']} (decided_by={p['decided_by']})")
    try:
        engine.dispatch(p["id"], actor="dima@me.com")
    except DispatchNotPermittedError as exc:
        print(f"  dispatch REFUSED as designed: {exc}")
    _print_events(engine, p["id"])


def scenario_full_dispatch(engine: AutonomyEngine) -> None:
    print("\n=== 3. Enabled + dispatch allowed: approve -> dispatch -> execute ===")
    policy = AutonomyPolicy(enabled=True, allowed_kinds=frozenset({ProposalKind.TASK_CREATION}),
                            allow_execution_dispatch=True)
    p = engine.create_proposal(
        kind=ProposalKind.TASK_CREATION, project="AICC", title="Add missing tests",
        rationale="coverage gap detected", policy=policy,
        evidence=[observe("task_gap", "project_intelligence.compute", "no tests for X")],
    )
    p = engine.assess(p["id"])
    p = engine.approve(p["id"], actor="dima@me.com")
    result = engine.dispatch(p["id"], actor="dima@me.com")
    print(f"  dispatched: state={result['proposal']['state']} route={result['plan']['dispatch_route']}")
    print("  plan steps:")
    for step in result["plan"]["steps"]:
        print(f"    - {step}")
    # The CALLER performs the action through the normal route, then confirms.
    task = runtime_db.create_task(engine.db_path, project="AICC", title="Add missing tests",
                                  task_type="implementation")
    p = engine.confirm_execution(p["id"], actor="executor", task_id=task["id"])
    print(f"  executed: state={p['state']} linked_task={p['dispatched_task_id']}")
    _print_events(engine, p["id"])


def scenario_merge_critical(engine: AutonomyEngine) -> None:
    print("\n=== 4. MERGE proposal is CRITICAL and never auto-approved ===")
    policy = AutonomyPolicy(enabled=True, allowed_kinds=ALL_KINDS,
                            auto_approve_max_risk=RiskLevel.HIGH, allow_execution_dispatch=True)
    p = engine.create_proposal(
        kind=ProposalKind.MERGE, project="AICC", title="Merge feature branch",
        rationale="PR is open with green checks", policy=policy,
        evidence=[observe("pr_state", "github.get_pr", "open, checks green")],
    )
    p = engine.assess(p["id"])
    print(f"  risk={p['risk_level']} final state: {p['state']} (needs a human)")
    _print_events(engine, p["id"])


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        scenario_disabled(_engine(tmp))
        scenario_human_gate(_engine(tmp))
        scenario_full_dispatch(_engine(tmp))
        scenario_merge_critical(_engine(tmp))
    print("\nAll scenarios complete. No repository was modified; no run was launched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
