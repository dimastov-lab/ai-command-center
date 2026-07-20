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
string scalars, `null`, booleans, numbers, and `[...]` lists of quoted
strings — never nested maps, block-style lists, block scalars, or multi-line
values), and this project takes no new third-party dependency (only
`streamlit` is declared) to parse it. A card using a YAML feature outside
that subset — a nested mapping (block-indented or inline `{...}`), a
block-style (`- item`) list, a block scalar (`|`/`>`), an unterminated quoted
string or flow list, or a duplicate key — fails closed with a clear
`PortfolioCardError` naming the offending field, rather than being silently
flattened, truncated, or misread. A card that fails to parse never produces a
partially-built `PortfolioTask`.
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
_DOUBLE_QUOTED_ITEM_RE = re.compile(r'^"((?:[^"\\]|\\.)*)"$')
_SINGLE_QUOTED_ITEM_RE = re.compile(r"^'([^']*)'$")

# `task_id` becomes a filesystem path component (git branch name, worktree
# directory name, per-task lock filename) in `command_center.portfolio_launch`
# — a card-authored, external, untrusted value, so it is never allowed to
# contain a path separator, `.`/`..`, whitespace, or any other character that
# could let it escape the sandboxed worktrees/locks roots it's joined onto
# there (Founder Gate re-review Blocker 1). This is the single definition of
# that rule: every module that turns a `task_id` into a path calls
# `validate_task_id` rather than re-deriving the allowlist.
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
MAX_TASK_ID_LENGTH = 128


def validate_task_id(task_id: str, *, source_path: Path | str | None = None) -> str:
    """Returns `task_id` unchanged if it is safe to use as a filesystem path
    component — letters/digits/`_`/`-` only, starting with a letter or digit,
    1-`MAX_TASK_ID_LENGTH` characters. Raises `PortfolioCardError` for
    anything else (a path separator, `.`/`..`, whitespace, an empty string, an
    over-length value): an invalid `task_id` is always rejected outright,
    never silently normalized/sanitized into something safe-looking."""
    prefix = f"{source_path}: " if source_path is not None else ""
    if not isinstance(task_id, str) or not task_id or len(task_id) > MAX_TASK_ID_LENGTH:
        raise PortfolioCardError(
            f"{prefix}task_id {task_id!r} is invalid — must be a non-empty string of at most "
            f"{MAX_TASK_ID_LENGTH} characters"
        )
    if not _TASK_ID_RE.match(task_id):
        raise PortfolioCardError(
            f"{prefix}task_id {task_id!r} is invalid — only letters, digits, '_' and '-' are allowed, "
            "and it must start with a letter or digit (no path separators, '.', '..', or whitespace)"
        )
    return task_id

# YAML block-scalar indicators (`|`, `>`, with optional chomping/indentation
# modifiers) — a value of exactly one of these on its own signals a
# multi-line block scalar is about to follow, which this flat, single-line
# subset does not support.
_BLOCK_SCALAR_MARKERS = frozenset({"|", ">", "|-", "|+", ">-", ">+"})


def _split_flow_list_items(inner: str, *, field_name: str, source_path: Path, raw_value: str) -> list[str]:
    """Split the inside of a `[...]` flow list on top-level commas, treating
    commas inside a quoted item (single or double) as part of that item
    rather than a separator. Raises on an unterminated quoted string so the
    caller never has to guess where an item actually ends."""
    items: list[str] = []
    buf: list[str] = []
    quote_char: str | None = None
    index = 0
    length = len(inner)
    while index < length:
        char = inner[index]
        if quote_char is not None:
            if quote_char == '"' and char == "\\" and index + 1 < length:
                buf.append(char)
                buf.append(inner[index + 1])
                index += 2
                continue
            buf.append(char)
            if char == quote_char:
                quote_char = None
            index += 1
            continue
        if char in ('"', "'"):
            quote_char = char
            buf.append(char)
            index += 1
            continue
        if char == ",":
            items.append("".join(buf))
            buf = []
            index += 1
            continue
        buf.append(char)
        index += 1
    if quote_char is not None:
        raise PortfolioCardError(
            f"{source_path}: field {field_name!r} has an unterminated quoted string in flow list: {raw_value!r}"
        )
    items.append("".join(buf))
    return items


def _parse_flow_list_item(item: str, *, field_name: str, source_path: Path, raw_value: str) -> str:
    """A single flow-list element. Only single- or double-quoted strings are
    supported — any other token (bare word, number, `true`/`false`, `null`)
    is rejected rather than silently dropped, since `_QUOTED_ITEM_RE.findall`
    previously extracted only the quoted items and quietly discarded every
    unquoted one (Founder Gate Blocker 1)."""
    stripped = item.strip()
    double_match = _DOUBLE_QUOTED_ITEM_RE.match(stripped)
    if double_match:
        return double_match.group(1).replace('\\"', '"')
    single_match = _SINGLE_QUOTED_ITEM_RE.match(stripped)
    if single_match:
        return single_match.group(1)
    raise PortfolioCardError(
        f"{source_path}: field {field_name!r} has an unsupported flow-list item {stripped!r} "
        f"in {raw_value!r} — flow-list items must be single- or double-quoted strings"
    )


def _parse_scalar(raw: str, *, field_name: str, source_path: Path) -> Any:
    raw = raw.strip()
    # Intentionally lowercase-only (Founder Gate re-review Nit): this flat
    # subset recognizes exactly `null`/`true`/`false`, not YAML 1.1/1.2's
    # other case variants (`Null`/`True`/`Yes`/...). A differently-cased
    # value is not a supported literal here — it falls through and is kept
    # as its own opaque string, never misread as a bool/null. Not expanding
    # this to broader YAML compatibility is deliberate, not an oversight.
    if raw == "null" or raw == "":
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw in _BLOCK_SCALAR_MARKERS:
        raise PortfolioCardError(
            f"{source_path}: field {field_name!r} uses a YAML block scalar ({raw!r}) — "
            "multi-line values are not supported, only flat single-line scalars"
        )
    if raw.startswith("{"):
        raise PortfolioCardError(
            f"{source_path}: field {field_name!r} is a nested mapping ({raw!r}) — "
            "nested mappings are not supported, only flat scalar/list fields"
        )
    if raw.startswith('"'):
        if raw.endswith('"') and len(raw) >= 2:
            return raw[1:-1].replace('\\"', '"')
        raise PortfolioCardError(
            f"{source_path}: field {field_name!r} has an unterminated quoted string: {raw!r}"
        )
    if raw.startswith("["):
        if not raw.endswith("]"):
            raise PortfolioCardError(
                f"{source_path}: field {field_name!r} has an unterminated flow list: {raw!r}"
            )
        inner = raw[1:-1].strip()
        if not inner:
            return []
        items = _split_flow_list_items(inner, field_name=field_name, source_path=source_path, raw_value=raw)
        return [
            _parse_flow_list_item(item, field_name=field_name, source_path=source_path, raw_value=raw)
            for item in items
        ]
    if _INT_RE.match(raw):
        return int(raw)
    if _FLOAT_RE.match(raw):
        return float(raw)
    return raw


def _parse_frontmatter_block(lines: list[str], *, source_path: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    last_key: str | None = None
    # True only when the most recently parsed key had an empty value (e.g.
    # `deliverables:` with nothing after the colon) — the one case where a
    # following unindented `- item` line is a plausible (if unsupported)
    # block-list value for that key. A key that already had its own value
    # (e.g. `status: "ready"`) cannot own a later `- item` line, so a block
    # item straight after it is a genuinely top-level/orphaned line, not
    # nested under that key (Founder Gate re-review Minor 1).
    last_key_awaits_list = False
    for raw_line in lines:
        if not raw_line.strip():
            continue
        if raw_line[:1] in (" ", "\t"):
            owner = f" (nested under {last_key!r})" if last_key else ""
            raise PortfolioCardError(
                f"{source_path}: indented/nested frontmatter content is not supported{owner}: {raw_line!r}"
            )
        stripped = raw_line.strip()
        if stripped == "-" or stripped.startswith("- "):
            if last_key and last_key_awaits_list:
                raise PortfolioCardError(
                    f"{source_path}: block-style list item is not supported (nested under {last_key!r}) — "
                    f"use an inline flow list instead, e.g. field: [\"a\", \"b\"]: {raw_line!r}"
                )
            raise PortfolioCardError(
                f"{source_path}: top-level block-style list item is not supported — "
                f"use an inline flow list instead, e.g. field: [\"a\", \"b\"]: {raw_line!r}"
            )
        if ":" not in raw_line:
            raise PortfolioCardError(
                f"{source_path}: malformed frontmatter line (no ':'): {raw_line!r}"
            )
        key, _, value = raw_line.partition(":")
        key = key.strip()
        if not key:
            raise PortfolioCardError(f"{source_path}: empty frontmatter key in line {raw_line!r}")
        if key in data:
            raise PortfolioCardError(f"{source_path}: duplicate frontmatter key {key!r}")
        data[key] = _parse_scalar(value, field_name=key, source_path=source_path)
        last_key = key
        last_key_awaits_list = value.strip() == ""
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
    raw_task_id = frontmatter.get("task_id")
    if raw_task_id is not None:
        # A missing `task_id` is reported later via `missing_required_fields`
        # (it's a required field, not a parse error) — this only validates
        # the *shape* of a `task_id` that is actually present.
        validate_task_id(raw_task_id, source_path=path)
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
