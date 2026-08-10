"""Exact, machine-checkable contract for merge-relevant workflow contexts."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github/workflows/ci.yml"
BOUNDARY_WORKFLOW = ROOT / ".github/workflows/arch-fitness.yml"

EXPECTED_CONTEXTS = {
    "detect-scope": "Detect change scope",
    "quality-gates": "Quality gates (whitespace · Ruff · compile · pytest)",
    "windows-quality-gates": "Windows quality gates (Ruff · compile · pytest)",
    "security-gates": "Security gates (workflow policy · provenance · supply chain)",
    "build-gates": "Build gates (web production)",
    "final-gate": "Final merge gate",
    "boundary-fitness": "Boundary fitness (import ban · anti-engine baseline)",
}

EXPECTED_STEPS = {
    "quality-gates": {"Pytest + coverage", "Real-browser E2E"},
    "windows-quality-gates": {"Desktop pytest-qt suite", "Real-browser E2E"},
    "security-gates": {
        "Release gate policy",
        "Build Python dependency SBOM",
        "Critical/high vulnerability scan (pip-audit)",
        "Secret scan",
        "Initialize CodeQL",
        "Autobuild for CodeQL",
        "Run CodeQL analysis",
        "Focused security regressions",
    },
    "build-gates": {"Web production build"},
    "final-gate": {"Assert required checks"},
    "boundary-fitness": {"AIOS boundary fitness tests"},
}

CANARY_LABELS = {
    "quality-gates": "release-gate-canary-linux",
    "windows-quality-gates": "release-gate-canary-windows",
    "security-gates": "release-gate-canary-security",
    "build-gates": "release-gate-canary-build",
    "boundary-fitness": "release-gate-canary-boundary",
}


def _workflow(path: Path) -> dict:
    # BaseLoader keeps the YAML 1.1 word ``on`` as a string instead of turning
    # it into True, while structure is all this policy test needs.
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _all_jobs() -> dict[str, dict]:
    jobs = {}
    for path in (CI_WORKFLOW, BOUNDARY_WORKFLOW):
        jobs.update(_workflow(path)["jobs"])
    return jobs


def test_release_context_names_and_workflow_coverage_are_exact() -> None:
    ci = _workflow(CI_WORKFLOW)
    boundary = _workflow(BOUNDARY_WORKFLOW)
    jobs = {**ci["jobs"], **boundary["jobs"]}

    assert set(ci["jobs"]) == set(EXPECTED_CONTEXTS) - {"boundary-fitness"}
    assert set(boundary["jobs"]) == {"boundary-fitness"}
    assert {job_id: job["name"] for job_id, job in jobs.items()} == EXPECTED_CONTEXTS

    for workflow in (ci, boundary):
        assert set(workflow["on"]) == {"pull_request", "push", "workflow_dispatch"}
        assert set(workflow["on"]["pull_request"]["types"]) == {
            "opened",
            "synchronize",
            "reopened",
            "labeled",
            "unlabeled",
        }
        if workflow is ci:
            assert workflow["permissions"] == {
                "contents": "read",
                "security-events": "write",
            }
        else:
            assert workflow["permissions"] == {"contents": "read"}

    for job_id, required_steps in EXPECTED_STEPS.items():
        step_names = {step.get("name") for step in jobs[job_id]["steps"]}
        assert required_steps <= step_names


def test_every_required_context_has_a_deliberate_failure_canary() -> None:
    jobs = _all_jobs()
    for job_id in CANARY_LABELS:
        job = jobs[job_id]
        (canary,) = [step for step in job["steps"] if step.get("name") == "Deliberate failure canary"]
        assert CANARY_LABELS[job_id] in canary["if"]
        assert canary["run"].strip() == "exit 1"


def test_all_actions_are_immutable_sha_pinned() -> None:
    for job in _all_jobs().values():
        for step in job["steps"]:
            if uses := step.get("uses"):
                assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", uses), uses


def test_final_gate_is_fail_closed_for_every_upstream_result() -> None:
    final_gate = _workflow(CI_WORKFLOW)["jobs"]["final-gate"]
    required = {
        "quality-gates",
        "windows-quality-gates",
        "security-gates",
        "build-gates",
    }
    # detect-scope is a shared prerequisite job listed in needs but not a
    # gating check — it only feeds outputs to downstream jobs.
    all_needs = {"detect-scope", *required}

    assert final_gate["if"] == "always()"
    assert set(final_gate["needs"]) == all_needs

    (assertion_step,) = [
        step for step in final_gate["steps"] if step.get("name") == "Assert required checks"
    ]
    script = assertion_step["run"]
    for job_id in required:
        assert f'${{{{ needs.{job_id}.result }}}}" != "success"' in script

    # windows-quality-gates is intentionally skipped on docs-only PRs; the
    # final-gate script accepts 'skipped' for that job only.
    skip_allowed = {"windows-quality-gates"}

    def accepted(results: dict[str, str]) -> bool:
        for job_id in required:
            r = results[job_id]
            if r == "success":
                continue
            if r == "skipped" and job_id in skip_allowed:
                continue
            return False
        return True

    success = {job_id: "success" for job_id in required}
    assert accepted(success)
    # skipped is acceptable only for skip_allowed jobs
    for job_id in skip_allowed:
        assert accepted(success | {job_id: "skipped"}), (job_id, "skipped")
    # failure and cancellation always block the gate
    for job_id in required:
        for negative_result in ("failure", "cancelled"):
            assert not accepted(success | {job_id: negative_result}), (job_id, negative_result)
    # skipped is NOT acceptable for non-skip_allowed jobs
    for job_id in required - skip_allowed:
        assert not accepted(success | {job_id: "skipped"}), (job_id, "skipped")
