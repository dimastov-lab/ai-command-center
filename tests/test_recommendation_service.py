from command_center import execution_queue, recommendation_service


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


def test_build_recommendation_views_includes_reasons_and_score():
    tasks = [_task(id="a", priority="Critical")]
    views = recommendation_service.build_recommendation_views(tasks)
    assert len(views) == 1
    assert views[0]["task_id"] == "a"
    assert views[0]["reasons"]
    assert isinstance(views[0]["score"], float)


def test_build_recommendation_views_resolves_dependencies():
    dep = _task(id="dep", title="Dependency", status="Done")
    task = _task(id="a", depends_on=["dep"])
    views = recommendation_service.build_recommendation_views([dep, task])
    view = next(v for v in views if v["task_id"] == "a")
    assert view["dependencies"] == [{"id": "dep", "title": "Dependency", "status": "Done", "done": True}]


def test_build_recommendation_views_impact_counts_dependents():
    base = _task(id="base")
    dependent_one = _task(id="d1", depends_on=["base"])
    dependent_two = _task(id="d2", depends_on=["base"])
    views = recommendation_service.build_recommendation_views([base, dependent_one, dependent_two])
    base_view = next(v for v in views if v["task_id"] == "base")
    assert base_view["impact"] == 2


def test_build_recommendation_views_ready_is_true_for_every_candidate():
    # recommend.py already excludes blocked tasks from candidacy, so every
    # recommendation view must be ready — never silently mislabel one.
    tasks = [_task(id="a")]
    views = recommendation_service.build_recommendation_views(tasks)
    assert views[0]["ready"] is True


def test_build_recommendation_views_marks_queued_state():
    task = _task(id="a")
    queue_entries = [
        {"id": "q1", "task_id": "a", "state": execution_queue.STATE_READY, "reason": None}
    ]
    views = recommendation_service.build_recommendation_views([task], queue_entries=queue_entries)
    assert views[0]["queued_state"] == execution_queue.STATE_READY


def test_build_recommendation_views_launched_entry_is_not_reported_as_queued():
    task = _task(id="a")
    queue_entries = [
        {"id": "q1", "task_id": "a", "state": execution_queue.STATE_LAUNCHED, "reason": None}
    ]
    views = recommendation_service.build_recommendation_views([task], queue_entries=queue_entries)
    assert views[0]["queued_state"] is None


def test_build_recommendation_views_respects_limit():
    tasks = [_task(id=f"t{i}") for i in range(5)]
    views = recommendation_service.build_recommendation_views(tasks, limit=2)
    assert len(views) == 2
