# AI Command Center — Streamlit Design System

Status: **implementation-ready tokens and component contracts** for the current Streamlit
desktop-first application. Binding for increments UX-1 through UX-6 (`IMPLEMENTATION_ROADMAP.md`)
unless a specific increment's acceptance criteria say otherwise.

Scope boundary: this document specifies the **Streamlit** application (`app.py`,
`command_center/ui/*`). It does not modify, extend, or contradict `docs/desktop/DESIGN_SYSTEM.md`
(the separate native PySide6/Qt initiative, still D0/documentation-only). Where a value or
component here is a deliberate visual echo of a `docs/desktop` token, it is called out — but the
two systems are implemented independently, on independent timelines, in independent code.

Grounding: `UX_AUDIT.md` established that **zero custom theming exists today** — no
`.streamlit/config.toml`, no injected CSS, no `unsafe_allow_html` anywhere in the codebase. Every
value below is a decision to make, not a migration from an existing custom look.

## 1. Principles

1. **Dense over spacious.** This is an operator's control plane for a single power user, not a
   marketing surface. Favor information density over whitespace, the way Linear, Raycast, and
   GitHub Desktop do — but never so dense that a click target drops under 32px or text drops
   under 13px (§3.2, §3.7).
2. **Calm, not decorative.** No gradients, no drop shadows on ordinary cards, no more than one
   accent hue. Hierarchy comes from spacing, weight, and a restrained semantic-color set — never
   from novelty.
3. **One card grammar, reused everywhere.** Every bordered surface in the product (task card, KPI
   tile, recommendation card, queue row, inspector panel) draws from the same border/radius/
   surface tokens (§3.3–§3.5). `UX_AUDIT.md` §2.9 documented five visually unrelated panels on one
   page; this system exists specifically to close that gap.
4. **Derived state is visually subordinate to stored state.** Kanban lane (stored,
   `KANBAN_COLUMNS`) is the primary signal on a task card. Execution status (`launch_status`,
   live-synced) and dependency readiness (derived, never stored) are real and must stay visible,
   but they read as secondary — smaller, quieter, positioned after — never competing with the
   lane for the eye's first stop. This directly answers `UX_AUDIT.md` §2.13.
5. **No stored column for a derived state.** Binding constraint carried over unchanged from the
   brief: `Blocked` and `Running` are never added to `KANBAN_COLUMNS`. They are always computed
   (`models.is_blocked`, `launch_status`) and rendered as badges, never as a lane a task can be
   dragged into.
6. **Every token is named and centralized.** No component hardcodes a color, spacing value, or
   radius. See §12 for where the centralized definition lives in a Streamlit app.

## 2. Product layout & App Shell

### 2.1 Regions

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Command Bar  (search / palette trigger · breadcrumb · project · profile) │
├───────────┬─────────────────────────────────────────────┬───────────────┤
│           │                                               │               │
│  Sidebar  │              Central Workspace                │   Inspector   │
│  (nav)    │        (page content: Kanban, Runs, …)         │  (optional,   │
│           │                                               │  collapsible) │
│           │                                               │               │
├───────────┴─────────────────────────────────────────────┴───────────────┤
│  Execution Strip (collapsible: active runs, queue depth, system status)  │
└─────────────────────────────────────────────────────────────────────────┘
```

- **Sidebar** — persistent, grouped navigation (§2.2). Collapsible to icon-rail width, never
  fully hidden except in Focus Mode (§2.6).
- **Command Bar** — one row, full width, replacing the redundant per-page `st.title` identified
  in `UX_AUDIT.md` §2.8. Holds: page breadcrumb/title (left), search/command-palette trigger
  (center or right), project-scope indicator and system status glyph (right).
- **Central Workspace** — the routed page content (Kanban, Runs, Execution Center, etc.).
- **Inspector** — optional right panel for task/run detail-on-demand (§9.13). Closed by default;
  opening it narrows the workspace, never overlays it, at ≥1440px (§2.4).
- **Execution Strip** — collapsible bottom region surfacing "what's running right now" and queue
  depth independent of which page is open, so execution state is never more than one glance away
  regardless of navigation. Collapsed to a single summary line by default; expands in place
  (pushes workspace up), never as a floating overlay.

This directly operationalizes the brief's required "clear separation of planning, execution, Git,
context, and system status": planning lives in the Central Workspace (Kanban), execution status
is always visible in the Execution Strip regardless of page, Git and context are workspace pages
reached through grouped sidebar sections (§2.2), and system status is a small always-visible
glyph in the Command Bar, not a page you have to navigate to.

### 2.2 Sidebar navigation groups

Replaces the flat 17-item `st.radio` (`UX_AUDIT.md` §2.7) with five labeled groups, order and
membership below. Group headers are static text, not navigable; items within a group keep
today's existing routing keys (`NAV` dict values) unchanged — this is a presentation grouping,
not a page rename.

| Group | Pages (existing `NAV` key → label) |
|---|---|
| **Overview** | `dashboard` (Обзор), `workspace_home` (Workspace Home), `executive` (Исполнительная панель) |
| **Plan** | `kanban` (Kanban), `create` (Создать задачу) |
| **Execute** | `execution_center` (Live Execution Center), `runs` (Журнал запусков), `focus` (Focus Mode), `agents` (AI-агенты) |
| **Git & Context** | `git_center` (Git Center), `workspace` (Workspace Launcher), `projects` (Проекты), `generated` (Сгенерированные задачи), `reports` (Отчёты), `context` (Глобальный контекст) |
| **Chat** | `chat` (Чат по проекту) |

Each group renders as a small uppercase caption label (`type.overline`, §3.2) followed by its
items, with `space.md` (§3.1) between groups. This is a pure layout change over the existing
`NAV` dict — no page is added, removed, or renamed.

### 2.3 Command Bar

One row, `control.height.commandbar` tall (§3.1), fixed to the top of the Central Workspace
region (not the whole viewport — the Sidebar keeps its own header, matching Streamlit's native
sidebar/main-container split). Contents, left to right:

1. Page title (replaces the per-page `st.subheader`, and the app-wide `st.title` moves here as a
   small wordmark/icon only, not a repeated headline — closing `UX_AUDIT.md` §2.8).
2. Command-palette trigger — visually a search-style input, `Mod+K` shortcut preserved unchanged
   from today's `_open_command_palette` (`app.py:1800`).
3. Project-scope indicator — same selected project the page-level `project_selector` already
   tracks, mirrored here for orientation when scrolled past the selector.
4. System status glyph — one compact indicator (queue depth + any run in a failed/attention
   state), always visible regardless of page, satisfying the "system status" separation
   requirement without a dedicated status page.

### 2.4 Breakpoint behavior

| Breakpoint | Sidebar | Central Workspace | Inspector | Execution Strip |
|---|---|---|---|---|
| **≥1920px** (27–32") | Expanded, full labels | Full width; Kanban shows all 5 columns at comfortable card width (`KANBAN_REDESIGN.md` §1) with room to spare | Opens as a true third column, ~360px, without compressing Kanban below its comfortable width | Expanded strip shows 3+ run summaries inline |
| **1728px** | Expanded, full labels | Full width; Kanban shows all 5 columns at minimum comfortable width | Opens as a third column, ~320px; may compress Kanban to its minimum card width (`KANBAN_REDESIGN.md` §1.2) | Expanded strip shows 2–3 run summaries |
| **1440px** | Collapsible to icon-rail; expanded by default | Kanban board becomes horizontally scrollable (`KANBAN_REDESIGN.md` §1.1) once the inspector or a wide execution strip is open | Opens as an overlay-like fourth-fifth of width; recommend collapsing sidebar to icon-rail first | Collapsed to single summary line by default |
| **Narrow fallback (<1280px)** | Auto-collapses to icon-rail | Kanban is horizontally scrollable by default (not optional); single-column stacking for KPI strip and recommendation cards | Opens as a full-width takeover of the workspace (temporary, closes on Escape or explicit close) | Collapsed to single summary line; tap to expand as a temporary overlay, not a push |

Streamlit cannot do true CSS container queries reliably (§12.3) — breakpoint behavior above is
implemented with Streamlit's own width-aware primitives (`st.columns` ratios that degrade
gracefully, `width="stretch"`, conditional column counts based on `st.session_state` width hints
where available) plus injected CSS `@media` rules scoped to the app's own containers. This is
achievable (§12.1) but must be verified per Streamlit version at each increment (`UX-1`
acceptance criteria).

### 2.5 Central Workspace vs. Kanban page composition

The Kanban page specifically composes, top to bottom: Command Bar (shared, §2.3) → Project
Selector (§9.4) → KPI strip (§9.5, replaces the current `st.metric` grid) → Recommendations rail
(§9.10) → Kanban board (`KANBAN_REDESIGN.md`) → collapsed by default, not stacked below the fold:
Execution Queue (§9.11, now living in the Execution Strip per §2.1, not stacked as a sixth
section on the page — this is the fix for `UX_AUDIT.md` §2.9's "five unrelated stacked panels").

### 2.6 Focus Mode

Existing behavior (`app.py:1790`, sidebar auto-collapses when `nav_page == "focus"`) is preserved
and generalized: Focus Mode is the one workspace state that may hide the Sidebar and Execution
Strip entirely, showing a single task's Inspector-equivalent detail full-width. This is the
correct existing precedent for "collapsible" regions — UX-1 through UX-5 extend the same
mechanism to the Inspector and Execution Strip rather than inventing a second collapse pattern.

## 3. Design tokens

### 3.1 Spacing scale

| Token | Value | Usage |
|---|---|---|
| `space.xs` | 4px | Icon-to-label gap, tight badge internal padding |
| `space.sm` | 8px | Within-component padding, badge-to-badge gap |
| `space.md` | 12px | Default control padding, card internal padding (compact) |
| `space.lg` | 16px | Card internal padding (comfortable), gap between cards |
| `space.xl` | 24px | Section padding, gap between Kanban columns |
| `space.xxl` | 32px | Page-level top margin, Command Bar to workspace gap |

`control.height.commandbar`: 48px. `control.height.sm` (badges, chips): 22px. `control.height.md`
(buttons, inputs): 32px. `control.height.lg` (primary launch buttons): 40px.

### 3.2 Typography scale

Streamlit's default font stack (`"Source Sans Pro", sans-serif` in recent versions) is kept —
introducing a bundled custom font is unnecessary risk for a local desktop tool and is explicitly
out of scope (§12.1). Only size/weight *roles* are specified:

| Token | Size | Weight | Usage |
|---|---|---|---|
| `type.page-title` | 20px | 600 | Command Bar page title |
| `type.card-title` | 15px | 600 | Task card title, KPI card label — replaces the `### h3` (24px) identified in `UX_AUDIT.md` §2.2 |
| `type.body` | 14px | 400 | Default body text, card metadata |
| `type.body-emphasis` | 14px | 500 | Emphasized inline text |
| `type.caption` | 12px | 400 | Secondary/meta text, timestamps, counts |
| `type.overline` | 11px | 600, uppercase, +0.04em tracking | Sidebar group headers (§2.2), section eyebrows |
| `type.mono` | 13px | 400, monospace | Run ids, branch names, commit hashes, paths — anything copy-verbatim |

The single highest-leverage change in this table is `type.card-title` at 15px replacing today's
24px `<h3>`. At the Kanban card widths specified in `KANBAN_REDESIGN.md` §1.2 this is what makes
most real task titles fit on one or two lines instead of four.

### 3.3 Surface hierarchy

| Token | Light | Dark | Usage |
|---|---|---|---|
| `surface.page` | `#F7F8FA` | `#14161A` | App background |
| `surface.sunken` | `#EEF0F3` | `#0E0F12` | Sidebar, Execution Strip background — one step recessed from the page |
| `surface.card` | `#FFFFFF` | `#1B1E24` | Task card, KPI card, recommendation card, inspector panel |
| `surface.card-hover` | `#FBFBFC` | `#20232A` | Hover state on an interactive card |
| `surface.overlay` | `#FFFFFF` | `#22252C` | Dialogs, command palette, toasts |
| `surface.selected` | `#EEF2FF` | `#1E2340` | Selected sidebar item, selected Kanban card |

### 3.4 Borders and radius

| Token | Value | Usage |
|---|---|---|
| `border.hairline` | 1px, `#E2E4E9` light / `#2A2E37` dark | Card and section borders — matches the existing `st.container(border=True)` visual language already used throughout the app, so this is a refinement, not a reinvention |
| `border.focus` | 2px, accent color, 1px offset | Keyboard focus ring (§8, `INTERACTION_MODEL.md` §3) |
| `border.emphasis` | 1px, accent color | Selected sidebar item, active tab, selected Kanban card |
| `radius.sm` | 4px | Badges, chips |
| `radius.md` | 8px | Cards, buttons, inputs |
| `radius.lg` | 12px | Dialogs, Command Bar search field |

### 3.5 Elevation

Streamlit has no first-class shadow primitive; elevation is expressed the same way
`docs/desktop/DESIGN_SYSTEM.md` §1.7 expresses it for the native app — border + background
contrast, not drop shadows — for visual consistency between the two products' calm aesthetic even
though they're built independently:

| Token | Treatment | Usage |
|---|---|---|
| `elevation.flat` | `border.hairline` only | Cards, list rows — the default for nearly everything |
| `elevation.raised` | `border.hairline` + `surface.overlay` + a genuinely subtle `box-shadow: 0 4px 16px rgba(0,0,0,0.08)` (light) / `rgba(0,0,0,0.4)` (dark) | Dialogs, command palette, toasts only — never on in-page cards |

### 3.6 Semantic colors

One accent hue, used sparingly; status colors are semantic tokens, never raw hex referenced
directly by a component:

| Token | Light | Dark | Meaning |
|---|---|---|---|
| `color.accent` | `#4F46E5` (indigo) | `#818CF8` | Primary actions, selected state, focus ring |
| `status.neutral` | `#6B7280` | `#9CA3AF` | Backlog, unconfigured, no data |
| `status.info` | `#2563EB` | `#60A5FA` | Next, Launching |
| `status.active` | `#2563EB` | `#60A5FA` | Running (paired with the live pulse indicator, §9.9) |
| `status.warning` | `#D97706` | `#FBBF24` | Requires Attention, dirty git state, waiting-on-dependency |
| `status.danger` | `#DC2626` | `#F87171` | Failed, Blocked |
| `status.success` | `#16A34A` | `#4ADE80` | Done, Completed, passing verdict, clean git state |

### 3.7 Streamlit's badge-color constraint

`st.badge` (and `st.metric`'s delta coloring) accept a **fixed enum** — `blue`, `green`, `orange`,
`red`, `violet`, `gray` — not arbitrary hex. This is a real platform constraint, not a choice: the
semantic tokens in §3.6 must map onto this fixed set rather than the exact hex values above
wherever a native `st.badge` is used (native badges, not custom-HTML ones — see §12.2 for why
custom-HTML badges are avoided). The mapping used throughout this system:

| Semantic token | `st.badge` color |
|---|---|
| `status.neutral` | `gray` |
| `status.info` / `status.active` | `blue` |
| `status.warning` | `orange` |
| `status.danger` | `red` |
| `status.success` | `green` |
| `color.accent` (rare, e.g. an "AI-assisted" tag) | `violet` |

## 4. Badge system

- One badge = one fact. Never combine two pieces of information in one badge label (e.g. not
  `"High · 3h"` — two badges, `High` and `3h`).
- Badge rows are grouped by *meaning*, per §1.4: a **planning** row (lane is implicit from the
  Kanban column itself, so this row holds priority/owner/estimate), an **execution** row
  (launch status, executor, branch, active-run link), and a **readiness** row (blocked reason or
  dependency-met confirmation) — same three clusters the existing code comment at `app.py:883–890`
  already describes, now given the column width (`KANBAN_REDESIGN.md` §1.2) to actually stay
  visually separated instead of wrapping into each other.
- Maximum 3 badges per row before overflow — a 4th+ fact moves to the card's expanded/inspector
  state (§9.13, progressive disclosure — `INTERACTION_MODEL.md` §10), never a 4th wrapped line.
- Icons are optional on badges and used only where the icon disambiguates faster than the label
  alone (owner → person icon, estimate → clock icon) — never decorative.

## 5. Button hierarchy

| Level | Streamlit mapping | Usage | Max per view |
|---|---|---|---|
| **Primary** | `st.button(type="primary")` | One decisive action per card/section — Launch, Запустить готовые | 1 |
| **Secondary** | `st.button(type="secondary")` (default) | Common actions — Workspace, Git, В очередь | 3–5 |
| **Tertiary / icon-only** | `st.button` with `icon=` and empty or minimal label, inside an already-disclosed context (expander, inspector) | Dense action rows once space allows (`KANBAN_REDESIGN.md` §2.2 expanded card, not the compact card) | No hard limit, but never the *only* way to reach a primary action |
| **Destructive** | `st.button` styled with `status.danger` treatment, always behind a confirmation (`INTERACTION_MODEL.md` §11) | Delete task, Cancel run | 1, always guarded |

A compact Kanban card (`KANBAN_REDESIGN.md` §2.1) shows **zero** buttons inline — actions live in
the expanded card or Inspector. This is the direct fix for `UX_AUDIT.md` §2.4 (five-button row in
a 190px column): the fix is not "make buttons smaller," it's "don't show eight buttons on a
compact card at all."

## 6. Icon rules

- **Material Symbols only**, via Streamlit's native `:material/name:` syntax — already used
  throughout the current codebase (`icon=":material/schedule:"`, etc.). No second icon system is
  introduced.
- Emoji are phased out of structural UI (the `🧭` app-icon, `🟢🟡🔴` health icons, `⏺` running
  dot) in favor of Material Symbols equivalents (`:material/explore:` or a wordmark,
  `:material/circle:` with semantic color, `:material/fiber_manual_record:`) so status is carried
  by the semantic color token (§3.6) plus one consistent icon family, not by which emoji happened
  to render well on the developer's OS. Emoji rendering is font/OS-dependent and outside this
  system's color control — a real risk for a "calm, professional" surface.
- Icon size: `icon.sm` 16px (inline, badges), `icon.md` 20px (buttons, nav), `icon.lg` 32px
  (empty-state illustrations only).
- Every icon-only button carries an accessible label (`st.button`'s `help=` tooltip at minimum;
  full text label wherever width allows) — never icon-only with zero text alternative.

## 7. Empty, loading, error, and success states

| State | Visual treatment | Copy pattern | Example |
|---|---|---|---|
| **Empty** | `icon.lg` + one line of body text + optional single primary action | State the absence and the one way out of it | "Очередь пуста. Добавьте задачу из рекомендаций или карточки Kanban." (existing copy at `queue_panel.py:53`, kept, just given the empty-state visual treatment) |
| **Loading** | Skeleton block matching the target component's shape (card-shaped skeleton for a loading card, not a generic spinner) where the wait is predictable (>300ms); Streamlit's native `st.spinner` for actions with unknown duration (agent launch, git operations) | No copy needed for skeletons; spinner copy names the action in progress ("Запуск агента…") | Kanban board skeleton while tasks load |
| **Error** | `status.danger` left border accent + icon + one-line cause + retry action if one exists | State what failed and, if possible, what to do next — never a raw exception string in the primary message (raw detail goes in an expandable "Подробнее") | Error Banner component, §9.14 |
| **Success** | `status.success` toast, auto-dismissing (~4s), non-blocking | Confirm the action completed; include a direct link/action to the result where relevant (e.g. "Запуск начат: `a1b2c3d4`" already links to the run — keep this pattern) | Existing `st.success(f"Запуск начат: \`{result.run_id}\`.")` at `recommendations_panel.py:111`, kept |

## 8. Dark and light theme

Streamlit 1.50+ has native light/dark theme switching (a user-facing setting); this system
defines both palettes as first-class, not light-only-with-dark-as-an-afterthought. Implementation
is the standard Streamlit mechanism: `[theme]` and, where the installed Streamlit version
supports it, a dark-mode-specific override block in `.streamlit/config.toml` for the base
`primaryColor`/`backgroundColor`/`secondaryBackgroundColor`/`textColor` tokens, plus the injected
CSS module (§12.1) for everything `config.toml` cannot express (badge backgrounds, card hover
states, the Command Bar). Every token in §3.3–§3.6 already carries both values; there is no
token in this system defined for only one theme.

## 9. Core components

Each component: purpose, anatomy, states, interactions, Streamlit feasibility, testing
expectations.

### 9.1 App Shell

- **Purpose**: the persistent frame (§2.1) every page renders inside.
- **Anatomy**: Command Bar + Sidebar + Central Workspace slot + optional Inspector + Execution
  Strip.
- **States**: default; Focus Mode (Sidebar + Execution Strip hidden, §2.6); Inspector open/closed;
  Execution Strip expanded/collapsed; narrow-fallback (Sidebar auto-collapsed, §2.4).
- **Interactions**: `Mod+K` opens command palette from any state; `Esc` closes Inspector or an
  expanded Execution Strip before it closes anything else (progressive, least-destructive-first).
- **Streamlit feasibility**: high. `st.sidebar` + a fixed-position injected-CSS Command Bar +
  `st.columns` for the Inspector split is achievable with today's Streamlit APIs
  (`st.container(horizontal=True)`, `width="stretch"`, already used in this codebase). The
  Execution Strip as a true bottom-fixed region requires CSS positioning (§12.1) since Streamlit
  has no native bottom-dock primitive — this is the shell's single highest-risk piece and is
  scoped as its own increment (UX-1) precisely because of that.
- **Testing**: `AppTest`-level — assert the right page renders under each `nav_page` value
  (existing pattern, `tests/test_app_streamlit.py`'s `_at_on_page` helper), plus a light manual/
  visual check per breakpoint at each increment (no automated visual diff until UX-6).

### 9.2 Sidebar

- **Purpose**: persistent, grouped navigation (§2.2).
- **Anatomy**: command-palette trigger button (existing, `app.py:1817`) → grouped nav items →
  registry count / mode caption (existing, `app.py:1835–1836`).
- **States**: expanded (labels visible), icon-rail (collapsed, icons + tooltip only), item
  selected/unselected, item hover.
- **Interactions**: click selects a page (existing `st.radio`-driven routing, unchanged
  mechanism); a collapse toggle switches expanded ↔ icon-rail.
- **Streamlit feasibility**: high — this is a styling and grouping pass over the existing
  `st.sidebar` + `st.radio` (`app.py:1816–1836`); the routing mechanism (`nav_page` session-state
  key, `_PENDING_KEY_MAP` staging pattern at `app.py:1773–1784`) is unchanged.
- **Testing**: existing `AppTest` navigation assertions continue to pass unmodified since routing
  keys don't change; add one test asserting every `NAV` key appears under exactly one group
  (prevents a future page from being silently un-grouped).

### 9.3 Command Bar

- **Purpose**: per-page orientation + always-available search, replacing the redundant repeated
  `st.title` (`UX_AUDIT.md` §2.8).
- **Anatomy**: page title/breadcrumb, command-palette trigger, project-scope indicator, system
  status glyph (§2.3).
- **States**: default; command palette open (existing `st.dialog`-based palette, `app.py:1843`,
  unchanged); project-scope set/unset.
- **Interactions**: click title area does nothing (not a button); click search trigger or `Mod+K`
  opens palette; click status glyph navigates to Execution Strip's expanded view.
- **Streamlit feasibility**: medium — a fixed-position top bar is CSS-positioning work (§12.1);
  the palette itself is a direct reuse of the existing `st.dialog` implementation
  (`_command_palette_dialog`, `app.py:1843–1880`), which needs no change.
- **Testing**: `AppTest` — palette open/search/select-command flow already covered by existing
  tests structurally (dialog + button click pattern); extend to assert the Command Bar's page
  title matches the active `nav_page`.

### 9.4 Project Selector

- **Purpose**: scope every downstream panel (Intelligence strip, Recommendations, Kanban board,
  Execution Queue) to one project or "all projects."
- **Anatomy**: existing `st.pills` row (`project_selector.py:41–48`) — unchanged data source
  (`models.PROJECT_IDS`) and unchanged top-3-by-activity ordering
  (`project_intelligence.rank_projects_by_activity`).
- **States**: "All projects" selected (default); one project selected; pill hover/focus.
- **Interactions**: click a pill to scope; selection persists in `st.session_state` per existing
  `key` parameter convention.
- **Streamlit feasibility**: high — this component already exists and already works; only visual
  tokens (§3) apply, no structural change, no risk.
- **Testing**: existing coverage (project selector is exercised indirectly through Kanban page
  tests) is sufficient; no new tests required beyond a visual check.

### 9.5 KPI Card

- **Purpose**: the Project Intelligence strip's individual metric tile (health, sprint progress,
  roadmap, remaining, blocked, completion — `project_intelligence_panel.py:22–40`).
- **Anatomy**: label (`type.overline`) → value (`type.page-title` weight, semantic-colored when
  the metric is a status like health) → optional help/reason caption on hover.
- **States**: normal; no-data (`—`, existing pattern already used for `sprint_progress_pct`,
  `roadmap_progress_pct`, `completion_pct` when `None`); at-risk (health = 🔴, uses
  `status.danger`).
- **Interactions**: hover shows the `help` reason text (existing `help=intel["health_reason"]`
  pattern, `project_intelligence_panel.py:25`); no click action — informational only.
- **Streamlit feasibility**: high — `st.metric(..., border=True)` already exists and is close to
  this spec; the change is purely visual-token application (`surface.card`, `type.*`) via the
  centralized CSS module, no data or structural change to
  `project_intelligence_panel.render_project_intelligence_strip`.
- **Testing**: existing `AppTest` coverage of the intelligence strip (rendered as part of Kanban
  page tests) is unaffected; no new behavioral tests needed.

### 9.6 Task Card

Fully specified in `KANBAN_REDESIGN.md` §2 (compact and expanded variants, information
hierarchy, all badge/action content). This entry cross-references rather than duplicates that
spec.

- **Purpose**: the single unit of planning + execution + readiness information for one task.
- **Streamlit feasibility**: high for the compact variant (a bordered container with tokenized
  typography/spacing — no new Streamlit primitive needed); medium for the expanded variant's
  inline action density (`KANBAN_REDESIGN.md` §2.2) — achievable with `st.container(horizontal=
  True)` and `width="stretch"` (already used at `app.py:891`), but requires careful column-ratio
  tuning per breakpoint (§12.1 risk).
- **Testing**: extend existing `AppTest` Kanban tests (`test_kanban_launcher_*`,
  `test_full_launch_flow_*`) to assert against the new component's `key=` structure once
  implemented; add a UX-6 visual snapshot test (`IMPLEMENTATION_ROADMAP.md`) since `AppTest`
  cannot assert wrap/overflow.

### 9.7 Status Badge

- **Purpose**: render one semantic fact (lane is not a badge — it's the column; this is for
  priority, launch status, verdict, git-dirty, etc.).
- **Anatomy**: optional icon + label, `radius.sm`, `control.height.sm`, one `st.badge` color from
  the §3.7 fixed enum.
- **States**: one per semantic token in §3.6 — neutral/info/active/warning/danger/success. Active
  (Running) additionally carries a subtle pulse/live indicator (§9.9 shares this treatment).
- **Interactions**: none — badges are not clickable. Where a badge's information should be
  actionable (e.g. "3 tasks blocked" should navigate to those tasks), use a button styled as a
  badge, not a real badge, so the affordance is honest.
- **Streamlit feasibility**: high — `st.badge` is a native, stable primitive already used
  throughout the app (`app.py:893`, `902`, etc.); this is a token-mapping exercise (§3.7), not new
  engineering.
- **Testing**: `AppTest` can assert badge presence/label/color via the rendered `st.badge` element
  tree — extend existing assertions incrementally as each badge site is migrated to the new
  token mapping.

### 9.8 Dependency Indicator

- **Purpose**: answer "is this task blocked, and by what" or "are its dependencies satisfied" —
  the readiness cluster from §1.4.
- **Anatomy**: `status.danger` badge "Заблокировано" + inline caption naming unmet dependencies
  (existing pattern, `app.py:916–922`) **or** `status.success` badge "Зависимости выполнены"
  (existing, `app.py:923–924`) when dependencies exist and are all met. Renders nothing when a
  task has no dependencies at all (existing behavior, implicit `elif`) — no empty indicator.
- **States**: blocked (names unmet deps); satisfied (names nothing further, just confirms); no
  dependencies (renders nothing, not an empty state — there's nothing to disclose).
- **Interactions**: clicking an unmet-dependency name (currently plain caption text, `app.py:919`)
  should become a real link/action that opens that dependency task in the Inspector (§9.13) —
  today it's inert text; this is the one behavioral upgrade this component needs, tracked in
  `INTERACTION_MODEL.md` §9.
- **Streamlit feasibility**: high for the badge/caption rendering (already works); medium for the
  "click a dependency name to open its Inspector" upgrade, since it requires wiring a per-name
  button/link rather than a caption string — small, contained change.
- **Testing**: `models.is_blocked` and `models.unmet_dependencies` are already pure-function
  tested at the model layer; add one `AppTest` asserting the blocked badge renders exactly when
  `is_blocked` is true, and (once wired) that clicking a dependency name opens that task's
  Inspector.

### 9.9 Run Progress

- **Purpose**: show execution progress for a task/run in flight — the `st.progress` bar +
  stage-text pattern already used at both the Task Card (`app.py:879–881`) and the Execution
  Center card (`app.py:1304–1308`).
- **Anatomy**: progress bar (`progress/100`) + stage label + percentage, exactly the existing
  format string (`f"{stage} — {progress}%"`).
- **States**: not started (0%, `Created` stage); in progress (any intermediate `EXECUTION_STAGES`
  value, `models.py:165–175`); complete (100%, terminal stage); the Execution Center variant
  additionally shows a heartbeat-staleness warning (existing, `app.py:1327–1332`) when a running
  session's last heartbeat is old.
- **Interactions**: none — read-only. A stale heartbeat is an informational warning, never an
  auto-action.
- **Streamlit feasibility**: high — `st.progress` is native and already used exactly this way in
  two places; this component formalizes it as one shared function instead of two separately
  inlined call sites (`app.py:881` and `app.py:1304–1308`), which is itself a small
  reuse/simplification win independent of the visual redesign.
- **Testing**: existing run-progress assertions in `tests/test_execution_center_ui.py` continue
  to apply; add one test asserting the Task Card and Execution Center card render progress
  through the same shared function once unified (regression guard against the two drifting
  apart again).

### 9.10 Recommendation Card

- **Purpose**: the "why this task next" surface (`recommendations_panel.py`).
- **Anatomy**: title, project + priority caption, reasons list, dependency summary, impact
  ("unblocks N tasks"), readiness caption, queued-state caption if applicable, two-button row
  (В очередь / Запустить) — all existing content (`recommendations_panel.py:47–113`), retokenized.
- **States**: ready-and-unqueued (both buttons active); already-queued (enqueue button disabled,
  existing `queue_disabled` logic at `recommendations_panel.py:73`); empty (existing
  `st.info("Нет рекомендованных незаблокированных задач.")`, retokenized as an Empty State, §9.15).
- **Interactions**: "В очередь" adds to queue and reruns; "Запустить" enqueues-then-launches in
  one action (existing combined behavior, `recommendations_panel.py:92–107`) — preserved exactly,
  since it's a deliberate existing design (queue is always the system of record, even for an
  immediate launch).
- **Streamlit feasibility**: high — purely a retokenization of an existing, working component; no
  behavioral change.
- **Testing**: existing recommendation-flow tests (via Kanban page `AppTest` coverage) are
  unaffected; no new behavioral tests required.

### 9.11 Queue Item

- **Purpose**: one row in the Execution Queue (waiting or ready state).
- **Anatomy**: state glyph (🟢 ready / 🟡 waiting today, migrating to Material Symbols per §6) +
  task title + project or wait-reason + remove action (existing, `queue_panel.py:96–110`).
- **States**: ready (`STATE_READY`); waiting (`STATE_WAITING`, shows `entry.get("reason")`).
- **Interactions**: "Убрать" (remove) dequeues immediately — this is a low-cost, easily-reversed
  action (re-adding a task to the queue is one click from its Recommendation Card or Task Card),
  so it intentionally does **not** get a confirmation dialog (`INTERACTION_MODEL.md` §11
  distinguishes this from genuinely destructive actions like task deletion).
- **Streamlit feasibility**: high — existing `st.columns([4,1])` row pattern, retokenized.
- **Testing**: existing queue tests unaffected; no new tests required.

### 9.12 Recommendation Card / Queue Item shared row grammar

Both 9.10 and 9.11 adopt the same card/row border, radius, and typography tokens as the Task Card
so that, per Principle 3 (§1), the Kanban page's Recommendations rail, board, and Execution Strip
read as one designed system rather than three independently-styled widgets — directly answering
`UX_AUDIT.md` §2.9.

### 9.13 Inspector Panel

- **Purpose**: full task/run detail on demand, replacing the current in-card `st.expander`
  ("Действия," `app.py:940`) as the primary home for deep detail, per the progressive-disclosure
  model in `INTERACTION_MODEL.md` §10. This is the **new** component this system introduces (no
  direct existing equivalent) — everything currently inside the Actions expander (metadata,
  goal/notes, repository/workspace paths, the 5+3 button action grid, agent launcher, timeline,
  dependency graph) moves here.
- **Anatomy**: header (title + close) → status/badge summary (compact, mirrors the Task Card's
  badge rows) → tabbed or sectioned body (Overview / Launch / History / Dependencies) → primary
  action bar pinned at the bottom (Launch, Workspace, Git, Report, Queue — same actions as today,
  just given real width instead of a 190px expander).
- **States**: closed (default); open, task-scoped; open, run-scoped (from the Execution Center);
  loading (data not yet available, e.g. git status).
- **Interactions**: opens on task-card click (not the status selector or delete button — those
  stay inline, `INTERACTION_MODEL.md` §2); closes on explicit close, `Esc`, or selecting a
  different task (replaces content, doesn't stack); every action currently in the Actions
  expander (`app.py:952–1031`) is reachable here with the exact same underlying calls
  (`launch.open_folder_at`, `execution_queue.enqueue`, `render_agent_launcher`, etc.) — this is a
  **layout** migration, not a behavior rewrite.
- **Streamlit feasibility**: medium — a persistent right-side panel driven by
  `st.session_state`'s existing `pending_*`/selection pattern (`app.py:1773–1784`) is achievable;
  the risk is state-management complexity (which task is "open," keeping it in sync with which
  card was clicked across reruns) rather than rendering. Scoped as its own increment (UX-5)
  specifically because of this risk.
- **Testing**: new `AppTest` coverage asserting: clicking a task card sets the Inspector's
  selected-task state; the Inspector renders the same data the old expander did (a direct
  migration-parity test); every existing Actions-expander button test
  (`test_kanban_launcher_*`, `test_full_launch_flow_*`, etc.) is re-pointed at the Inspector's
  equivalent `key=` and continues to pass.

### 9.14 Empty State

- **Purpose**: shared rendering for "nothing here yet" across Kanban columns, Execution Queue,
  Recommendations, Runs log, etc.
- **Anatomy**: per §7 — icon + one line + optional single action.
- **States**: n/a (it *is* a state of its parent component).
- **Interactions**: the optional action is the one way out (e.g. "Создать задачу" from an empty
  Backlog column).
- **Streamlit feasibility**: high — formalizes existing scattered patterns (`st.caption("Пусто")`
  at `app.py:2450`, `st.info(...)` at `recommendations_panel.py:41`, `st.caption(...)` at
  `queue_panel.py:53`) into one shared function so every empty state looks the same instead of
  three different treatments (`st.caption` vs `st.info` vs plain caption) as today.
- **Testing**: existing empty-state assertions (e.g. `test_runs_page_renders_empty_state`)
  continue to apply; add one shared-component unit test once the empty-state function exists.

### 9.15 Error Banner

- **Purpose**: shared rendering for recoverable failures (launch validation errors, git-status
  read failures, report-parse failures) — today handled ad hoc via `st.error`/`st.warning` at
  many call sites (e.g. `app.py:982`, `1389`, `1422`).
- **Anatomy**: per §7 — left accent border in `status.danger`, icon, one-line cause,
  expandable "Подробнее" for raw detail, retry action where one exists.
- **States**: warning-level (recoverable, e.g. dirty git state) vs. danger-level (blocking, e.g.
  launch validation failure) — both route through this component with different semantic tokens,
  not different components.
- **Interactions**: dismissible; retry action (if present) re-attempts the failed operation
  without a full page reload.
- **Streamlit feasibility**: high — `st.error`/`st.warning` are native and already used
  extensively; this component formalizes their usage into one consistent shape (icon +
  one-line + optional detail expander) rather than each call site inventing its own message
  format.
- **Testing**: existing error-path tests (e.g.
  `test_kanban_launcher_blocking_validation_error_cannot_be_bypassed`) continue to apply against
  the same underlying `st.error` calls; no behavioral change, only a shared formatting wrapper.

### 9.16 Confirmation Dialog

- **Purpose**: gate destructive actions (`INTERACTION_MODEL.md` §11) — task deletion, run
  cancellation.
- **Anatomy**: `st.dialog`-based (same primitive as the existing command palette,
  `app.py:1843`) — title naming the action, one-line consequence statement, explicit confirm
  (danger-styled) + cancel buttons. Never a bare checkbox-then-button with no dialog, **except**
  where that pattern already exists and is defensible: run cancellation today uses an inline
  "Подтвердить" checkbox + Cancel button with server-side re-validation
  (`app.py:1377–1399`, deliberately commented as defense-in-depth against `AppTest.click()`
  bypassing a `disabled=` attribute) — this pattern is preserved as-is for Cancel (it already
  meets the bar), while task deletion (currently a bare button with **no** confirmation at all,
  `app.py:1046–1048`) is upgraded to a full dialog, since deleting a task is unrecoverable and
  currently one accidental click away.
- **States**: closed; open, awaiting confirmation; confirmed (action executes); cancelled (no-op).
- **Interactions**: confirm executes and closes; cancel or `Esc` closes with no effect; the
  confirm button is danger-styled (§5) and is the only path to the destructive action — no
  keyboard-Enter-to-confirm-by-default, to avoid a stray Enter deleting a task.
- **Streamlit feasibility**: high — direct reuse of the existing `st.dialog` mechanism.
- **Testing**: new test for task deletion asserting the dialog appears before deletion and that
  `delete_task` (`app.py:333`) is not called until confirmed — mirroring the existing
  server-side re-validation test pattern already used for Cancel
  (`test_kanban_launcher_blocking_validation_error_cannot_be_bypassed` as the structural template).

## 10. Interaction model cross-reference

Click behavior, keyboard navigation, filtering/search, drag-and-drop feasibility, and progressive
disclosure are specified in full in `INTERACTION_MODEL.md`. This document defines the visual and
structural contract those interactions operate on; it does not duplicate the interaction spec.

## 11. Kanban cross-reference

Column widths, card variants, and the complete task-card information hierarchy are specified in
full in `KANBAN_REDESIGN.md`. §9.6 above is a pointer, not a duplicate.

## 12. Streamlit constraints

### 12.1 Where custom CSS is acceptable

One centralized CSS module (a new, single Python file — e.g. `command_center/ui/theme.py`,
exporting one `inject_theme_css()` function called once near the top of `app.py`, immediately
after `st.set_page_config`) is the **only** place custom CSS is written. It targets:

- Streamlit's stable `data-testid` attributes (`[data-testid="stSidebar"]`,
  `[data-testid="stAppViewContainer"]`, `[data-testid="stMetric"]`, etc.) — these are the closest
  thing Streamlit has to a stable styling hook, though they are **not** a versioned public API
  (§12.4 risk).
- Structural layout the App Shell needs and Streamlit has no primitive for: the fixed Command Bar
  (§9.3), the bottom-docked Execution Strip (§9.1), the Inspector's push-not-overlay behavior at
  wide breakpoints (§2.4).
- Token application: mapping §3's spacing/typography/surface/color tokens onto the components
  that need them beyond what `st.badge`'s fixed enum (§3.7) or `st.metric(border=True)` already
  provide natively.

No other file in the codebase injects CSS. This directly prevents the "another large monolithic
`app.py`" failure mode named in the mission brief (§12.5) by giving styling exactly one owned,
testable seam instead of letting every page accumulate its own inline `st.markdown(unsafe_allow_
html=True)` block.

### 12.2 Where HTML injection should be avoided

- **No `unsafe_allow_html=True` inside page-routing code** (the `elif page_key == ...` blocks in
  `app.py`). All HTML injection is confined to `theme.py` (§12.1) and, where a component
  genuinely needs custom markup beyond CSS-on-native-widgets (e.g. a pulse-animation dot for
  "Running" state, §3.6), a small number of named, tested helper functions in the same module —
  never inline `st.markdown(f"<div>...</div>", unsafe_allow_html=True)` scattered through card
  renderers.
- **Never inject HTML built from unsanitized task/user data.** Task titles, goals, notes, and
  branch names are user-authored strings; if any future component renders them through
  `unsafe_allow_html`, they must be escaped first. Today's implementation never does this (all
  user data goes through `st.markdown`/`st.caption`/`st.write` without `unsafe_allow_html`, or
  through `st.badge`/`st.metric` labels, which Streamlit escapes) — this system does not change
  that, and any future exception must escape explicitly.
- **Never inject `<script>` tags or event handlers via `unsafe_allow_html`.** If genuine
  client-side interactivity beyond CSS is needed (§12.3), use Streamlit's supported custom-
  component mechanism, not ad hoc injected `<script>`.

### 12.3 Interactions not realistically reliable in Streamlit

- **True drag-and-drop** (dragging a Kanban card between columns) is not reliably achievable
  with Streamlit's rerun-on-every-interaction model without a custom bidirectional component
  (significant engineering investment, its own state-sync failure modes, and fragile across
  Streamlit upgrades). `INTERACTION_MODEL.md` §7 specifies the **explicit-control** alternative
  (the existing status `st.selectbox`, retained and better-positioned) as the supported
  mechanism, and defers true drag-and-drop to the native PySide6/Qt client as a candidate future
  feature — not to a future Streamlit increment.
- **Container-query-based responsive layout** (a card resizing based on its own container rather
  than the viewport) has no native Streamlit/CSS-only equivalent that works reliably inside
  Streamlit's iframe-per-widget rendering quirks in all supported browsers; breakpoint behavior
  (§2.4) instead uses viewport-width `@media` rules plus session-state width hints, which is
  coarser but reliable.
- **Sub-300ms micro-interactions** (hover-triggered previews, live-typing filter-as-you-type
  without a rerun) are constrained by Streamlit's script-rerun execution model — every
  interaction that changes rendered output triggers a full script rerun. Filtering/search
  (`INTERACTION_MODEL.md` §5, §6) is designed around debounced, explicit-submit or
  `on_change`-triggered reruns, not true instant client-side filtering, unless implemented as a
  custom component.
- **Persistent client-side keyboard shortcuts beyond what `st.button(shortcut=...)` supports**
  (already used for `Mod+K`, `app.py:1820`) are limited to whatever Streamlit's own shortcut
  registration exposes — a broader shortcut system (e.g. `j`/`k` list navigation) requires a
  custom component and is scoped as a stretch goal for UX-5, not a guarantee.

### 12.4 Versioning risk

`data-testid` selectors and Streamlit's internal DOM structure are not a versioned public
contract — they can change on a Streamlit upgrade. Mitigation: `theme.py` (§12.1) is the single
seam that would need updating, and UX-6 (`IMPLEMENTATION_ROADMAP.md`) introduces visual
regression tests specifically to catch this kind of breakage at upgrade time rather than in
production.

### 12.5 Avoiding another monolithic `app.py`

`app.py` is already 3,265 lines and growing; this system deliberately does not add to that
growth pattern:

- New rendering logic (App Shell, Command Bar, Inspector, Empty State, Error Banner,
  Confirmation Dialog) is added as new modules under `command_center/ui/`, following the existing
  precedent set by `project_selector.py`, `project_intelligence_panel.py`,
  `recommendations_panel.py`, and `queue_panel.py` — never as new inline blocks appended to
  `app.py`.
- `app.py` retains its role as router + call-site wiring only: each page's `elif page_key == ...`
  block calls into `command_center/ui/*` render functions, exactly as the Kanban page already
  does today (`app.py:2411–2472` calls `project_selector.render_project_selector`,
  `project_intelligence_panel.render_project_intelligence_strip`,
  `recommendations_panel.render_recommendations_panel`, `queue_panel.render_execution_queue_panel`
  — this system extends that pattern rather than replacing it).
- The one large existing exception, `render_task_card` (currently ~190 lines inline in `app.py`,
  `app.py:862–1048`), is extracted into `command_center/ui/task_card.py` as part of
  `KANBAN_REDESIGN.md`'s implementation (UX-3) — closing the one place this precedent was not
  yet followed, rather than adding a second one.
