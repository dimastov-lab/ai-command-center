"""Parsing and in-memory modeling of Portfolio task cards.

Portfolio (a separate repository, `~/Projects/Portfolio` by default) tracks
cross-project engineering work as one Markdown file per task, YAML-frontmatter
+ body, under `tasks/<lane>/<PROJECT>/<TASK-ID>.md` for `lane` in
`PORTFOLIO_LANES`. This module only reads that tree and turns it into plain,
immutable `PortfolioTask` records — no Streamlit, no execution, no git
mutation, no dependency on `command_center.execution_queue` or
`command_center.launch`. `command_center.portfolio_launch` is the only module
that turns a `PortfolioTask` into a real launch.

The frontmatter parser below is deliberately not a general YAML parser: every
card observed in Portfolio uses a flat, single-line, flow-style subset (quoted
string scalars, `null`, and `[...]` lists of quoted strings — never nested
maps, block scalars, or multi-line values), and this project takes no new
third-party dependency (only `streamlit` is declared) to parse it. A card
using a YAML feature outside that subset fails to parse with a clear
`PortfolioCardError` naming the offending line, rather than being silently
misread.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PORTFOLIO_LANES: tuple[str, ...] = ("ready", "review", "blocked", "backlog")

# Only lane whose tasks may ever be offered for automatic launch — see
# `command_center.portfolio_launch.build_launch_plan`. Cards found in every
# other lane are still parsed and included in the dependency graph (a
# `requires` reference needs to resolve against them), just never launchable.
LAUNCHABLE_LANE = "ready"

REQUIRED_FIELDS: tuple[str, ...] = (
    "task_id",
    "project",
    "title",
    "repository",
    "base_branch",
    "status",
)

_LIST_FIELDS: tuple[str, ...] = (
    "requires",
    "blocks",
    "conflicts_with",
    "deliverables",
    "validation",
    "stop_conditions",
    "evidence",
    "gated_by",
)


class PortfolioCardError(Exception):
    """Raised for a card that cannot be safely parsed — missing frontmatter
    delimiters, an unterminated block, a malformed line, or a YAML feature
    outside the supported flat flow-style subset."""


@dataclass(frozen=True)
class CardIssue:
    """One card that failed to load cleanly — either a parse error or a
    required-field/duplicate-id problem caught after parsing. Surfaced by the
    loader instead of raising, so one broken card never hides every other
    valid one."""

    source_path: Path
    message: str


@dataclass(frozen=True)
class PortfolioTask:
    lane: str
    source_path: Path
    frontmatter: dict[str, Any]
    body: str
    raw_text: str

    @property
    def task_id(self) -> str | None:
        return self.frontmatter.get("task_id")

    @property
    def project(self) -> str | None:
        return self.frontmatter.get("project")

    @property
    def title(self) -> str | None:
        return self.frontmatter.get("title")

    @property
    def type(self) -> str | None:
        return self.frontmatter.get("type")

    @property
    def capability(self) -> str | None:
        return self.frontmatter.get("capability")

    @property
    def priority(self) -> str | None:
        return self.frontmatter.get("priority")

    @property
    def status(self) -> str | None:
        return self.frontmatter.get("status")

    @property
    def repository(self) -> str | None:
        return self.frontmatter.get("repository")

    @property
    def base_branch(self) -> str | None:
        return self.frontmatter.get("base_branch")

    @property
    def branch(self) -> str | None:
        return self.frontmatter.get("branch")

    @property
    def worktree(self) -> str | None:
        return self.frontmatter.get("worktree")

    @property
    def agent(self) -> str | None:
        return self.frontmatter.get("agent")

    @property
    def autonomy(self) -> str | None:
        return self.frontmatter.get("autonomy")

    @property
    def parallel_group(self) -> str | None:
        return self.frontmatter.get("parallel_group")

    @property
    def requires(self) -> list[str]:
        return list(self.frontmatter.get("requires") or [])

    @property
    def blocks(self) -> list[str]:
        return list(self.frontmatter.get("blocks") or [])

    @property
    def conflicts_with(self) -> list[str]:
        return list(self.frontmatter.get("conflicts_with") or [])

    @property
    def deliverables(self) -> list[str]:
        return list(self.frontmatter.get("deliverables") or [])

    @property
    def validation(self) -> list[str]:
        return list(self.frontmatter.get("validation") or [])

    @property
    def stop_conditions(self) -> list[str]:
        return list(self.frontmatter.get("stop_conditions") or [])

    @property
    def evidence(self) -> list[str]:
        return list(self.frontmatter.get("evidence") or [])

    @property
    def confidence(self) -> str | None:
        return self.frontmatter.get("confidence")

    @property
    def gated_by(self) -> list[str]:
        return list(self.frontmatter.get("gated_by") or [])

    def missing_required_fields(self) -> list[str]:
        return [name for name in REQUIRED_FIELDS if not self.frontmatter.get(name)]


@dataclass
class PortfolioLoadResult:
    portfolio_root: Path
    missing: bool
    tasks: list[PortfolioTask] = field(default_factory=list)
    card_issues: list[CardIssue] = field(default_factory=list)

    def tasks_by_id(self) -> dict[str, PortfolioTask]:
        return {task.task_id: task for task in self.tasks if task.task_id}

    def ready_tasks(self) -> list[PortfolioTask]:
        return [task for task in self.tasks if task.lane == LAUNCHABLE_LANE]


# --------------------------------------------------------------------------
# Frontmatter parsing (flat flow-style YAML subset only — see module docstring)
# --------------------------------------------------------------------------

_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d+\.\d+$")
_QUOTED_ITEM_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw == "null" or raw == "":
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        return raw[1:-1].replace('\\"', '"')
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [match.replace('\\"', '"') for match in _QUOTED_ITEM_RE.findall(inner)]
    if _INT_RE.match(raw):
        return int(raw)
    if _FLOAT_RE.match(raw):
        return float(raw)
    return raw


def _parse_frontmatter_block(lines: list[str], *, source_path: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for raw_line in lines:
        if not raw_line.strip():
            continue
        if ":" not in raw_line:
            raise PortfolioCardError(
                f"{source_path}: malformed frontmatter line (no ':'): {raw_line!r}"
            )
        key, _, value = raw_line.partition(":")
        key = key.strip()
        if not key:
            raise PortfolioCardError(f"{source_path}: empty frontmatter key in line {raw_line!r}")
        data[key] = _parse_scalar(value)
    return data


def _split_frontmatter(text: str, *, source_path: Path) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise PortfolioCardError(f"{source_path}: missing leading '---' frontmatter delimiter")

    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index is None:
        raise PortfolioCardError(f"{source_path}: unterminated frontmatter block (no closing '---')")

    frontmatter = _parse_frontmatter_block(lines[1:end_index], source_path=source_path)
    body = "\n".join(lines[end_index + 1 :]).lstrip("\n")
    return frontmatter, body


def parse_card(path: Path, *, lane: str) -> PortfolioTask:
    """Parse a single card. Raises `PortfolioCardError` for anything that
    cannot be safely read — never returns a partially-parsed record."""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PortfolioCardError(f"{path}: could not read file: {exc}") from exc

    frontmatter, body = _split_frontmatter(raw_text, source_path=path)
    for list_field in _LIST_FIELDS:
        if list_field in frontmatter and not isinstance(frontmatter[list_field], list):
            raise PortfolioCardError(
                f"{path}: field {list_field!r} must be a flow-style list, got {frontmatter[list_field]!r}"
            )
    return PortfolioTask(lane=lane, source_path=path, frontmatter=frontmatter, body=body, raw_text=raw_text)


def load_portfolio_tasks(portfolio_root: Path) -> PortfolioLoadResult:
    """Load every card under `portfolio_root/tasks/{ready,review,blocked,backlog}`.

    Never raises: a missing Portfolio checkout, a malformed card, a card
    missing a required field, and a duplicate `task_id` are all reported via
    `PortfolioLoadResult.card_issues` rather than aborting the whole load —
    one bad card must never hide every other valid one.
    """
    portfolio_root = Path(portfolio_root).expanduser()
    if not portfolio_root.is_dir():
        return PortfolioLoadResult(portfolio_root=portfolio_root, missing=True)

    tasks: list[PortfolioTask] = []
    card_issues: list[CardIssue] = []
    seen_ids: dict[str, Path] = {}

    for lane in PORTFOLIO_LANES:
        lane_dir = portfolio_root / "tasks" / lane
        if not lane_dir.is_dir():
            continue
        for path in sorted(lane_dir.rglob("*.md")):
            try:
                task = parse_card(path, lane=lane)
            except PortfolioCardError as exc:
                card_issues.append(CardIssue(source_path=path, message=str(exc)))
                continue

            missing = task.missing_required_fields()
            if missing:
                card_issues.append(
                    CardIssue(
                        source_path=path,
                        message=f"missing required field(s): {', '.join(missing)}",
                    )
                )
                continue

            task_id = task.task_id
            if task_id is None:
                # `missing_required_fields` already checked `task_id` above
                # (it's in `REQUIRED_FIELDS`) — unreachable in practice, kept
                # only to narrow the type for the `seen_ids` indexing below.
                continue
            if task_id in seen_ids:
                card_issues.append(
                    CardIssue(
                        source_path=path,
                        message=f"duplicate task_id {task_id!r} (already loaded from {seen_ids[task_id]})",
                    )
                )
                continue

            seen_ids[task_id] = path
            tasks.append(task)

    return PortfolioLoadResult(portfolio_root=portfolio_root, missing=False, tasks=tasks, card_issues=card_issues)


def unmet_requirements(task: PortfolioTask, tasks_by_id: dict[str, PortfolioTask]) -> list[str]:
    """`requires` entries still present among the loaded (non-terminal)
    lanes — i.e. still open somewhere in ready/review/blocked/backlog. A
    `requires` id absent from `tasks_by_id` is assumed already completed:
    this module never loads `tasks/done` or `tasks/archive`, so "not found"
    is the only signal available for "already finished" and is treated as
    satisfied. This is a documented, conservative assumption, not a
    guarantee — see README/limitations."""
    return [dep_id for dep_id in task.requires if dep_id in tasks_by_id]
