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
