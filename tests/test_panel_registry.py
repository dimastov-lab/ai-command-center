"""Tests for `command_center/panel_registry.py` -- the static panel metadata
registry (see docs/workspace/NAVIGATION_AND_PANEL_MODEL.md's panel contracts
and IMPLEMENTATION_ROADMAP.md's UW-1 exit criteria: "every panel ... has a
corresponding registry entry; no duplicate keys").

Plain pytest, no Streamlit anywhere -- this module has no Streamlit import.
"""

from __future__ import annotations

from command_center import panel_registry


def test_registry_builds_without_duplicate_keys():
    assert len(panel_registry.PANELS) == len(panel_registry._SPECS)


def test_workspace_home_is_registered():
    """`WorkspaceContext.active_panel` defaults to `"workspace_home"` --
    reconciliation's panel-validity check (WORKSPACE_CONTEXT_MODEL.md step 6)
    would fail against its own default if this key were missing."""
    assert "workspace_home" in panel_registry.PANELS


def test_every_panel_contract_has_a_registry_entry():
    expected_keys = {
        "workspace_home",
        "project_overview",
        "kanban",
        "task_detail",
        "agents",
        "git",
        "documents",
        "runtime",
        "execution_queue",
        "reports",
        "chat",
        "inspector",
        "notification_center",
    }
    assert expected_keys == set(panel_registry.PANELS)


def test_every_spec_has_well_formed_metadata():
    valid_data_kinds = {"none", "project", "task", "run"}
    for key, spec in panel_registry.PANELS.items():
        assert spec.key == key
        assert isinstance(spec.label, str) and spec.label
        assert isinstance(spec.icon, str) and spec.icon
        assert isinstance(spec.is_primary_nav, bool)
        assert spec.data_kind in valid_data_kinds
        # Foundation stage only -- no shell exists yet to wire real renderers.
        assert spec.render is None


def test_primary_nav_panels_matches_documented_primary_destinations():
    primary_keys = {spec.key for spec in panel_registry.primary_nav_panels()}
    assert primary_keys == {
        "workspace_home",
        "project_overview",
        "kanban",
        "agents",
        "git",
        "documents",
        "runtime",
        "reports",
        "chat",
    }


def test_get_returns_none_for_unknown_key():
    assert panel_registry.get("does_not_exist") is None


def test_duplicate_key_raises_at_build_time():
    duplicate_specs = (
        panel_registry._spec("dup", "A", ":material/a:", True),
        panel_registry._spec("dup", "B", ":material/b:", False),
    )
    try:
        panel_registry._build_registry(duplicate_specs)
    except ValueError as exc:
        assert "dup" in str(exc)
    else:
        raise AssertionError("expected ValueError for duplicate panel key")
