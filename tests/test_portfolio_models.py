from pathlib import Path

from command_center.portfolio_models import (
    PortfolioCardError,
    load_portfolio_tasks,
    parse_card,
    unmet_requirements,
)

VALID_CARD = """---
schema_version: "1.0"
task_id: "AICC-UI-001"
title: "Do the thing"
project: "AICC"
type: "implementation"
capability: "ui"
priority: "low"
status: "ready"
repository: "~/Projects/ai-command-center"
base_branch: "main"
branch: "fix/thing"
worktree: "~/Projects/ai-command-center"
agent: null
autonomy: "confirmed"
parallel_group: null
requires: []
blocks: ["AICC-HYG-001"]
conflicts_with: []
deliverables: ["app.py change committed"]
validation: ["python -m pytest -q"]
stop_conditions: ["Stop once committed."]
evidence: ["some evidence"]
confidence: null
gated_by: []
---

# Do the thing

## Objective

Do the thing.
"""


def _write_card(root: Path, lane: str, project: str, filename: str, text: str) -> Path:
    lane_dir = root / "tasks" / lane / project
    lane_dir.mkdir(parents=True, exist_ok=True)
    path = lane_dir / filename
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Single-card parsing
# --------------------------------------------------------------------------


def test_parse_card_reads_all_frontmatter_fields(tmp_path):
    path = _write_card(tmp_path, "ready", "AICC", "AICC-UI-001.md", VALID_CARD)
    task = parse_card(path, lane="ready")

    assert task.task_id == "AICC-UI-001"
    assert task.project == "AICC"
    assert task.title == "Do the thing"
    assert task.status == "ready"
    assert task.repository == "~/Projects/ai-command-center"
    assert task.base_branch == "main"
    assert task.branch == "fix/thing"
    assert task.worktree == "~/Projects/ai-command-center"
    assert task.agent is None
    assert task.autonomy == "confirmed"
    assert task.requires == []
    assert task.blocks == ["AICC-HYG-001"]
    assert task.deliverables == ["app.py change committed"]
    assert task.validation == ["python -m pytest -q"]
    assert task.stop_conditions == ["Stop once committed."]
    assert task.confidence is None
    assert task.missing_required_fields() == []


def test_parse_card_preserves_original_text_verbatim(tmp_path):
    path = _write_card(tmp_path, "ready", "AICC", "AICC-UI-001.md", VALID_CARD)
    task = parse_card(path, lane="ready")
    assert task.raw_text == VALID_CARD
    assert "## Objective" in task.body


def test_parse_card_raises_on_missing_frontmatter_delimiter(tmp_path):
    path = tmp_path / "bad.md"
    path.write_text("task_id: \"X\"\n# no frontmatter delimiter\n", encoding="utf-8")
    try:
        parse_card(path, lane="ready")
        assert False, "expected PortfolioCardError"
    except PortfolioCardError:
        pass


def test_parse_card_raises_on_unterminated_frontmatter(tmp_path):
    path = tmp_path / "bad.md"
    path.write_text('---\ntask_id: "X"\n', encoding="utf-8")
    try:
        parse_card(path, lane="ready")
        assert False, "expected PortfolioCardError"
    except PortfolioCardError:
        pass


def test_parse_card_raises_on_malformed_line(tmp_path):
    path = tmp_path / "bad.md"
    path.write_text("---\nnot a valid line without colon\n---\nbody\n", encoding="utf-8")
    try:
        parse_card(path, lane="ready")
        assert False, "expected PortfolioCardError"
    except PortfolioCardError:
        pass


def test_parse_card_raises_on_non_list_value_for_list_field(tmp_path):
    text = VALID_CARD.replace('requires: []', 'requires: "not-a-list"')
    path = _write_card(tmp_path, "ready", "AICC", "bad.md", text)
    try:
        parse_card(path, lane="ready")
        assert False, "expected PortfolioCardError"
    except PortfolioCardError:
        pass


def test_parse_card_handles_colon_inside_quoted_value(tmp_path):
    text = VALID_CARD.replace(
        'title: "Do the thing"', 'title: "Fix: the thing, properly"'
    )
    path = _write_card(tmp_path, "ready", "AICC", "colon.md", text)
    task = parse_card(path, lane="ready")
    assert task.title == "Fix: the thing, properly"


# --------------------------------------------------------------------------
# Directory loading
# --------------------------------------------------------------------------


def test_load_portfolio_tasks_missing_root_reports_missing(tmp_path):
    result = load_portfolio_tasks(tmp_path / "does-not-exist")
    assert result.missing is True
    assert result.tasks == []


def test_load_portfolio_tasks_loads_from_all_four_lanes(tmp_path):
    _write_card(tmp_path, "ready", "AICC", "AICC-1.md", VALID_CARD)
    blocked_text = VALID_CARD.replace('task_id: "AICC-UI-001"', 'task_id: "AICC-2"').replace(
        'status: "ready"', 'status: "blocked"'
    )
    _write_card(tmp_path, "blocked", "AICC", "AICC-2.md", blocked_text)

    result = load_portfolio_tasks(tmp_path)
    assert result.missing is False
    ids = {t.task_id for t in result.tasks}
    assert ids == {"AICC-UI-001", "AICC-2"}
    assert len(result.ready_tasks()) == 1
    assert result.ready_tasks()[0].task_id == "AICC-UI-001"


def test_load_portfolio_tasks_ignores_gitkeep_and_non_markdown(tmp_path):
    lane_dir = tmp_path / "tasks" / "ready" / "AICC"
    lane_dir.mkdir(parents=True)
    (lane_dir / ".gitkeep").write_text("")
    result = load_portfolio_tasks(tmp_path)
    assert result.tasks == []
    assert result.card_issues == []


def test_load_portfolio_tasks_reports_corrupted_card_without_aborting_others(tmp_path):
    _write_card(tmp_path, "ready", "AICC", "good.md", VALID_CARD)
    _write_card(tmp_path, "ready", "AICC", "bad.md", "not frontmatter at all\n")

    result = load_portfolio_tasks(tmp_path)
    assert len(result.tasks) == 1
    assert result.tasks[0].task_id == "AICC-UI-001"
    assert len(result.card_issues) == 1
    assert "bad.md" in str(result.card_issues[0].source_path)


def test_load_portfolio_tasks_reports_missing_required_fields(tmp_path):
    text = VALID_CARD.replace('project: "AICC"', 'project: null')
    _write_card(tmp_path, "ready", "AICC", "missing.md", text)

    result = load_portfolio_tasks(tmp_path)
    assert result.tasks == []
    assert len(result.card_issues) == 1
    assert "project" in result.card_issues[0].message


def test_load_portfolio_tasks_reports_duplicate_task_id(tmp_path):
    _write_card(tmp_path, "ready", "AICC", "first.md", VALID_CARD)
    _write_card(tmp_path, "backlog", "AICC", "second.md", VALID_CARD)

    result = load_portfolio_tasks(tmp_path)
    assert len(result.tasks) == 1
    assert len(result.card_issues) == 1
    assert "duplicate" in result.card_issues[0].message


def test_load_portfolio_tasks_sorts_deterministically(tmp_path):
    for name in ["c.md", "a.md", "b.md"]:
        text = VALID_CARD.replace('task_id: "AICC-UI-001"', f'task_id: "AICC-{name}"')
        _write_card(tmp_path, "ready", "AICC", name, text)

    result = load_portfolio_tasks(tmp_path)
    ids = [t.task_id for t in result.tasks]
    assert ids == sorted(ids)


# --------------------------------------------------------------------------
# Dependency resolution
# --------------------------------------------------------------------------


def test_unmet_requirements_returns_deps_still_present_in_loaded_lanes(tmp_path):
    _write_card(tmp_path, "ready", "AICC", "dependent.md", VALID_CARD.replace(
        'task_id: "AICC-UI-001"', 'task_id: "AICC-DEP"'
    ).replace('requires: []', 'requires: ["AICC-BLOCKER"]'))
    _write_card(tmp_path, "blocked", "AICC", "blocker.md", VALID_CARD.replace(
        'task_id: "AICC-UI-001"', 'task_id: "AICC-BLOCKER"'
    ).replace('status: "ready"', 'status: "blocked"'))

    result = load_portfolio_tasks(tmp_path)
    tasks_by_id = result.tasks_by_id()
    dependent = tasks_by_id["AICC-DEP"]
    assert unmet_requirements(dependent, tasks_by_id) == ["AICC-BLOCKER"]


def test_unmet_requirements_treats_unknown_dependency_as_satisfied(tmp_path):
    _write_card(tmp_path, "ready", "AICC", "dependent.md", VALID_CARD.replace(
        'requires: []', 'requires: ["ALREADY-ARCHIVED"]'
    ))
    result = load_portfolio_tasks(tmp_path)
    tasks_by_id = result.tasks_by_id()
    task = tasks_by_id["AICC-UI-001"]
    assert unmet_requirements(task, tasks_by_id) == []
