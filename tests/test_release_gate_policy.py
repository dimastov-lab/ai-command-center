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
    "security-gates": "Security gates (workflow policy · provenance · path containment)",
    "build-gates": "Build gates (web production)",
    "boundary-fitness": "Boundary fitness (import ban · anti-engine baseline)",
}

EXPECTED_STEPS = {
    "quality-gates": {"Pytest + coverage", "Real-browser E2E"},
    "windows-quality-gates": {"Desktop pytest-qt suite", "Real-browser E2E"},
    "security-gates": {"Release gate policy", "Focused security regressions"},
    "build-gates": {"Web production build"},
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
        assert workflow["permissions"] == {"contents": "read"}

    for job_id, required_steps in EXPECTED_STEPS.items():
        step_names = {step.get("name") for step in jobs[job_id]["steps"]}
        assert required_steps <= step_names


def test_every_required_context_has_a_deliberate_failure_canary() -> None:
    for job_id, job in _all_jobs().items():
        (canary,) = [step for step in job["steps"] if step.get("name") == "Deliberate failure canary"]
        assert CANARY_LABELS[job_id] in canary["if"]
        assert canary["run"].strip() == "exit 1"


def test_all_actions_are_immutable_sha_pinned() -> None:
    for job in _all_jobs().values():
        for step in job["steps"]:
            if uses := step.get("uses"):
                assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", uses), uses
