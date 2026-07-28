# AI Command Center — Interaction Model

Status: **implementation-ready.** Specifies click behavior, selection, keyboard navigation,
filtering/search, drag-and-drop feasibility, confirmations, and progressive disclosure for the
current Streamlit application. Consumes components from `DESIGN_SYSTEM.md` §9 and the Kanban
card variants from `KANBAN_REDESIGN.md` §2.

## 1. Selection model

There is exactly one thing "selected" at a time in the Central Workspace: a task (opens the
Inspector, `DESIGN_SYSTEM.md` §9.13), a run (opens the Inspector in run-scoped mode), or nothing
(Inspector closed). Selecting a new task while the Inspector is already open **replaces** its
content — it never stacks a second panel, never opens a second dialog. This mirrors the existing
`pending_*` session-state staging pattern (`app.py:1773–1784`) already used for cross-page
navigation: selection is one `st.session_state` key (`selected_task_id` /
`selected_run_id`), set on click, read once at the top of the Inspector's render call.

## 2. Click behavior

| Target | Action |
|---|---|
| Task card body (compact, on-board) | Opens that task in the Inspector (§DESIGN_SYSTEM §9.13), expanded variant (`KANBAN_REDESIGN.md` §2.2). Does **not** change the task's Kanban lane. |
| Task card's status selector (Inspector footer, `KANBAN_REDESIGN.md` §2.2) | Changes lane immediately on selection — no confirmation (a lane move is low-cost and one click to undo). |
| Task card's delete button (Inspector footer) | Opens the Confirmation Dialog (§11) — never deletes on the first click. |
| A dependency name (blocked-reason caption, `KANBAN_REDESIGN.md` §2.5) | Opens *that* dependency task in the Inspector, replacing the current selection. |
| An active-run link (`KANBAN_REDESIGN.md` §2.4) | Opens that run in the Inspector (run-scoped) or navigates to the Execution Strip's expanded view — does not navigate away from the current page. |
| Recommendation Card's "В очередь" | Enqueues immediately, no confirmation (existing behavior, `recommendations_panel.py:74–83`, unchanged) — reversible in one click via Queue Item's "Убрать." |
| Recommendation Card's "Запустить" | Enqueues-then-launches immediately (existing combined behavior, `recommendations_panel.py:86–114`, unchanged) — see §8 for why this one *is* a real launch action but still doesn't need a confirmation dialog. |
| Queue Item's "Убрать" | Dequeues immediately, no confirmation (`DESIGN_SYSTEM.md` §9.11 — low-cost, reversible). |
| Command Bar's project-scope indicator | Opens the same project selector already on the page (`DESIGN_SYSTEM.md` §9.4), scrolled into view if needed — does not duplicate the selector's state. |
| Command Bar's system-status glyph | Expands the Execution Strip (§DESIGN_SYSTEM §2.1) in place — does not navigate to a different page. |
| Sidebar nav item | Routes via the existing `nav_page` session-state key, unchanged mechanism (`app.py:1827–1833`). |

## 3. Task selection and keyboard navigation

- **Mouse**: click anywhere on a compact card's body (outside its badges, which are not
  independently clickable, `DESIGN_SYSTEM.md` §9.7) to select/open it.
- **Keyboard, within a column**: `↓`/`↑` moves selection between cards in the currently-focused
  column; `Enter` opens the focused card in the Inspector; `Tab`/`Shift+Tab` moves focus between
  columns and other focusable page elements in DOM order (native browser behavior — Streamlit
  renders standard focusable elements, so this requires no custom handling beyond ensuring every
  card is a real focusable element, e.g. a `button`-rendered container, not a `div` with a click
  handler).
- **Keyboard, global**: `Mod+K` opens the command palette from anywhere (existing, unchanged,
  `app.py:1820`). `Esc` closes whatever is topmost — command palette, then Confirmation Dialog,
  then Inspector, in that priority order (never closes more than one layer per press).
- Every focusable element shows the visible focus ring (`border.focus`, `DESIGN_SYSTEM.md` §3.4)
  on keyboard focus. This is the one interaction requirement most at risk under Streamlit's
  default widget rendering (§7 constraints below) and is flagged as an explicit UX-5 acceptance
  criterion (`IMPLEMENTATION_ROADMAP.md`).
- `↓`/`↑`/`Enter` list-style navigation within a column is a **stretch goal**, not a guarantee —
  see `DESIGN_SYSTEM.md` §12.3 on the limits of custom keyboard handling in Streamlit without a
  custom component. If it cannot be implemented reliably by UX-5, `Tab`-based native focus order
  plus mouse click remains the supported baseline, and this is called out explicitly in that
  increment's risk section rather than silently dropped.

## 4. Command palette

Unchanged from the existing implementation (`app.py:1800–1880`): `Mod+K` or the sidebar trigger
button opens an `st.dialog`; typing filters `build_commands()`'s list (page navigation + "new
task in project X" shortcuts) case-insensitively by substring match on the label; clicking a
result stages a `pending_nav` (and `pending_create_project` where relevant) and reruns. No
behavioral change specified here — only the visual retokenization already covered in
`DESIGN_SYSTEM.md` §9.3.

One functional addition scoped for UX-5: extend `build_commands()` to include "open task <title>"
entries for recently-viewed or in-progress tasks, so the palette can jump directly into the
Inspector, not just to a page. This is additive to the existing command list, not a change to how
the palette itself works.

## 5. Filtering

- **Project filter** (`project_selector`, pills) and **priority filter** (`st.multiselect`,
  `app.py:2431`) are unchanged in mechanism — both are Streamlit `on_change`-triggering widgets
  that cause a full rerun with the new filter applied, which is the correct and only reliable
  pattern under Streamlit's execution model (`DESIGN_SYSTEM.md` §12.3).
- Filters apply to the Kanban board, KPI strip, Recommendations rail, and Execution Queue
  simultaneously (existing behavior — all four already read the same `project_filter`,
  `app.py:2414–2471`) — this redesign does not change filter scope, only makes the filter
  controls visually consistent with the rest of the Command Bar area.
- Active filters that narrow results to zero use the Empty State component (`DESIGN_SYSTEM.md`
  §9.14) with copy that names the filter as the cause ("Нет задач с приоритетом Critical в
  проекте AICOS") and a one-click "Сбросить фильтры" action — today a filtered-empty board just
  shows five empty columns with no explanation of why.

## 6. Search

The command palette (§4) is today's only search surface, and it searches pages/commands, not
task content. This document does not add a separate task-content search box as a Kanban-page
feature — the existing Kanban filters (project, priority) plus the command palette's planned
"open task" extension (§4) are judged sufficient for the current task volume. A dedicated
full-text task search is an explicit **open founder decision** (see Founder Review Report) rather
than something this spec silently assumes; if the founder wants it, it belongs in the Command Bar
(`DESIGN_SYSTEM.md` §9.3) as a scoped addition to a later increment, not folded into UX-1–UX-5.

## 7. Drag-and-drop

**Not implemented in this phase, and not recommended for the Streamlit application at all.**
Reasoning, expanded from `DESIGN_SYSTEM.md` §12.3:

- Streamlit's execution model reruns the whole script on every state-changing interaction; native
  HTML5 drag-and-drop events don't natively round-trip into that model without a custom
  bidirectional component (a real engineering investment: a JS/React component package, its own
  state-sync protocol, and a new class of bugs — e.g. a drag that starts before a rerun completes).
- The existing explicit control — a per-card `st.selectbox` of `KANBAN_COLUMNS`
  (`app.py:1034–1044`, relocated to the Inspector footer per `KANBAN_REDESIGN.md` §2.2) — is
  reliable, keyboard-accessible, and already works today. This redesign's job is to give it more
  room and a clearer position, not to replace it.
- Drag-and-drop is exactly the kind of high-fidelity, native-feeling interaction the mission
  brief identifies as belonging to "the future native client" — **explicitly deferred to the
  PySide6/Qt initiative** (`docs/desktop/*`) as a candidate feature there, not committed to any
  Streamlit increment in this roadmap.

## 8. Launch confirmation

- **Launching from the Task Card / Inspector** (`render_agent_launcher`): the existing multi-step
  gate is preserved exactly — an explicit "Open launcher" action, a required confirmation
  checkbox (`..._launch_confirmed`, exercised by `test_kanban_launcher_blocking_validation_error_
  cannot_be_bypassed`), then the launch button, with server-side re-validation that the checkbox
  was actually checked (defense against a bypassed client-side `disabled=`). This is already the
  right shape and is not weakened by this redesign — only its container moves from a 190px
  expander to the Inspector.
- **Launching from a Recommendation Card** ("Запустить," `recommendations_panel.py:86–114`) skips
  the multi-step launcher gate and launches in one click. This is a deliberate existing design,
  not an oversight: a recommendation is, by construction, already validated as unblocked and
  ready (`recommendation_service.build_recommendation_views` filters to ready tasks), so the
  extra confirmation step exists specifically for the general launcher (where a user might launch
  against an unconfigured or unexpected workspace) and is redundant for a pre-validated
  recommendation. This document keeps that distinction rather than forcing both paths through
  identical friction.

## 9. Enqueue behavior

- Adding to the queue (from a Task Card action, a Recommendation Card, or directly) is always
  immediate, never confirmed — queueing is inherently non-destructive and reversible via Queue
  Item's "Убрать" (§2).
- `execution_queue.reevaluate_and_persist` runs on every relevant page load (existing behavior,
  `recommendations_panel.py:34`, `queue_panel.py:44`) — this redesign does not change when
  re-evaluation happens, only how its results are displayed (queue-state dot on the card,
  `KANBAN_REDESIGN.md` §2.6).

## 10. Blocked-state explanations and progressive disclosure

- **Compact card**: blocked state is one badge + one line naming what's unmet
  (`KANBAN_REDESIGN.md` §2.5) — enough to explain *that* and *why*, not enough to act on.
- **Inspector (expanded)**: the same information plus clickable links to each blocking
  dependency (§2), plus the full dependency graph (`render_dependency_graph`, existing,
  unchanged) for tasks with a non-trivial dependency chain.
- This two-level pattern — one-line explanation on the compact surface, full detail one click
  away — is the general progressive-disclosure rule for this redesign, applied consistently to:
  blocked reasons (above), git status (badge on compact card → full git detail in Inspector),
  and verdict (badge on compact card → full report text in Inspector, existing
  `report_open` pattern at `app.py:984–989`, relocated). Nothing new is invented per-surface;
  this is the same disclosure shape reused everywhere.

## 11. Destructive actions

| Action | Today | This spec |
|---|---|---|
| Delete task (`app.py:1046–1048`) | Bare button, **no confirmation** — one accidental click permanently removes a task | Confirmation Dialog (`DESIGN_SYSTEM.md` §9.16) — names the task, states the consequence, requires explicit confirm |
| Cancel run (`app.py:1376–1399`) | Inline "Подтвердить" checkbox + Cancel button, server-side re-validated | **Unchanged** — this already meets the confirmation bar (explicit opt-in step + server-side re-check); not migrated to a full dialog, since the existing pattern is deliberately lighter-weight for an action that's paused, not destroyed (a cancelled run's task and history remain) |
| Dequeue (§9) | Immediate, no confirmation | **Unchanged** — not destructive (§9) |
| Pause / Resume / Restart (`app.py:993–1003`) | Immediate status-label change, no confirmation | **Unchanged** — these are explicitly documented (existing caption, `app.py:1004–1007`) as planning-status labels, not process control; they don't destroy anything, so no gate is added |

The rule this table encodes: a confirmation gate is added only where an action is genuinely
irreversible (task deletion) or already correctly gated (run cancellation). Actions that are
cheap to undo (dequeue, status labels, lane moves) stay ungated — adding friction there would
cost more in daily usability than it would prevent in accidents.

## 12. Refresh behavior

- The Kanban board, KPI strip, and Execution Queue re-read their underlying data
  (`load_tasks()`, `execution_queue.reevaluate_and_persist`) on every Streamlit rerun — i.e.
  every user interaction on the page already refreshes this data as a side effect of Streamlit's
  execution model. No polling loop is added to the Kanban page itself.
- The Execution Strip (§DESIGN_SYSTEM §2.1), when expanded, mirrors the existing Live Execution
  Center's poll-based refresh pattern (`_render_live_execution_center_poll_2s` through `_poll_5s`,
  `app.py:1539–1554`) — an already-established, working pattern for surfacing near-live run state
  without a full page reload — reused rather than reinvented for the strip's summary view.
- No page silently auto-refreshes data the user is actively reading in the Inspector (e.g.
  editing launch parameters) — the Inspector only re-reads its selected task's data on an
  explicit rerun-triggering interaction (a button click within it), never on a background timer,
  so an open Inspector never yanks focus or state out from under an in-progress action.

## 13. What this document does not change

- The underlying `st.rerun()`-driven state machine for launch, queue, and status changes —
  every mechanism named above already exists in the current codebase and is reused, not rebuilt.
- The command palette's data source (`build_commands()`) beyond the additive "open task" entries
  named in §4.
- Any interaction inside `render_agent_launcher`, `render_task_timeline`, or
  `render_dependency_graph` — these are relocated (`KANBAN_REDESIGN.md` §2.2) but not modified.
