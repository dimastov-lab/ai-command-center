# AI Command Center — UX Audit

Status: **evidence-based audit, documentation only.** No application code was changed to produce
this document. Scope is the current Streamlit desktop-first application (`app.py`,
`command_center/ui/*`) on branch `feature/desktop-architecture-d0` at commit `8589069`. This
audit does **not** cover `docs/desktop/*` (the separate native PySide6/Qt initiative) — that
initiative is D0 documentation-only with no production code yet, and is out of scope for this
phase by explicit instruction.

## 1. Method

Inspected directly:

- `app.py` (3,265 lines) — single-file Streamlit entry point; all page routing, the task card
  renderer, the Kanban board, the agent launcher, and the execution center card live here.
- `command_center/ui/project_selector.py`, `project_intelligence_panel.py`,
  `recommendations_panel.py`, `queue_panel.py` — the four extracted UI modules.
- `command_center/models.py` — the state vocabulary (`WORKFLOW_STAGES`, `EXECUTION_STAGES`,
  `VERDICT_LABELS`, priority/launch-status color maps) that every badge in the UI renders.
- Theme/CSS surface: `.streamlit/config.toml` (absent), and a repo-wide grep for
  `unsafe_allow_html` / inline `<style>` (zero hits).
- `tests/test_app_streamlit.py`, `tests/test_execution_center_ui.py`,
  `tests/test_workspace_home_ui.py`, `tests/conftest.py` — existing `AppTest`-based coverage, to
  establish what is and isn't regression-protected today.

Every finding below cites the file and line it was observed at, on the commit named above. Line
numbers will drift as the app changes; treat them as pointers at time of writing, not permanent
anchors.

## 2. Findings

Findings are ordered by severity: **Critical** (breaks the core Kanban workflow at normal desktop
widths), **High** (materially hurts scanability or coherence), **Medium** (real but contained),
**Note** (constraint to preserve, not a defect).

### 2.1 Critical — Kanban columns are fixed-equal-width with no minimum and no scroll

`app.py:2440` renders the board as `st.columns(len(KANBAN_COLUMNS))` — five equal-width flex
columns, no `min-width`, no horizontal scroll fallback. Streamlit's main content area on a
27–32" monitor with the sidebar expanded is roughly 1100–1150px wide after padding; divided five
ways minus each column's internal gutter, each column has **~190–210px of usable content width**.
That is narrower than a single badge row needs, let alone a title. On a 1440px laptop viewport
the same math yields **~150–170px per column** — narrower than this document's inline code spans.

There is no responsive behavior at all: the same five-equal-columns layout is used from 1280px up
to 3200px+, so the board never uses the extra space a 27–32" monitor actually offers; it just
stretches five already-too-narrow columns a little wider.

### 2.2 Critical — Task titles wrap badly because they render as `<h3>` in a ~190px column

`app.py:876`: `st.markdown(f"### {title}")`. An `<h3>` is Streamlit's largest sub-heading size
(~1.5rem/24px), rendered unconditionally for every task title regardless of column width. Real
titles in this codebase run long — e.g. `"Bridge Kanban Launch onto v2 Session Supervisor"` (from
this branch's own commit `fb48662`) is 47 characters. At 24px in a ~190px column, that wraps to
3–4 lines before any other card content even starts, so a five-task column can exceed one screen
height before showing a single badge. This is the single biggest driver of "hierarchy is unclear"
— the least information-dense element (a heading) claims the most vertical space.

### 2.3 High — Badge rows overflow their own column and wrap into ragged multi-line soup

The task card's own code comment (`app.py:883–890`) states the intent precisely: planning state,
execution state, and dependency readiness are "deliberately never merged into one badge row, so
... each read as their own answer instead of one ambiguous chip soup." The intent is correct; the
layout defeats it. Three separate `st.container(horizontal=True)` badge rows are emitted
back-to-back (`app.py:891–897`, `899–912`, `916–922`), each holding 2–4 `st.badge` pills (icon +
label + padding, ~60–90px each) inside the same ~190px column identified in §2.1. Any row with
more than two badges wraps, and because the rows are visually identical (same pill shape, same
size, same font), the wrap boundary is the *only* thing separating "priority" from "execution
status" from "blocked reason" once they wrap — the reader has to parse position, not design, to
tell the three clusters apart. The intended separation becomes chip soup precisely because the
column is too narrow to hold it, which is the opposite of what the comment says it's for.

### 2.4 High — The Actions expander is unusable at Kanban column width

Every card's `st.expander("Действия", ...)` (`app.py:940`) contains a 5-button row
(`st.columns(5)`, `app.py:952`) followed by a 3-button row (`st.columns(3)`, `app.py:991`). Both
render inside the same ~190px column as everything else, giving each of the five buttons
(Workspace / Git / Промпт / Отчёт / В очередь) **~35–38px** — narrower than most single Material
icons plus their default Streamlit hit-target padding. In practice this forces icon-only,
tooltip-dependent buttons that are easy to mis-click and impossible to read at a glance. This
expander also holds the full agent launcher (`render_agent_launcher`, invoked at `app.py:1011`),
task timeline, and dependency graph — a large, high-functionality surface folded into a
216px-wide accordion is not a discoverable or comfortable place to launch an agent run.

### 2.5 High — Zero visual customization exists; the app is provably still default Streamlit

A repo-wide search for `.streamlit/config.toml`, `unsafe_allow_html`, and inline `<style>` tags
returns **no results**. `requirements.txt` pins `streamlit>=1.50,<2.0` but nothing in the
codebase touches its theme system. This means "the interface still looks like default Streamlit"
is not a subjective impression to be argued about — it is literally, verifiably true: the app has
never shipped a single custom color, font, spacing value, or CSS rule. Every visual property in
the product today — from the default blue accent to the default sans-serif — is whatever
Streamlit ships out of the box. This is good news for the redesign: there is no legacy theme
debt to unwind, only a blank canvas to fill deliberately (§6, Streamlit constraints).

### 2.6 High — One card component, no compact variant, used everywhere at full cost

`render_task_card()` (`app.py:862`) is the single card renderer for both the Kanban board and
Focus Mode (`show_kanban_controls` only toggles the trailing status selector and delete button).
There is no lighter-weight row/compact variant. Every rendering context — including Focus Mode,
which by its own framing (`app.py:3196`, `"Нет активных задач для фокуса"`) is meant to be a
dense, single-task-at-a-time view — pays the full vertical cost of title + progress bar + three
badge rows + optional blocked banner + git badges + full actions expander. A list of five tasks
in Focus Mode currently costs as much vertical space as five full Kanban cards, because it *is*
five full Kanban cards.

### 2.7 High — Flat, ungrouped, 17-item sidebar navigation

`NAV` (`app.py:173–191`) is a single flat dict rendered as one `st.radio` list
(`app.py:1827–1833`): Обзор, Workspace Home, Исполнительная панель, Создать задачу, Чат по
проекту, Kanban, AI-агенты, Live Execution Center, Журнал запусков, Таймлайн, Проекты,
Сгенерированные задачи, Отчёты, Глобальный контекст, Git Center, Workspace Launcher, Focus Mode —
17 items, no section headers, no grouping by concern. Planning (Kanban, Create task), execution
(Live Execution Center, Runs, Focus Mode), Git (Git Center), context (Global context, Projects,
Generated, Reports), and system-level views (Dashboard, Executive, Workspace Home) are
interleaved in registration order rather than grouped by what a user is trying to do. This is a
direct, concrete instance of "Project Intelligence, Recommendations, Kanban, and Execution Queue
do not feel like one coherent desktop product" — there is no navigational structure expressing
that these things belong together.

### 2.8 Medium — Redundant page-level header on every page

`st.title("🧭 AI Command Center")` and a caption (`app.py:1793–1794`) render once, above the
sidebar-independent page router, on **every** page — Kanban, Dashboard, Execution Center, Git
Center, etc. all show the identical app-level title. This claims a full title-block's worth of
vertical space without ever telling the user which page they are on (that information is only in
the sidebar's radio selection). A command bar or per-page header would use that space to orient
the user instead of repeating the app name.

### 2.9 Medium — Three visually unrelated panels stacked on the Kanban page

The Kanban page (`app.py:2411–2472`) stacks: the project selector (`st.pills`, pill-shaped
buttons), the Project Intelligence strip (`st.metric` grid, `project_intelligence_panel.py:21`),
the Recommendations panel (bordered card grid, `recommendations_panel.py:44–46`), the Kanban
board itself (unbordered `st.columns` + bordered task cards), and the Execution Queue panel
(plain caption rows with inline buttons, `queue_panel.py:96`, `106`) — separated only by
`st.divider()`. Each section uses a different Streamlit primitive with a different visual
grammar (metrics vs. bordered cards vs. plain rows), so nothing on the page reads as one
designed surface; it reads as five independently-built widgets placed in sequence.

### 2.10 Medium — Recommendation cards split width evenly by count with no responsive floor

`recommendations_panel.py:44`: `st.columns(len(views))` with `limit=3` at the Kanban call site
(`app.py:2427`). Unlike the Kanban board this renders at full page width, so three cards is
currently tolerable — but the pattern (divide by count, no minimum) is the same one that breaks
the Kanban board at higher column counts, and it has no defined behavior if `limit` is ever
raised or a narrower breakpoint is targeted.

### 2.11 Medium — No layout regression coverage in existing tests

`tests/test_app_streamlit.py`'s Kanban-related tests (`test_kanban_launcher_present_but_never_
calls_subprocess_on_render`, `test_full_launch_flow_records_run_and_parses_verdict`, etc.)
exercise button clicks and launch-flow state transitions via `AppTest`, which is exactly right
for behavior — but none assert anything about column count, card width, or badge wrapping,
because `AppTest` does not render pixels. There is currently no regression net for the visual
problems this audit documents, which §5 (Implementation Roadmap dependency) and
`IMPLEMENTATION_ROADMAP.md`'s UX-6 increment address directly (visual snapshot testing).

### 2.12 Note — UI copy is Russian-language throughout; preserve this, don't hardcode English

`st.title`, all labels, captions, and button text are Russian (`"Единый центр управления
проектами..."`, `"Приоритет"`, `"В очередь"`, etc.). This is a constraint the redesign must
respect, not a defect: any new component copy, empty-state text, or confirmation-dialog wording
must be added through whatever the app's existing string convention is (plain Python string
literals today — there is no i18n layer to route through), not introduced as new hardcoded
English. Flagged as an explicit open founder decision in the Founder Review Report if a future
i18n layer is desired.

### 2.13 Note — Kanban status is already correctly the only *stored* lane

`KANBAN_COLUMNS = ["Backlog", "Next", "In Progress", "Review", "Done"]` (`app.py:143–149`) is the
only status persisted as a Kanban column. `launch_status` (Ready/Launching/Running/Completed/
Failed/Requires Attention, synced live from `runtime.db` per ADR 0003) and blocked/dependency
state (`models.is_blocked`, derived, never stored) are already correctly kept out of
`KANBAN_COLUMNS` — this matches the brief's explicit constraint ("Do not add Blocked or Running
as stored planning columns") without any change needed. The problem is not the data model; it is
that the *visual weight* given to derived execution-state badges (§2.3) currently competes with,
rather than clearly subordinates to, the primary planning-lane signal. `KANBAN_REDESIGN.md` §3
addresses this as a visual-hierarchy fix, not a data-model change.

## 3. Summary table

| # | Area | Severity | Root cause | Fix location |
|---|---|---|---|---|
| 2.1 | Kanban board | Critical | Equal-width columns, no min-width, no scroll | `KANBAN_REDESIGN.md` §1 |
| 2.2 | Task card title | Critical | `<h3>` heading in ~190px column | `KANBAN_REDESIGN.md` §2 |
| 2.3 | Task card badges | High | 3 badge rows exceed column width | `KANBAN_REDESIGN.md` §2, `DESIGN_SYSTEM.md` §7 |
| 2.4 | Task card actions | High | 5+3 button grid in 190px expander | `KANBAN_REDESIGN.md` §2, `INTERACTION_MODEL.md` §9 |
| 2.5 | Theming | High | Zero custom CSS/theme anywhere | `DESIGN_SYSTEM.md` §3, §12 |
| 2.6 | Card variants | High | One card, no compact variant | `KANBAN_REDESIGN.md` §2.2 |
| 2.7 | Navigation | High | Flat 17-item ungrouped sidebar | `DESIGN_SYSTEM.md` §2 (App Shell) |
| 2.8 | Page header | Medium | Redundant app title on every page | `DESIGN_SYSTEM.md` §2.3 (Command Bar) |
| 2.9 | Kanban page composition | Medium | 5 visually unrelated stacked panels | `DESIGN_SYSTEM.md` §9 (Components) |
| 2.10 | Recommendation cards | Medium | Divide-by-count, no responsive floor | `KANBAN_REDESIGN.md` §7 |
| 2.11 | Test coverage | Medium | No visual/layout regression tests | `IMPLEMENTATION_ROADMAP.md` UX-6 |
| 2.12 | i18n | Note | Russian-language copy throughout | Preserve; founder decision if i18n layer wanted |
| 2.13 | Data model | Note | Stored/derived state already correctly separated | Preserve; fix visual weight only |

## 4. What this audit does not claim

- It does not claim the underlying data model (`command_center/models.py`, `task_view.py`,
  `execution_queue.py`, `recommendation_service.py`) needs to change. Every finding above is a
  rendering/layout problem, not a state-shape problem — the read models already separate
  planning, execution, and dependency state correctly (§2.13); the UI just doesn't have room to
  show that separation clearly.
- It does not propose changes to `docs/desktop/*` or the native PySide6/Qt initiative. Where a
  desired interaction is not reliably achievable in Streamlit, this audit and its companion
  documents say so explicitly and defer it (see `INTERACTION_MODEL.md` §7 and
  `DESIGN_SYSTEM.md` §12 for the specific list).
- It does not include screenshots. `AppTest` (Streamlit's test harness used throughout this
  repo's test suite) does not render pixels, and no browser automation was run against a live
  instance during this documentation-only phase — see the Founder Review Report for how to
  validate visually before UX-1 lands.
