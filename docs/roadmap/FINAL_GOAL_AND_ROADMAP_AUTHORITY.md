# Decision Record — Final Goal, Roadmap Authority, and Candidate Roadmap Disposition

- **Record id**: DR-ROADMAP-AUTHORITY-001
- **Status**: Accepted (founder-level program decision)
- **Date**: 2026-07-28
- **Branch**: `docs/final-goal-roadmap-authority`
- **Scope**: Documentation and program-governance decision only. **No runtime code changed, no
  `data/tasks.json` write, no roadmap import executed.**
- **Supersedes for the questions it answers**: the open recommendations in
  `docs/roadmap/ROADMAP_RECONCILIATION.md` §4 (Phase 3 was left to "a future phase/founder
  decision" — this record is that decision).

## Inputs read

`CURRENT_STATE.md`; ADR 0001–0005 (`docs/adr/`); the Desktop D0 set (`docs/desktop/*`);
`docs/roadmap/ROADMAP_RECONCILIATION.md` (Phase 2) and `docs/roadmap/ROADMAP_SOURCE_INDEX.md`
(Phase 1); the program roadmap package (`roadmap/program/PROGRAM_ROADMAP.md`,
`program_roadmap.json`); the Founder Functional Audit `9761459` and its `_STATUS.md`; and the live
canonical registry in `command_center/models.py` / `command_center/project_config.py` (read-only).

---

## 1. Final goal

**Committed final goal (what this program is held to):**

> AI Command Center becomes a **native-desktop, local-first, single-user developer control plane**
> that is the operator's daily driver for orchestrating AI-agent work and observing state
> (repositories, worktrees, runs, reports, artifacts) across the tracked projects — reaching, then
> deliberately exceeding, today's Streamlit feature set, with **fail-closed safety on every
> privileged action** (worktree creation, push, PR, merge, autonomous execution).

This is the goal the repository can actually be measured against and is consistent with every
in-repo Source-of-Truth document (Desktop `PRODUCT_VISION.md` §1/§8/§10; ADR 0001–0005; the
Execution/Autonomy safety posture in `CURRENT_STATE.md` §0).

**Aspirational horizon (explicitly NOT adopted as the committed final goal):** the program
roadmap's "Global AI Platform" end-state — distributed runtime, federation, marketplace,
enterprise operations, and a self-development engine (`GLOBAL-001` and its Level 3–8 ancestry).
This is recorded as a directional horizon (see §6, horizon **H3**), not a commitment. It is not
approved as the final goal and must not be treated as such. Adopting any part of it as committed
scope requires a separate, evidenced decision.

## 2. Success measures

The final goal is met when all of the following hold and are demonstrable:

1. **Desktop parity gate.** A developer launches the native desktop app on macOS (Apple Silicon)
   and Windows 11 (x64) with no separate Python interpreter, browser, or local HTTP server, and
   Workspace Home reaches functional parity with the Streamlit Workspace Home for the in-scope
   sections (Projects, repo/worktree state, recent runs, artifacts, reports) — **preserving every
   sensitivity/redaction guarantee** already implemented (Desktop `PRODUCT_VISION.md` §8).
2. **Safety gate.** Every privileged capability remains fail-closed: normal task-v2 launches
   confirm before provisioning and verify source repo, expected branch, worktree isolation, and
   status policy before process launch; push/PR/merge and completion-autopilot stay opt-in and
   default-off (`CURRENT_STATE.md` §0).
3. **Data-integrity gate.** 100% of tasks in `data/tasks.json` carry a `project` value that
   resolves against the canonical registry (§4), and every write path goes through the validating
   `command_center/task_import.py` / `scripts/import_tasks.py` pipeline. (Currently **failing** —
   see §7, Follow-up F2.)
4. **Documentation-truth gate.** `README.md` and `CHANGELOG.md` describe what is actually on
   `main` (ADR 0001–0005 work), and the canonical registry is recorded by an ADR
   (Reconciliation Conflict 1/3; §7 Follow-ups F1/F3).
5. **Audit-closure gate.** The Founder Audit's four Blockers are closed or explicitly accepted,
   verified by a refreshed audit against current `main` (`FOUNDER_..._STATUS.md` required steps).
6. **Process discipline** (standing, per `CURRENT_STATE.md` Global Operating Rules): one agent =
   one task = one branch = one worktree; independent review before commit/merge; no
   commit/push/PR/merge without explicit authorization; every task has a measurable Definition of
   Done.

## 3. In-scope products

- **In scope to build (the product this repository owns):** **AI Command Center** — the
  single-user developer control plane (Streamlit today, native desktop as the committed target).
- **In scope to *track / orchestrate* (not built here):** the projects AI Command Center observes
  and runs agents *about* — **AIOS** and **AICOS** are separate products with their own roadmaps;
  AI Command Center does not implement their logic and is **not** AICOS (Desktop
  `PRODUCT_VISION.md` §5). `BANK` and `LEGAL` are tracked projects subject to sensitivity
  redaction, **not** compliance/banking systems in their own right; `BUSINESS` and `PERSONAL` are
  ordinary tracked projects.
- **Explicitly out of scope** (binding, Desktop `README.md` decisions 3–5, `PRODUCT_VISION.md`
  §9): multi-user/team/server operation; direct connection to banking systems, data marts, queues,
  enterprise buses, or customer data; being or replacing AICOS as an operational AML workplace;
  production signing/notarization/auto-update/SSO/server mode within the committed horizon.

## 4. Canonical project-id mapping

**Authority:** `command_center/models.py` `PROJECT_IDS` is the single canonical registry. It is
runtime ground truth; every document that disagrees is stale and must be corrected to match it (not
the reverse).

Canonical registry (9): `AICC, AIOS, AICOS, PRODUCT, ECOSYSTEM, BANK, LEGAL, BUSINESS, PERSONAL`.
Sensitive (redacted): `BANK, LEGAL`.

The program roadmap uses a **fourth, incompatible taxonomy** and is internally inconsistent (its
`projects` header declares 6 scopes, but its task rows use 7 — a stray `AICC` alongside
`AI_COMMAND_CENTER`). Resolution against the live alias table
(`command_center/project_config.normalize_project_id`, verified read-only during this decision):

| Program-roadmap project | Resolves today? | Canonical target | Disposition |
|---|---|---|---|
| `AIOS` | yes -> `AIOS` | `AIOS` | Accept as-is |
| `AICOS` | yes -> `AICOS` | `AICOS` | Accept as-is |
| `AICC` | yes -> `AICC` | `AICC` | Accept as-is (used only by the stray 47th task) |
| `AI_COMMAND_CENTER` | NO -> `None` (underscores not folded) | `AICC` | **Remap required** before any import |
| `AIOS_PRODUCT` | NO -> `None` (underscores not folded) | `PRODUCT` | **Remap required** before any import |
| `PORTFOLIO` | NO -> `None` | *(no canonical scope)* | **No canonical mapping — reject/hold** |
| `PLATFORM` | NO -> `None` | *(no canonical scope)* | **No canonical mapping — reject/hold** |

Rules that follow from this mapping:
- The alias table folds case and whitespace only, **not** underscores. `"AI_COMMAND_CENTER"` and
  `"AIOS_PRODUCT"` therefore **fail validation** and must be remapped to `AICC` / `PRODUCT`
  *before* any re-import.
- Extending `PROJECT_NAME_ALIASES` (or `PROJECT_IDS`) to accommodate the roadmap package's naming
  is a **deliberate founder decision recorded in a new ADR** — never a script workaround. Adding
  underscore aliases just to make an unreviewed package import is **rejected** (§7 R4).
- `PORTFOLIO` and `PLATFORM` have no canonical home; any roadmap content under them is aspirational
  (H3) and is not importable as a tracked-project task.

## 5. Authority hierarchy

For any conflict about "what is true / what wins," authority descends in this order:

1. **Ground truth — code & tests.** `command_center/*`, `models.py`, `tests/*`. Arbiter of what
   currently exists and of the canonical registry.
2. **Canonical architecture record (immutable once written).** `ARCHITECTURE.md` (root), ADR
   0001–0005, `WORKSPACE_HOME_ARCHITECTURE.md`. New decisions get a **new ADR**, never an edit to
   an old one.
3. **This decision record**, for the specific questions it settles (final goal, success measures,
   in-scope products, canonical registry authority, horizons, roadmap disposition). Authoritative
   going forward until superseded by a newer dated decision record or ADR.
4. **Product-facing summaries (must be kept current).** `README.md`, `CHANGELOG.md`,
   `RELEASE_NOTES_*`. Currently the stalest tier — subordinate to 1–2, obligated to track them.
5. **Approved target architecture, not yet built (SoT-Target).** `docs/desktop/*` (all 10 files).
   Binding decisions, but must be re-checked against tier 1 (they drifted into Conflict 1).
6. **Assessment / audit (historical, always paired with its status tracker).** Founder Audit +
   `_STATUS.md`; never read the audit alone.
7. **Candidate backlogs (require triage before becoming roadmap).** Audit 33-item task package;
   ADR 0001 "Tier B"; `roadmap/program/*`. None is authoritative on its own.

Standing across all tiers: the seven **Global Operating Rules** in `CURRENT_STATE.md` remain
authoritative process principles.

## 6. Horizon boundaries

- **H0 — Shipped (now).** Local Streamlit app; `runtime.db` schema 7 as async-execution source of
  truth; Execution Center (supervision, streaming, cancellation, timeouts, restart reconciliation);
  guarded task-v2 launch (fail-closed workspace verification); persisted completion pipeline
  (autopilot/auto-merge opt-in, default-off); autonomy-proposal **domain/API only** (no UI, no
  driver/executor). Source: `CURRENT_STATE.md` §0, ADR 0003/0004/0005.
- **H1 — Committed next.** (a) Desktop Increment 1 D1A->D4 per
  `docs/desktop/IMPLEMENTATION_ROADMAP.md` (read-only except repo-path/theme/window prefs). (b)
  Close the Founder Audit Blockers, foremost the network-bind Blocker and the registry/import
  data-integrity Blocker (§7, F2).
- **H2 — Planned (accepted in principle, pending the §7 triage).** Parallel execution supervisor
  UI, Universal Workspace Manager, Git Center, AI Agent Registry, program dependency engine,
  cross-project integration center — i.e. the program roadmap's `AICC-D2-*` / `AICC-GIT-001` /
  `AICC-INT-001`, ADR 0001 Tier B, and the audit's non-Blocker waves. Committed only per item as
  triage confirms it is Still-Open and approved.
- **H3 — Aspirational (directional, NOT committed).** Distributed runtime & capacity planning,
  federation, marketplace/plugin SDK, enterprise operations, self-development engine — program
  roadmap Levels 3–8 (`AIOS-DIST-*`, `AICC-DIST-*`, `FED-*`, `MARKET-*`, `*-ENT-*`, `AICC-SELF-*`,
  `GLOBAL-001`). Recorded as direction only; the boundary between "we plan to build this" and "this
  is a stated ambition" is drawn between **H2 and H3**.

## 7. Disposition of candidate roadmap content

### Accepted

- **A1. The Desktop D0 set (`docs/desktop/*`) as the canonical near-term product track**, and
  specifically the D1A->D4 sequencing in `IMPLEMENTATION_ROADMAP.md` as the **definition-of-done for
  the program roadmap's `AICC-D1-001`** — resolving Reconciliation Conflict 5 (nine sequenced
  sub-increments *are* the DoD of the one flat roadmap task; they are not competing plans).
- **A2. The canonical registry (§4) from `command_center/models.py`** as authoritative, overriding
  every "six projects" statement in the docs (README, root ARCHITECTURE, ADR 0002 "Known
  limitation", `docs/desktop/PRODUCT_VISION.md`/`WORKSPACE_HOME_SPEC.md`, the audit).
- **A3. The Founder Audit's safety/integrity findings as must-fix ahead of new features** — the
  network-bind Blocker and the registry/import-integrity Blocker take priority over any H2/H3 work.

### Deferred (accepted in principle; committed only after one triage pass)

A single triage pass shall classify every item in all three backlogs as
**Done / Still-Open / Superseded / Duplicate** against current `main`, exactly as
`FOUNDER_..._STATUS.md` already prescribes for its own package — extended to cover all three:

- **D1.** The Founder Audit 33-item task package (after schema conversion per `_STATUS.md`).
- **D2.** ADR 0001 "Tier B" list.
- **D3.** The program roadmap's *ideas* for `AICC-D2-*`, `AICC-GIT-001`, `AICC-INT-001` (the H2
  concepts) — retained as candidate inputs to the same triage, **not** as approved tasks.

Known/plausible overlaps to resolve in triage (not merge mechanically): `AICC-GIT-001` vs audit
MINOR-4/NIT-1; `AICC-SELF-001` vs the audit's "Wave 4 Self-Development"; `AICC-D1-002` (task
import) vs already-shipped PR #9 `task_import.py`.

### Rejected

- **R1. The `roadmap/program/` package as an authoritative or importable artifact, in its current
  form.** Reasons, each independently sufficient: (a) **it is not even internally consistent** — the
  JSON carries **47** tasks while its own human-readable `PROGRAM_ROADMAP.md` shows **46**; the
  extra task `AICC-LAUNCH-001` ("Worktree-aware Agent Launcher") uses a project id (`AICC`) not in
  the package's own declared `projects` set; (b) its project taxonomy is incompatible with the
  canonical registry and only partially resolvable (§4); (c) it is a documented dependency-island
  of unknown provenance (Reconciliation §3); (d) its importer already caused a **live data-integrity
  regression** (26 of 56 live `tasks.json` rows carry non-resolving `project` values). It is
  **rejected as a source of truth and as an import candidate** — but its cross-project *ambitions*
  survive as deferred input (D3) and as the H3 horizon, not as approved scope.
- **R2. `roadmap/program/import_program_roadmap.py` and `ready_tasks.py` as an import/worktree
  mechanism.** Superseded on arrival by the validated `scripts/import_tasks.py` /
  `command_center/task_import.py` pipeline. **Do not run them.**
- **R3. Program roadmap `PORTFOLIO` / `PLATFORM` scopes (and the Level 4/6/8 `FED-*` / `MARKET-*` /
  `GLOBAL-001` items) as committed scope.** They have no canonical project home and belong to H3.
- **R4. Any script-level extension of `PROJECT_NAME_ALIASES` / `PROJECT_IDS`** to make the roadmap
  package import. Registry changes require a deliberate founder decision recorded in a new ADR (F3).

## 8. Required follow-ups (not performed here — this is a decision, not an implementation)

- **F1.** Refresh `README.md` and `CHANGELOG.md` against `main` (ADR 0001–0005; PR #9). Conflict 3.
- **F2.** Resolve the live `data/tasks.json` integrity issue (26/56 non-resolving `project` values)
  by re-validating/remapping through `scripts/import_tasks.py` — a data decision, flagged for the
  founder, **not** resolved here (runtime/data untouched per this task's constraints).
- **F3.** Write the missing **registry-expansion ADR** recording the 9-project `PROJECT_IDS` and the
  validating task-import pipeline. Note: ADR 0004/0005 were written for other topics (autonomy), so
  the registry ADR recommended by Reconciliation §4.1 **still does not exist**.
- **F4.** Run the single three-backlog triage pass (§7 Deferred) and then a refreshed Founder Audit
  against current `main` (`FOUNDER_..._STATUS.md` required steps 1–5).
- **F5.** Correct the "six projects" language in `docs/desktop/PRODUCT_VISION.md` §1/§5 and
  `WORKSPACE_HOME_SPEC.md` to the canonical registry (§4 / A2).

---

*This record settles the program-governance questions above so that a later
`MASTER_PRODUCT_ROADMAP.md` may be built. It merges nothing, imports nothing, and changes no runtime
code or data; it draws its authority boundaries from live code and the reviewed in-repo document
set, and explicitly declines to approve the existing program roadmap.*
