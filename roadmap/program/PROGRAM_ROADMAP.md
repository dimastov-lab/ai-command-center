# Global AI Platform — Program Roadmap

Всего задач: **46**.

Задачи сгруппированы по уровням зависимостей. Внутри уровня одновременно запускаются только задачи, у которых выполнены `depends_on` и отсутствуют конфликты worktree/репозиториев.

## Level 0 — Foundation

| ID | Project | Task | Depends on | Parallel group | Critical |
|---|---|---|---|---|---|
| `AIOS-RECON-001` | AIOS | AIOS main reconciliation | — | `L0-A` | Yes |
| `AICOS-RECON-001` | AICOS | AICOS Wave 4 and Portfolio reconciliation | — | `L0-A` | Yes |
| `AICOS-ARCH-001` | AICOS | Tenant isolation enforcement architecture | — | `L0-A` | Yes |
| `AICC-D1-001` | AI_COMMAND_CENTER | Desktop shell and Workspace Home | — | `L0-B` | Yes |
| `AICC-D1-002` | AI_COMMAND_CENTER | Task import and upload pipeline | — | `L0-B` | Yes |
| `AICC-D1-003` | AI_COMMAND_CENTER | Reliable execution queue | — | `L0-B` | Yes |
| `AICC-D1-004` | AI_COMMAND_CENTER | Project intelligence and recommendations | — | `L0-B` | No |
| `PRODUCT-POS-001` | AIOS_PRODUCT | Complete Product Positioning | — | `L0-C` | Yes |

## Level 1 — Platform Stabilization

| ID | Project | Task | Depends on | Parallel group | Critical |
|---|---|---|---|---|---|
| `AIOS-REL-001` | AIOS | Packaging and CI baseline | AIOS-RECON-001 | `L1-AIOS` | Yes |
| `AIOS-IDENT-001` | AIOS | Identity and tenant foundation | AIOS-RECON-001, AICOS-ARCH-001 | `L1-AIOS` | Yes |
| `AIOS-API-001` | AIOS | Public API stabilization | AIOS-RECON-001, AIOS-IDENT-001 | `L1-AIOS-B` | Yes |
| `AIOS-WS-001` | AIOS | Workspace lifecycle | AIOS-RECON-001, AIOS-IDENT-001 | `L1-AIOS-B` | Yes |
| `AIOS-MEM-001` | AIOS | Memory API and reconciliation cleanup | AIOS-RECON-001 | `L1-AIOS` | Yes |
| `AIOS-SDK-001` | AIOS | SDK baseline | AIOS-REL-001, AIOS-API-001 | `L1-AIOS-C` | Yes |
| `AICOS-W5-001` | AICOS | Functional Architecture Wave 5 | AICOS-RECON-001 | `L1-AICOS` | Yes |
| `AICC-D2-001` | AI_COMMAND_CENTER | Parallel execution supervisor | AICC-D1-001, AICC-D1-002, AICC-D1-003 | `L1-AICC` | Yes |
| `AICC-D2-002` | AI_COMMAND_CENTER | Universal Workspace Manager | AICC-D1-001 | `L1-AICC` | Yes |
| `AICC-D2-003` | AI_COMMAND_CENTER | AI Agent Registry | AICC-D1-003 | `L1-AICC` | No |
| `AICC-D2-004` | AI_COMMAND_CENTER | Program dependency engine | AICC-D1-004 | `L1-AICC` | Yes |
| `PRODUCT-WEB-001` | AIOS_PRODUCT | Website information architecture | PRODUCT-POS-001 | `L1-PRODUCT` | No |
| `PRODUCT-README-001` | AIOS_PRODUCT | Canonical README standards and repository messaging | PRODUCT-POS-001, AIOS-RECON-001, AICOS-RECON-001 | `L1-PRODUCT` | No |
| `PRODUCT-LIC-001` | AIOS_PRODUCT | Licensing and packaging policy | PRODUCT-POS-001 | `L1-PRODUCT` | No |

## Level 2 — Cross-project Integration

| ID | Project | Task | Depends on | Parallel group | Critical |
|---|---|---|---|---|---|
| `AICOS-AML-001` | AICOS | Canonical AML object model | AICOS-W5-001 | `L2-AICOS` | Yes |
| `AICOS-RULES-001` | AICOS | Rule engine specification | AICOS-W5-001 | `L2-AICOS` | No |
| `AICOS-XAI-001` | AICOS | Explainability layer specification | AICOS-W5-001 | `L2-AICOS` | No |
| `AICOS-RUNTIME-001` | AICOS | AICOS runtime integration baseline | AIOS-IDENT-001, AIOS-API-001, AIOS-WS-001, AIOS-MEM-001, AIOS-SDK-001, AICOS-AML-001, AICOS-RULES-001, AICOS-XAI-001 | `L2-INTEGRATION` | Yes |
| `AICC-GIT-001` | AI_COMMAND_CENTER | Git Center | AICC-D2-002 | `L2-AICC` | No |
| `PORTFOLIO-CTRL-001` | PORTFOLIO | Canonical project status register | AICOS-RECON-001, AICC-D1-002, AICC-D2-004 | `L2-GOV` | Yes |
| `AICC-INT-001` | AI_COMMAND_CENTER | Cross-project integration center | AICC-D2-001, AICC-D2-002, AICC-GIT-001, AIOS-SDK-001, AICOS-RUNTIME-001, PORTFOLIO-CTRL-001 | `L2-INTEGRATION` | Yes |
| `PRODUCT-DOCS-001` | AIOS_PRODUCT | Documentation portal baseline | PRODUCT-README-001, AIOS-SDK-001 | `L2-PRODUCT` | No |
| `PRODUCT-PRICE-001` | AIOS_PRODUCT | Pricing architecture | PRODUCT-LIC-001 | `L2-PRODUCT` | No |

## Level 3 — Distributed Execution

| ID | Project | Task | Depends on | Parallel group | Critical |
|---|---|---|---|---|---|
| `AIOS-DIST-001` | AIOS | Distributed runtime and remote workers | AIOS-INT-001 | `L3-DIST` | Yes |
| `AIOS-INT-001` | AIOS | AIOS platform integration gate | AIOS-REL-001, AIOS-IDENT-001, AIOS-API-001, AIOS-WS-001, AIOS-MEM-001, AIOS-SDK-001 | `L3-GATES` | Yes |
| `AICOS-MVP-001` | AICOS | AICOS first deployable AML slice | AICOS-RUNTIME-001, AIOS-INT-001 | `L3-MVP` | Yes |
| `AICC-DIST-001` | AI_COMMAND_CENTER | Distributed scheduler and capacity planner | AICC-INT-001, AIOS-DIST-001 | `L3-DIST` | Yes |

## Level 4 — Federation

| ID | Project | Task | Depends on | Parallel group | Critical |
|---|---|---|---|---|---|
| `FED-001` | PLATFORM | Federation protocol and trust model | AIOS-DIST-001, AICC-DIST-001 | `L4-FED` | Yes |
| `FED-002` | PLATFORM | Federated capacity execution | FED-001 | `L4-FED` | Yes |

## Level 5 — Self Development

| ID | Project | Task | Depends on | Parallel group | Critical |
|---|---|---|---|---|---|
| `AICC-SELF-001` | AI_COMMAND_CENTER | Self-development planning engine | AICC-INT-001, AICC-D2-004, AICC-GIT-001, PORTFOLIO-CTRL-001, FED-002 | `L5-SELF` | Yes |

## Level 6 — Product Ecosystem

| ID | Project | Task | Depends on | Parallel group | Critical |
|---|---|---|---|---|---|
| `MARKET-001` | PLATFORM | Marketplace and plugin SDK | FED-002, AICC-SELF-001 | `L6-ECOSYSTEM` | No |
| `PRODUCT-WEB-002` | AIOS_PRODUCT | Public website and product pages | PRODUCT-WEB-001, PRODUCT-DOCS-001, PRODUCT-PRICE-001 | `L6-PRODUCT` | No |
| `PRODUCT-SALES-001` | AIOS_PRODUCT | Enterprise sales kit | PRODUCT-LIC-001, PRODUCT-PRICE-001, AICOS-MVP-001 | `L6-PRODUCT` | No |

## Level 7 — Enterprise

| ID | Project | Task | Depends on | Parallel group | Critical |
|---|---|---|---|---|---|
| `AIOS-ENT-001` | AIOS | Enterprise platform baseline | AIOS-INT-001, AIOS-IDENT-001 | `L7-ENTERPRISE` | Yes |
| `AICOS-ENT-001` | AICOS | AICOS enterprise controls | AICOS-MVP-001, AIOS-ENT-001 | `L7-ENTERPRISE` | No |
| `AICC-ENT-001` | AI_COMMAND_CENTER | Command Center enterprise operations | AICC-INT-001, AIOS-ENT-001 | `L7-ENTERPRISE` | No |

## Level 8 — Global Platform

| ID | Project | Task | Depends on | Parallel group | Critical |
|---|---|---|---|---|---|
| `PRODUCT-LAUNCH-001` | AIOS_PRODUCT | Platform launch readiness | PRODUCT-DOCS-001, PRODUCT-WEB-002, PRODUCT-SALES-001, AICOS-MVP-001 | `L8-GATES` | No |
| `GLOBAL-001` | PLATFORM | Global AI Platform founder gate | AICC-SELF-001, MARKET-001, AIOS-ENT-001, AICOS-ENT-001, AICC-ENT-001, PRODUCT-LAUNCH-001 | `L8-GATES` | Yes |
