# AI Command Center — Desktop Documentation

**Current status: D0 — documentation only.** No desktop production code exists yet. Nothing
under `command_center.desktop`, `command_center.application`, or `command_center.platform` has
been implemented — every package name in this documentation set is target structure, clearly
marked as such in each document. **Next implementation stage: D1A** (dependency and package
skeleton — see `IMPLEMENTATION_ROADMAP.md`).

This directory converts the reviewed Lightweight Cross-Platform Desktop Architecture and Design
into a canonical, implementation-ready documentation set for a native PySide6/Qt Widgets desktop
application.

## Reading order

1. **`PRODUCT_VISION.md`** — why this product exists, who it is for, and its boundary relative
   to AIOS/AICOS. Read this first.
2. **`ARCHITECTURE.md`** — the target package architecture, dependency rules, threading model,
   and lifecycle.
3. **`INFORMATION_ARCHITECTURE.md`** — navigation structure, eventual and Desktop Increment 1.
4. **`DESIGN_DIRECTIONS.md`** — the three design alternatives considered and the approved
   direction.
5. **`DESIGN_SYSTEM.md`** — implementation-ready tokens and component contracts for the approved
   direction.
6. **`WORKSPACE_HOME_SPEC.md`** — the native Home page's spec, built on the existing
   `build_workspace_home_snapshot` read model.
7. **`PLATFORM_BEHAVIOR.md`** — macOS and Windows 11 behavior and the platform abstraction
   contract.
8. **`DESKTOP_INCREMENT_1.md`** — the frozen scope for D1–D4, with acceptance criteria per stage.
9. **`IMPLEMENTATION_ROADMAP.md`** — the small, commit-sized steps that build D1–D4.

## Purpose of each document

| Document | Purpose |
|---|---|
| `PRODUCT_VISION.md` | Product purpose, boundary, and relationship to AIOS/AICOS |
| `ARCHITECTURE.md` | Target package architecture, dependency rules, thread/lifecycle model |
| `INFORMATION_ARCHITECTURE.md` | Navigation structure, eventual and Desktop Increment 1 |
| `DESIGN_DIRECTIONS.md` | Design alternatives considered; the approved direction and why |
| `DESIGN_SYSTEM.md` | Design tokens and component contracts |
| `WORKSPACE_HOME_SPEC.md` | Native Home page spec, built on the existing read model |
| `PLATFORM_BEHAVIOR.md` | macOS/Windows behavior and the platform abstraction contract |
| `DESKTOP_INCREMENT_1.md` | Frozen D1–D4 scope and acceptance criteria |
| `IMPLEMENTATION_ROADMAP.md` | Commit-sized implementation sequence for D1–D4 |

## Binding decisions

The following are treated as approved throughout this documentation set, not open questions:

1. AI Command Center is a lightweight, local-first, installable desktop development tool.
2. Its daily-use interface is desktop-native, not browser-based.
3. It remains a single-user developer control plane.
4. It is not the AICOS operational AML workplace.
5. It does not connect directly to banking systems, data marts, queues, enterprise buses, or
   customer data.
6. macOS Apple Silicon and Windows 11 x64 are the first target platforms.
7. PySide6 with Qt Widgets is the selected desktop framework.
8. The desktop application runs in one local process and must not require a browser, a local
   HTTP server, or a separate Python installation for packaged builds.
9. Existing `command_center/*` runtime and read-model modules are reused, not rewritten.
10. Existing Streamlit functionality remains available until native parity is deliberately
    achieved.
11. Desktop Increment 1 is read-only except for repository-path configuration, theme/window
    preferences, and window geometry.
12. Starting agents, cancellation, streaming, git writes, multi-session control, an embedded
    terminal, auto-update, production signing, server mode, SSO, and AICOS interfaces are out of
    scope for Desktop Increment 1.
13. Design Direction A — Professional Control Plane — is approved (see `DESIGN_DIRECTIONS.md`).
14. The design reserves forward-compatible affordances for future mission-control features
    without prematurely implementing them (see `DESIGN_DIRECTIONS.md` §5).

See `DESKTOP_INCREMENT_1.md` for the full, binding scope definition these decisions constrain.
