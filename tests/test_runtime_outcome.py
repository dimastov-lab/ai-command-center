from command_center.runtime import outcome


# --------------------------------------------------------------------------
# detect_blocker_language — bounded, deterministic phrase classifier
# --------------------------------------------------------------------------


def test_detect_blocker_language_none_for_empty_or_missing_text():
    assert outcome.detect_blocker_language(None) is None
    assert outcome.detect_blocker_language("") is None


def test_detect_blocker_language_none_for_ordinary_report_text():
    text = "All tests pass. Verdict: APPROVED FOR COMMIT. Files modified: foo.py"
    assert outcome.detect_blocker_language(text) is None


def test_detect_blocker_language_matches_cannot_execute():
    assert outcome.detect_blocker_language("I cannot execute this task without Bash access.") is not None


def test_detect_blocker_language_matches_permission_denied():
    assert outcome.detect_blocker_language("The Write tool call failed: permission denied.") is not None


def test_detect_blocker_language_matches_bash_unavailable():
    assert outcome.detect_blocker_language("Bash is unavailable in this session, so I cannot proceed.") is not None


def test_detect_blocker_language_matches_waiting_for_approval():
    assert outcome.detect_blocker_language("Waiting for approval to run this command.") is not None


# --------------------------------------------------------------------------
# permission_denial_tool_names
# --------------------------------------------------------------------------


def test_permission_denial_tool_names_empty_for_none_or_empty():
    assert outcome.permission_denial_tool_names(None) == []
    assert outcome.permission_denial_tool_names([]) == []


def test_permission_denial_tool_names_extracts_and_sorts_unique_names():
    denials = [
        {"tool_name": "Write", "tool_use_id": "a"},
        {"tool_name": "Edit", "tool_use_id": "b"},
        {"tool_name": "Write", "tool_use_id": "c"},
    ]
    assert outcome.permission_denial_tool_names(denials) == ["Edit", "Write"]


def test_permission_denial_tool_names_ignores_malformed_entries():
    assert outcome.permission_denial_tool_names([{"no_tool_name": True}, "not-a-dict"]) == []


# --------------------------------------------------------------------------
# classify_process_result — the EvaluatingResult decision for exit_code == 0
# --------------------------------------------------------------------------


def test_classify_ok_when_nothing_blocked_and_changes_made():
    classification, reason = outcome.classify_process_result(
        task_type="implementation", result_text="Done. Verdict: APPROVED FOR COMMIT",
        permission_denials=[], working_tree_changed=True,
    )
    assert (classification, reason) == (outcome.OK, None)


def test_classify_blocked_prefers_structured_permission_denials_over_text():
    """Required fix 6: structured event evidence wins over text
    classification when both are present."""
    classification, reason = outcome.classify_process_result(
        task_type="implementation",
        result_text="Everything looks fine, no issues at all.",
        permission_denials=[{"tool_name": "Write"}],
        working_tree_changed=False,
    )
    assert classification == outcome.BLOCKED
    assert reason == "permission_denied:Write"


def test_classify_blocked_falls_back_to_final_response_text_when_no_structured_evidence():
    classification, reason = outcome.classify_process_result(
        task_type="implementation",
        result_text="I cannot execute this task: Bash is unavailable.",
        permission_denials=[],
        working_tree_changed=False,
    )
    assert classification == outcome.BLOCKED
    assert reason is not None and reason.startswith("final_response:")


def test_classify_incomplete_when_requires_changes_and_working_tree_unchanged():
    classification, reason = outcome.classify_process_result(
        task_type="implementation",
        result_text="Everything is done, no changes needed.",
        permission_denials=[],
        working_tree_changed=False,
    )
    assert (classification, reason) == (outcome.INCOMPLETE, "working_tree_unchanged")


def test_detect_completion_evidence_matches_explicit_test_pass():
    assert outcome.detect_completion_evidence("All 2029 tests pass. Dashboard already done.") is not None
    assert outcome.detect_completion_evidence("19 tests passed; no regressions.") is not None
    assert outcome.detect_completion_evidence("===== 2029 passed in 12.3s =====") is not None


def test_detect_completion_evidence_matches_countless_test_pass():
    """A final message that reports tests passing *without* a numeric count
    (e.g. 'all tests passed', 'tests passed') is still positive completion
    evidence — the agent verified its work against the suite."""
    assert outcome.detect_completion_evidence("All tests passed. The fix is complete.") is not None
    assert outcome.detect_completion_evidence("Tests passed after applying the patch.") is not None
    assert outcome.detect_completion_evidence("all tests pass on my machine") is not None
    # still refused when a failure signal is present in the same text
    assert outcome.detect_completion_evidence("All tests passed but 3 failed later") is None


def test_detect_completion_evidence_none_for_bare_done_verdict():
    """A bare 'done'/'approved' verdict carries no proof the work was
    verified — it must NOT upgrade an unchanged-tree run to OK."""
    assert outcome.detect_completion_evidence("Verdict: APPROVED_FOR_COMMIT") is None
    assert outcome.detect_completion_evidence("done") is None
    assert outcome.detect_completion_evidence(None) is None


def test_detect_completion_evidence_none_when_failure_evidence_present():
    """A mixed 'passed, failed' summary is a failing run — the failure guard
    refuses the upgrade even though a pass phrase is present."""
    assert outcome.detect_completion_evidence("2029 passed, 3 failed") is None
    assert outcome.detect_completion_evidence("19 tests passed, but 2 failing tests remain") is None
    assert outcome.detect_completion_evidence("5 errors during collection") is None


def test_classify_ok_when_unchanged_tree_but_agent_reports_passing_tests():
    """The recurring false negative: a task whose implementation already
    landed in an earlier run leaves a clean tree; the agent's final message
    carrying explicit test-pass evidence upgrades the would-be INCOMPLETE to
    OK so the task stops looping on `incomplete:working_tree_unchanged`."""
    classification, reason = outcome.classify_process_result(
        task_type="implementation",
        result_text="All 2029 tests pass. Module is already fully implemented per spec.",
        permission_denials=[],
        working_tree_changed=False,
        provider_completion_valid=True,
    )
    assert (classification, reason) == (outcome.OK, None)


def test_classify_incomplete_when_provider_says_not_complete_even_with_test_evidence():
    """A provider that explicitly reports `provider_completion_valid=False`
    (it did not produce a final message) cannot be upgraded by text alone."""
    classification, reason = outcome.classify_process_result(
        task_type="implementation",
        result_text="All 19 tests pass.",
        permission_denials=[],
        working_tree_changed=False,
        provider_completion_valid=False,
    )
    assert (classification, reason) == (outcome.INCOMPLETE, "working_tree_unchanged")


def test_classify_blocked_takes_priority_over_completion_evidence():
    classification, reason = outcome.classify_process_result(
        task_type="implementation",
        result_text="All 2029 tests pass but I cannot continue without Bash.",
        permission_denials=[],
        working_tree_changed=False,
        provider_completion_valid=True,
    )
    assert classification == outcome.BLOCKED
    assert reason is not None and reason.startswith("final_response:")


def test_classify_ok_for_read_only_task_type_even_when_working_tree_unchanged():
    """A `review`/read-only task type is never expected to change the
    working tree, so an unchanged tree is not evidence of incompleteness for
    it — only `REQUIRES_CHANGES_TASK_TYPES` are held to that bar."""
    classification, reason = outcome.classify_process_result(
        task_type="review", result_text="APPROVED", permission_denials=[], working_tree_changed=False,
    )
    assert (classification, reason) == (outcome.OK, None)


def test_classify_blocked_takes_priority_over_incomplete():
    classification, reason = outcome.classify_process_result(
        task_type="implementation",
        result_text="",
        permission_denials=[{"tool_name": "Bash"}],
        working_tree_changed=False,
    )
    assert classification == outcome.BLOCKED
    assert reason == "permission_denied:Bash"


def test_intentional_git_write_denial_is_not_a_blocker():
    """An implementation agent that did its work but incidentally tried a
    blocked git command (`git stash list`, `git commit`, …) is NOT blocked —
    git-write is denied by design (the pipeline owns it), not a permission gap."""
    from command_center.runtime import outcome
    denials = [{"tool_name": "Bash", "tool_input": {"command": 'cd /x && echo "===stash==="; git stash list'}}]
    result, reason = outcome.classify_process_result(
        task_type="implementation", result_text="Done.", permission_denials=denials, working_tree_changed=True
    )
    assert result == outcome.OK
    assert reason is None


def test_non_git_permission_denial_still_blocks():
    """A denial that is NOT an intentional git-write block still marks the run
    blocked — that is a real permission gap the operator must see."""
    from command_center.runtime import outcome
    denials = [{"tool_name": "WebFetch", "tool_input": {"url": "https://x"}}]
    result, reason = outcome.classify_process_result(
        task_type="implementation", result_text="", permission_denials=denials, working_tree_changed=False
    )
    assert result == outcome.BLOCKED
    assert "WebFetch" in reason
