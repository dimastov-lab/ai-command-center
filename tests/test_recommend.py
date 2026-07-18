from command_center import recommend


def _task(**overrides):
    task = {
        "id": overrides.get("id", "t"),
        "project": "AIOS",
        "title": "Task",
        "status": "Backlog",
        "priority": "Medium",
        "depends_on": [],
    }
    task.update(overrides)
    return task


def test_recommend_next_task_returns_none_when_no_open_tasks():
    assert recommend.recommend_next_task([]) is None
    assert recommend.recommend_next_task([_task(status="Done")]) is None


def test_recommend_next_task_excludes_blocked_tasks():
    blocker = _task(id="a", status="Backlog")
    blocked = _task(id="b", depends_on=["a"])
    result = recommend.recommend_next_task([blocker, blocked])
    assert result.task["id"] == "a"


def test_recommend_next_task_prefers_higher_priority():
    low = _task(id="low", priority="Low")
    critical = _task(id="critical", priority="Critical")
    result = recommend.recommend_next_task([low, critical])
    assert result.task["id"] == "critical"
    assert any("приоритет" in reason for reason in result.reasons)


def test_recommend_next_task_penalizes_repo_with_another_task_running():
    idle = _task(id="idle", repository_path="/repos/a")
    busy_running = _task(id="busy-running", repository_path="/repos/b", launch_status="Running")
    busy_candidate = _task(id="busy-candidate", repository_path="/repos/b", priority="Critical")
    result = recommend.recommend_next_task([idle, busy_running, busy_candidate])
    # idle repo's candidate should win over the higher-priority-but-busy-repo one
    assert result.task["id"] == "idle"


def test_recommend_next_task_filters_by_project():
    aios_task = _task(id="a", project="AIOS")
    aicos_task = _task(id="b", project="AICOS", priority="Critical")
    result = recommend.recommend_next_task([aios_task, aicos_task], project="AIOS")
    assert result.task["id"] == "a"


def test_recommend_next_task_always_returns_reasons():
    result = recommend.recommend_next_task([_task()])
    assert result.reasons
    assert all(isinstance(reason, str) and reason for reason in result.reasons)


def test_recommend_next_task_boosts_remediation_stage():
    plain = _task(id="plain")
    remediation = _task(id="remediation", workflow_stage="Remediation")
    result = recommend.recommend_next_task([plain, remediation])
    assert result.task["id"] == "remediation"
