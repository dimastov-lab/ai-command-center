from pathlib import Path

import pytest

from command_center.portfolio_models import (
    PortfolioCardError,
    _parse_scalar,
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
# Fail-closed validation — nested mappings, block lists, block scalars,
# unterminated values, and duplicate keys must all be rejected explicitly
# rather than silently flattened/truncated/misread (Founder Gate Major 2).
# --------------------------------------------------------------------------


def test_parse_card_raises_on_nested_mapping_in_known_field(tmp_path):
    text = VALID_CARD.replace(
        'title: "Do the thing"',
        'title:\n  summary: "Do the thing"\n  detail: "more"',
    )
    path = _write_card(tmp_path, "ready", "AICC", "nested-known.md", text)
    with pytest.raises(PortfolioCardError, match="title"):
        parse_card(path, lane="ready")


def test_parse_card_raises_on_nested_mapping_in_additional_field(tmp_path):
    text = VALID_CARD.replace(
        "confidence: null",
        'confidence: null\nmetadata:\n  owner: "someone"\n  reviewed: true',
    )
    path = _write_card(tmp_path, "ready", "AICC", "nested-extra.md", text)
    with pytest.raises(PortfolioCardError, match="metadata"):
        parse_card(path, lane="ready")


def test_parse_card_raises_on_inline_flow_mapping(tmp_path):
    """A single-line `{...}` nested mapping is a different YAML spelling of
    the same unsupported feature — must fail closed exactly like the
    block-indented form above, not be silently kept as an opaque string."""
    text = VALID_CARD.replace("confidence: null", 'confidence: {"level": "high"}')
    path = _write_card(tmp_path, "ready", "AICC", "inline-map.md", text)
    with pytest.raises(PortfolioCardError, match="confidence"):
        parse_card(path, lane="ready")


def test_parse_card_raises_on_block_style_list_indented(tmp_path):
    text = VALID_CARD.replace(
        'deliverables: ["app.py change committed"]',
        'deliverables:\n  - "app.py change committed"\n  - "more"',
    )
    path = _write_card(tmp_path, "ready", "AICC", "block-list-indented.md", text)
    with pytest.raises(PortfolioCardError, match="deliverables"):
        parse_card(path, lane="ready")


def test_parse_card_raises_on_block_style_list_at_top_level(tmp_path):
    """A block list whose items sit at column 0 (a valid YAML spelling, not
    indented under the key) bypasses the indentation check and must be
    caught by the leading `"- "` check instead."""
    text = VALID_CARD.replace(
        'deliverables: ["app.py change committed"]',
        'deliverables:\n- "app.py change committed"',
    )
    path = _write_card(tmp_path, "ready", "AICC", "block-list-top.md", text)
    with pytest.raises(PortfolioCardError, match="deliverables"):
        parse_card(path, lane="ready")


def test_parse_card_accepts_null_boolean_and_numeric_scalars(tmp_path):
    """Regression guard: the stricter validation above must not start
    rejecting the flat scalar types the format has always supported."""
    text = VALID_CARD.replace(
        "confidence: null",
        "confidence: null\nreviewed: true\nrejected: false\nattempt: 3\nscore: 4.5",
    )
    path = _write_card(tmp_path, "ready", "AICC", "scalars.md", text)
    task = parse_card(path, lane="ready")
    assert task.frontmatter["confidence"] is None
    assert task.frontmatter["reviewed"] is True
    assert task.frontmatter["rejected"] is False
    assert task.frontmatter["attempt"] == 3
    assert task.frontmatter["score"] == 4.5


def test_parse_card_raises_on_block_scalar_marker(tmp_path):
    text = VALID_CARD.replace('title: "Do the thing"', 'title: |\n  Do the thing\n  across lines')
    path = _write_card(tmp_path, "ready", "AICC", "block-scalar.md", text)
    with pytest.raises(PortfolioCardError, match="title"):
        parse_card(path, lane="ready")


def test_parse_card_raises_on_multiline_unterminated_quoted_string(tmp_path):
    text = VALID_CARD.replace(
        'title: "Do the thing"',
        'title: "Do the thing\nacross two physical lines"',
    )
    path = _write_card(tmp_path, "ready", "AICC", "multiline.md", text)
    with pytest.raises(PortfolioCardError, match="title"):
        parse_card(path, lane="ready")


def test_parse_card_raises_on_unterminated_flow_list(tmp_path):
    """Syntactically corrupted YAML (a flow list missing its closing
    bracket) must fail closed rather than being misread."""
    text = VALID_CARD.replace('blocks: ["AICC-HYG-001"]', 'blocks: ["AICC-HYG-001"')
    path = _write_card(tmp_path, "ready", "AICC", "unterminated-list.md", text)
    with pytest.raises(PortfolioCardError, match="blocks"):
        parse_card(path, lane="ready")


def test_parse_card_raises_on_duplicate_frontmatter_key(tmp_path):
    text = VALID_CARD.replace('status: "ready"', 'status: "ready"\nstatus: "blocked"')
    path = _write_card(tmp_path, "ready", "AICC", "dup-key.md", text)
    with pytest.raises(PortfolioCardError, match="status"):
        parse_card(path, lane="ready")


def test_parse_card_raises_accurate_message_for_top_level_orphan_list_item(tmp_path):
    """`- orphan item` straight after a key that already has its own value
    (`status: "ready"`) is not nested under that key — it's an unrelated
    top-level line. The error must say so plainly instead of claiming a
    nesting relationship that doesn't exist (Founder Gate re-review Minor 1)."""
    text = VALID_CARD.replace('status: "ready"', 'status: "ready"\n- orphan item')
    path = _write_card(tmp_path, "ready", "AICC", "orphan-top-level.md", text)
    with pytest.raises(PortfolioCardError) as exc_info:
        parse_card(path, lane="ready")
    message = str(exc_info.value)
    assert "top-level" in message
    assert "nested under" not in message


def test_parse_card_block_list_after_empty_key_still_reports_nesting(tmp_path):
    """Regression guard for the fix above: when the preceding key genuinely
    has no value (`deliverables:` with nothing after the colon), a following
    `- item` line is still plausibly that key's value, so the message should
    keep naming it as nested under that key."""
    text = VALID_CARD.replace(
        'deliverables: ["app.py change committed"]',
        'deliverables:\n- "app.py change committed"',
    )
    path = _write_card(tmp_path, "ready", "AICC", "block-list-owned.md", text)
    with pytest.raises(PortfolioCardError, match="nested under 'deliverables'"):
        parse_card(path, lane="ready")


# --------------------------------------------------------------------------
# Flow-list contract — only single-/double-quoted items are supported. An
# unquoted, non-empty item must fail closed rather than being silently
# dropped, since `_QUOTED_ITEM_RE.findall` used to extract only the quoted
# items and silently discard the rest (Founder Gate re-review Blocker 1):
#   _parse_scalar('[a, "b"]', ...) used to wrongly return ["b"]
#   _parse_scalar("[1, 2]", ...) used to wrongly return []
#   _parse_scalar("[true, false]", ...) used to wrongly return []
# --------------------------------------------------------------------------


def test_parse_scalar_direct_repro_rejects_mixed_quoted_and_bare_item():
    """Exact repro from the Founder Gate re-review finding."""
    with pytest.raises(PortfolioCardError):
        _parse_scalar('[a, "b"]', field_name="deliverables", source_path=Path("x.md"))


@pytest.mark.parametrize(
    "flow_list",
    [
        '[a, "b"]',
        '["a", b]',
        "[a]",
        "[1, 2]",
        "[true, false]",
        "[null]",
        '["a", 2, "b"]',
    ],
)
def test_parse_card_rejects_unquoted_flow_list_items(tmp_path, flow_list):
    text = VALID_CARD.replace(
        'deliverables: ["app.py change committed"]', f"deliverables: {flow_list}"
    )
    path = _write_card(tmp_path, "ready", "AICC", "unquoted-item.md", text)
    with pytest.raises(PortfolioCardError, match="deliverables"):
        parse_card(path, lane="ready")


def test_parse_card_rejects_unquoted_flow_list_item_without_partial_task(tmp_path):
    """A mixed list must never yield a partially-parsed list or task — the
    whole card fails, nothing downstream sees a truncated `deliverables`."""
    text = VALID_CARD.replace(
        'deliverables: ["app.py change committed"]', 'deliverables: ["a", 2, "b"]'
    )
    path = _write_card(tmp_path, "ready", "AICC", "mixed-list.md", text)
    result = None
    try:
        result = parse_card(path, lane="ready")
    except PortfolioCardError:
        pass
    assert result is None


@pytest.mark.parametrize(
    "flow_list,expected",
    [
        ("[]", []),
        ('["a"]', ["a"]),
        ('["a", "b"]', ["a", "b"]),
        ("['a', 'b']", ["a", "b"]),
        ('["app.py", "tests/test_x.py"]', ["app.py", "tests/test_x.py"]),
        ('["value, with comma", "other"]', ["value, with comma", "other"]),
    ],
)
def test_parse_card_accepts_valid_quoted_flow_lists(tmp_path, flow_list, expected):
    text = VALID_CARD.replace(
        'deliverables: ["app.py change committed"]', f"deliverables: {flow_list}"
    )
    path = _write_card(tmp_path, "ready", "AICC", "valid-list.md", text)
    task = parse_card(path, lane="ready")
    assert task.deliverables == expected


def test_parse_card_failure_never_yields_a_partial_task(tmp_path):
    """A field-level parse failure must abort before any `PortfolioTask` is
    constructed — never a partially-populated record with some fields set
    from before the bad line and others missing."""
    text = VALID_CARD.replace('title: "Do the thing"', 'title:\n  nested: "x"')
    path = _write_card(tmp_path, "ready", "AICC", "partial.md", text)
    result = None
    try:
        result = parse_card(path, lane="ready")
    except PortfolioCardError:
        pass
    assert result is None


def test_parse_card_valid_existing_card_still_parses_unchanged(tmp_path):
    """Regression: the exact fixture every other test in this module relies
    on must still parse identically after the stricter validation above."""
    path = _write_card(tmp_path, "ready", "AICC", "still-valid.md", VALID_CARD)
    task = parse_card(path, lane="ready")
    assert task.task_id == "AICC-UI-001"
    assert task.title == "Do the thing"
    assert task.missing_required_fields() == []
    assert task.blocks == ["AICC-HYG-001"]


def test_parse_card_unquoted_requires_item_raises_instead_of_silently_dropping_dependency(tmp_path):
    """Founder Gate re-review Blocker 1's concrete danger scenario: before the
    fix, `requires: [BLOCKING-TASK-1, BLOCKING-TASK-2]` (unquoted — a
    plausible authoring mistake, not just a contrived adversarial input)
    silently parsed to `task.requires == []`, which `unmet_requirements`
    would then treat as "no dependencies" — silently bypassing the
    dependency gate `build_launch_plan` exists to enforce. This must now
    raise instead of ever producing that empty list."""
    text = VALID_CARD.replace("requires: []", "requires: [BLOCKING-TASK-1, BLOCKING-TASK-2]")
    path = _write_card(tmp_path, "ready", "AICC", "unquoted-requires.md", text)
    with pytest.raises(PortfolioCardError, match="requires"):
        parse_card(path, lane="ready")


# --------------------------------------------------------------------------
# `task_id` path-safety contract (Founder Gate re-review Blocker 1) — a
# `task_id` becomes a filesystem path component in
# `command_center.portfolio_launch` (branch name, worktree directory name,
# per-task lock filename), so it must never contain a path separator, `.`/
# `..`, whitespace, or any other character that could escape the sandboxed
# roots it's joined onto there.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_task_id",
    [
        "../escaped_dir/evil",
        "../../OUTSIDE_LOCK_ESCAPE",
        "foo/bar",
        "foo\\bar",
        ".",
        "..",
        " leading-space",
        "trailing-space ",
        "foo bar",
        "/absolute/path",
        "",
    ],
)
def test_parse_card_rejects_unsafe_task_id(tmp_path, bad_task_id):
    text = VALID_CARD.replace('task_id: "AICC-UI-001"', f'task_id: "{bad_task_id}"')
    path = _write_card(tmp_path, "ready", "AICC", "unsafe-task-id.md", text)
    with pytest.raises(PortfolioCardError, match="task_id"):
        parse_card(path, lane="ready")


@pytest.mark.parametrize(
    "good_task_id",
    ["TASK-001", "task_001", "task-001", "A1", "portfolio_task_7"],
)
def test_parse_card_accepts_safe_task_id(tmp_path, good_task_id):
    text = VALID_CARD.replace('task_id: "AICC-UI-001"', f'task_id: "{good_task_id}"')
    path = _write_card(tmp_path, "ready", "AICC", "safe-task-id.md", text)
    task = parse_card(path, lane="ready")
    assert task.task_id == good_task_id


def test_parse_card_unsafe_task_id_never_yields_a_partial_task(tmp_path):
    text = VALID_CARD.replace('task_id: "AICC-UI-001"', 'task_id: "../escaped"')
    path = _write_card(tmp_path, "ready", "AICC", "unsafe-task-id-partial.md", text)
    result = None
    try:
        result = parse_card(path, lane="ready")
    except PortfolioCardError:
        pass
    assert result is None


def test_validate_task_id_rejects_non_string(tmp_path):
    """Defense-in-depth: `task_id: 123` (an integer, not a quoted string) —
    `_parse_scalar` would happily coerce it — must still be rejected by
    `validate_task_id` rather than being accepted as a non-string value that
    downstream f-string path construction would silently stringify."""
    from command_center.portfolio_models import validate_task_id

    with pytest.raises(PortfolioCardError):
        validate_task_id(123)  # type: ignore[arg-type]


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
