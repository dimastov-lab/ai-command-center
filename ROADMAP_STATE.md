# Program Roadmap State

Updated: 2026-08-08 18:53 MSK
Current wave: **Wave 0 — executing; gate not accepted**
Tracking issue: [#163](https://github.com/dimastov-lab/ai-command-center/issues/163)

## Dependency gate

Wave 1 is blocked until every Wave 0 row below has verified canonical SHA, automation ownership, snapshot/data-root evidence, and no unresolved duplicate persistent root. PR #157 and PR #158 must remain separate and unmerged.

## Required provenance

| task_id | repository | worktree | branch | base_sha | head_sha | tests | pr | ci | accepted_sha | deployed_sha |
|---|---|---|---|---|---|---|---|---|---|---|
| W0-AICC-BASELINE | `dimastov-lab/ai-command-center` | `/Users/dmitrijcernikov/Projects/_worktrees/ai-command-center/wave0-baseline-20260808` | `codex/wave0-baseline-20260808` | `4295f9b0c70e3ae3a5f37a209b805723fb067549` | branch HEAD (PR head is authoritative) | Ruff/compile pass; pytest `2869 passed` | pending | live-main CI + boundary: success | pending | production observed `4295f9b0c70e3ae3a5f37a209b805723fb067549` |
| W0-AIOS-BASELINE | `dimastov-lab/aios` | no writer claimed | none | `12002501573b32ba731261f93d98a35685342c9d` | n/a | live-main CI + capability: success | none open | success | pending | unverified |
| W0-AICOS-BASELINE | `dimastov-lab/aicos-runtime` / `aicos-specs` | no writer claimed | none | runtime `1ac9620d890e5ce59695e344b0bc72d348acabc0`; specs `1b3386d4de4baee66af3259154906796ebc0e3fd` | n/a | runtime CI: success; specs: no CI | none open | runtime success | pending | unverified |
| W0-AML-ESF-GR-BASELINE | AML / ESF / Golden Record | no writer claimed | none | AML `e7e2c73523e8b28a1b330a940990a6ec2834e02a`; ESF `a5df81ca1175f49f62a1b1c4039c5c5d1df616c6`; GR `fe345fcd221ac9a3b033ced42bf707f12527678b` | n/a | exact-main CI: success | ESF #24 open/DIRTY; others none | success at recorded SHAs | pending | ESF SHA unprovable; AML/GR stopped |

## Wave 0 evidence

- AICC canonical Git and production code SHA are both `4295f9b...`; live data root is `/Users/dmitrijcernikov/Projects/ai-command-center/data`.
- AICC daily-audit is unloaded. Runtime pipeline settings are fail-closed (`enabled=false`, auto launch/merge/rework/remediation=false); state mtimes stayed stable after the 15-second tick interval.
- AICC snapshot: `/Users/dmitrijcernikov/Projects/ai-command-center/data/backups/wave0-20260808T1852-msk`; checksum manifest present; SQLite integrity is `ok`.
- AIOS control-state snapshot: `/Users/dmitrijcernikov/Projects/_local-backups/wave0-20260808/aios-control.tar`, SHA-256 `65a7ee0b9f60053b6ab9ab4b3c635ee3fe413b01c0982adbd97aee0dbefbcb63`; AICOS has no persistent DB to snapshot.
- AML PostgreSQL snapshot: `/Users/dmitrijcernikov/Projects/_local-backups/wave0-20260808/aml_aml_pgdata-20260808.tar.gz`, SHA-256 `fa4c7cf37db185bd0bba589c083c8e607896f92980b0adcd4a697eab303e54ad`.
- Golden Record PostgreSQL snapshot: `/Users/dmitrijcernikov/Projects/_local-backups/wave0-20260808/golden-record_gr_pgdata-20260808.tar.gz`, SHA-256 `f547255df6796cfeb144d78f0c77876375e70b1679e5d81755f91da5c61cc565`.
- ESF verified backup: `/Users/dmitrijcernikov/.config/esf/backups/pre-acceptance-20260808.dump`, SHA-256 `529aaa6499e83c6ddececbc5a62ccabeff940391f3851b9f9f782a62bfc0e093`.

## Open Wave 0 blockers

- AIOS uses relative `.aios` locally and `/data/aios` in containers; no accepted canonical deployment root or general run-to-Git-SHA link exists.
- AICOS runtime DB is cwd-relative and absent; its AIOS baseline lock is 22 commits stale.
- AML canonical volume is snapshotted, but `aml-governance-platform_pgdata` remains an unresolved duplicate-root decision.
- ESF has a dormant second DB volume; deployed image has no Git-SHA label, so deployed SHA is unprovable.
- AICC PR #157 and #158 are draft/behind with failing checks and overlapping history; they are held separate.
- The configured checkout `/Users/dmitrijcernikov/Projects/ai-command-center-ci` is absent; canonical work proceeds from the verified AICC repository and isolated Wave 0 worktree.

## Next allowed work

Complete the independent Wave 0 snapshots/root decisions, verify the AICC branch, open (do not merge) the Wave 0 PR, and keep Wave 1 blocked until the acceptance checklist is fully evidenced.
