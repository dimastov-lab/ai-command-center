# AI Command Center — Kanban Redesign

Status: **implementation-ready.** Specifies the Kanban board and task card in full — column
widths, scroll behavior, card variants, and complete information hierarchy — for the current
Streamlit application. Builds directly on `UX_AUDIT.md` §2.1–§2.4 (the four Kanban-specific
findings) and consumes tokens from `DESIGN_SYSTEM.md` §3–§6. Read that document first if the
token names below (`space.*`, `type.*`, `status.*`) are unfamiliar.

Binding constraint, unchanged from the brief and from the current implementation
(`UX_AUDIT.md` §2.13): **`Blocked` and `Running` are never added to `KANBAN_COLUMNS` as stored
planning lanes.** `KANBAN_COLUMNS = ["Backlog", "Next", "In Progress", "Review", "Done"]`
(`app.py:143–149`) stays exactly as it is today. Everything in this document is a rendering and
layout change over that unchanged five-lane model.

## 1. Board layout

### 1.1 Column widths and scroll behavior

The current implementation (`app.py:2440`, `st.columns(len(KANBAN_COLUMNS))`) divides available
width evenly by column count with no minimum — the root cause of `UX_AUDIT.md` §2.1 and §2.2.
This redesign replaces "divide evenly, shrink forever" with "fixed comfortable width, scroll
horizontally once the viewport can't fit all five":

| Token | Value | Meaning |
|---|---|---|
| `kanban.column.width.comfortable` | 320px | Default column width when ≥5 columns fit the workspace at once |
| `kanban.column.width.minimum` | 260px | Floor width before the board switches to horizontal scroll instead of shrinking further |
| `kanban.column.gap` | `space.xl` (24px) | Gap between columns |
| `kanban.board.scroll` | horizontal, native momentum scroll, column-snap | Activates whenever `5 × (width + gap)` exceeds the Central Workspace's available width |

At the breakpoints from `DESIGN_SYSTEM.md` §2.4:

| Breakpoint | Central Workspace width (typical, Inspector closed) | Columns visible without scrolling | Behavior |
|---|---|---|---|
| **≥1920px** | ~1600–1850px | All 5, at `comfortable` width with room to spare | No scroll; columns may grow slightly beyond `comfortable` to fill space evenly, capped at 380px so cards don't over-stretch |
| **1728px** | ~1400px | All 5, at `comfortable` width, tight | No scroll |
| **1440px** | ~1100px | 3–4 at `comfortable` width | Horizontal scroll for the remaining 1–2 columns; scroll-snap so a column is never left half-visible at rest |
| **Narrow fallback (<1280px)** | ~900px or less | 2–3 at `minimum` width | Horizontal scroll is the default expectation, not an edge case |

This is the direct fix for `UX_AUDIT.md` §2.1: a column is never asked to render at less than
260px. Five columns of 260px plus four 24px gaps is 1,396px — below that, the board scrolls
instead of continuing to compress columns toward zero.

### 1.2 Card width within a column

Card width equals column content width minus the column's own internal padding
(`space.md`, 12px each side): **~296px comfortable / ~236px minimum.** This is the number that
matters for `UX_AUDIT.md` §2.2 — at 236–296px, `type.card-title` (15px, `DESIGN_SYSTEM.md` §3.2)
fits most real task titles (including the 47-character example cited in the audit) on one or two
lines instead of the three to four lines the current 24px `<h3>` produces at ~190px.

### 1.3 Column header

Each column header (replacing the current bare `st.markdown(f"**{status}**")` +
`st.caption(f"{len(status_tasks)} задач")`, `app.py:2446–2447`) shows: lane name (`type.card-
title` weight) + task count badge (`status.neutral`, not plain caption text — makes the count
scannable at a glance across all five columns) +, for the `Review` column only, a secondary count
of how many of those tasks have a failing/pending verdict, since Review is the lane where verdict
state is most decision-relevant. No column header carries an add-task button — task creation
stays on the dedicated Create page (`NAV["create"]`), consistent with the existing information
architecture; a column-level "+" is a plausible UX-2+ addition but is not specified here to avoid
scope creep into task-creation flow, which this document does not otherwise touch.

### 1.4 Empty column

Uses the shared Empty State component (`DESIGN_SYSTEM.md` §9.14) instead of today's bare
`st.caption("Пусто")` (`app.py:2450`): icon + "Нет задач в этом статусе" + no action button (task
creation is intentionally off-board, §1.3).

## 2. Task card

### 2.1 Compact variant (default, on-board)

The default rendering for every card on the Kanban board. Zero inline buttons (`DESIGN_SYSTEM.md`
§5) — this is the direct fix for `UX_AUDIT.md` §2.4 (the unusable 5+3 button grid at 190px). All
actions move to the Inspector Panel (`DESIGN_SYSTEM.md` §9.13), opened by clicking the card
(`INTERACTION_MODEL.md` §2).

```
┌─────────────────────────────────────┐
│ Fix sort order regression in Kanban  │  ← type.card-title, max 2 lines,
│                                       │    ellipsis on a 3rd
│ AICOS · implementation               │  ← type.caption, project · task type
│                                       │
│ ▓▓▓▓▓▓▓▓░░░░░░░░  Implementation 45% │  ← Run Progress (§9.9), only if
│                                       │    launch_status != "Ready" (§2.4)
│ [High] [dc] [3h]                     │  ← planning row: priority, owner, estimate
│ [● Running] [claude-code] [main]     │  ← execution row: launch_status, executor, branch
│ [⛔ Blocked — waiting: Task #42]      │  ← readiness row, only if blocked (§2.5)
└─────────────────────────────────────┘
```

- Card container: `surface.card`, `border.hairline`, `radius.md`, `space.md` internal padding.
- Selected/open-in-Inspector state: `border.emphasis` + `surface.selected` background.
- Hover: `surface.card-hover`, cursor pointer — the whole card is the click target
  (`INTERACTION_MODEL.md` §2), not just the title.
- Maximum 3 badges per row (`DESIGN_SYSTEM.md` §4) — a task with more facts than fit
  (e.g. both a branch **and** an active-run link) truncates to the row's 3 and moves the rest
  to the Inspector, never to a 4th wrapped line.
- Run Progress renders only when there's something to show progress *of* — a `Backlog`-lane task
  with `launch_status == "Ready"` has no active run and shows no progress bar at all (today's
  implementation always renders `st.progress`, even at 0%, for every task regardless of lane —
  this redesign suppresses it for never-launched tasks to reduce visual noise on the columns
  where it's least meaningful: Backlog and Next).

### 2.2 Expanded variant (Inspector-opened)

Everything currently inside the on-card `st.expander("Действия")` (`app.py:940–1031`) — metadata,
goal/notes, repository/workspace paths, the full action grid, agent launcher, timeline, dependency
graph — renders here, in the Inspector Panel (`DESIGN_SYSTEM.md` §9.13), at real width (≥320px,
often the full Inspector column), not a 190px accordion. This is a **layout** move, not new
content: every field and action listed below already exists in the current implementation; the
change is where it renders and how much room it gets.

```
┌───────────────────────────────────────────────┐
│ Fix sort order regression in Kanban        [×] │  ← header + close
├───────────────────────────────────────────────┤
│ [High] [dc] [3h]  [● Running] [claude-code]    │  ← same badge rows as compact,
│ [main]  [⛔ Blocked — waiting: Task #42]        │    given full width, no wrap
├───────────────────────────────────────────────┤
│ Overview │ Launch │ History │ Dependencies      │  ← sectioned body (tabs or
├───────────────────────────────────────────────┤     stacked sections)
│  ID: a1b2c3d4 · Created 12.07.2026             │
│  Стадия workflow: Финальная проверка            │
│  Цель: ...                                     │
│  Заметки: ...                                  │
│  Репозиторий: `...` · Workspace: `...`          │
│  Git: [Изменения] · PR ↗ · [Одобрено]           │
├───────────────────────────────────────────────┤
│  [Workspace] [Git] [Промпт] [Отчёт] [В очередь] │  ← action bar, full-width now,
│  [Pause] [Resume] [Restart]                    │    each button gets real space
├───────────────────────────────────────────────┤
│  Запуск: (render_agent_launcher, unchanged)     │
└───────────────────────────────────────────────┘
[Статус: In Progress ▾]              [Удалить] ← guarded by Confirmation Dialog, §DESIGN_SYSTEM §9.16
```

The status selector and delete button (today rendered directly on the card,
`app.py:1033–1048`) move to the Inspector's footer, always visible without opening the "Действия"
accordion — since changing lane and deleting are the two actions a user reaches for most often
without needing the rest of the detail, they get a fixed, un-collapsed spot rather than living
inside a sectioned tab.

### 2.3 Information hierarchy (both variants)

In priority order, top to bottom, matching how quickly each fact should be scannable:

1. **Title** — what is this. `type.card-title`.
2. **Project · task type** — whose work is this, what kind. `type.caption`.
3. **Progress + stage** (if a run has ever started) — how far along. Run Progress, §9.9.
4. **Planning row** — priority, owner/agent, estimate. These are attributes the user *set*, so
   they read before anything the system computed.
5. **Execution row** — launch status, executor, branch, active-run link. System-computed,
   live-synced state — deliberately after the planning row per `DESIGN_SYSTEM.md` §1.4 (derived
   state is visually subordinate).
6. **Readiness row** — blocked reason or dependency-met confirmation. Last, because it's only
   ever relevant when it says something is *wrong* or *newly unblocked* — a task with no
   dependencies shows nothing here at all (§2.5).
7. **Git state + verdict** (expanded variant only) — clean/dirty, PR link, latest verdict. Detail
   users check when deciding whether a task is ready to move lanes, not needed for a board-level
   scan.
8. **Everything else** (expanded variant only) — id, timestamps, workflow stage, goal, notes,
   paths, full action grid, launcher, timeline, dependency graph.

### 2.4 Execution state (launch status)

Unchanged data source (`task.get("launch_status", "Ready")`, live-synced from `runtime.db` per
ADR 0003) and unchanged color mapping (`LAUNCH_STATUS_COLORS`, `app.py:160–167`), retokenized
onto `DESIGN_SYSTEM.md` §3.6/§3.7:

| `launch_status` | Badge color | Compact-card behavior |
|---|---|---|
| `Ready` | `status.neutral` (gray) | Badge suppressed on the compact card — "Ready" is the resting state and showing it on every not-yet-launched task is noise; still shown in the Inspector's full badge row |
| `Launching` | `status.info` (blue) | Shown |
| `Running` | `status.active` (blue) + live pulse indicator | Shown; this is the one state that also suppresses the "Ready" suppression rule above — an in-flight run is always visible on the compact card |
| `Completed` | `status.success` (green) | Shown until the task moves out of `Review`/`Done`, then suppressed the same way `Ready` is (a `Done`-lane task's completed badge is redundant with the lane itself) |
| `Failed` | `status.danger` (red) | Always shown, never suppressed — a failure is exactly the fact a board scan must not miss |
| `Requires Attention` | `status.warning` (orange) | Always shown, never suppressed |

The active-run link (today a plain caption, `app.py:911–912`,
`f"run \`{run_id[:8]}\` · Live Execution Center"`) becomes a real link/button that opens that run
directly in the Execution Strip or Execution Center page (`INTERACTION_MODEL.md` §2), not inert
text — the one behavioral upgrade this section specifies, mirroring the same upgrade named for
the Dependency Indicator (`DESIGN_SYSTEM.md` §9.8).

### 2.5 Dependency readiness

Unchanged logic (`models.is_blocked`, `models.unmet_dependencies`), retokenized:

- **Blocked**: `status.danger` badge "Заблокировано" + inline caption naming unmet dependencies
  (existing content, `app.py:918–922`), each dependency name becomes a clickable link into that
  task's Inspector (§2.4's same upgrade pattern, tracked in `INTERACTION_MODEL.md` §9).
- **Dependencies met**: `status.success` badge "Зависимости выполнены" (existing,
  `app.py:923–924`) — quiet, single badge, no elaboration needed.
- **No dependencies**: nothing rendered. This is not an empty state to fill; a task with no
  `depends_on` simply has no readiness row.

### 2.6 Queue state

A task currently in the Execution Queue (waiting or ready, `execution_queue.py`) shows one
additional quiet indicator on its compact card — a small `status.info` dot + "В очереди" caption,
distinct from the launch-status badge (§2.4), since a task can be simultaneously `Ready` (launch
status) and queued (waiting for its turn or its dependencies). This closes a small gap in the
current implementation: today the Task Card has no visual indication that a task is in the
Execution Queue at all — that information is only visible on the separate Queue panel. Making it
visible on the card itself is a small net-new addition (not a pure retokenization), justified by
`DESIGN_SYSTEM.md` §2.1's requirement that execution state be legible without leaving the Kanban
board.

### 2.7 Owner / agent, branch, estimate

Unchanged data and unchanged badges (`task.get("owner")`, `task.get("estimate_hours")`,
`task.get("branch")`, `executors.get_executor(executor_id).label`) — §2.3's row placement and
§DESIGN_SYSTEM §4's "one badge = one fact" rule are the only changes; no field is added, renamed,
or removed.

## 3. What does not change

- `KANBAN_COLUMNS` — five lanes, unchanged, still the single stored planning axis.
- `render_task_card`'s underlying data reads (`task_view`, `models`, `executors`,
  `task_view.cached_git_status`) — every field this document places on the card already exists in
  the current implementation; this is a layout and hierarchy spec, not a data-model change.
- The status-change mechanism (`update_task_status`, a `st.selectbox` of `KANBAN_COLUMNS`) — kept
  as the supported way to move a task between lanes. See `INTERACTION_MODEL.md` §7 for why
  drag-and-drop is deferred rather than added here.
- The launch flow, agent launcher, and queue/enqueue behavior — unchanged; only their on-screen
  location moves from a 190px expander to a full-width Inspector.

## 4. Component ownership

`render_task_card` (`app.py:862–1048`, ~190 lines currently inline in `app.py`) is extracted into
`command_center/ui/task_card.py`, exposing `render_task_card_compact(...)` (the board variant,
§2.1) and `render_task_card_expanded(...)` (the Inspector variant, §2.2), sharing one internal
data-preparation step so the two variants can never drift out of sync on what data they read.
This follows the existing extraction precedent (`project_selector.py`,
`recommendations_panel.py`, `queue_panel.py`) named in `DESIGN_SYSTEM.md` §12.5.

## 5. Streamlit feasibility summary

| Piece | Feasibility | Note |
|---|---|---|
| Fixed-width columns + horizontal scroll | Medium | Requires CSS (`DESIGN_SYSTEM.md` §12.1) since `st.columns` alone always divides evenly; achievable, needs per-breakpoint verification |
| Compact card, zero inline buttons | High | Pure retokenization + removing the inline action grid |
| Column-snap scrolling | Medium | CSS `scroll-snap-type`; verify touch/trackpad momentum behaves acceptably inside Streamlit's iframe boundaries |
| Expanded card in Inspector | Medium | Depends on the Inspector Panel itself (`DESIGN_SYSTEM.md` §9.13, scoped as UX-5) |
| Clickable dependency names / run links | Low-medium | Small, contained wiring change — button-per-name instead of a caption string |
| Queue-state dot on card | High | New but simple boolean check against `execution_queue` entries already loaded per-page |

## 6. Testing expectations

- Extend existing `AppTest` Kanban coverage (`test_kanban_launcher_*`, `test_full_launch_flow_*`,
  `tests/test_app_streamlit.py`) to target the new `task_card.py` functions' `key=` structure —
  these tests assert *behavior* (button click → state change), which `AppTest` can do regardless
  of layout, so they should require only `key=` updates, not new assertions.
- Add migration-parity tests: every field/action present in today's `render_task_card` Actions
  expander must be reachable from the new Inspector — write one test per action verifying it's
  present and wired (Workspace, Git, Промпт, Отчёт, В очередь, Pause/Resume/Restart, launcher,
  timeline, dependency graph).
- Add the new task-deletion Confirmation Dialog test (`DESIGN_SYSTEM.md` §9.16).
- Visual/layout properties (column width, scroll activation, badge wrap) are not assertable via
  `AppTest` — covered by UX-6's visual regression tooling (`IMPLEMENTATION_ROADMAP.md`), not by
  new `AppTest` cases.

## 7. Recommendation-rail width note

The Recommendations rail (`recommendations_panel.py:44`, `st.columns(len(views))`,
`UX_AUDIT.md` §2.10) adopts the same "fixed comfortable width + scroll if needed" pattern as the
Kanban board itself (§1.1) rather than continuing to divide evenly by count, so a future increase
in `limit` (currently 3) doesn't reintroduce the same compression problem the board just fixed.
Card width for a Recommendation Card matches `kanban.column.width.comfortable` (320px) for visual
consistency with the board directly beneath it.
