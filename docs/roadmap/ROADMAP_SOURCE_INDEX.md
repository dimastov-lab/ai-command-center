# Roadmap Source Index — Phase 1: Repository Inventory

Status: **Inventory only. No synthesis, no roadmap decisions, no code/document changes.**

Purpose: a complete index of every document in this repository that may contain requirements,
architectural decisions, ideas, epics, features, or roadmap-relevant planning, as input to
reconstructing a Master Product Roadmap in a later phase.

Scope of this pass: full-text or representative read of every candidate document below. `.venv/`
(third-party package docs), `.git/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/` were excluded
as not part of this repository's own documentation.

No file was modified. `data/tasks.json` was not read or written. This document is the only file
created.

---

## 1. Summary counts

| Metric | Count |
|---|---|
| **Total documents indexed** | **50** (42 authored `.md`/`.json`/script docs + 14 empty placeholder files counted separately in §6 minus double-counted; see per-section tables for exact membership) |
| Roadmap-relevant documents (contain explicit forward-looking scope/plan) | 11 — `roadmap/program/PROGRAM_ROADMAP.md`, `roadmap/program/program_roadmap.json` (**top priority — see §2.7**), `WORKSPACE_HOME_ARCHITECTURE.md`, `docs/desktop/IMPLEMENTATION_ROADMAP.md`, `docs/desktop/DESKTOP_INCREMENT_1.md`, `docs/adr/0001` (Tier B section), `CHANGELOG.md` (`[Unreleased]` entries), `docs/audits/FOUNDER_FUNCTIONAL_AUDIT_9761459_STATUS.md`, `docs/audits/FOUNDER_FUNCTIONAL_AUDIT_TASKS_9761459.json`, `RELEASE_NOTES_v1.1.md`, `RELEASE_NOTES_v1.2.md` |
| Architecture / ADR documents | 13 — `ARCHITECTURE.md`, `WORKSPACE_HOME_ARCHITECTURE.md`, `docs/adr/0001`, `0002`, `0003`, `docs/desktop/ARCHITECTURE.md`, `docs/desktop/INFORMATION_ARCHITECTURE.md`, `docs/desktop/PLATFORM_BEHAVIOR.md`, `docs/desktop/DESIGN_SYSTEM.md` (component contracts), `docs/desktop/WORKSPACE_HOME_SPEC.md`, plus 3 ADRs already counted — see §2.3/§2.4 for the non-overlapping list |
| Founder Audit documents | 3 — `FOUNDER_FUNCTIONAL_AUDIT_9761459.md`, `FOUNDER_FUNCTIONAL_AUDIT_9761459_STATUS.md`, `FOUNDER_FUNCTIONAL_AUDIT_TASKS_9761459.json` |
| Product / UX / Design documents | 6 — `docs/desktop/PRODUCT_VISION.md`, `DESIGN_DIRECTIONS.md`, `DESIGN_SYSTEM.md`, `INFORMATION_ARCHITECTURE.md`, `WORKSPACE_HOME_SPEC.md`, `docs/desktop/README.md` |
| Project/context status documents | 9 — `CURRENT_STATE.md`, `context/AIOS_CONTEXT.md`, `context/BANK_CONTEXT.md`, `context/LEGAL_CONTEXT.md`, `projects/AIOS.md`, `projects/BANK_STRATEGY.md`\*, `projects/BUSINESS.md`\*, `projects/LEGAL.md`\*, `projects/PERSONAL.md`\* (\*empty — see §6) |
| Agent-generated artifact logs (not authored docs) | ~127 — `generated/AIOS/*.md` (101), `reports/AIOS/*.md` (25), `reports/AICC/*.md` (1) — catalogued as a bulk category in §4, not individually indexed |
| Empty placeholder files (0 bytes) | 10 — `DECISIONS.md`, `INBOX.md`, 4× `projects/*.md`, `prompts/*.md` ×5 minus overlap — see §6 for the exact list |
| Categories still needing deep analysis in Phase 2 | See §7 |

No `architecture/` top-level directory exists. A root-level `roadmap/` directory did **not** exist
at the start of this inventory pass but **appeared during it** (see §2.7) — it is included below
since scanning `roadmap/**` was explicitly in scope.

---

## 2. Full document inventory

Legend for the "Contains" column: **E**pics · **F**eatures · **A**rchitecture decisions · **U**X ·
**R**oadmap · **I**deas · **Eng**ineering tasks.

### 2.1 Root-level product documents

| Path | Type | Purpose | Size | Topics | Contains |
|---|---|---|---|---|---|
| `README.md` | Product doc | User-facing setup + feature description, v1.1/v1.2 scope | 253 lines | Getting started, runtime data contract, v1.1 Sprint 2 features, v1.2 Agent Workflow, scope/limitations, testing | F, A (light), U |
| `ARCHITECTURE.md` | Architecture doc | Authoritative description of the Streamlit app as built, v1.2 baseline | 363 lines | System shape, execution model, module layout, pages, state management, subprocess boundary, data model, directory contract, extension points, v1.2 agent-workflow security boundaries | A, Eng |
| `CHANGELOG.md` | Changelog | Keep-a-Changelog-style history, v1.0 → v1.2 → two `[Unreleased]` entries (Desktop D0 docs; Sprint 3 Workspace Home) | 163 lines | Release history, most recent unreleased work | F, R (unreleased entries), A |
| `CURRENT_STATE.md` | Project status | Per-project (AIOS/Bank/Legal/Business/Personal) status snapshot + 7 global operating rules | 86 lines, dated 2026-07-15 | Cross-project status, operating rules | Eng (light), possibly **stale** — see §5 |
| `WORKSPACE_HOME_ARCHITECTURE.md` | Architecture doc (large) | Full design + review history for the Workspace Home read model; explicitly still authoritative for the read-model/redaction layer even though `docs/desktop/WORKSPACE_HOME_SPEC.md` exists | 1149 lines | Executive verdict, existing capabilities, review/remediation history (multiple review passes: BLOCKED → resolved → APPROVED), 10-step implementation plan (§17, now built per its own header note) | A, Eng, E (implementation plan) |
| `RELEASE_NOTES_v1.1.md` | Release notes | User-facing summary of Sprint 2 (v1.1) | 86 lines | Executive Dashboard, Command Palette, Focus Mode, Timeline, AI Agents, Smart Tasks, Git Center, Workspace Launcher | F |
| `RELEASE_NOTES_v1.2.md` | Release notes | User-facing summary of v1.2 Agent Workflow | 143 lines | Project Chat, Claude Code runner, report parsing, Create Next Task, run journal, security boundaries | F, A (security) |
| `DECISIONS.md` | Placeholder | Intended decision log | 0 bytes | — empty | none |
| `INBOX.md` | Placeholder | Intended inbox/triage file | 0 bytes | — empty | none |

### 2.2 `docs/adr/` — Architecture Decision Records

| Path | Type | Purpose | Size | Topics | Contains |
|---|---|---|---|---|---|
| `docs/adr/0001-engineering-control-center-v2-increment-1.md` | ADR, Accepted/Implemented | Founding decision for "Engineering Control Center v2" — extends `tasks.json` rather than migrating to SQLite for the task model, moves business logic out of `app.py` into `command_center/*`, executor abstraction, automation-safety boundary. **Contains an explicit Tier A (built) / Tier B (roadmap, not built) split** | 227 lines | Task model, launch system, executor abstraction, automation safety, Pause/Resume/Restart semantics, known gaps, **Tier B roadmap list** (Project Dashboard/Engineering Health rebuild, Live Execution Center rebuild, periodic Engineering Supervisor, standalone Engineering Health/Workspace Manager/Reporting pages, global search, real ChatGPT/Codex/Gemini/Remote executors) | A, E, **R (explicit)**, Eng |
| `docs/adr/0002-project-config-as-canonical-engineering-defaults.md` | ADR, Accepted/Implemented | Project Config becomes canonical owner of engineering-environment defaults (`default_branch`/`default_executor`/`default_prompt`) rather than Launch's runtime fallback | 154 lines | Project Config ownership, workspace/branch resolution | A, Eng |
| `docs/adr/0003-live-execution-center-v2-and-kanban-launch-bridge.md` | ADR, Accepted/Implemented | `runtime.db` becomes sole execution-state source of truth; the real Kanban Launch button is bridged onto the v2 Session Supervisor (async, non-blocking `Popen`) | 200 lines | Execution state source of truth, Kanban↔Supervisor bridge, `launch_service.execute_agent_launch_v2` | A, Eng, F |

**Note:** none of the three ADRs, nor the v2 Engineering Control Center / Portfolio Execution / Session
Supervisor work they describe, is mentioned anywhere in `CHANGELOG.md` or `README.md` — see §5.

### 2.3 `docs/audits/` — Founder Functional Audit

| Path | Type | Purpose | Size | Topics | Contains |
|---|---|---|---|---|---|
| `docs/audits/FOUNDER_FUNCTIONAL_AUDIT_9761459.md` | Independent read-only audit (Russian) | Full-product functional audit at HEAD `9761459`, per-category readiness percentages (Task Management 75%, Task Import 25%, Agent Launch 75%, Parallel Execution 50%, Git/Worktree 50%, Runtime v2 75%, Portfolio Execution 75%, Project Management 50%, Reports 75%, Desktop UI 0%, Security 50%, Reliability 50%, Full Founder Workflow 50%) | 324 lines | System map, findings (Blocker/Major/Minor/Nit), security gaps (network bind, path traversal), concurrency gaps (`tasks.json`/`execution_queue.json` unlocked writes), verdict: **READY AFTER REMEDIATION** | **E, F, A, R, Eng** — dense source of engineering-task candidates |
| `docs/audits/FOUNDER_FUNCTIONAL_AUDIT_9761459_STATUS.md` | Status/tracking doc | Tracks audit baseline (26 findings: 4 Blocker/9 Major/10 Minor/3 Nit), notes PR #9 already resolved part of it, defines required triage steps before the task package can be imported | 55 lines | Audit-to-backlog reconciliation process, explicit "not yet imported / not schema-compatible" warning | **R (explicit next steps)**, Eng |
| `docs/audits/FOUNDER_FUNCTIONAL_AUDIT_TASKS_9761459.json` | Task package (JSON, not yet imported) | 33 candidate remediation/engineering tasks derived from the audit, tagged by wave (`wave: 0`/`1`), priority, parallel group, target version (`v1.3`), dependencies | 33 array entries | Network bind lockdown, `tasks.json` write locking, canonical task model, worktree auto-creation, task import, and more (per §3 of the STATUS doc) | **R, Eng — explicit structured backlog**, schema currently incompatible with `task_import.py` |

**This is the single richest, most explicit roadmap-shaped source found in this pass** — a
33-item, wave-tagged, dependency-annotated backlog, explicitly flagged as not-yet-triaged/not-yet-imported.

### 2.4 `docs/desktop/` — Desktop application documentation set (status: D0, docs-only, no code)

| Path | Type | Purpose | Size | Topics | Contains |
|---|---|---|---|---|---|
| `docs/desktop/README.md` | Index | Reading order and purpose table for the whole desktop doc set; lists 14 "binding decisions" | 71 lines | Reading order, binding decisions (lightweight local-first desktop tool, PySide6/Qt Widgets, macOS+Windows targets, D1 scope boundary) | A, R |
| `docs/desktop/PRODUCT_VISION.md` | Product vision | Why the desktop app exists, target user, boundary vs. AIOS/AICOS, success criteria, non-goals | 147 lines | Product purpose, primary workflows, product boundary, long-term direction | **E, F, U, R** |
| `docs/desktop/ARCHITECTURE.md` | Target architecture (D0, no code yet) | `command_center.desktop` / `.application` / `.platform` package architecture, dependency rules, threading model | 341 lines | Package layering, Qt threading (`QThreadPool`), lifecycle, packaging (§16) | A |
| `docs/desktop/INFORMATION_ARCHITECTURE.md` | IA spec | 9-section eventual navigation; which 3 are active in Increment 1 | 149 lines | Navigation structure, disabled-section rendering rule | U, F |
| `docs/desktop/DESIGN_DIRECTIONS.md` | Design decision doc | 3 design directions evaluated; "Professional Control Plane" approved | 154 lines | Visual character, density, strengths/weaknesses per direction | U, A (decision) |
| `docs/desktop/DESIGN_SYSTEM.md` | Design system spec | Implementation-ready tokens (spacing, typography, control heights, radii) and component contracts | 506 lines | Design tokens, component contracts (`MetricCard`, `ProjectCard`, etc.), accessibility | U |
| `docs/desktop/WORKSPACE_HOME_SPEC.md` | Page spec | Native Home page spec built on `build_workspace_home_snapshot`; explicitly defers to root `WORKSPACE_HOME_ARCHITECTURE.md` for the read model itself | 244 lines | Layouts (wide/medium/minimum), project cards, edge states | U, F |
| `docs/desktop/PLATFORM_BEHAVIOR.md` | Platform contract | macOS/Windows 11 native behavior contract (`command_center.platform`) | 91 lines | Packaging, Finder/Explorer reveal, menu behavior, Gatekeeper, settings storage | A |
| `docs/desktop/DESKTOP_INCREMENT_1.md` | Frozen scope doc | Binding D1–D4 scope, explicit read/write boundary, explicit **out-of-scope** list | 193 lines | Per-stage acceptance criteria, forbidden scope | **E, R (frozen scope)** |
| `docs/desktop/IMPLEMENTATION_ROADMAP.md` | Implementation roadmap | Commit-sized sequencing of D1A → D4 final gate | 222 lines | Per-step scope/dependencies/acceptance criteria/forbidden scope | **R (explicit, most granular roadmap document found)** |

### 2.5 Context and per-project status documents

| Path | Type | Purpose | Size | Topics | Contains |
|---|---|---|---|---|---|
| `context/AIOS_CONTEXT.md` | Context pack | AIOS project context for agent runs | 126 lines, dated 2026-07-15 | AIOS product direction (open-source core, enterprise, developer platform, banking/AML/compliance, commercial packaging), completed P0 capabilities | E, F |
| `context/BANK_CONTEXT.md` | Context pack | Bank Strategy project context | 53 lines | Sensitive (BANK) | Eng (light) |
| `context/LEGAL_CONTEXT.md` | Context pack | Legal project context | 55 lines | Sensitive (LEGAL) | Eng (light) |
| `projects/AIOS.md` | Project control card | AIOS objective, operating model, streams | 354 lines, dated 2026-07-15 | Reliable memory/state, API/SDK, auth, dev tooling, enterprise security, commercial packaging, AML/compliance scenarios | **E, F** — largest non-AICC product-vision document in the repo, but it describes the **AIOS** product, not AI Command Center itself |
| `projects/BANK_STRATEGY.md` | Project control card | — | 0 bytes | empty | none |
| `projects/BUSINESS.md` | Project control card | — | 0 bytes | empty | none |
| `projects/LEGAL.md` | Project control card | — | 0 bytes | empty | none |
| `projects/PERSONAL.md` | Project control card | — | 0 bytes | empty | none |

**Important scoping note for Phase 2**: `projects/AIOS.md` and `context/AIOS_CONTEXT.md` describe
the roadmap of the **AIOS project** (a separate tracked project), not of **AI Command Center**
(this repository's own product). Only include them in an AI-Command-Center roadmap if the task is
intentionally cross-project; otherwise they are out of scope noise for "AI Command Center's own
roadmap."

### 2.7 `roadmap/` — externally-supplied program roadmap package (appeared mid-inventory)

**Timeline note**: this directory did not exist when this inventory began. It appeared on disk
partway through this pass — first as `roadmap/aicc_program_roadmap.zip.b64`, which was then decoded
to `roadmap/aicc_program_roadmap.zip` and extracted to `roadmap/program/` by a process outside this
session (not created or run by this task). Internal file timestamps (`06:52`) predate the extraction
(`11:37`), consistent with a pre-built package being dropped in and unpacked, not authored live. It
is indexed here, unmodified, because `roadmap/**` was explicitly in scope for this scan — no file
inside it was altered, and neither of its two scripts (which mutate `data/tasks.json` and create git
worktrees) was executed.

| Path | Type | Purpose | Size | Topics | Contains |
|---|---|---|---|---|---|
| `roadmap/program/README.md` | Package index | Describes the 4 files in the package and their install/usage commands | 32 lines | Install instructions for a task import + a worktree-preparation script | R, Eng |
| `roadmap/program/PROGRAM_ROADMAP.md` | **Human-readable program roadmap** | "Global AI Platform — Program Roadmap": 46 tasks across 9 dependency levels (Level 0 Foundation → Level 8 Global Platform), spanning **6 project scopes**: `AIOS`, `AICOS`, `AI_COMMAND_CENTER`, `AIOS_PRODUCT`, `PORTFOLIO`, `PLATFORM` | 98 lines | Foundation → Platform Stabilization → Cross-project Integration → Distributed Execution → Federation → Self Development → Product Ecosystem → Enterprise → Global Platform. AI Command Center's own items: `AICC-D1-001..004` (desktop shell, task import/upload, execution queue, project intelligence), `AICC-D2-001..004` (parallel execution supervisor, universal workspace manager, agent registry, program dependency engine), `AICC-GIT-001`, `AICC-INT-001`, `AICC-DIST-001`, `AICC-SELF-001`, `AICC-ENT-001` | **E, F, A, R — the single largest and most structured roadmap document found in this repository** |
| `roadmap/program/program_roadmap.json` | **Canonical machine-readable roadmap** | Same 46 tasks as the `.md`, structured: `id`, `project`, `stream`, `level`, `title`, `goal`, `task_type`, `priority`, `repository_path`, `branch`, `workspace_path`, `depends_on`, `blocks`, `parallel_group`, `required_capabilities`, `conflicts_with`, `critical_path`, `status`, `ready_to_start`, `prompt` per task | 119,476 bytes, 46 tasks | Full dependency graph, per-task prompts, repository/branch/workspace targets | **E, F, A, R, Eng — canonical source for the `.md` above** |
| `roadmap/program/import_program_roadmap.py` | Script (not executed) | Imports `program_roadmap.json` tasks into `data/tasks.json`, duplicate-aware | 3595 bytes | Would mutate runtime task store if run — **not run in this pass**, per this task's constraints | Eng (tooling) |
| `roadmap/program/ready_tasks.py` | Script (not executed) | Computes which roadmap tasks have satisfied dependencies; `--prepare-worktrees` would run `git worktree add` | 2176 bytes | Would mutate the working tree if run with `--prepare-worktrees` — **not run in this pass** | Eng (tooling) |
| `roadmap/aicc_program_roadmap.zip` | Archive | The packaged form of `roadmap/program/`'s contents | 14,851 bytes | Same as above, zipped | — |

**This package directly names itself "Program Roadmap" and covers AI Command Center as one of six
tracked project scopes** (`AI_COMMAND_CENTER`), sitting inside a larger "Global AI Platform" plan
alongside AIOS/AICOS/AIOS_PRODUCT/PORTFOLIO/PLATFORM. It is the single most on-topic document for a
Master Product Roadmap reconstruction found anywhere in this inventory — flagged as the top priority
input for Phase 2 (see §7).

### 2.6 Prompts and templates (all empty)

| Path | Type | Size |
|---|---|---|
| `prompts/architecture_review.md`, `prompts/final_gate.md`, `prompts/implementation.md`, `prompts/remediation.md`, `prompts/review.md` | Prompt template placeholders | 0 bytes each |
| `templates/agent_report.md`, `templates/project_status.md`, `templates/task.md` | Report/status/task template placeholders | 0 bytes each |

None contain any content. The actual prompt logic these filenames imply lives in
`scripts/start-task.sh` (not read in this pass — script, not documentation) rather than in these
files.

---

## 3. JSON documents (non-`data/`, non-example) that may carry roadmap content

| Path | Type | Purpose |
|---|---|---|
| `docs/audits/FOUNDER_FUNCTIONAL_AUDIT_TASKS_9761459.json` | 33-item structured task package | See §2.3 — the most directly actionable roadmap-shaped artifact in the repository |
| `.claude/settings.local.json` | Tool permission config | Not roadmap-relevant — local Claude Code permission settings only |

`data/*.json`/`*.jsonl` (tasks, runs, chats, activity, project_config, execution_queue, portfolio_*)
were **not opened** beyond directory listing — these are gitignored runtime state per
`README.md`/`.gitignore`, not documentation, and `data/tasks.json` specifically was excluded per
this task's instructions. `data/*.example.*` siblings are schema illustrations, not
requirements/roadmap sources.

---

## 4. Bulk category: agent-generated artifacts (not individually indexed)

Two directories hold large numbers of **agent-run outputs**, not authored planning documents.
Cataloguing every file individually was out of proportion for a Phase 1 inventory; they are
recorded here as a bulk category with a breakdown, for Phase 2 to decide whether any are worth
mining for historical decisions.

| Directory | File count | Nature |
|---|---|---|
| `generated/AIOS/*.md` | 101 (91 `implementation`, 10 `review`) | Agent task prompts generated by `scripts/start-task.sh` for the **AIOS** project — each is a task brief handed to an agent, not a roadmap document for AI Command Center itself |
| `reports/AIOS/*.md` | 25 | Completed agent-run reports for AIOS (`_adhoc_claude_code.md`, run-id-suffixed files) |
| `reports/AICC/*.md` | 1 | One completed v2 Session Supervisor run report for **AI Command Center itself** (`AICC-DOCS-001`) — the only report directly about this repository's own product |

These are transient/historical execution artifacts (per `README.md`'s own description of
`generated/`/`reports/` as gitignored, transient), not part of the durable documentation set. Flagged
in §7 as a category to only mine selectively (e.g. the `architecture_review`-type reports, if any
exist among the 10 `review`-type `generated/AIOS` files) rather than read in full.

---

## 5. Cross-document overlaps, tensions, and potential staleness

These are observations from reading the documents, not conclusions — Phase 2 should verify each
against current code before treating any as fact.

1. **Two files named `ARCHITECTURE.md`.** Root `ARCHITECTURE.md` describes the *existing* Streamlit
   app (v1.2 baseline). `docs/desktop/ARCHITECTURE.md` describes the *target* (unbuilt) desktop
   package architecture. Same filename, disjoint scope — easy to conflate when indexing by name
   alone.
2. **Two Workspace Home documents, deliberately layered, not duplicates.** `WORKSPACE_HOME_ARCHITECTURE.md`
   (repo root) is explicitly still authoritative for the read model/redaction stage;
   `docs/desktop/WORKSPACE_HOME_SPEC.md` explicitly defers to it and only specs the native
   *presentation* layer on top. Documented cross-reference exists in both files — treat as one
   logical unit in Phase 2, not two competing designs.
3. **`README.md` and `CHANGELOG.md` do not mention the "Engineering Control Center v2" work at all.**
   `docs/adr/0001`–`0003` and the Founder Audit both describe substantial, already-implemented
   functionality — `runtime.db` (v2 SQLite Session Supervisor), Portfolio Execution, the Kanban
   Launch Bridge, an executor abstraction — none of which appears in `CHANGELOG.md`'s version
   history or `README.md`'s feature list. Either these user-facing docs are stale relative to the
   ADRs/audit, or a documentation update was scoped but not done. This is a high-priority
   reconciliation item for Phase 2.
4. **`CURRENT_STATE.md` is dated 2026-07-15**, before v1.2, before all three ADRs, before the
   desktop documentation set, and before the Founder Audit (2026-07-21). Its per-project status
   ("AIOS: P1 in progress...") should be treated as a stale snapshot, not current fact, until
   cross-checked.
5. **The Founder Audit (2026-07-21) is the most recent and most comprehensive assessment of the
   product's actual state**, and it directly contradicts or updates claims in `README.md` (e.g.
   "Local-only tool: no network services" vs. the audit's Blocker-severity finding that Streamlit
   binds to all interfaces by default) and in `WORKSPACE_HOME_ARCHITECTURE.md`/ADR self-descriptions
   of test counts. Its own `_STATUS.md` companion explicitly warns it must not be treated as current
   source of truth until reconciled — this warning should be preserved into Phase 2, not dropped.
6. **`FOUNDER_FUNCTIONAL_AUDIT_TASKS_9761459.json`'s 33 candidates and ADR 0001's "Tier B" list**
   are two independently-produced backlog-shaped sources (one audit-driven, one architecture-driven)
   that likely overlap in places (e.g. both touch task-model/import gaps) — Phase 2 should
   cross-reference rather than treat them as additive.
7. **`projects/AIOS.md` / `context/AIOS_CONTEXT.md` describe a different product's roadmap** (the
   AIOS platform) than this repository's own product (AI Command Center) — a likely source of
   confusion if pulled into an AI-Command-Center-specific roadmap without the distinction called
   out (see §2.5).
8. **Prompts/templates directories are empty placeholders** (`prompts/*.md`, `templates/*.md`,
   `DECISIONS.md`, `INBOX.md`) — present in the file tree, contributing zero content. Worth noting
   only so Phase 2 doesn't re-discover and re-investigate them as "missing" documents.

---

## 6. Empty placeholder files (0 bytes, contain nothing)

`DECISIONS.md`, `INBOX.md`, `projects/BANK_STRATEGY.md`, `projects/BUSINESS.md`,
`projects/LEGAL.md`, `projects/PERSONAL.md`, `prompts/architecture_review.md`,
`prompts/final_gate.md`, `prompts/implementation.md`, `prompts/remediation.md`,
`prompts/review.md`, `templates/agent_report.md`, `templates/project_status.md`,
`templates/task.md` — 14 files total, verified via `ls -la` (0-byte size), not merely short.

---

## 7. Categories requiring further analysis in the next phase

0. **[Top priority] Cross-reference `roadmap/program/program_roadmap.json` (46-task Global AI
   Platform program roadmap, §2.7) against everything else in this index** — it is dated/packaged
   independently (appeared mid-session, not part of the pre-existing repository state), spans six
   project scopes, and its `AICC-*` items (desktop shell, execution supervisor, universal workspace
   manager, agent registry, program dependency engine, self-development planning engine) overlap
   heavily with `docs/desktop/*` (D0 desktop docs), ADR 0001's Tier B list, and the Founder Audit's
   findings — but none of those other documents reference this package or vice versa. Phase 2 must
   determine whether this package is the authoritative forward roadmap, a draft/proposal, or a
   duplicate effort, before treating it as ground truth. Do **not** run
   `roadmap/program/import_program_roadmap.py` or `ready_tasks.py --prepare-worktrees` without
   explicit user direction — both mutate state (`data/tasks.json`, git worktrees respectively).
1. **Reconcile the Founder Audit (33-item task package + narrative findings) against ADR 0001's
   Tier B roadmap and the current `main` branch** — the STATUS doc's own "Required next steps"
   (§4 of that file) already defines this reconciliation process; Phase 2 roadmap reconstruction
   should follow it rather than starting fresh.
2. **Reconcile `README.md`/`CHANGELOG.md` against the three ADRs** — determine whether the
   Engineering Control Center v2 / Portfolio Execution / Session Supervisor work is undocumented-but-shipped,
   or whether the ADRs describe work beyond what's actually on `main` (the ADRs self-report
   "Accepted, implemented" — verify against code, not just ADR status headers).
3. **Sample the `generated/AIOS/*_review.md` and `_architecture_review.md` files** (10 `review`-type
   generated tasks in `generated/AIOS/`, plus the `architecture_review`-suffixed files in
   `generated/AIOS/` per the file-name breakdown in §4) for any AIOS-specific architectural decisions
   worth cross-referencing — currently unopened.
4. **Verify current staleness of `CURRENT_STATE.md`** against the newer ADRs/CHANGELOG/audit — decide
   whether Phase 2 treats it as a source at all, or supersedes it entirely with the Founder Audit's
   system map (§4 of that audit).
5. **Decide the AIOS-vs-AI-Command-Center scoping question** (§5.7 above) before pulling
   `projects/AIOS.md`/`context/AIOS_CONTEXT.md` content into any Master Product Roadmap for AI
   Command Center specifically.
6. **`scripts/*.sh`/`scripts/*.py` were not read as documentation** in this pass (out of scope —
   code, not docs) but `scripts/start-task.sh` is referenced by nearly every document above as the
   mechanism that actually defines task types/prompts; Phase 2 may need to read it directly since
   `prompts/*.md` (the obvious place to look) are empty.
7. **`.agents/skills/` and `.claude/skills/`** were checked and found to contain no additional
   markdown documentation beyond tool configuration — confirmed empty of roadmap content, no further
   action needed.
