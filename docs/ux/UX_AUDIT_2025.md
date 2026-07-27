# AI Command Center — UX Audit & Redesign (2025, post-UX-1)

Status: **evidence-based audit of the *current* app + a concrete redesign to world-class.**
Baseline: `UX_AUDIT.md` (commit `8589069`, `app.py` = 3265 lines) is **stale** — the app is now
5193 lines and UX-1 (design tokens, App Shell, grouped sidebar, command bar, native theme,
styled Home dashboard) has landed. This document audits what the app looks like *today* and
proposes the next redesign pass. Every finding cites a file:line so it can be re-verified.

Scope: the Streamlit web UI (`app.py`, `command_center/ui/*`). The PySide6/Qt desktop shell
(`command_center/desktop/*`, `docs/desktop/*`) is a separate initiative and is out of scope.

Grounding convention: `app.py:NNNN` = line in `app.py`; `ui/<mod>.py:NN` = line in
`command_center/ui/<mod>.py`.

---

## 0. What changed since the 2024 audit (so we don't re-litigate solved problems)

| 2024 finding (`UX_AUDIT.md`) | Current state | Verdict |
|---|---|---|
| Zero custom theming, default Streamlit look | `.streamlit/config.toml` defines light + dark themes; `ui/theme.py`, `ui/tokens.py` centralize tones | ✅ Solved |
| Flat 17-item `st.radio` sidebar | `ui/sidebar.py` groups into 4 labeled expanders; 4 pages hidden from sidebar (folded into project tabs) | ✅ Solved (see §3 for residual issues) |
| Redundant per-page `st.title` | `ui/top_bar.py` renders one command-bar header | ✅ Solved (see §3 — bar is under-built) |
| One card component, no compact variant | `ui/home_dashboard.py` adds a full `.hx-*` card grammar; `board_style.card_rail` for board rows | ⚠️ Partial — two competing card languages now (§6) |
| Kanban equal-width columns, no scroll | Kanban is now **vertically stacked** full-width containers (`app.py:4244`) | ⚠️ Regressed into a list, not a board (§6) |
| Russian UI copy, no i18n | Preserved | ✅ Keep as-is |

**Net:** the shell and theme are in place. The remaining problems are (a) two visual languages
that don't match, (b) three overlapping overview pages, (c) a command bar and inspector that
were scaffolded but never wired, (d) a Kanban that lost its board shape, and (e) a dashboard
full of non-clickable elements. These are what "world-class" requires fixing next.

---

## 1. Inactive / disabled buttons — should they be active?

Every `disabled=` site in `app.py` (17 total), with the gate condition and a verdict on whether
the disabled state is correct or a UX defect.

| # | Location | Control | Disabled when | Verdict |
|---|---|---|---|---|
| 1 | `app.py:803` | «Подтвердить и запустить» (task launch confirm) | not confirmed ‖ not `prep.launchable` ‖ warnings unack'd ‖ provider unavailable | ✅ Correct — defense-in-depth, re-checked server-side (`app.py:828`) |
| 2 | `app.py:1387` | «Запустить» (Execution Center ad-hoc launch) | not ready (confirm + sensitivity ack + instruction non-empty + provider + not codex-on-checkout) | ✅ Correct, but see §2 — the Codex path shows an `st.error` *and* disables, double-punishing |
| 3 | `app.py:1677` | (project launcher) | `not workspace_path` | ✅ Correct |
| 4 | `app.py:1880` | «Папка» (exec card) | `not session["workspace_path"]` | ✅ Correct |
| 5 | `app.py:1887` | «Терминал» (exec card) | `not session["workspace_path"]` | ✅ Correct |
| 6 | `app.py:1898` | «Задача» (exec card) | `real_task is None` | ✅ Correct — ad-hoc runs have no task |
| 7 | `app.py:1907` | «Отчёт» (exec card) | `not session["report_path"]` | ✅ Correct |
| 8 | `app.py:1915` | «Отменить» (exec card) | `not cancel_ack` (confirm checkbox) | ✅ Correct — re-checked server-side (`app.py:1923`) |
| 9 | `app.py:2430` | «Исправить (N)» (attention bulk) | `not chosen` (nothing selected) | ✅ Correct |
| 10 | `app.py:2445` | «Скрыть выбранные (N)» (attention bulk) | `not chosen` | ✅ Correct |
| 11 | `app.py:2517` | «Задача» (attention row) | `session.get("task_id") is None` | ✅ Correct |
| 12 | `app.py:2626` | (queue panel launch) | `entry.get("task_id") not in tasks_by_id` | ✅ Correct — stale task ref |
| 13 | `app.py:2710` | (kanban card launch) | `not gate.allowed` | ✅ Correct — gate is an affordance (`ui/live_board.py:18`) |
| 14 | `app.py:2858` | (task detail launch) | `not gate.allowed` | ✅ Correct |

**Verdict: no disabled button is wrongly disabled.** The gates are consistent and re-checked
server-side. The only friction here is *discoverability*: a disabled launch button shows no
reason on its own — the operator must find the gate explanation elsewhere on the card. The
`live_board` gate already produces a reason code (`waiting_dependency`, `workspace_busy`, …);
it should render as the button's `help=` tooltip and as a one-line caption directly under the
disabled button, not only inside an expander.

**Real "should be active but effectively dead" elements (not `disabled=`, but inert):**

| Element | Location | Problem |
|---|---|---|
| «Инспектор» popover | `ui/top_bar.py:23` → `ui/inspector.py:15` | Opens a **placeholder stub** ("Контекстные детали появятся здесь в следующей фазе"). A top-bar control that opens an empty box reads as broken. |
| AI Supervisor «Active» badge | `ui/home_dashboard.py:322` | Hardcoded `Active`/green dot — never reflects any real state. Always-on green status is the classic "trust-eroding" pattern. |
| KPI tiles (5) | `ui/home_dashboard.py:186` | Pure HTML, not clickable. A KPI tile that says "3 attention" should deep-link to the attention bucket. |
| Kanban overview columns | `ui/home_dashboard.py:273` | Counts are inert. Should link to Kanban filtered to that column. |
| Queue rows | `ui/home_dashboard.py:218` | Inert. Should open the run in Execution Center. |
| Health gauge | `ui/home_dashboard.py:285` | Inert SVG. Should link to project health detail. |
| `st.metric` tiles (executive) | `app.py:3832–3836` | Inert. Same problem as KPI tiles. |

---

## 2. Redundancy — pages to merge, fold into lists/tabs, or demote

The app has **20 nav keys** (`app.py:183`), 16 surfaced across 4 sidebar groups, 4 hidden into
project tabs. The consolidation is partial: three overview surfaces and two run monitors still
compete.

| Redundancy | Pages | Action |
|---|---|---|
| **Three overview pages** | `dashboard` (styled dark KPI/gauge/queue), `executive` (flat `st.metric` + bar chart + blocked list + agent-run metrics), `workspace_home` (read-only cross-project repo/run/artifact summary) | **Merge into one dashboard with tabs.** `dashboard` becomes the landing page with 2 tabs: «Обзор» (today's styled KPIs + queue + health) and «Аналитика» (the executive's project-status grid, priority chart, agent-run metrics, blocked list). `workspace_home` becomes a third tab «Репозитории и артефакты» (or a tab inside the Projects «Все» cross-project view, since it is already cross-project and read-only). Demote `executive` and `workspace_home` from the sidebar — keep them reachable by URL/command palette for deep links, like the already-hidden chat/generated/reports/context. |
| **Two+ run monitors** | `execution_center` (live board, 4 buckets), `runs` (filterable journal with per-run expanders + manual correction), `timeline` | Keep `execution_center` as the live monitor and `runs` as the historical journal — they have distinct jobs. **Fold `timeline` into `runs`** as a toggle/view switch (list ↔ timeline) rather than a separate page; a timeline of the same runs is a view, not a destination. |
| **Portfolio pair** | `portfolio` (execution), `portfolio_overview` (overview) | **Merge into one Portfolio page with two tabs** («Обзор», «Исполнение»). Two sidebar entries for one feature is exactly the split the 2024 audit warned about. |
| **Kanban page overload** | `kanban` hosts: project selector, project-intelligence strip, recommendations panel, backlog reconcile panel, priority filter, 5 vertical column containers, execution queue panel (`app.py:4201–4273`) | The Kanban page is now a scrolling stack of 6 panels *before* the board. **Move recommendations + backlog reconcile into a collapsible «Планирование» expander above the board**, default-collapsed, so the board is the first thing visible. The queue panel belongs in the Execution Strip (§3), not under Kanban. |
| **Hidden pages still in command palette** | `chat`, `generated`, `reports`, `context` are hidden from sidebar (`ui/sidebar.py:45`) but `build_commands` (`app.py:3326`) lists **every** NAV key | Either drop them from the palette (they live in project tabs now) or label them «(в проекте)» so the operator isn't sent to a standalone page that duplicates the project tab. |

**Sidebar target after consolidation:** from 16 entries to ~10:

```
Основное      Обзор · Kanban · Execution Center · Проекты · Git Center
Планирование  Волны · Создать задачу
Аналитика     Журнал запусков · AI-агенты · Портфель
Режимы        Focus Mode
```

---

## 3. Navigation, transitions, and the App Shell — what's scaffolded but not built

The App Shell (`ui/shell.py`) composes `top_bar` + `sidebar`. The sidebar is good; the command
bar is a skeleton.

### 3.1 Command Bar (`ui/top_bar.py`) — under-built vs. the design spec
`DESIGN_SYSTEM.md §2.3` promised: page title, command-palette trigger, **project-scope
indicator**, **system-status glyph**. What shipped: title + caption + an Inspector popover
(`ui/top_bar.py:17`). The palette trigger lives only in the sidebar (`ui/sidebar.py:92`); there
is no project-scope indicator and no status glyph. On a wide desktop the top bar is mostly empty.

**Fix:** add to the top bar, right-aligned: (a) a «Поиск» button mirroring the sidebar palette
trigger, (b) the currently-selected project (the same value `project_selector` tracks, mirrored
for orientation when scrolled past the page-level selector), (c) a status glyph — queue depth +
a dot if any run is in an attention state. These three make the bar earn its width and satisfy
the "system status is always one glance away" requirement without a dedicated status page.

### 3.2 Inspector — placeholder
`ui/inspector.py:15` is an inert stub surfacing from the top-bar popover. **Wire it** to the
currently-selected task/run: clicking a Kanban card or an execution row should populate the
inspector with that entity's detail (deps, run history, git status, report verdict) without
navigating away. This is the single highest-leverage interaction upgrade — it converts the app
from "navigate to see detail" to "select to see detail" (Linear/Vercel pattern).

### 3.3 Execution Strip — missing
`DESIGN_SYSTEM.md §2.1` + `IMPLEMENTATION_ROADMAP.md` UX-1 called for a collapsible bottom dock
surfacing active runs regardless of page. It did not ship. Today, execution state is invisible
the moment you leave Execution Center — the core friction the operator reported ("страница
медленно обновляется", "процент не меняется"). **Add the strip**: a slim bottom bar with
"▲ N запущено · M в очереди · K требуют внимания", expandable to show live run rows. Implement
it as a `st.fragment` that reruns on its own cadence (§5) so it updates without reloading the
page behind it.

### 3.4 Breadcrumbs / "where am I"
The active sidebar item uses `type="primary"` (`ui/sidebar.py:113`) — good. But collapsed groups
auto-expand only for the current group; there is no breadcrumb in the command bar and no
`st.page_link`-style URL affordance. The URL carries `?page=<key>` (`ui/sidebar.py:72`), which is
excellent for bookmarks/deep links — preserve it. Add a lightweight breadcrumb in the command
bar: `Проекты / <project> / Отчёты` when inside a project tab, so the operator never loses
context after collapsing the sidebar.

### 3.5 The full-page rerun flash (the operator's reported pain)
Streamlit reruns the whole script on every interaction and on `st.rerun()`. The 5-second
auto-refresh and the "page visibly blinks" complaint are this rerun scope, not slow data.
**Fix with `st.fragment` (Streamlit ≥1.33):** wrap the live board, the Execution Strip, and any
auto-polling region in `@st.fragment(run_every=...)` so only that fragment reruns on the poll
cadence; the rest of the page stays painted. This is the single most impactful performance/UX
fix and it is a ~10-line change per polling region. Pair with CSS `transition` on cards (§6) so
the repaint is a fade, not a flash.

---

## 4. Dashboard audit — missing, redundant, non-clickable

### 4.1 The Home dashboard (`render_home_dashboard`, `ui/home_dashboard.py`)
**Strengths:** real data (counts from live task/run state, not mock deltas — module docstring is
explicit), sparklines from real series, dark/light variants, CSP-safe inline SVG.

**Defects:**

| Defect | Detail | Fix |
|---|---|---|
| **Nothing is clickable** | KPI tiles, kanban-overview columns, queue rows, health gauge, supervisor card are all inert HTML | Make each a deep link: KPI «Требуют внимания» → Execution Center attention bucket; kanban column count → Kanban filtered to that status; queue row → Execution Center with that run highlighted (`pending_exec_center_run` already exists, `app.py:3310`). |
| **AI Supervisor always "Active"** | `ui/home_dashboard.py:322` hardcodes green "Active" | Drive from real state: if no agents configured/no runs → "Ожидает"; if any run failed → amber "Требует внимания"; only green when ≥1 run healthy and none failed. Never show green for an empty system. |
| **No empty states** | With zero tasks/runs, KPI tiles show "0" with no guidance | Add a one-line empty state per tile ("Нет активных задач — создайте первую" with a button to `create`). |
| **No deltas** | Module docstring honestly says it won't fabricate "+3 from yesterday" | If run history is available (it is — `agent_runner.load_runs`), compute real 24h deltas for "Запусков сегодня" vs yesterday. Real deltas are world-class; absence reads as unfinished. |
| **Two card languages** | `.hx-card` (16px radius, `#141b2e` bg, custom) vs native `st.container(border=True)` everywhere else | §6 — pick one. |

### 4.2 The Executive panel (`app.py:3819`)
A second dashboard built from raw `st.metric` + `st.bar_chart` + `st.container(border=True)`.
It duplicates the Home dashboard's job (task counts, project status, blocked tasks) and adds
agent-run metrics + open findings. **It is the Home dashboard's «Аналитика» tab in substance.**
Keeping it as a separate page forces the operator to learn two layouts for the same numbers.

### 4.3 What the dashboard is missing (world-class baseline)
- **A "next action" prompt** — the executive panel already has `render_next_task_callout`
  (`app.py:3822`); promote it to the Home dashboard's hero, above the KPI row. The one thing an
  operator opens the dashboard for is "what should I do next".
- **Recent activity feed** — last 5 runs/events as a compact list with status dots; today this
  lives only in the full Журнал.
- **Keyboard reachability** — Mod+K palette exists; add `/` focus to the project selector and
  `g` then letter for go-to-page (Linear/GitHub pattern). Streamlit supports `shortcut=` on
  buttons (`ui/sidebar.py:95` already uses it).

---

## 5. User scenarios — friction walk

| Scenario | Path today | Friction | Fix |
|---|---|---|---|
| **"What needs my attention right now?"** | Open sidebar → Execution Center → scroll to «Требуют внимания» bucket → select rows → «Исправить» | Attention is invisible from every other page; no global glyph | Status glyph in command bar (§3.1) + Execution Strip (§3.3); clicking the glyph jumps straight to the attention bucket |
| **"Launch this ready task"** | Kanban → find card → open launch gate expander → confirm → launch | The launch button is disabled with the reason buried in an expander | Surface gate reason as button `help=` + one-line caption (§1) |
| **"Why did this run fail?"** | Execution Center → expand run card → read error → «Отчёт» toggle → scroll report | Many steps; the report verdict and blocker count aren't on the card face | Show verdict badge + top finding on the card face; inspector (§3.2) shows full detail on click |
| **"See everything for one project"** | Projects → select project → 8 tabs | Good (the tab consolidation works); but the cross-project «Все» view is just a list of border-boxes with counts | Make «Все» a real portfolio grid: clickable cards → open project; add the workspace_home content as a tab here |
| **"Create a task and run it"** | Create → fill form → launch → switch to Execution Center to watch | Two pages, no continuity | After launch, auto-navigate to Execution Center with the new run highlighted (the `pending_exec_center_run` mechanism exists — wire it to the launch success path) |
| **"Re-run a failed task"** | Execution Center attention → select → «Исправить» | Works well; the bulk «Выбрать все» + «Исправить (N)» is genuinely good | Keep; add a confirm toast (`_ATTENTION_FLASH_KEY` exists — surface it as a status notification, not just a flash) |
| **"Compare projects"** | Portfolio Overview OR Projects «Все» OR Workspace Home | Three places, three layouts | One portfolio view (§2) |
| **"Find a specific run from yesterday"** | Журнал запусков → set date filter → scroll | Filters are good (project/agent/status/verdict/date/task) | Keep; add a search-by-text box over run stdout/title |

---

## 6. Visual style — two languages, one product

Today the app speaks **two visual languages**:

1. **The Home dashboard** (`ui/home_dashboard.py`): custom dark "command-center" CSS —
   indigo/teal/violet accents (`_ACCENTS`, line 26), 16px card radius, `#0b1020`/`#141b2e`
   surfaces, KPI tiles with accent halos, sparklines, donut gauge. Polished, but self-contained
   and only on one page.
2. **Every other page**: native Streamlit theme from `config.toml` — GitHub-like palette
   (`#0969da`/`#0d1117`), `baseRadius="medium"`, `st.container(border=True)` cards,
   `st.metric`/`st.badge`/`st.bar_chart`. Calm and consistent, but visually unrelated to the
   Home dashboard.

The moment you navigate from **Обзор** to **Kanban** the product looks like a different app.
World-class apps have **one** visual language. Pick one direction:

**Option A (recommended): unify on the native theme + tokens, retire the custom dark CSS.**
Make `home_dashboard` render with `st.container(border=True)`, `st.metric`, `st.badge`, and the
existing `tokens.py` tones instead of `.hx-*` classes. Both light and dark then work everywhere
(they already do in `config.toml`), the product looks like one piece, and maintenance drops
(no duplicated palette). The donut gauge and sparkline stay as inline-SVG helpers, but read
tokens for color. This is less visually distinctive but is what Linear/GitHub/Vercel do —
restraint reads as mature.

**Option B: commit to the dark "command center" aesthetic app-wide.**
Set `config.toml` dark theme as the default and carry the `hx-*` card grammar to every page
via a single `cards.css` injected by `theme.py`. Higher visual ambition, higher cost, and
Streamlit's native widgets (selectbox, tabs, expander) will still render in their own chrome —
so you get a custom look only on the custom surfaces, reintroducing the split.

**Recommendation: A.** It is lower-risk, matches the "calm, not decorative" principle in
`DESIGN_SYSTEM.md §1`, and the existing token/theme infrastructure already supports it.

### 6.1 Card grammar
Adopt **one** card contract (already specified in `DESIGN_SYSTEM.md §3.3–§3.5`): border 1px,
radius = `baseRadius`, surface = theme secondary bg, no shadow except on hover. Apply to task
cards, KPI tiles, queue rows, run rows, inspector. The `.hx-*` classes and `st.container(border=True)`
should converge — if A is chosen, `.hx-*` is removed and `st.container(border=True)` becomes the
single card primitive.

### 6.2 Kanban — restore the board
`app.py:4241–4261` stacks the 5 columns vertically, each full-width. The comment explains why
(horizontal `st.columns` compressed cards). But a vertical Kanban is a list grouped by status —
it loses the board's core affordance: seeing flow across stages at a glance. **Restore a
horizontal board** with `st.columns(5)` at ≥1440px and horizontal scroll below
(`KANBAN_REDESIGN.md §1.1` already specifies this). Use `st.container(border=True, height=...)`
per column with internal vertical scroll so cards don't compress horizontally. Keep the
vertical stack only as the narrow-width fallback.

### 6.3 Motion / transitions
Streamlit reruns blank the viewport, so transitions matter most for perceived quality:
- **CSS transitions** on cards/buttons: `transition: border-color .15s, box-shadow .15s,
  transform .1s` and a subtle `:hover` lift on interactive cards. Inject once in `theme.py`
  (the one CSS-allowed module per UX-1's boundary).
- **Skeleton placeholders** during fragment reruns: a CSS shimmer on the board region while the
  fragment re-fetches, instead of a blank flash.
- **Fragment isolation** (§3.5) so only the polling region repaints — the biggest perceived-
  performance win.
- **Status transitions**: when a run moves from Running → Completed, flash the row green once
  (the `_ATTENTION_FLASH_KEY` pattern generalizes). Avoid ambient animation on idle elements
  (world-class apps are still, not twitchy).

---

## 7. Proposed redesign — the world-class target

Building on the landed UX-1 shell, the next pass is **UX-2: Interactive shell + unified visual
language**, scoped to ship without data-model or runtime changes.

### 7.1 Information architecture (target)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Command Bar  [wordmark]  [breadcrumb]        [🔍 Mod+K] [project] [●N] │
├───────────┬─────────────────────────────────────────────┬───────────────┤
│ Sidebar   │  Central Workspace                           │  Inspector    │
│ (grouped) │  (Dashboard tabs / Kanban board / Runs / …)  │  (on demand)  │
│           │                                             │               │
├───────────┴─────────────────────────────────────────────┴───────────────┤
│ Execution Strip  ▲ 2 запущено · 1 требует внимания            [expand ▼] │
└─────────────────────────────────────────────────────────────────────────┘
```

- **Dashboard** = 3 tabs: «Обзор» (styled KPIs + next-action hero + activity feed + health),
  «Аналитика» (absorbs executive), «Репозитории и артефакты» (absorbs workspace_home).
- **Kanban** = horizontal board, recommendations/reconcile collapsed above, queue moved to the
  Execution Strip.
- **Runs** = list + timeline toggle (absorbs `timeline`).
- **Portfolio** = 2 tabs (absorbs portfolio + portfolio_overview).
- **Projects** = unchanged 8-tab project view (this is the best-structured page; keep it).

### 7.2 Interaction model upgrades
1. **Inspector wired** — select any task/run to populate the right panel; no navigation.
2. **Command bar completed** — search trigger, project scope, status glyph.
3. **Execution Strip** — `@st.fragment(run_every=5s)` bottom dock, expandable.
4. **Clickable dashboard** — every KPI/count/row deep-links via existing `pending_*` mechanisms.
5. **Fragment-based polling** — live board + strip rerun independently; no full-page flash.
6. **Launch continuity** — successful launch auto-navigates to Execution Center with the run
   highlighted.
7. **Gate reasons on disabled buttons** — `help=` + one-line caption.

### 7.3 Visual unification (Option A)
- Remove `.hx-*` custom CSS; rebuild Home dashboard tiles with `st.metric`/`st.badge`/
  `st.container(border=True)` reading `tokens.py` tones.
- Keep inline-SVG sparkline + donut as `ui/charts.py` helpers, color-bound to tokens.
- Add hover transitions + skeleton shimmer via one `theme.py` CSS block.
- Single accent: the existing `primaryColor` from `config.toml` (blue). Retire the 7-hue
  `_ACCENTS` palette in favor of the semantic tones (`tokens.py`) + one accent.

### 7.4 What stays (do not break)
- Russian UI copy, no i18n layer.
- `nav_page` session-state routing + `?page=` URL deep links.
- All widget `key=`s (tests drive by key).
- The `live_board` gate reason-code vocabulary.
- The attention triage + «Исправить» flow (genuinely good).
- The 8-tab project view.

---

## 8. Implementation plan (increments, each independently shippable)

Each increment is scoped to not break the `AppTest` suite or change data/runtime behavior.

| Increment | Scope | Key files | Acceptance |
|---|---|---|---|
| **UX-2a** Fragment polling ✅ PR #57 (`f16d30d`) | Wrap live board + add Execution Strip as `@st.fragment(run_every=...)`; eliminate full-page flash | `app.py` (board region), new `ui/execution_strip.py` | Page does not blank on poll; strip updates live from any page |
| **UX-2b** Interactive dashboard ✅ PR #58 (`8075556`) | Clickable KPIs/counts/queue rows via `pending_*`; real 24h deltas; real AI-Supervisor state; empty states; next-action hero | `ui/home_dashboard.py`, `app.py:render_home_dashboard` | Every dashboard tile deep-links; no hardcoded "Active"; windowed 7-day health; real activity-log events; `AICC_OPERATOR` env greeting |
| **UX-2c** Command bar + Inspector | Top bar: search, project scope, status glyph; inspector wired to selected task/run | `ui/top_bar.py`, `ui/inspector.py`, `app.py` (card click handlers) | Inspector shows real detail; bar shows live glyph |
| **UX-2d** Visual unification | Retire `.hx-*`; rebuild dashboard on tokens/`st.container`; one card grammar; hover transitions + skeleton CSS in `theme.py` | `ui/home_dashboard.py`, `ui/theme.py`, `ui/tokens.py`, new `ui/charts.py` | Light+dark consistent across all pages; one card style |
| **UX-2e** IA consolidation | Merge executive + workspace_home into dashboard tabs; merge timeline into runs; merge portfolio pair; demote merged pages from sidebar (keep URL-reachable) | `app.py` (dispatch), `ui/sidebar.py` | 10 sidebar entries; no content lost; deep links still resolve |
| **UX-2f** Kanban board restore | Horizontal 5-col board with per-column scroll at ≥1440px, vertical fallback below; collapse reco/reconcile above | `app.py:4198`, `ui/board_style.py` | Board shows 5 columns at once on desktop; cards legible |

Sequence 2a → 2b → 2c → 2d → 2e → 2f. 2a delivers the operator's reported pain fix first
(no more blink/slow refresh); 2e is the biggest IA change and lands once the shell is interactive.

---

## 9. Appendix — evidence index

- Nav keys (20): `app.py:183`
- Sidebar groups + hidden pages: `ui/sidebar.py:32,45`
- Command palette: `app.py:3321,3353`
- Home dashboard: `app.py:3798` → `ui/home_dashboard.py` (CSS `:63`, KPI `:186`, gauge `:285`,
  supervisor `:322`)
- Executive panel: `app.py:3819`
- Workspace Home: `app.py:3806` → `render_workspace_home_page` `app.py:3180`
- Kanban (vertical stack): `app.py:4244`
- Execution Center: `app.py:4338` → board buckets `ui/live_board.py:39`
- Attention triage + fix: `app.py:2412,2466,2522`
- Runs journal + filters: `app.py:4353`
- Project tabs (8): `app.py:4564`
- Focus mode: `app.py:5093`
- Portfolio pair: `app.py:5184,5191`
- Disabled-button gates: `app.py:803,1387,1677,1880,1887,1898,1907,1915,2430,2445,2517,2626,2710,2858`
- Inspector stub: `ui/inspector.py:15`
- Top bar (skeleton): `ui/top_bar.py:17`
- Theme/tokens: `.streamlit/config.toml`, `ui/theme.py`, `ui/tokens.py`
- Prior design docs (baseline): `docs/ux/{DESIGN_SYSTEM,KANBAN_REDESIGN,INTERACTION_MODEL,
  VISUAL_LANGUAGE,COMPONENT_CATALOG,FOUNDER_DESIGN_PRINCIPLES,IMPLEMENTATION_ROADMAP}.md`