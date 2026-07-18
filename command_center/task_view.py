"""Pure read-model / bookkeeping helpers for rendering a task card.

Kept separate from `app.py` — and with zero `st.*` calls — so a future
non-Streamlit UI (the desktop shell documented under `docs/desktop/`) can
reuse the exact same task-card data logic through its own
`command_center.application` adapter layer, per `docs/desktop/
ARCHITECTURE.md` §5/§7 ("adapters call existing functions verbatim... no
forked copies"). `app.py`'s `render_task_card` calls into this module and
only handles widget rendering.
"""

from __future__ import annotations

from pathlib import Path

from command_center import git_info, models


def cached_git_status(workspace_path: str | None, cache: dict[str, dict]) -> dict:
    """Memoized git status lookup — one subprocess call per unique
    workspace path per render pass, however many tasks share that repo."""
    if not workspace_path:
        return {"is_repo": False}
    if workspace_path not in cache:
        cache[workspace_path] = git_info.get_status(Path(workspace_path))
    return cache[workspace_path]


def set_manual_launch_status(task: dict, status: str, note: str) -> None:
    """Pause/Resume/Restart bookkeeping — advisory status only. See
    `command_center.launch`'s module docstring for why this can't be real
    process control for the synchronous v1.1 runner."""
    task["launch_status"] = status
    task["updated_at"] = models.iso_now()
    event_type = "launch_requires_attention" if status == "Failed" else "executor_started"
    models.append_timeline_event(task, event_type, note)


def sorted_timeline(task: dict) -> list[dict]:
    """Newest-first timeline events — plain data, no formatting/markup."""
    return sorted(task.get("timeline") or [], key=lambda event: event.get("ts", ""), reverse=True)


def dependency_graph_dot(task: dict, tasks_by_id: dict[str, dict]) -> str | None:
    """Builds Graphviz DOT source for a task's dependency neighborhood
    (depends_on / blocks / parent / children). Returns `None` when there is
    nothing to draw. Rendering (`st.graphviz_chart`) stays in `app.py` —
    this function returns plain text, not a UI element, so it's reusable
    from a non-Streamlit renderer too."""
    edges = models.derive_dependency_edges(task, list(tasks_by_id.values()))
    depends_on = task.get("depends_on") or []
    parent_id = task.get("parent_task_id")
    if not (depends_on or edges["blocks"] or edges["children"] or parent_id):
        return None

    def node_label(task_id: str) -> str:
        other = tasks_by_id.get(task_id)
        label = (other.get("title") if other else None) or task_id[:8]
        return label.replace('"', "'")[:40]

    lines = ["digraph {", "rankdir=LR;", 'node [shape=box, style="rounded,filled", fillcolor="#eef2ff"];']
    lines.append(f'"{task["id"]}" [label="{node_label(task["id"])}", fillcolor="#c7d2fe"];')
    for dep_id in depends_on:
        lines.append(f'"{dep_id}" [label="{node_label(dep_id)}"];')
        lines.append(f'"{dep_id}" -> "{task["id"]}";')
    for blocked_id in edges["blocks"]:
        lines.append(f'"{blocked_id}" [label="{node_label(blocked_id)}"];')
        lines.append(f'"{task["id"]}" -> "{blocked_id}";')
    if parent_id:
        lines.append(f'"{parent_id}" [label="{node_label(parent_id)}", fillcolor="#fef3c7"];')
        lines.append(f'"{parent_id}" -> "{task["id"]}" [style=dashed];')
    for child_id in edges["children"]:
        lines.append(f'"{child_id}" [label="{node_label(child_id)}", fillcolor="#fef3c7"];')
        lines.append(f'"{task["id"]}" -> "{child_id}" [style=dashed];')
    lines.append("}")
    return "\n".join(lines)
