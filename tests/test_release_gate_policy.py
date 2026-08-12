"""Exact, machine-checkable contract for merge-relevant workflow contexts."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github/workflows/ci.yml"
BOUNDARY_WORKFLOW = ROOT / ".github/workflows/arch-fitness.yml"

EXPECTED_CONTEXTS = {
    "quality-gates": "Quality gates (whitespace · Ruff · compile · pytest)",
    "windows-quality-gates": "Windows quality gates (Ruff · compile · pytest)",
    "security-gates": "Security gates (workflow policy · provenance · supply chain)",
    "build-gates": "Build gates (web production)",
    # Advisory-only fast pre-check (dependency-based test-impact selection). It is
    # deliberately NOT wired into `final-gate.needs` (see
    # `test_final_gate_is_fail_closed_for_every_upstream_result`), so it can never
    # become the sole gate and never reduces coverage.
    "impact-fast-check": "Impact fast pre-check (advisory)",
    "final-gate": "Final merge gate",
    "boundary-fitness": "Boundary fitness (import ban · anti-engine baseline)",
}

EXPECTED_STEPS = {
    # The required test gate runs as a parallel body (pytest-xdist) plus a short
    # serial tail; both together run every test exactly once.
    "quality-gates": {
        "Pytest + coverage (parallel)",
        "Pytest (serial tail)",
        "Real-browser E2E",
    },
    "impact-fast-check": {"Select impacted tests", "Run impacted tests (parallel)"},
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

    assert final_gate["if"] == "always()"
    assert set(final_gate["needs"]) == required

    (assertion_step,) = [
        step for step in final_gate["steps"] if step.get("name") == "Assert required checks"
    ]
    script = assertion_step["run"]
    for job_id in required:
        assert f'${{{{ needs.{job_id}.result }}}}" != "success"' in script

    def accepted(results: dict[str, str]) -> bool:
        return all(results[job_id] == "success" for job_id in required)

    success = {job_id: "success" for job_id in required}
    assert accepted(success)
    for job_id in required:
        for negative_result in ("failure", "cancelled", "skipped"):
            results = success | {job_id: negative_result}
            assert not accepted(results), (job_id, negative_result)
