"""Static panel registry -- the foundation piece of the Universal Workspace's
panel model.

See `docs/workspace/NAVIGATION_AND_PANEL_MODEL.md` (per-panel contracts) and
`docs/workspace/UNIVERSAL_WORKSPACE_ARCHITECTURE.md`'s "Proposed modules"
section for `panel_registry.py`'s documented responsibility: a static mapping
from panel key to metadata, not logic. This module holds no runtime state and
imports nothing beyond stdlib typing, per that document's "prohibited
responsibilities" for this module.

This is metadata only. `render` stays `None` for every panel until the shell
(`ui/workspace_shell.py`, out of scope until UW-2 per
`docs/workspace/IMPLEMENTATION_ROADMAP.md`) wires real Streamlit render
functions in -- registering a panel here changes no existing page's behavior.
`data_kind` exists so `workspace_service.panel_data` can resolve a panel's
data without either module needing to know about the other's internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

PanelRenderFn = Callable[..., None]

# What kind of selected id (from `WorkspaceContext`) a panel needs to resolve
# its own data via `workspace_service.panel_data`.
PanelDataKind = str  # "none" | "project" | "task" | "run"


@dataclass(frozen=True)
class PanelSpec:
    key: str
    label: str
    icon: str
    is_primary_nav: bool
    data_kind: PanelDataKind = "none"
    render: PanelRenderFn | None = None


def _spec(
    key: str,
    label: str,
    icon: str,
    is_primary_nav: bool,
    data_kind: PanelDataKind = "none",
) -> PanelSpec:
    return PanelSpec(key=key, label=label, icon=icon, is_primary_nav=is_primary_nav, data_kind=data_kind)


# One entry per panel contract in NAVIGATION_AND_PANEL_MODEL.md, plus
# "workspace_home" -- `WorkspaceContext.active_panel`'s documented default,
# which must resolve here or `workspace_context.reconcile`'s own panel-check
# step (WORKSPACE_CONTEXT_MODEL.md step 6) would immediately fail against its
# own default value.
_SPECS: tuple[PanelSpec, ...] = (
    _spec("workspace_home", "Workspace Home", ":material/home_work:", True, "none"),
    _spec("project_overview", "Projects", ":material/folder_open:", True, "project"),
    _spec("kanban", "Kanban", ":material/view_kanban:", True, "project"),
    _spec("task_detail", "Task Detail", ":material/task:", False, "task"),
    _spec("agents", "Agents", ":material/smart_toy:", True, "none"),
    _spec("git", "Git", ":material/commit:", True, "project"),
    _spec("documents", "Documents", ":material/description:", True, "project"),
    _spec("runtime", "Execution", ":material/bolt:", True, "run"),
    _spec("execution_queue", "Execution Queue", ":material/list_alt:", False, "project"),
    _spec("reports", "Reports", ":material/summarize:", True, "project"),
    _spec("chat", "AI Chat", ":material/forum:", True, "project"),
    _spec("inspector", "Inspector", ":material/info:", False, "none"),
    _spec("notification_center", "Notifications", ":material/notifications:", False, "none"),
)


def _build_registry(specs: tuple[PanelSpec, ...]) -> dict[str, PanelSpec]:
    registry: dict[str, PanelSpec] = {}
    for spec in specs:
        if spec.key in registry:
            raise ValueError(f"Duplicate panel key in panel_registry: {spec.key!r}")
        registry[spec.key] = spec
    return registry


PANELS: dict[str, PanelSpec] = _build_registry(_SPECS)


def get(key: str) -> PanelSpec | None:
    return PANELS.get(key)


def primary_nav_panels() -> list[PanelSpec]:
    return [spec for spec in PANELS.values() if spec.is_primary_nav]
