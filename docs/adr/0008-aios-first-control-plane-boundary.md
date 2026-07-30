# ADR-0008: AIOS-first boundary for AI Command Center

Status: **Accepted**

## Context

AIOS is the single infrastructure engine and system of record for execution
lifecycle, orchestration, policy, audit, validation and completion. AI Command
Center is its operator-facing control plane. The existing local
`command_center.runtime` predates this boundary and must not become a second
platform engine.

## Decision

The desktop application consumes AIOS only through versioned public API, event
and SDK contracts. Presentation code depends on application-layer ports and
DTOs; it does not read an AIOS database, launch an AIOS process, or infer AIOS
readiness from the legacy local runtime.

The first integration is read-only Core status:

- version;
- health and readiness;
- declared capabilities;
- acceptance gates;
- evidence references.

Network transport is disabled by default. It is enabled only when
`AICC_AIOS_STATUS_ENABLED=1`, an HTTPS `AICC_AIOS_STATUS_URL`, bearer token,
tenant identifier and explicit host allowlist are all configured. Redirects are
accepted only when their final URL still satisfies the same HTTPS allowlist.
Offline and invalid-contract responses are rendered as explicit states. There
is no fallback to `command_center.runtime`, no dual-write and no management
command.

## Contract dependency

AIOS does not yet expose a confirmed stable endpoint for this projection.
`GET /v1/core/status` and its fields are therefore an AICC-side proposed
dependency, not a claim about current AIOS. Until AIOS accepts and implements a
versioned contract, the UI must show **«Контракт ожидается»** and must not invent
version, health, capabilities, gates or evidence.

Required AIOS dependency: publish an authenticated, tenant-safe, versioned,
read-only Core-status contract with bounded payloads, structured non-sensitive
evidence references and stable readiness semantics. A response is accepted only
when its `contract`, `contract_version` and `tenant_id` match the configured
expectations.

## Consequences

- New engine capabilities are prohibited in AI Command Center.
- Existing runtime code remains temporarily for compatibility but receives no
  new AIOS-equivalent responsibilities.
- Each later migration slice maps one legacy capability to an accepted AIOS
  contract, verifies parity, cuts reads or commands over behind a flag, and only
  then retires the duplicate implementation.
- AML and Golden Record development remain outside this work and blocked until
  AIOS Core acceptance.
