# Program Roadmap State

Updated: 2026-08-08 21:37 MSK
Current wave: **Wave 2 — AICC PR/worktree reconciliation and safe delivery**
Tracking issue: [#167](https://github.com/dimastov-lab/ai-command-center/issues/167)

## Dependency gate

Wave 1 is accepted on AICC main `d4f245cbef80d2ff5ce36ebc981cdbb0d115430c`. Wave 2 may prepare separate atomic delivery and cleanup proposals, but merge, PR closure, production deploy, history rewrite, legacy-volume deletion, and worktree cleanup remain explicit human gates. PR #157 and PR #158 remain separate and unmerged.

## Required provenance

| task_id | repository | worktree | branch | base_sha | head_sha | tests | pr | ci | accepted_sha | deployed_sha |
|---|---|---|---|---|---|---|---|---|---|---|
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

## Deferred human gates

- Draft PR review/ready/merge requires explicit permission; no accepted delivery head has been merged or deployed.
- ESF production run-to-Git-SHA proof and the stale AICOS AIOS baseline belong to Wave 1 provenance, not the Wave 0 data-root gate.
- Legacy-volume restore drills, migration, or deletion remain destructive operations requiring separate authorization.
- PR closure and worktree cleanup require a fresh owner/process/dirt/ancestry check plus explicit approval; the reconciliation classifications are proposals only.

## Next allowed work

Finish the Wave 2 governance suite, open its draft PR, and require exact-head CI on both Wave 2 branches. Do not merge, close PRs, deploy, rewrite history, or delete worktrees without explicit approval.
