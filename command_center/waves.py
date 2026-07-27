"""Wave grouping: read a task's declared parallel group and present the tasks
as ordered, launch-in-one-click waves.

A *wave* is a set of tasks meant to be worked in parallel, declared on the task
itself. Founder-audit and roadmap packages already carry this — as
`metadata.parallel_group` (`BOOTSTRAP-W0`, `wave0-a`, …), and a bare `wave`
field is honored too — so this module reads that declaration rather than
inventing a second one. It never computes waves from the dependency graph:
that is the scheduler's job, and a declared wave is an operator's stated intent
about *ordering of work*, which is a different thing from *what may run right
now*.

The module is pure and Streamlit-free. It answers two questions:

- **What are the waves, in order, and how far along is each one?** —
  `build_waves`. Each `Wave` reports how many of its tasks are done, open, or
  already queued, so the UI can show "3/5 · 2 queued" without recomputing.
- **Which tasks in a wave can actually be enqueued?** — a task already `Done`,
  or already sitting in an open queue entry, is excluded, so pressing "enqueue
  this wave" twice is idempotent and never double-queues anything.

Enqueuing itself is deliberately *not* here: it goes through
`execution_queue.enqueue_and_persist`, the same locked path every other
enqueue uses. This module only decides the membership; the UI calls the
existing queue service for the side effect.
"""

from __future__ import annotations

from dataclasses import dataclass

from command_center import execution_queue, models, project_config

# The task fields consulted for a wave label, in precedence order. `metadata`
# is where `task_import` preserves package-specific provenance verbatim (see its
# module docstring), which is where `parallel_group` lands; a top-level `wave`
# is accepted too for hand-authored tasks.
_WAVE_KEYS: tuple[str, ...] = ("parallel_group", "wave")


def wave_label(task: dict) -> str | None:
    """The wave a task declares, or `None`. Reads `metadata.parallel_group`
    first (where imports put it), then `metadata.wave`, then a top-level
    `wave`/`parallel_group`. A blank or non-string value is treated as absent so
    a stray `""` never creates an empty phantom wave."""
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    for source in (metadata, task):
        for key in _WAVE_KEYS:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


@dataclass(frozen=True)
class WaveTask:
    """One task's place in a wave, decorated with the two facts the wave view
    needs and nothing more: whether it is finished, and whether it is already
    represented by an open queue entry (so re-enqueuing it would be a no-op)."""

    task_id: str
    title: str
    project: str | None
    status: str | None
    priority: str | None
    done: bool
    queued: bool

    @property
    def enqueueable(self) -> bool:
        """A task that is neither finished nor already queued — the set a
        wave-enqueue action actually acts on."""
        return not self.done and not self.queued


@dataclass(frozen=True)
class Wave:
    """One declared parallel group, with its membership and a progress summary.

    `label` is the group's own name (`BOOTSTRAP-W0`). `tasks` is every task
    carrying that label, ordered by priority (most urgent first) then task id so
    the display is stable. The counts are precomputed so the UI never has to
    re-scan."""

    label: str
    tasks: tuple[WaveTask, ...] = ()

    @property
    def total(self) -> int:
        return len(self.tasks)

    @property
    def done(self) -> int:
        return sum(1 for t in self.tasks if t.done)

    @property
    def queued(self) -> int:
        return sum(1 for t in self.tasks if t.queued)

    @property
    def enqueueable_ids(self) -> tuple[str, ...]:
        """Task ids a wave-enqueue action would add — everything not already
        done or queued. Empty when the whole wave is finished or in flight, in
        which case the enqueue button has nothing to do and can be disabled."""
        return tuple(t.task_id for t in self.tasks if t.enqueueable)

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.done == self.total


def _priority_rank(priority: str | None) -> int:
    """A sort key with the *most urgent* priority first (smallest value).

    `models.TASK_PRIORITIES` is least-urgent-first (`Low … Critical`), so a
    task's urgency is its index there; negating it makes Critical sort ahead of
    Low. An unknown or absent priority sorts after every known one (a large
    positive), never crashing on an unexpected value."""
    known = {name: index for index, name in enumerate(models.TASK_PRIORITIES)}
    if priority not in known:
        return len(models.TASK_PRIORITIES)
    return -known[priority]


def build_waves(
    tasks: list[dict],
    queue_entries: list[dict] | None = None,
    *,
    project: str | None = None,
) -> list[Wave]:
    """Group `tasks` into their declared waves, best-first within each wave and
    waves in label order.

    A task with no wave label is simply not part of any wave and is omitted —
    this surface is about the tasks that declared themselves parallel, not a
    second view of the whole backlog. When `project` is given, only that
    project's tasks are considered, matched through
    `project_config.project_matches` so a task storing a display name
    ("AI Command Center") lines up with its canonical id ("AICC").

    `queue_entries` is the current execution queue; a task with an open entry
    there is marked `queued` so the wave view and the enqueue action agree on
    what is already in flight."""
    open_task_ids = {
        entry.get("task_id")
        for entry in (queue_entries or [])
        if entry.get("state") in execution_queue.OPEN_STATES
    }

    grouped: dict[str, list[WaveTask]] = {}
    for task in tasks:
        task_id = task.get("id")
        if not task_id:
            continue
        if project is not None and not project_config.project_matches(task.get("project"), project):
            continue
        label = wave_label(task)
        if label is None:
            continue
        grouped.setdefault(label, []).append(
            WaveTask(
                task_id=task_id,
                title=task.get("title") or task_id,
                project=task.get("project"),
                status=task.get("status"),
                priority=task.get("priority"),
                done=task.get("status") == "Done",
                queued=task_id in open_task_ids,
            )
        )

    waves: list[Wave] = []
    for label in sorted(grouped):
        members = sorted(grouped[label], key=lambda t: (_priority_rank(t.priority), t.task_id))
        waves.append(Wave(label=label, tasks=tuple(members)))
    return waves
