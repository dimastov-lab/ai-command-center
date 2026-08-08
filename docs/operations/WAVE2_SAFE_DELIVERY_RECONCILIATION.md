# Wave 2 safe-delivery reconciliation

Snapshot: 2026-08-08 21:29 MSK. Owner for every row is `dimastov-lab` unless
stated otherwise. A decision of **close-candidate** is a proposal only: this
wave does not close, merge, rebase, force-push, deploy, or delete anything.

Canonical AICC `main` is
`d4f245cbef80d2ff5ce36ebc981cdbb0d115430c`. The historical AICC PR base
`7963a4a3f8c835f16cc6ca6b23036085b7ceef77` is 17 main-side commits behind.

## Pull requests

| Repo / PR | Head | Base | Completed CI | Decision and recovery evidence |
|---|---|---|---|---|
| AICC #145 | `7a4f5b8d7e0594713c4fb1c107b3824a53f21e9b` | `7963a4a` | none | **close-candidate**: conflicting shared head; recover `format_age` alone from `439e815` only under a new issue/test. |
| AICC #146 | `7a4f5b8d7e0594713c4fb1c107b3824a53f21e9b` | `7963a4a` | none | **close-candidate**: same shared head as #145/#151; recover provider-id intent from `66e3ce0` separately if still required. |
| AICC #148 | `e9db97c8e4d268383c41e51134de949999b50ce8` | `7963a4a` | boundary/Windows success; Linux failure | **close-candidate**: same head as #149, stale evidence; re-audit the task-store repair against canonical data before any new branch. |
| AICC #149 | `e9db97c8e4d268383c41e51134de949999b50ce8` | `7963a4a` | boundary/Windows success; Linux failure | **close-candidate**: same head as #148; documentation intent is recoverable from `123b402` without the shared ancestry. |
| AICC #150 | `c7ef41f918b75f383554cb800e035847c81d34c8` | `7963a4a` | none | **close-candidate**: conflicting stacked head; auto-commit behavior requires a new safety review, not replay. |
| AICC #151 | `7a4f5b8d7e0594713c4fb1c107b3824a53f21e9b` | `7963a4a` | none | **close-candidate**: shared with #145/#146; dry-run intent must be isolated from sequencer ancestry. |
| AICC #152 | `1f2073825d33438e7f0d79f75f7387e2dbe69e2f` | `7963a4a` | none | **close-candidate**: same stacked head as #153–#156; recover only its `.aider` hygiene delta under a new branch. |
| AICC #153 | `1f2073825d33438e7f0d79f75f7387e2dbe69e2f` | `7963a4a` | none | **close-candidate**: shared/conflicting head; re-test Closed-lane behavior on current main before reissue. |
| AICC #154 | `1f2073825d33438e7f0d79f75f7387e2dbe69e2f` | `7963a4a` | none | **close-candidate**: shared/conflicting head; current main already has fail-closed task-store handling, so do not replay. |
| AICC #155 | `1f2073825d33438e7f0d79f75f7387e2dbe69e2f` | `7963a4a` | none | **close-candidate**: shared/conflicting head; persistence needs a fresh sequencer boundary decision. |
| AICC #156 | `1f2073825d33438e7f0d79f75f7387e2dbe69e2f` | `7963a4a` | none | **close-candidate**: shared/conflicting head; auto-fix/auto-complete chaining is forbidden by the Wave 2 gate. |
| AICC #157 | `1e913b0ab4345a88c7b856bf6cd2ae2338845ff6` | `7963a4a` | boundary/Windows success; Linux failure | **supersede / close-candidate**: 64 branch-only commits and 97 files diverge from current main; it bundles #145–#156, a forbidden auto-complete chain, and unrelated SDK/UI/runtime changes. Its only tail changes fake sleep 5s→30s; both affected tests pass on current main with 5s (`2 passed in 7.70s`), so the tail defect was not reproduced and is not reissued. |
| AICC #158 | `ce67fa436d35fbe36ad38fba8b1447daef5c71a6` | `7963a4a` | boundary success; Linux/Windows failure | **superseded delivery / close-candidate**: it carries all #157 ancestry. Its sole unique commit `ce67fa4` (`models.py` + tests) is extracted without ancestry as draft #168. The extraction also fixes the original `max_len=0` contract violation. |
| AICC #168 | `605a03c6031d605ac535756bafcb90f7320bfc4b` | `d4f245c` | boundary success; Linux/Windows pending at snapshot | **keep / remediate**: atomic #158 extraction, separate worktree and branch; require exact-head completed CI and human review. |
| ESF #24 | `a4b3c451ac080f3c0dd1bab19f1be2d73b83cbfd` | `b66dc8e` | lint/audit/docker/test success | **close-candidate**: conflicting one-file project-state snapshot; ESF main is now `5d2fada0cbba52029ea84a5b23a7f37b1d775317`. Verify current PROJECT_STATE directly rather than merge stale status prose. |

PR #157 and #158 therefore remain separate and untouched. Draft #168 contains
only the two-file #158 intent; it does not carry, merge, or close #157.

## Linked AICC worktrees

`dirty` is the exact porcelain line count at the snapshot. No candidate below
is approved for deletion.

| Worktree | Branch / head | dirty | Classification / owner |
|---|---|---:|---|
| `/Users/dmitrijcernikov/Projects/ai-command-center` | `main` / `4295f9b` | 0 | **retain**, canonical operator checkout; intentionally behind live remote. |
| `.ai-command-center-production-daily-audit-worktrees/0cea…` | `codex/daily-audit-20260806-0cea1075` / `5597ab1` | 0 | **stale-candidate**, daily-audit owner; no daily-audit process observed. |
| `.ai-command-center-production-daily-audit-worktrees/4de1…` | `codex/daily-audit-20260806-4de12364` / `0975560` | 0 | **stale-candidate**, daily-audit owner; no process observed. |
| `.ai-command-center-production-daily-audit-worktrees/5bfd…` | `codex/daily-audit-20260806-5bfd62ad` / `be5e564` | 0 | **stale-candidate**, daily-audit owner; no process observed. |
| `.ai-command-center-production-daily-audit-worktrees/b232…` | `codex/daily-audit-20260806-b232907b` / `9f45e8a` | 0 | **stale-candidate**, daily-audit owner; no process observed. |
| `.ai-command-center-production-daily-audit-worktrees/c68d…` | `codex/daily-audit-20260806-c68d558f` / `a3c1e8b` | 0 | **stale-candidate**, daily-audit owner; head already in main, no process observed. |
| `.ai-command-center-production-daily-audit-worktrees/cdd9…` | `codex/daily-audit-20260806-cdd94a00` / `53efb25` | 0 | **stale-candidate**, daily-audit owner; no process observed. |
| `.ai-command-center-production-daily-audit-worktrees/eb1a…` | `codex/daily-audit-20260806-eb1a07f9` / `b7a4475` | 0 | **stale-candidate**, daily-audit owner; no process observed. |
| `_worktrees/ai-command-center/p2-native-sections` | `codex/p2-native-sections` / `6d1b8ed` | 0 | **stale-candidate**, #159 delivery lineage retained for recovery. |
| `_worktrees/ai-command-center/wave0-baseline-20260808` | `codex/wave0-baseline-20260808` / `b8c247c` | 1 | **retain**, user-owned `ROADMAP_STATE.md` deletion; never clean automatically. |
| `_worktrees/ai-command-center/wave1-provenance-20260808` | `codex/wave1-provenance-20260808` / `4c4d914` | 0 | **retain**, merged Wave 1 evidence. |
| `_worktrees/ai-command-center/wave2-safe-delivery-20260808` | `codex/wave2-safe-delivery-20260808` / `d4f245c` | 2 | **active**, Wave 2 governance writer. |
| `_worktrees/ai-command-center/pr158-truncate-text-20260808` | `codex/pr158-truncate-text-20260808` / `605a03c` | 0 | **active**, W2-AICC-PR158-EXTRACT-001. |
| `ai-command-center-codex-provider` | `feature/codex-execution-provider` / `a83d11d` | 0 | **stale-candidate**, provider delivery recovery; no process observed. |
| `ai-command-center-mobile` | `feature/mobile-companion` / `bc7af9a` | 0 | **retain**, separate mobile feature owner. |
| `ai-command-center-production` | `feat/esf-aml-registry` / `f45e041` | 0 | **retain**, production-named recovery checkout. |
| `ai-command-center-production-4295f9b` | detached / `4295f9b` | 0 | **retain**, active Streamlit PID 79974 and launchd `com.ai-command-center.ui`. |
| `ai-command-center-production-canonical` | detached / `7963a4a` | 0 | **retain**, canonical rollback snapshot. |
| `ai-command-center-win-d1-runner` | `chore/windows-d1-runner` / `11282b1` | 0 | **retain**, external Windows acceptance evidence. |
| `ai-command-center/.claude/worktrees/admiring-feynman-e54554` | `claude/admiring-feynman-e54554` / `d714978` | 0 | **stale-candidate**, Claude owner; no process observed. |
| `ai-command-center/.claude/worktrees/wizardly-dubinsky-2210c4` | `claude/wizardly-dubinsky-2210c4` / `83230f0` | 0 | **stale-candidate**, Claude owner; no process observed. |

## Manual delivery checklist

1. Bind the candidate SHA to the PR `headRefOid`; mismatch is a hard stop.
2. Require at least one check and require every relevant check to be
   `COMPLETED/SUCCESS` for that exact SHA. Missing, pending, cancelled, skipped,
   neutral, timed-out, or failed evidence is not green.
3. Keep the PR draft until human review; never turn a green check into an
   automatic merge, close, deployment, or chained follow-on task.
4. Record candidate, PR head, completed checks, acceptance, and target-verified
   deployment independently. Unknown evidence remains unknown.
5. Cleanup is a separate human-approved operation. Re-check dirt, process use,
   ancestry, recovery branch, and owner immediately before any deletion.
