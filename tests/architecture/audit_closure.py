"""Parsers and probes for the Founder Functional Audit 9761459 closure claims.

The audit's status document (``docs/audits/FOUNDER_FUNCTIONAL_AUDIT_9761459_STATUS.md``)
closes the audit by asserting, for each of the 13 ``AICC-AUDIT-W*`` rows, whether the
remediation is merged on a *pinned* commit of the shared branch. Those assertions are
prose; the roadmap JSON that inherits the residual work is data; and the code they both
describe lives in git. Nothing kept the three in step — a documentation-only task runs
``DEFAULT_VALIDATION_COMMANDS`` (a bare ``compileall``), which passes on an empty diff.

This module supplies the mechanical half: it parses the document's merge-verification
table, reads the roadmap rows, and probes the pinned commit through ``git`` so the
closure verdict can go red when doc, tracker, and code drift apart.

The probed ref is *pinned* (parsed out of the document heading), not ``origin/main``.
The document makes claims about one specific commit, so the probes stay deterministic
as the branch advances; a later merge cannot silently rewrite history that the audit
already certified.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

STATUS_DOC = REPO_ROOT / "docs/audits/FOUNDER_FUNCTIONAL_AUDIT_9761459_STATUS.md"
ROADMAP_JSON = REPO_ROOT / "docs/roadmap/MASTER_ROADMAP_TASKS.json"

ROW_ID_PREFIX = "AICC-AUDIT-"

MERGE_SECTION_HEADING = "### Merge verification"

#: Outcomes in the merge-verification table that mean "shipped on the pinned ref".
#: Everything else must *not* read ``Done`` in the roadmap.
MERGED_OUTCOMES = frozenset({"merged", "merged, still partial"})

#: Outcomes that additionally mean "the roadmap row stays open".
_DONE_STATUS = "Done"

#: Rows the closure folded into other roadmap tasks rather than tracking further.
#: They appear in the document's table but deliberately have *no* ``AICC-AUDIT-W*``
#: roadmap row of their own — their residual work lives under the targets, which
#: must exist for the fold to mean anything.
FOLDED_ROWS: dict[str, tuple[str, ...]] = {
    "W3-002": ("AICC-D2A", "AICC-D2B", "AICC-D2C", "AICC-D2D"),
}

#: Code the document claims to have read on the pinned ref, one entry per row it
#: calls merged. Each entry is ``(path_on_ref, required_substrings)``. This table
#: is deliberately explicit rather than scraped from the prose: the assertion
#: ``anchors.keys() == merged rows`` then forces a new merged row to arrive with
#: its own machine-checkable evidence instead of prose alone.
MERGED_CODE_ANCHORS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "W0-006": ((
        "command_center/runtime/reports.py",
        ("def _safe_path_component", "def report_path_for"),
    ),),
    "W1-004": (("app.py", ("unacknowledged_warning_codes",)),),
    "W1-005": ((
        "command_center/portfolio_launch.py",
        ("PROTECTED_BRANCH_NAMES", "casefold() in PROTECTED_BRANCH_NAMES"),
    ),),
    "W1-006": (("command_center/portfolio_launch.py", ("def recover_stale_claim",)),),
    "W1-007": (("app.py", ("_delete_confirm_open",)),),
    "W1-009": (("command_center/agent_runner.py", ("def claude_cli_preflight",)),),
    "W2-004": (("app.py", ("def render_project_planning_intelligence",)),),
    "W2-006": ((
        "command_center/git_info.py",
        ("def fetch_remotes", "def get_ahead_behind"),
    ),),
}

#: The other direction: rows the document calls *still open*, with the evidence that
#: they are. A row that quietly got fixed on the pinned ref would make the closure
#: verdict wrong in the optimistic direction, which is the more dangerous one.
STILL_OPEN_ANCHORS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    # (path, substrings that must be present, substrings that must be absent)
    "W1-002": ("scripts/start-task.sh", ("  AIOS)", "  BANK|BANK_STRATEGY)"), ("  AICC)",)),
    "W4-003": ("command_center/task_pipeline.py", ("AICC_BACKGROUND_SYNC",), ()),
}


class GitObjectUnavailable(RuntimeError):
    """The pinned commit is not present in this checkout (shallow clone, no ``.git``)."""


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GitObjectUnavailable(
            f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def git_available() -> bool:
    """True when this checkout can answer questions about the pinned commit."""
    if not (REPO_ROOT / ".git").exists():
        return False
    try:
        _git("cat-file", "-e", f"{pinned_ref()}^{{commit}}")
    except GitObjectUnavailable:
        return False
    return True


# --------------------------------------------------------------------------- doc

def status_doc_text() -> str:
    return STATUS_DOC.read_text(encoding="utf-8")


def merge_verification_section(text: str | None = None) -> str:
    """The body of the authoritative merge-verification section."""
    text = status_doc_text() if text is None else text
    start = text.index(MERGE_SECTION_HEADING)
    nxt = text.find("\n### ", start + 1)
    return text[start:] if nxt == -1 else text[start:nxt]


def pinned_ref(text: str | None = None) -> str:
    """The commit the merge-verification section certifies against.

    Parsed from its heading, e.g. ``### Merge verification, 2026-08-07
    (`origin/main` @ `fb3da7f`) — authoritative``.
    """
    section = merge_verification_section(text)
    heading = section.splitlines()[0]
    match = re.search(r"@ `([0-9a-f]{7,40})`", heading)
    if match is None:
        raise AssertionError(
            f"{MERGE_SECTION_HEADING!r} heading must pin the verified commit as "
            f"'@ `<sha>`'; got: {heading!r}"
        )
    return match.group(1)


def evidence_commits(text: str | None = None) -> list[str]:
    """The commit SHAs the section declares to be ancestors of the pinned ref."""
    section = merge_verification_section(text)
    match = re.search(
        r"evidence commits\s*\n?\((?P<shas>.*?)\)\s*are confirmed ancestors",
        section,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError(
            "merge-verification section must list its evidence commits as "
            "'evidence commits (`sha`, `sha`, ...) are confirmed ancestors'"
        )
    return re.findall(r"`([0-9a-f]{7,40})`", match.group("shas"))


def documented_outcomes(text: str | None = None) -> dict[str, str]:
    """``{'W0-006': 'Merged', ...}`` from the merge-verification table."""
    section = merge_verification_section(text)
    outcomes: dict[str, str] = {}
    for line in section.splitlines():
        match = re.match(r"\|\s*(W\d-\d{3})\s*\|\s*([^|]+?)\s*\|", line)
        if match:
            row, outcome = match.group(1), match.group(2)
            if row in outcomes:
                raise AssertionError(f"row {row} listed twice in the table")
            outcomes[row] = outcome
    return outcomes


# ----------------------------------------------------------------------- roadmap

def roadmap_rows(payload: dict | None = None) -> dict[str, dict]:
    """``{'W0-006': {...}, ...}`` for every ``AICC-AUDIT-W*`` roadmap task."""
    if payload is None:
        payload = json.loads(ROADMAP_JSON.read_text(encoding="utf-8"))
    tasks = payload["tasks"] if isinstance(payload, dict) else payload
    rows: dict[str, dict] = {}
    for task in tasks:
        task_id = task.get("id", "")
        if task_id.startswith(f"{ROW_ID_PREFIX}W"):
            rows[task_id[len(ROW_ID_PREFIX) :]] = task
    return rows


def status_mismatches(
    outcomes: dict[str, str], rows: dict[str, dict]
) -> list[str]:
    """Rows where the document's outcome and the roadmap's status disagree.

    ``Merged`` (including *merged, still partial*) is the only outcome compatible
    with a ``Done`` roadmap row, and every merged row must be ``Done``. A partial
    row is the one exception in the other direction: it merged but the residual
    work keeps it open, so the document must say so explicitly. A *folded* row is
    handled by :func:`fold_problems` instead — it has no row of its own by design.
    """
    problems: list[str] = []
    for row, outcome in sorted(outcomes.items()):
        if row in FOLDED_ROWS:
            continue
        task = rows.get(row)
        if task is None:
            problems.append(f"{row}: in the status document but not in the roadmap")
            continue
        status = task.get("status")
        normalized = outcome.strip().lower()
        merged = normalized in MERGED_OUTCOMES
        partial = "partial" in normalized
        if merged and not partial and status != _DONE_STATUS:
            problems.append(
                f"{row}: document says {outcome!r} but roadmap status is {status!r} "
                f"(expected {_DONE_STATUS!r})"
            )
        elif (not merged or partial) and status == _DONE_STATUS:
            problems.append(
                f"{row}: document says {outcome!r} but roadmap status is "
                f"{_DONE_STATUS!r} (row is not fully shipped)"
            )
    for row in sorted(set(rows) - set(outcomes)):
        problems.append(f"{row}: in the roadmap but missing from the status document")
    return problems


def fold_problems(
    outcomes: dict[str, str], payload: dict | None = None
) -> list[str]:
    """Folded rows must be gone from the audit track and present under their targets.

    A "folded" row is the easiest way to lose work silently: it disappears from the
    audit's own tracker on the promise that another row picked it up. This checks
    both halves of that promise.
    """
    if payload is None:
        payload = json.loads(ROADMAP_JSON.read_text(encoding="utf-8"))
    tasks = payload["tasks"] if isinstance(payload, dict) else payload
    all_ids = {task.get("id", "") for task in tasks}
    problems: list[str] = []
    for row, targets in sorted(FOLDED_ROWS.items()):
        outcome = outcomes.get(row, "").strip().lower()
        if outcome != "folded":
            problems.append(
                f"{row}: recorded as folded here but the status document says "
                f"{outcomes.get(row)!r}"
            )
        if f"{ROW_ID_PREFIX}{row}" in all_ids:
            problems.append(
                f"{row}: folded rows must not keep an {ROW_ID_PREFIX}* roadmap row"
            )
        for target in targets:
            if target not in all_ids:
                problems.append(
                    f"{row}: fold target {target} does not exist in the roadmap, so "
                    f"the folded work is tracked nowhere"
                )
    return problems


def evidence_gaps(
    outcomes: dict[str, str], rows: dict[str, dict], commits: list[str]
) -> list[str]:
    """``Done`` roadmap rows whose ``ready_reason`` cites no declared evidence commit."""
    gaps: list[str] = []
    for row, task in sorted(rows.items()):
        if task.get("status") != _DONE_STATUS:
            continue
        reason = task.get("ready_reason") or ""
        if not any(sha in reason for sha in commits):
            gaps.append(
                f"{row}: roadmap status is {_DONE_STATUS!r} but ready_reason cites "
                f"none of the evidence commits declared in the status document"
            )
    return gaps


# --------------------------------------------------------------------------- git

def blob_on_ref(path: str, ref: str | None = None) -> str:
    ref = pinned_ref() if ref is None else ref
    return _git("show", f"{ref}:{path}")


def paths_on_ref(ref: str | None = None) -> set[str]:
    ref = pinned_ref() if ref is None else ref
    return set(_git("ls-tree", "-r", "--name-only", ref).splitlines())


def is_ancestor(sha: str, ref: str | None = None) -> bool:
    ref = pinned_ref() if ref is None else ref
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, ref],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise GitObjectUnavailable(
            f"cannot test ancestry of {sha}: {result.stderr.strip()}"
        )
    return result.returncode == 0


def production_call_sites(symbol: str, ref: str | None = None) -> list[str]:
    """Non-test Python references to ``symbol``, excluding its own definition."""
    ref = pinned_ref() if ref is None else ref
    result = subprocess.run(
        ["git", "grep", "-n", symbol, ref, "--", "*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise GitObjectUnavailable(f"git grep failed: {result.stderr.strip()}")
    hits: list[str] = []
    for line in result.stdout.splitlines():
        location = line[len(ref) + 1 :] if line.startswith(f"{ref}:") else line
        if location.startswith("tests/") or f"def {symbol}" in line:
            continue
        hits.append(location)
    return hits
