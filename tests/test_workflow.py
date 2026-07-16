from command_center import models, report_parser, workflow


def _run_with_verdict(verdict: str | None) -> dict:
    run = models.new_run_record(
        project="AIOS",
        task_id="task-1",
        agent="claude_code",
        task_type="review",
        repository_path="/tmp/repo",
        prompt="p",
        timeout_seconds=60,
    )
    parsed = report_parser.empty_parsed_result()
    parsed["verdict"] = verdict
    run["parsed"] = parsed
    run["status"] = "completed"
    return run


def test_not_approved_for_commit_suggests_remediation():
    suggestion = workflow.suggest_next_task(_run_with_verdict(models.VERDICT_NOT_APPROVED_FOR_COMMIT))
    assert suggestion["task_type"] == "remediation"
    assert suggestion["workflow_stage"] == "Remediation"
    assert suggestion["requires_user_choice"] is False


def test_not_ready_for_final_review_suggests_remediation():
    suggestion = workflow.suggest_next_task(_run_with_verdict(models.VERDICT_NOT_READY_FOR_FINAL_REVIEW))
    assert suggestion["task_type"] == "remediation"


def test_ready_for_final_review_suggests_final_gate():
    suggestion = workflow.suggest_next_task(_run_with_verdict(models.VERDICT_READY_FOR_FINAL_REVIEW))
    assert suggestion["task_type"] == "final_gate"
    assert suggestion["workflow_stage"] == "Final Review"


def test_ready_for_commit_requires_user_choice_between_final_gate_and_implementation():
    suggestion = workflow.suggest_next_task(_run_with_verdict(models.VERDICT_READY_FOR_COMMIT))
    assert suggestion["task_type"] is None
    assert suggestion["requires_user_choice"] is True
    assert suggestion["task_type_choices"] == workflow.TASK_TYPE_CHOICES_FOR_READY_FOR_COMMIT


def test_approved_for_commit_suggests_commit_preparation():
    suggestion = workflow.suggest_next_task(_run_with_verdict(models.VERDICT_APPROVED_FOR_COMMIT))
    assert suggestion["task_type"] == "implementation"
    assert suggestion["workflow_stage"] == "Commit Pending"
    assert suggestion["requires_user_choice"] is False


def test_unknown_verdict_requires_user_choice():
    suggestion = workflow.suggest_next_task(_run_with_verdict(None))
    assert suggestion["requires_user_choice"] is True
    assert suggestion["task_type"] is None


def test_objective_draft_includes_findings_and_next_action():
    run = _run_with_verdict(models.VERDICT_NOT_APPROVED_FOR_COMMIT)
    run["parsed"]["findings"]["Blocker"] = ["SQL injection in query builder"]
    run["parsed"]["recommended_next_action"] = "Fix the injection bug, then re-run review."
    suggestion = workflow.suggest_next_task(run)
    assert "SQL injection in query builder" in suggestion["objective_draft"]
    assert "Fix the injection bug, then re-run review." in suggestion["objective_draft"]


def test_suggestion_never_executes_or_creates_anything():
    """suggest_next_task must be a pure read: it must never write tasks/runs/reports."""
    run = _run_with_verdict(models.VERDICT_APPROVED_FOR_COMMIT)
    before = dict(run)
    workflow.suggest_next_task(run)
    assert run == before


# --------------------------------------------------------------------------
# F-03: a contradictory parse must force manual review before next-task suggestion
# --------------------------------------------------------------------------


def test_contradictory_verdict_forces_requires_user_choice():
    run = _run_with_verdict(models.VERDICT_APPROVED_FOR_COMMIT)
    run["parsed"]["verdict_contradictory"] = True
    suggestion = workflow.suggest_next_task(run)
    assert suggestion["requires_user_choice"] is True
    assert suggestion["contradictory"] is True


def test_contradictory_verdict_warning_appears_in_objective_draft():
    run = _run_with_verdict(models.VERDICT_NOT_APPROVED_FOR_COMMIT)
    run["parsed"]["verdict_contradictory"] = True
    suggestion = workflow.suggest_next_task(run)
    assert "противоречивые вердикты" in suggestion["objective_draft"]


def test_manual_verdict_correction_resolves_contradiction():
    run = _run_with_verdict(models.VERDICT_APPROVED_FOR_COMMIT)
    run["parsed"]["verdict_contradictory"] = True
    run["parsed"]["manual_corrections"] = {"verdict": models.VERDICT_NOT_APPROVED_FOR_COMMIT}
    suggestion = workflow.suggest_next_task(run)
    assert suggestion["contradictory"] is False
    assert suggestion["requires_user_choice"] is False
    assert suggestion["task_type"] == "remediation"


def test_non_contradictory_verdict_does_not_force_user_choice():
    run = _run_with_verdict(models.VERDICT_APPROVED_FOR_COMMIT)
    suggestion = workflow.suggest_next_task(run)
    assert suggestion["contradictory"] is False
    assert suggestion["requires_user_choice"] is False
