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

    # The exact trigger set, and it is a security statement rather than
    # bookkeeping: every entry here is a context in which these gates run with
    # the repository's own token, so a trigger added without review is a new
    # way to reach that token. This test caught `merge_group` being added and
    # made the addition deliberate, which is the whole point of pinning it.
    #
    # `merge_group` is admitted because the merge queue is where the required
    # gates must run once the queue is enabled: a workflow that does not
    # subscribe to it never reports there and every queue entry times out.
    #
    # **What is NOT true, and was written here before independent acceptance
    # corrected it: that the queue "never runs a fork's code".** The queue ref
    # (`gh-readonly-queue/<base>`) is created in this repository, but it
    # carries the pull request's own commits — a fork's commits included. And
    # because the event is a base-repository event rather than a
    # fork-originated one, **secrets are passed**, where the `pull_request`
    # path gives a fork an empty secret and a read-only token.
    #
    # That matters concretely here: `ci.yml` runs
    # `scripts/fetch_aios_sdk_artifact.py` — a script from the checked-out
    # tree — with `AIOS_ARTIFACT_READ_TOKEN` in its environment, and that token
    # reads the *private* `dimastov-lab/aios` repository. The need is not
    # removable while AIOS is private, so the exposure is closed at the step
    # instead: see
    # `test_every_step_holding_a_repository_secret_first_proves_its_code_is_ours`
    # (`VOYN-W0-AICC-MERGE-QUEUE-FORK-POLICY`).
    for workflow in (ci, boundary):
        assert set(workflow["on"]) == {
            "pull_request",
            "merge_group",
            "push",
            "workflow_dispatch",
        }
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
                # Read-only, and used by exactly one caller: the trust guard has
                # to resolve the queued pull request to learn its head
                # repository, which the `merge_group` payload omits.
                "pull-requests": "read",
            }
        else:
            assert workflow["permissions"] == {"contents": "read"}

    for job_id, required_steps in EXPECTED_STEPS.items():
        step_names = {step.get("name") for step in jobs[job_id]["steps"]}
        assert required_steps <= step_names


SECRET_REFERENCE = re.compile(r"\$\{\{\s*secrets\.([A-Za-z_][A-Za-z0-9_]*)[^}]*\}\}")

# `GITHUB_TOKEN` is minted per run and already scoped by `permissions:`; it is
# not a standing credential and the guard itself needs it to do its work.
EPHEMERAL_SECRETS = {"GITHUB_TOKEN"}

TRUST_GUARD = "python scripts/assert_trusted_head_repository.py"


def _secret_names(value: object) -> set[str]:
    """Every repository secret reachable from a YAML fragment."""
    if isinstance(value, str):
        return set(SECRET_REFERENCE.findall(value))
    if isinstance(value, dict):
        return set().union(set(), *(_secret_names(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(set(), *(_secret_names(item) for item in value))
    return set()


def _first_command(run: str) -> str:
    for line in run.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def test_every_step_holding_a_repository_secret_first_proves_its_code_is_ours() -> None:
    """A standing credential may only be handed to code this repository wrote.

    The `merge_group` trigger runs in the base repository and therefore *is*
    given repository secrets, while the ref it builds carries the pull
    request's own commits — a fork's included. `AIOS_ARTIFACT_READ_TOKEN` reads
    the private `dimastov-lab/aios` repository, and it is handed to
    `scripts/fetch_aios_sdk_artifact.py`, a script read out of that same tree.
    The need cannot be designed away while AIOS is private, so the reachability
    is closed instead: every step that carries a standing secret must first run
    the trust guard, which refuses any run whose head repository is not this
    one.

    Pinned per step rather than per job, because a secret is scoped to the step
    that declares it; and pinned as the *first* command, so the guard cannot be
    demoted to a step that runs after the credential has already been used.
    """
    offenders: list[tuple[str, str, str | None, str]] = []
    for path in (CI_WORKFLOW, BOUNDARY_WORKFLOW):
        workflow = _workflow(path)
        for job_id, job in workflow["jobs"].items():
            for step in job["steps"]:
                secrets = _secret_names(step) - EPHEMERAL_SECRETS
                if not secrets:
                    continue
                first = _first_command(step.get("run", ""))
                if first != TRUST_GUARD:
                    offenders.append((path.name, job_id, step.get("name"), first))
                    continue
                if "GITHUB_TOKEN" not in _secret_names(step.get("env", {})):
                    offenders.append(
                        (path.name, job_id, step.get("name"), "guard has no GITHUB_TOKEN")
                    )
    assert not offenders, (
        "steps receive a repository secret without first proving the checked-out "
        f"code was authored here: {offenders}"
    )


def test_repository_secrets_are_never_scoped_wider_than_a_single_step() -> None:
    """A workflow- or job-level secret would leak past the guarded step.

    The guard protects the step that declares the credential. Declaring one at
    workflow or job level would put it in the environment of every step in
    scope, including steps the guard does not precede, which would make the
    per-step proof above meaningless without failing it.
    """
    for path in (CI_WORKFLOW, BOUNDARY_WORKFLOW):
        workflow = _workflow(path)
        assert not _secret_names(workflow.get("env", {})) - EPHEMERAL_SECRETS, path.name
        for job_id, job in workflow["jobs"].items():
            leaked = _secret_names(job.get("env", {})) - EPHEMERAL_SECRETS
            assert not leaked, (path.name, job_id, leaked)


def test_the_trust_guard_exists_where_the_workflows_expect_it() -> None:
    assert (ROOT / "scripts/assert_trusted_head_repository.py").is_file()


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
