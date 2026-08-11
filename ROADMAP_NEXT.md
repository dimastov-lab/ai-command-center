# ROADMAP_NEXT

Nearest-horizon program status by lane. One line per task; details live in
each task's PR. Companion to `roadmap/program/PROGRAM_ROADMAP.md` (dependency
graph) and `ROADMAP_STATE.md` (wave provenance).

## PRODUCT lane — complete (2026-08-11)

Repository: `dimastov-lab/aios-product` (private). All PRs merged to `main`.

| task | PR | merge SHA | status |
|---|---|---|---|
| PRODUCT-POS-001 | #6 | `d44cff44d0677e94a1b3efcb405b866a1cb4abb6` | merged — positioning decision-complete, pending founder sign-off (G7) |
| PRODUCT-LIC-001 | #5 + #7 | `c7c7f0b` (initial), `463be04f1f4553d942e1c6c1decaa2209372f434` (reconcile) | merged — reconciled to positioning 0.4.0 |
| PRODUCT-README-001 | #8 | `8cc4d410f6ebba4a7c38fb83acf22264ba5b61f1` | merged — canonical texts ready; apply-to-repos gated by G7 |
| PRODUCT-WEB-001 | #9 | `925824b6538695113a8cc8dc00f1a275ddf91879` | merged — IA complete; public launch gated (G3/G4/G7) |
| PRODUCT-DOCS-001 | #10 | `5a55e3e89115b4c5c8d408882a93b67e2a9f9211` | merged — portal baseline; internal-first |
| PRODUCT-PRICE-001 | #11 | `a75799a3afe9ef592e8fc6f6a9a99e85a1e07fcb` | merged — internal-only model |
| manifest sync | #12 | `eea6c2ba9ea5f7571413519a1b1751d074defbe7` | merged — section statuses synced |

## Not started (blocked by design)

- PRODUCT-WEB-002, PRODUCT-SALES-001 — wait on AICOS-MVP-001.
- PRODUCT-LAUNCH-001 — final gate, after WEB-002/SALES-001.

## Open founder gates (recorded in the positioning document, §9)

G1 legal entity · G2 probe fork · G3 licensing reconciliation · G4 naming ·
G5 probe budget · G6 editions decision · G7 sign-off + downstream reconciliation.
