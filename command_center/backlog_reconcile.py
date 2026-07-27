"""Backlog reconciliation — find open tasks that are almost certainly already
done, so the operator never launches the same work twice.

Three signals, cheapest and most certain first, computed over the plain task
list (pure, no I/O, no Streamlit — the UI renders these findings and the deep
agent check consumes the same function):

1. **self_completed** — the task's *own* latest run already finished successfully
   (`launch_status == "Completed"`), but the card still sits in an open lane.
   The work is done; the card just was not moved. Highest certainty.
2. **duplicate_of_done** — the task's title/goal closely matches a task already
   in the `Done` lane. Almost certainly the same work under a second card.
3. **duplicate_open** — two open tasks are near-identical; one is redundant.
   Reported once, on the newer of the pair, so the older (usually the one with
   history) is kept.

Similarity is a normalized `difflib` ratio over `title` + `goal`. It is a
*suggestion* signal, never an automatic action: every finding drives a proposal
the operator confirms, exactly as the user asked ("если задача уже реализована,
он должен просто предложить её удалить"). The deterministic matcher here is the
fast first pass; the deep "compare against the guide and the dev branch" check is
an agent task that reads the same findings and confirms or overrides them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

DONE_STATUS = "Done"
COMPLETED_LAUNCH_STATUS = "Completed"

# Kinds, ordered by how safe the proposal is.
KIND_SELF_COMPLETED = "self_completed"
KIND_DUPLICATE_OF_DONE = "duplicate_of_done"
KIND_DUPLICATE_OPEN = "duplicate_open"

# Above this normalized-title+goal ratio two tasks are treated as the same work.
# Deliberately high: a false "already done" that deletes real remaining work is
# far worse than missing a fuzzy duplicate, so the matcher errs toward silence.
DEFAULT_SIMILARITY_THRESHOLD = 0.82

_WORD_RE = re.compile(r"[^\wа-яё]+", re.IGNORECASE)


@dataclass(frozen=True)
class ReconcileFinding:
    """One open task that is probably already done, with why and what to do."""

    task_id: str
    title: str
    kind: str
    reason: str
    suggested_action: str            # "delete" | "mark_done"
    match_task_id: str | None = None
    match_title: str | None = None
    confidence: float = 1.0

    @property
    def is_delete(self) -> bool:
        return self.suggested_action == "delete"


def _normalize(text: str | None) -> str:
    """Lowercased, punctuation-collapsed key for similarity — so "Audit Task
    Model" and "audit task-model!" compare equal."""
    return _WORD_RE.sub(" ", (text or "").lower()).strip()


def _signature(task: dict) -> str:
    return f"{_normalize(task.get('title'))} {_normalize(task.get('goal'))}".strip()


def similarity(a: dict, b: dict) -> float:
    """Normalized [0,1] title+goal similarity between two tasks."""
    sig_a, sig_b = _signature(a), _signature(b)
    if not sig_a or not sig_b:
        return 0.0
    return SequenceMatcher(None, sig_a, sig_b).ratio()


def _started_at_key(task: dict) -> str:
    return task.get("created_at") or task.get("updated_at") or ""


def find_reconcilable(
    tasks: list[dict], *, threshold: float = DEFAULT_SIMILARITY_THRESHOLD
) -> list[ReconcileFinding]:
    """Every open task that is probably already done, most-certain first.

    An open task is one whose Kanban `status` is not `Done`. Each task yields at
    most one finding (the first, most-certain signal that fires), so the same
    card is never proposed for deletion twice.
    """
    done = [t for t in tasks if (t.get("status") or "") == DONE_STATUS and t.get("id")]
    open_tasks = [t for t in tasks if (t.get("status") or "") != DONE_STATUS and t.get("id")]

    findings: list[ReconcileFinding] = []
    # Track open tasks already claimed as the *redundant* half of an open pair,
    # so the kept task in the pair is not itself also flagged against the one we
    # just proposed deleting.
    claimed: set[str] = set()

    for task in open_tasks:
        task_id = task["id"]
        if task_id in claimed:
            continue
        title = task.get("title") or task_id

        # 1) self_completed — its own run already succeeded.
        if (task.get("launch_status") or "") == COMPLETED_LAUNCH_STATUS:
            findings.append(
                ReconcileFinding(
                    task_id=task_id,
                    title=title,
                    kind=KIND_SELF_COMPLETED,
                    reason="Прогон этой задачи уже завершился успешно (launch=Completed), "
                    "но карточка осталась в открытой колонке.",
                    suggested_action="mark_done",
                    confidence=1.0,
                )
            )
            continue

        # 2) duplicate_of_done — matches a task already in Done.
        best_done, best_done_sim = _best_match(task, done)
        if best_done is not None and best_done_sim >= threshold:
            findings.append(
                ReconcileFinding(
                    task_id=task_id,
                    title=title,
                    kind=KIND_DUPLICATE_OF_DONE,
                    reason=f"Совпадает с уже завершённой задачей «{(best_done.get('title') or '')[:60]}» "
                    f"({int(best_done_sim * 100)}% сходства) — вероятно, уже реализована.",
                    suggested_action="delete",
                    match_task_id=best_done.get("id"),
                    match_title=best_done.get("title"),
                    confidence=best_done_sim,
                )
            )
            continue

        # 3) duplicate_open — matches another still-open task; keep the older.
        others = [o for o in open_tasks if o["id"] != task_id and o["id"] not in claimed]
        best_open, best_open_sim = _best_match(task, others)
        if best_open is not None and best_open_sim >= threshold:
            # Delete the *newer* of the two (keep the one with more history).
            if _started_at_key(task) >= _started_at_key(best_open):
                redundant, kept = task, best_open
            else:
                redundant, kept = best_open, task
            claimed.add(redundant["id"])
            findings.append(
                ReconcileFinding(
                    task_id=redundant["id"],
                    title=redundant.get("title") or redundant["id"],
                    kind=KIND_DUPLICATE_OPEN,
                    reason=f"Дублирует другую открытую задачу «{(kept.get('title') or '')[:60]}» "
                    f"({int(best_open_sim * 100)}% сходства) — оставить одну.",
                    suggested_action="delete",
                    match_task_id=kept.get("id"),
                    match_title=kept.get("title"),
                    confidence=best_open_sim,
                )
            )

    return findings


def _best_match(task: dict, candidates: list[dict]) -> tuple[dict | None, float]:
    """The candidate most similar to `task`, and that similarity."""
    best: dict | None = None
    best_sim = 0.0
    for candidate in candidates:
        sim = similarity(task, candidate)
        if sim > best_sim:
            best, best_sim = candidate, sim
    return best, best_sim
