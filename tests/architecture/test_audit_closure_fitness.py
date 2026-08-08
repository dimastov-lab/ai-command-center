"""Fitness gates for the Founder Functional Audit 9761459 closure.

The audit is closed by ``docs/audits/FOUNDER_FUNCTIONAL_AUDIT_9761459_STATUS.md``,
which hands its residual work to the ``AICC-AUDIT-W*`` rows in
``docs/roadmap/MASTER_ROADMAP_TASKS.json``. A closure like that is only worth the
prose it is written in if something keeps the prose, the tracker, and the code in
step — a documentation task otherwise validates as "1/1 commands passed" (a bare
``compileall``) while asserting anything at all about the repository.

These tests are that something. They are split so the doc↔tracker half runs
everywhere (no git needed) and only the code-probe half depends on the pinned
commit being present in the checkout.

Parsers and probes live in ``tests/architecture/audit_closure.py``.
"""

from __future__ import annotations

import pytest

from tests.architecture import audit_closure as closure

requires_pinned_commit = pytest.mark.skipif(
    not closure.git_available(),
    reason=(
        "the commit pinned by the audit's merge-verification section is not in this "
        "checkout (no .git, or a shallow clone)"
    ),
)


# ------------------------------------------------------- doc <-> tracker (no git)

def test_status_document_and_roadmap_agree_on_every_row():
    """Every ``AICC-AUDIT-W*`` row reads the same in the document and the roadmap.

    This is the gap the closure passes kept falling into: all 13 roadmap rows read
    ``Backlog`` for months while the document called seven of them shipped, so the
    designated tracker carried no signal on this track. Either artifact moving
    without the other now fails.
    """
    outcomes = closure.documented_outcomes()
    rows = closure.roadmap_rows()
    assert outcomes, "no merge-verification table found in the status document"
    problems = closure.status_mismatches(outcomes, rows)
    assert not problems, (
        "the audit-closure document and the roadmap disagree; both describe the "
        "same rows and must be updated together (docs/audits/"
        "FOUNDER_FUNCTIONAL_AUDIT_9761459_STATUS.md, §'Merge verification'):\n"
        + "\n".join(problems)
    )


def test_every_done_row_cites_a_declared_evidence_commit():
    """A ``Done`` row must point at a commit the document declares as evidence.

    Guards against a row being marked ``Done`` with a prose justification and no
    traceable commit — the shape that let closed-unmerged PRs read as delivered.
    """
    outcomes = closure.documented_outcomes()
    rows = closure.roadmap_rows()
    commits = closure.evidence_commits()
    assert commits, "the merge-verification section declares no evidence commits"
    gaps = closure.evidence_gaps(outcomes, rows, commits)
    assert not gaps, "\n".join(gaps)


def test_folded_row_is_tracked_by_its_fold_targets():
    """W3-002 left the audit track on the promise that ``AICC-D2*`` picked it up.

    Both halves are checked: it must no longer carry an ``AICC-AUDIT-W*`` row, and
    every task it was folded into must exist. A fold whose targets vanished is work
    tracked nowhere.
    """
    problems = closure.fold_problems(closure.documented_outcomes())
    assert not problems, "\n".join(problems)


def test_merged_rows_all_carry_a_machine_checkable_anchor():
    """The set of rows called merged equals the set with a code anchor.

    Keeps the anchor table below from drifting behind the document: a row promoted
    to *merged* without evidence a test can read fails here, not silently later.
    """
    outcomes = closure.documented_outcomes()
    merged = {
        row
        for row, outcome in outcomes.items()
        if outcome.strip().lower() in closure.MERGED_OUTCOMES
    }
    anchored = set(closure.MERGED_CODE_ANCHORS)
    assert merged == anchored, (
        "every row the status document calls merged needs an entry in "
        "audit_closure.MERGED_CODE_ANCHORS (and vice versa).\n"
        f"  documented but unanchored: {sorted(merged - anchored)}\n"
        f"  anchored but not documented as merged: {sorted(anchored - merged)}"
    )


# ------------------------------------------------------------- code probes (git)

@requires_pinned_commit
def test_evidence_commits_are_ancestors_of_the_pinned_ref():
    """"Merged" means reachable from the shared branch the document pinned.

    Prior passes certified against the *local* ``main``, which had diverged from
    ``origin/main`` in both directions; ancestry against the pinned commit is the
    check that would have caught it.
    """
    ref = closure.pinned_ref()
    missing = [sha for sha in closure.evidence_commits() if not closure.is_ancestor(sha, ref)]
    assert not missing, (
        f"evidence commits declared merged are not ancestors of {ref}: {missing}"
    )


@requires_pinned_commit
def test_merged_rows_read_as_merged_in_the_code():
    """Each merged row's remediation is actually present on the pinned commit."""
    ref = closure.pinned_ref()
    problems: list[str] = []
    for row, anchors in sorted(closure.MERGED_CODE_ANCHORS.items()):
        for path, required in anchors:
            try:
                source = closure.blob_on_ref(path, ref)
            except closure.GitObjectUnavailable:
                problems.append(f"{row}: {path} does not exist on {ref}")
                continue
            for needle in required:
                if needle not in source:
                    problems.append(f"{row}: {path} on {ref} does not contain {needle!r}")
    assert not problems, (
        f"rows the audit closed as merged are not readable on {ref}:\n"
        + "\n".join(problems)
    )


@requires_pinned_commit
def test_still_open_rows_are_still_open_in_the_code():
    """Rows the closure leaves open have not quietly been fixed on the pinned ref.

    The optimistic direction is the dangerous one — a closure that under-reports
    delivered work costs a re-read; one that over-reports ships a false all-clear.
    This gate covers the reverse case for the rows with an unambiguous probe.
    """
    ref = closure.pinned_ref()
    outcomes = closure.documented_outcomes()
    problems: list[str] = []
    for row, (path, required, forbidden) in sorted(closure.STILL_OPEN_ANCHORS.items()):
        assert outcomes.get(row, "").strip().lower() == "still open", (
            f"{row} has a still-open anchor but the document calls it "
            f"{outcomes.get(row)!r}"
        )
        source = closure.blob_on_ref(path, ref)
        for needle in required:
            if needle not in source:
                problems.append(f"{row}: expected {needle!r} in {path} on {ref}")
        for needle in forbidden:
            if needle in source:
                problems.append(
                    f"{row}: {path} on {ref} contains {needle!r} — the row may have "
                    f"been remediated; re-read it before trusting this closure"
                )
    assert not problems, "\n".join(problems)


@requires_pinned_commit
def test_w1_006_remains_partial_for_the_documented_reason():
    """W1-006 is closed as *merged, still partial* on one specific ground.

    ``recover_stale_claim`` landed but has no production call site, so an operator
    cannot recover an orphaned claim. If a caller appears, the row is no longer
    partial and the closure document is stale.
    """
    ref = closure.pinned_ref()
    callers = closure.production_call_sites("recover_stale_claim", ref)
    assert not callers, (
        "portfolio_launch.recover_stale_claim now has a production call site on "
        f"{ref} ({callers}); AICC-AUDIT-W1-006 is no longer 'merged, still partial' "
        "— update §'Merge verification' in the audit status document"
    )


@requires_pinned_commit
def test_documented_carry_over_is_genuinely_not_on_the_pinned_ref():
    """The carry-over note names a local-only commit; it must stay local-only here.

    ``744a09c`` (task-level ``repository_path`` for workspace isolation) was
    described as delivered while living only on an unpushed local branch. The
    document records it as *not* on the verified commit; this pins that claim.
    """
    ref = closure.pinned_ref()
    assert not closure.is_ancestor("744a09c", ref), (
        f"744a09c is an ancestor of {ref}; the carry-over note in §'Merge "
        "verification' is stale and should be removed"
    )


# ------------------------------------------------------------------- gate hygiene

def test_gate_detects_a_flipped_row():
    """The consistency checker is not vacuous: flip a row and it goes red.

    Mutation check for :func:`audit_closure.status_mismatches` — the assertion the
    other tests lean on. Without it, a parser that silently matched nothing would
    make every gate above pass on any document.
    """
    outcomes = {"W0-006": "Merged", "W1-002": "Still open"}
    rows = {
        "W0-006": {"id": "AICC-AUDIT-W0-006", "status": "Done"},
        "W1-002": {"id": "AICC-AUDIT-W1-002", "status": "Backlog"},
    }
    assert closure.status_mismatches(outcomes, rows) == []

    shipped_but_backlog = dict(rows, **{"W0-006": {"status": "Backlog"}})
    assert closure.status_mismatches(outcomes, shipped_but_backlog)

    open_but_done = dict(rows, **{"W1-002": {"status": "Done"}})
    assert closure.status_mismatches(outcomes, open_but_done)

    partial_marked_done = closure.status_mismatches(
        {"W1-006": "Merged, still partial"}, {"W1-006": {"status": "Done"}}
    )
    assert partial_marked_done

    assert closure.status_mismatches({"W9-999": "Merged"}, {})
    assert closure.status_mismatches({}, {"W9-999": {"status": "Backlog"}})


def test_gate_detects_a_broken_fold():
    """Mutation check for :func:`audit_closure.fold_problems`."""
    row, targets = next(iter(closure.FOLDED_ROWS.items()))
    intact = {"tasks": [{"id": target} for target in targets]}
    assert closure.fold_problems({row: "Folded"}, intact) == []

    lost_target = {"tasks": [{"id": target} for target in targets[1:]]}
    assert closure.fold_problems({row: "Folded"}, lost_target)

    still_tracked = {"tasks": [{"id": t} for t in (*targets, f"{closure.ROW_ID_PREFIX}{row}")]}
    assert closure.fold_problems({row: "Folded"}, still_tracked)

    assert closure.fold_problems({row: "Merged"}, intact)


def test_gate_detects_an_undocumented_done_row():
    """Mutation check for :func:`audit_closure.evidence_gaps`."""
    commits = ["7bfb025"]
    cited = {"W0-006": {"status": "Done", "ready_reason": "shipped in 7bfb025"}}
    assert closure.evidence_gaps({}, cited, commits) == []

    uncited = {"W0-006": {"status": "Done", "ready_reason": "looks done to me"}}
    assert closure.evidence_gaps({}, uncited, commits)


@requires_pinned_commit
def test_code_probes_are_not_silently_empty():
    """The git probes distinguish present from absent, on the pinned ref.

    ``production_call_sites`` returning ``[]`` is the evidence that W1-006 is still
    partial, so a probe that returned ``[]`` unconditionally — a swallowed git
    error, a wrong ref — would assert exactly that for free. A symbol that *is*
    called in production pins the positive case.
    """
    ref = closure.pinned_ref()
    assert closure.production_call_sites("report_path_for", ref), (
        "probe found no production caller for a symbol that has several; "
        "production_call_sites is not reading the tree"
    )
    assert closure.production_call_sites("no_such_symbol_anywhere_xyz", ref) == []

    source = closure.blob_on_ref("command_center/git_info.py", ref)
    assert "def fetch_remotes" in source
    assert "def no_such_function_xyz" not in source
    with pytest.raises(closure.GitObjectUnavailable):
        closure.blob_on_ref("no/such/path/xyz.py", ref)


def test_document_parsers_find_real_content():
    """The parsers read the live document, not an empty match.

    A regex that stops matching after an edit would turn every gate above into a
    no-op; these bounds are the tripwire.
    """
    outcomes = closure.documented_outcomes()
    commits = closure.evidence_commits()
    rows = closure.roadmap_rows()
    # 14 dispositions in the table: the 13 executable roadmap rows plus W3-002,
    # which was folded into the desktop D2 tasks and so has no row of its own.
    assert len(outcomes) == 14, f"expected 14 documented rows, got {sorted(outcomes)}"
    assert len(rows) == 13, f"expected 13 roadmap rows, got {sorted(rows)}"
    assert len(commits) == 9, f"expected 9 evidence commits, got {commits}"
    assert closure.pinned_ref()
