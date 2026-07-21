"""AICC-UI-001 remediation: every project-scoped filter, counter, ranking, and
grouping in the app must agree on which project a task belongs to, whether the
task stores the canonical `models.PROJECT_IDS` id ("AICC") or a display
name / alias ("AI Command Center"). The single source of truth is
`project_config.canonical_project_id` / `project_config.project_matches`; these
tests prove no consumer of it drifts.

Kept Streamlit-free (the end-to-end pill == lane == strip proof lives in
`tests/test_app_streamlit.py`) so the invariant is fast and unambiguous.
"""

from __future__ import annotations

from command_center import (
    models,
    project_config,
    project_intelligence,
    recommend,
    task_view,
)


# --------------------------------------------------------------------------
# The shared helper itself
# --------------------------------------------------------------------------


def test_canonical_project_id_resolves_id_display_name_and_alias():
    # Canonical id passes through unchanged (the identity property every
    # comparison site relies on to be a safe drop-in).
    assert project_config.canonical_project_id("AICC") == "AICC"
    # Display name and case/whitespace-variant alias both collapse to the id.
    assert project_config.canonical_project_id("AI Command Center") == "AICC"
    assert project_config.canonical_project_id("  ai   command center ") == "AICC"
    assert project_config.canonical_project_id("AIOS Product") == "PRODUCT"


def test_canonical_project_id_falls_back_to_raw_for_unknown():
    # A genuinely unknown project is never broadened onto a real lane: it falls
    # back to itself so it matches only itself.
    assert project_config.canonical_project_id("Totally Unknown") == "Totally Unknown"
    assert project_config.canonical_project_id(None) is None
    assert project_config.canonical_project_id("") == ""


def test_project_matches_none_is_all_projects():
    assert project_config.project_matches("AI Command Center", None) is True
    assert project_config.project_matches("anything", None) is True


def test_project_matches_display_name_against_canonical_selection():
    assert project_config.project_matches("AI Command Center", "AICC") is True
    assert project_config.project_matches("AICC", "AICC") is True
    assert project_config.project_matches("AI Command Center", "AIOS") is False
    # Unknown project matches only its own exact raw value.
    assert project_config.project_matches("Totally Unknown", "AICC") is False
    assert project_config.project_matches("Totally Unknown", "Totally Unknown") is True


# --------------------------------------------------------------------------
# "AICC" and "AI Command Center" must behave identically *everywhere*
# --------------------------------------------------------------------------


def _mixed_tasks(aicc_project_value: str) -> list[dict]:
    """Five open AICC tasks (none Done/blocked, so they are also all
    recommendation candidates) plus a couple of other-project tasks. The AICC
    tasks store `aicc_project_value` — the tests call this once with the
    canonical id and once with the display name and assert every consumer is
    invariant to the choice."""
    return [
        {"id": "a1", "project": aicc_project_value, "priority": "P0", "status": "Backlog", "depends_on": []},
        {"id": "a2", "project": aicc_project_value, "priority": "High", "status": "Next", "depends_on": []},
        {"id": "a3", "project": aicc_project_value, "priority": "Medium", "status": "In Progress", "depends_on": []},
        {"id": "a4", "project": aicc_project_value, "priority": "Low", "status": "Backlog", "depends_on": []},
        {"id": "a5", "project": aicc_project_value, "priority": "Critical", "status": "Review", "depends_on": []},
        {"id": "o1", "project": "AIOS", "priority": "High", "status": "Backlog", "depends_on": []},
        {"id": "o2", "project": "AICOS", "priority": "High", "status": "Backlog", "depends_on": []},
    ]


def _consumer_snapshot(tasks: list[dict]) -> dict:
    """What every project-scoped consumer reports for the AICC lane — the values
    that must be identical regardless of how the AICC tasks spell their project."""
    options = task_view.kanban_priority_options(tasks)
    lane_ids = {t["id"] for t in task_view.filter_kanban_tasks(tasks, project="AICC", priorities=options)}

    # The exact counting expression `ui.project_selector.render_project_selector`
    # uses for its pill labels.
    pill_count = sum(1 for t in tasks if project_config.canonical_project_id(t.get("project")) == "AICC")

    intel = project_intelligence.compute_project_intelligence("AICC", tasks)
    ranking = project_intelligence.rank_projects_by_activity(list(models.PROJECT_IDS), tasks)
    reco_ids = {rec.task["id"] for rec in recommend.list_recommendations(tasks, project="AICC", limit=100)}

    return {
        "lane_ids": lane_ids,
        "pill_count": pill_count,
        "intel_total": intel["total"],
        "intel_health": intel["health"],
        "ranking": ranking,
        "reco_ids": reco_ids,
    }


def test_display_name_and_canonical_id_behave_identically_everywhere():
    canonical = _consumer_snapshot(_mixed_tasks("AICC"))
    display = _consumer_snapshot(_mixed_tasks("AI Command Center"))
    assert canonical == display, (
        "A task storing the display name 'AI Command Center' is treated "
        "differently from one storing the canonical id 'AICC' by some "
        "project-scoped consumer:\n"
        f"  canonical: {canonical}\n  display:   {display}"
    )


def test_all_project_counters_agree_for_the_same_lane():
    # For a fixture with no Done/blocked AICC tasks and all priorities selected,
    # the Kanban lane, the pill count, the intelligence-strip total, and the
    # recommendation inputs must all report the same five AICC tasks — the exact
    # cross-component consistency AICC-UI-001's partial fix had broken (lane/pill
    # said one number while the strip and recommendations said another).
    tasks = _mixed_tasks("AI Command Center")
    snap = _consumer_snapshot(tasks)

    expected_ids = {"a1", "a2", "a3", "a4", "a5"}
    assert snap["lane_ids"] == expected_ids
    assert snap["reco_ids"] == expected_ids
    assert snap["pill_count"] == len(expected_ids)
    assert snap["intel_total"] == len(expected_ids)
    # lane == pill == strip == recommendation inputs, stated as one equality.
    assert len(snap["lane_ids"]) == snap["pill_count"] == snap["intel_total"] == len(snap["reco_ids"])


def test_ranking_counts_display_name_tasks_under_canonical_project():
    # AICC has the most active tasks once display-name tasks are counted; a raw
    # comparison would have hidden them and mis-ranked the project.
    tasks = _mixed_tasks("AI Command Center")
    ranking = project_intelligence.rank_projects_by_activity(list(models.PROJECT_IDS), tasks)
    assert ranking[0] == "AICC"
