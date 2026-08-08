# Program Roadmap State

Updated: 2026-08-09
Current wave: **Wave 5 — AICC to AIOS SDK boundary and decomposition**
Tracking issue: [#174](https://github.com/dimastov-lab/ai-command-center/issues/174)

## Dependency gate

Wave 5 TasksGateway is accepted on AICC main `9613d805041ac581cec885f25e50dcdd73eae72f`; AIOS SDK is accepted on main `acaa035386a4c9aca4bf901c24c1669745d8405f`. W5-AICC-SDK-CONTRACT-001 is the dependent immutable artifact and read-only status cutover only. Supervisor/provider/execution cutover, merge, production deploy, PR closure, and worktree cleanup remain explicit later gates. PR #157 and PR #158 remain untouched.

## Required provenance

| task_id | repository | worktree | branch | base_sha | head_sha | tests | pr | ci | accepted_sha | deployed_sha |
|---|---|---|---|---|---|---|---|---|---|---|
| W5-AICC-SDK-CONTRACT-001 | `dimastov-lab/ai-command-center` | `/Users/dmitrijcernikov/Projects/_worktrees/ai-command-center/wave5-sdk-contract-20260809` | `codex/wave5-sdk-contract-20260809` | `9613d805041ac581cec885f25e50dcdd73eae72f` | implementation/final head tracked by draft PR | RED import/collection failure; focused `44 passed`; full exactly once `2939 passed, 1 skipped, 1 environment failure` (missing local Chromium); targeted browser remediation `1 passed`; 86% coverage; Ruff/compile/diff clean | issue [#176](https://github.com/dimastov-lab/ai-command-center/issues/176); draft PR pending | pending exact-head Linux/Windows/boundary CI | unknown until exact-head CI + human review | unknown; no deploy |
| W5-AICC-TASKS-GATEWAY-001 | `dimastov-lab/ai-command-center` | `/Users/dmitrijcernikov/Projects/_worktrees/ai-command-center/wave5-tasks-gateway-20260809` | `codex/wave5-tasks-gateway-20260809` | `802ac54e9a1838c1ac7157571eed291a7accbfce` | implementation `04a8646326d9f3b7b7a0b3535adb671d779ccaed`; remediation/final head tracked by PR | RED import/collection failure; focused `123 passed`; full exactly once `2933 passed`, 185 warnings, 86% coverage; review remediation `76 passed`, cross-process contract `16 passed`; Ruff/compileall/diff-check clean | issue [#174](https://github.com/dimastov-lab/ai-command-center/issues/174); draft [#175](https://github.com/dimastov-lab/ai-command-center/pull/175) | fresh exact-head Linux/Windows/boundary CI pending | unknown until exact-head CI + human review | unknown; no deploy |
| W4-AICC-TRUTHFUL-DASHBOARD-001 | `dimastov-lab/ai-command-center` | `/Users/dmitrijcernikov/Projects/_worktrees/ai-command-center/wave4-truthful-dashboard-20260809` | `codex/wave4-truthful-dashboard-20260809` | `857ddcbbffea87f797382300ee93c787ae7b8c07` | implementation `a8e4047db63d820e6a178d8ac7938250461fdf27`; final docs head tracked by PR | RED import failure; final focused truth/provenance/UI/browser/architecture `64 passed`; broader API/DB/UI/architecture/browser `167 passed`; Chromium keyboard/semantics/320px/200%-text journey `1 passed`; full ran exactly once and produced empty `lastfailed` plus finalized coverage XML, but terminal summary/exit was lost during tool transport, so no local count is claimed; Ruff/compileall/diff-check clean | issue [#172](https://github.com/dimastov-lab/ai-command-center/issues/172); draft [#173](https://github.com/dimastov-lab/ai-command-center/pull/173) | exact-head Linux/Windows/boundary CI in progress; authoritative full-suite gate | unknown until exact-head CI + human review | unknown; no deploy |
| W3-AICC-PROVIDER-ROUTE-001 | `dimastov-lab/ai-command-center` | `/Users/dmitrijcernikov/Projects/_worktrees/ai-command-center/wave3-provider-route-20260808` | `codex/wave3-provider-route-20260808` | `c07ecaac42f180b0c265e60ea8b7f4ffcc956ad0` | implementation `0539cfdbf7085de5d85d6d752dc893f23eb0039d`; final docs head tracked by PR | RED import failure; focused `132 passed`; Supervisor failure/lifecycle `7 passed`; manual hermetic journey green with zero external calls; full ran once: `2909 passed, 1 failed` (legacy policy exception ordering), remediated focused `36 passed`; final route/idempotency `15 passed`; architecture `17 passed`; exact-head CI authoritative | issue [#170](https://github.com/dimastov-lab/ai-command-center/issues/170); draft [#171](https://github.com/dimastov-lab/ai-command-center/pull/171) | pending exact-head Linux/Windows/boundary CI | unknown until exact-head CI + human review | unknown; no deploy |
| W2-AICC-SAFE-DELIVERY-001 | `dimastov-lab/ai-command-center` | `/Users/dmitrijcernikov/Projects/_worktrees/ai-command-center/wave2-safe-delivery-20260808` | `codex/wave2-safe-delivery-20260808` | `d4f245cbef80d2ff5ce36ebc981cdbb0d115430c` | pending commit | RED import failure + inventory fixture failure; focused + boundary `8 passed`; full ran once but terminal summary was lost on transport disconnect, so no local count claimed; Ruff/compile clean | issue [#167](https://github.com/dimastov-lab/ai-command-center/issues/167); draft PR pending | exact-head CI is authoritative full-suite gate | unknown until exact-head CI + human review | unknown; no deploy |
| W2-AICC-PR158-EXTRACT-001 | `dimastov-lab/ai-command-center` | `/Users/dmitrijcernikov/Projects/_worktrees/ai-command-center/pr158-truncate-text-20260808` | `codex/pr158-truncate-text-20260808` | `d4f245cbef80d2ff5ce36ebc981cdbb0d115430c` | `605a03c6031d605ac535756bafcb90f7320bfc4b` | RED `2 failed`; focused `27 passed`; full `2890 passed`, 1 warning; Ruff clean | draft [#168](https://github.com/dimastov-lab/ai-command-center/pull/168); #158 untouched | boundary/Windows success; Linux pending | unknown until exact-head CI + human review | unknown; no deploy |
| W1-AICC-PROVENANCE-001 | `dimastov-lab/ai-command-center` | `/Users/dmitrijcernikov/Projects/_worktrees/ai-command-center/wave1-provenance-20260808` | `codex/wave1-provenance-20260808` | `253ab4591498682f6889438380c3a901952c3485` | `4c4d914766ed7644dd9af2c92271f34b4937261f` | RED import failure; focused `18 + 147 + 20 + 85 + 95 passed`; exact-head CI full suite green | issue [#165](https://github.com/dimastov-lab/ai-command-center/issues/165); merged [#166](https://github.com/dimastov-lab/ai-command-center/pull/166) | Linux/Windows/boundary success | main merge `d4f245cbef80d2ff5ce36ebc981cdbb0d115430c` | unknown; no deploy |
| W0-AICC-BASELINE | `dimastov-lab/ai-command-center` | `/Users/dmitrijcernikov/Projects/_worktrees/ai-command-center/wave0-baseline-20260808` | `codex/wave0-baseline-20260808` | `4295f9b0c70e3ae3a5f37a209b805723fb067549` | branch/PR head | Ruff/compile; pytest `2869 passed`; snapshot checks | [#164](https://github.com/dimastov-lab/ai-command-center/pull/164) | prior head `59592de`: Linux/Windows/boundary success; final docs CI pending | pending final CI | production observed `4295f9b0c70e3ae3a5f37a209b805723fb067549` |
| W0-AIOS-DATAROOT-001 | `dimastov-lab/aios` | `/Users/dmitrijcernikov/Projects/_worktrees/aios/w0-canonical-data-root` | `codex/w0-canonical-data-root` | `12002501573b32ba731261f93d98a35685342c9d` | `57825305e59b915f7aa0aaf1028c3f977ad12c84` | Ruff/mypy; pytest `2461 passed`, `14 skipped` | [#141](https://github.com/dimastov-lab/aios/pull/141) | 10/10 success; CLEAN | `57825305e59b915f7aa0aaf1028c3f977ad12c84` | n/a |
| W0-AICOS-DATAROOT-001 | `dimastov-lab/aicos-runtime` | `/Users/dmitrijcernikov/Projects/_worktrees/aicos-runtime/w0-canonical-data-root` | `codex/w0-canonical-data-root` | `1ac9620d890e5ce59695e344b0bc72d348acabc0` | `0fb93535b23c4a61685e02d8485b3279695633ad` | `12 passed`; boundary clean | [#3](https://github.com/dimastov-lab/aicos-runtime/pull/3) | Python 3.12/3.13 success; CLEAN | `0fb93535b23c4a61685e02d8485b3279695633ad` | n/a |
| W0-AML-DATAROOT-001 | `dimastov-lab/aml` | `/Users/dmitrijcernikov/Projects/_worktrees/aml/w0-root-closure` | `codex/w0-aml-root-closure` | `e7e2c73523e8b28a1b330a940990a6ec2834e02a` | `91f33f25276f74ffbd48bc240f7c0264e7516cb2` | RED 2; focused 2; local 55; PostgreSQL CI | [#6](https://github.com/dimastov-lab/aml/pull/6) | success; CLEAN | `91f33f25276f74ffbd48bc240f7c0264e7516cb2` | n/a |
| W0-ESF-DATAROOT-001 | `dimastov-lab/esf-enterprise-platform` | `/Users/dmitrijcernikov/Projects/_worktrees/esf/w0-root-closure` | `codex/w0-esf-root-closure` | `a5df81ca1175f49f62a1b1c4039c5c5d1df616c6` | `b2e5fc70e747396680687302e5affed6a62cf31a` | RED 3; focused 3; full `275 passed`; 90.52% | [#27](https://github.com/dimastov-lab/esf-enterprise-platform/pull/27) | lint/audit/build/test/fitness success; CLEAN | `b2e5fc70e747396680687302e5affed6a62cf31a` | pre-task SHA unprovable; no deploy |
| W0-GR-SNAPSHOT-001 | `dimastov-lab/golden-record` | snapshot-only; no writer | none | `fe345fcd221ac9a3b033ced42bf707f12527678b` | n/a | exact-main CI success; archive verified | none | success | `fe345fcd221ac9a3b033ced42bf707f12527678b` | stopped; no deploy |

## Wave 0 evidence

- AICC live root is `/Users/dmitrijcernikov/Projects/ai-command-center/data`; daily-audit is unloaded and all pipeline mutation switches are off.
- AICC snapshot `/Users/dmitrijcernikov/Projects/ai-command-center/data/backups/wave0-20260808T1852-msk` passes its SHA-256 manifest and SQLite integrity check.
- AIOS and AICOS delivery heads require explicit fail-closed roots and provide tested non-fabricating snapshot paths.
- AML retains `aml_aml_pgdata`; `aml-governance-platform_pgdata` is `QUARANTINED / NO-USE` and untouched.
- ESF retains production `pg_data` and `esf_storage`; legacy `postgres_data` is `QUARANTINED / NO-USE`, dev persistence is tmpfs, and the active stack was not restarted.
- AML, Golden Record, and ESF backup artifacts are checksummed; all Wave 0 worktrees are retained.

## Wave 1 evidence contract

- One `run_provenance` record binds task/run to canonical repository, worktree, branch, base/head SHA, PR head, completed CI conclusions, accepted SHA, and deployed SHA; absent evidence remains explicitly unknown.
- GitHub acceptance is rejected when CI belongs to a different SHA or the PR is draft. Runtime health/journey without an immutable SHA never proves deployment.
- Deployment evidence is accepted only from a verified GitHub deployment or signed target manifest bound to the accepted SHA. Domain-native payloads retain stable integrity IDs and idempotent storage.
- A bounded, idempotent legacy backfill preserves run history and never promotes a historical worktree path into a fabricated canonical repository.

## Wave 2 safe-delivery evidence

- `delivery_gate.evaluate_delivery` fails closed for a PR/check SHA mismatch, missing/pending/failed CI, or any requested auto-complete chain.
- The reconciliation snapshot classifies 15 observed open PRs and all 21 linked AICC worktrees with owner, recovery evidence, exact heads, CI/dirt, and non-destructive decisions.
- #157 is proposed for supersession as a conflicting 64-commit umbrella. Its sole tail test deflake was not reproduced on current main (`2 passed` with the existing 5-second fake run).
- #158 remains untouched; only its unique two-file intent is extracted to #168 from current main, with `max_len <= 0` explicitly returning empty output.

## Wave 3 provider-route evidence

- Policy filtering precedes provider preference; the selected route is immutable, ordered, distinct, and bounded by its provider count.
- Only explicitly classified provider-local transient failures with verified unchanged workspace evidence advance to the next provider. Authentication, policy, invalid request, cancel, timeout, incomplete result, changed workspace, and unknown exits terminate immediately.
- Canonical provenance records the route policy/reason and append-only attempt outcomes without prompts or secrets. The hermetic acceptance fixture proves transient A to successful B in exactly two attempts, distinct-provider exhaustion, and non-retryable fail-fast without external provider calls.

## Wave 4 truthful Dashboard and accessibility evidence

- One pure projection consumes canonical `TaskSnapshot`, API-enriched run provenance, authoritative `count_runs`, and stale-run evidence; every aggregate identifies its entity, source, and bounded window.
- Dashboard delivery rows distinguish unknown, unaccepted, accepted-but-undeployed, verified deploy, stale runtime, and latest runtime SHA mismatch without promoting green CI or health to acceptance/deployment.
- Status text and semantic roles supplement colour; progress and SVG graphics have accessible names; light/dark status tokens meet WCAG AA contrast; visible focus, keyboard movement, 320 CSS px reflow, and 200% text sizing are exercised in Chromium.

## Wave 5 AICC TasksGateway evidence

- AICC owns the task Protocol, DTOs, and typed safe errors; one adapter is the sole top-level `aios_sdk` import site, while core and SDK-internal imports fail the architecture gate.
- Create uses a deterministic non-secret idempotency key. The validated identity map writes atomically, fails closed on corruption, and reconciles a remote-success/local-write-crash replay without duplicate creation.
- The identity map holds the shared cross-process file lock across reload and read-modify-write, preventing concurrent writers from discarding distinct correlations.
- Remote `Task.state` is authoritative for lanes; `Review` and other lossy transitions fail explicitly, while cancelled tasks are treated as remote tombstones and excluded from live board projection. Outbound payloads exclude prompts and local paths; returned evidence preserves only event and request ID.
- The adapter owns and exposes client close/context-manager lifecycle; mandatory contract tests remain hermetic and do not skip when the AIOS SDK artifact is absent.
- Final AIOS artifact pinning, status/core/execution cutover, Supervisor retirement, merge, and deploy are outside this task.

## Wave 5 immutable SDK and status evidence

- AICC pins the accepted AIOS main `acaa035386a4c9aca4bf901c24c1669745d8405f`, artifact id `9028887683`, and wheel SHA-256 `48cc8b028d6a0f7f4be56d385c502cd5a5bfe34b26de0416d6bf30ad58942a0e`; acquisition has no sibling-path, branch, latest, or vendored fallback.
- Clean CI must download with the existing read-only artifact secret, verify both the artifact manifest and locked checksum, install the exact wheel, and run mandatory SDK tests without import skips. The broad token should be rotated later to a fine-grained Actions-read credential.
- The sole SDK adapter composes health, readiness, whoami, and bounded workspace timeline into an AICC-owned port. Timeline projection allowlists only event id/type/time; request IDs remain evidence while principals, payloads, prompts, and paths do not cross the boundary.
- Tenant/auth/authz/timeout/remote/contract failures are typed and fail closed. Health, readiness, CI, or a timeline never infer `accepted_sha` or `deployed_sha`; both remain unknown without qualifying immutable evidence.

## Deferred human gates

- Draft PR review/ready/merge requires explicit permission; no accepted delivery head has been merged or deployed.
- ESF production run-to-Git-SHA proof and the stale AICOS AIOS baseline belong to Wave 1 provenance, not the Wave 0 data-root gate.
- Legacy-volume restore drills, migration, or deletion remain destructive operations requiring separate authorization.
- PR closure and worktree cleanup require a fresh owner/process/dirt/ancestry check plus explicit approval; the reconciliation classifications are proposals only.

## Next allowed work

Require focused/full verification, exact-head CI, and human review for W5-AICC-SDK-CONTRACT-001. Do not begin Supervisor/provider/execution cutover, merge, close PRs, deploy, rewrite history, or delete worktrees without explicit approval.
