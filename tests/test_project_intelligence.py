from command_center import models, project_intelligence


def _task(**overrides):
    task = {
        "id": overrides.get("id", "t"),
        "project": "AIOS",
        "title": "Task",
        "status": "Backlog",
        "priority": "Medium",
        "depends_on": [],
        "progress": 0,
    }
    task.update(overrides)
    return task


def test_rank_projects_by_activity_orders_by_active_task_count():
    tasks = [
        _task(id="a", project="AICOS"),
        _task(id="b", project="AICOS"),
        _task(id="c", project="AIOS"),
    ]
    ranked = project_intelligence.rank_projects_by_activity(models.PROJECT_IDS, tasks)
    assert ranked[0] == "AICOS"
    assert ranked[1] == "AIOS"
    # every registered project must still appear, never dropped
    assert set(ranked) == set(models.PROJECT_IDS)


def test_rank_projects_by_activity_never_drops_a_zero_activity_project():
    ranked = project_intelligence.rank_projects_by_activity(models.PROJECT_IDS, [])
    assert set(ranked) == set(models.PROJECT_IDS)
    assert len(ranked) == len(models.PROJECT_IDS)


def test_rank_projects_by_activity_does_not_mix_counts_across_split_projects():
    """Founder Review regression: AICC/AIOS/AICOS/PRODUCT/ECOSYSTEM must each
    accrue their own activity count — a task tagged for one must never bump
    another's count, which is exactly what happened before the registry
    split (an "AI Command Center" task and an "AICOS" task both counted as
    the single id AICOS)."""
    tasks = [
        _task(id="a", project="AICC"),
        _task(id="b", project="AICC"),
        _task(id="c", project="AICC"),
        _task(id="d", project="ECOSYSTEM"),
    ]
    ranked = project_intelligence.rank_projects_by_activity(models.PROJECT_IDS, tasks)
    assert ranked[0] == "AICC"
    assert ranked[1] == "ECOSYSTEM"
    # AICOS/AIOS/PRODUCT got zero tasks — their count must stay exactly 0,
    # never inflated by AICC's or ECOSYSTEM's tasks.
    assert ranked.index("AICOS") > 1
    assert ranked.index("AIOS") > 1
    assert ranked.index("PRODUCT") > 1


def test_rank_projects_by_activity_is_deterministic_on_ties():
    ranked_a = project_intelligence.rank_projects_by_activity(models.PROJECT_IDS, [])
    ranked_b = project_intelligence.rank_projects_by_activity(models.PROJECT_IDS, [])
    assert ranked_a == ranked_b == models.PROJECT_IDS


def test_compute_project_intelligence_completion_and_remaining():
    tasks = [
        _task(id="a", status="Done"),
        _task(id="b", status="Backlog"),
        _task(id="c", status="Backlog"),
    ]
    intel = project_intelligence.compute_project_intelligence("AIOS", tasks)
    assert intel["total"] == 3
    assert intel["done"] == 1
    assert intel["remaining"] == 2
    assert intel["completion_pct"] == 33


def test_compute_project_intelligence_scopes_to_project():
    tasks = [_task(id="a", project="AIOS"), _task(id="b", project="AICOS")]
    intel = project_intelligence.compute_project_intelligence("AIOS", tasks)
    assert intel["total"] == 1


def test_compute_project_intelligence_none_project_aggregates_all():
    tasks = [_task(id="a", project="AIOS"), _task(id="b", project="AICOS")]
    intel = project_intelligence.compute_project_intelligence(None, tasks)
    assert intel["total"] == 2


def test_compute_project_intelligence_health_good_when_nothing_blocked():
    tasks = [_task(id="a")]
    intel = project_intelligence.compute_project_intelligence("AIOS", tasks)
    assert intel["health"] == project_intelligence.HEALTH_GOOD
    assert intel["blocked"] == 0


def test_compute_project_intelligence_health_at_risk_when_mostly_blocked():
    blocker = _task(id="a")
    blocked_tasks = [_task(id=f"b{i}", depends_on=["a"]) for i in range(5)]
    intel = project_intelligence.compute_project_intelligence("AIOS", [blocker, *blocked_tasks])
    assert intel["health"] == project_intelligence.HEALTH_AT_RISK
    assert intel["blocked"] == 5


def test_compute_project_intelligence_critical_path_follows_dependency_chain():
    a = _task(id="a")
    b = _task(id="b", title="B", depends_on=["a"])
    c = _task(id="c", title="C", depends_on=["b"])
    intel = project_intelligence.compute_project_intelligence("AIOS", [a, b, c])
    # deepest blocked chain: c -> b (both blocked; a is not blocked, so the
    # chain search starts from blocked tasks only)
    assert intel["critical_path"][0] in ("C", "B")
    assert len(intel["critical_path"]) >= 1


def test_compute_project_intelligence_sprint_progress_is_mean_of_active_progress():
    tasks = [_task(id="a", progress=20), _task(id="b", progress=60)]
    intel = project_intelligence.compute_project_intelligence("AIOS", tasks)
    assert intel["sprint_progress_pct"] == 40


def test_compute_project_intelligence_no_tasks_returns_none_percentages():
    intel = project_intelligence.compute_project_intelligence("AIOS", [])
    assert intel["completion_pct"] is None
    assert intel["sprint_progress_pct"] is None
    assert intel["total"] == 0
