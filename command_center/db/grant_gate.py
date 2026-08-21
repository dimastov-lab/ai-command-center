"""The deployment gate: a migrated database is not ready until its grants land.

Migrations create tables and functions and grant nothing. The privilege matrix
lives in `command_center.db.roles` and is applied by a separate call
(`apply_table_grants()`), which `python -m command_center.db upgrade` makes
immediately after every migration run. Skip that call — a hand-run `psql -f`,
a restore that recreated objects under a new owner, a migration applied by a
one-off script — and the database is structurally correct and *protocol*
broken, because in this schema the grants are not hygiene: they are part of the
claim protocol's correctness.

The concrete failure this module exists to stop, reproduced in
`tests/db/test_grant_gate.py`: PostgreSQL gives `PUBLIC` `EXECUTE` on every new
function by default, and `0002_queue_claim` contains no `GRANT`/`REVOKE` at
all. A worker role therefore reaches `queue_enqueue`, `queue_reap` and
`queue_redrive` — the control plane's recovery surface — and can redrive its
own dead-letter, raising its own attempt budget (measured: `max_attempts` 1 to
6) and re-running work the queue had already given up on. The attempt budget
is the only bound on a poisoned item, so an execution host that can raise it
has escaped the bound.

**What is checked is the catalog, not a marker.** A "grants were applied at
version N" stamp would be cheap and would be a lie the moment someone stamped
it without the grants, or revoked one afterwards; it also cannot see a grant
that was applied but is *wrong*. This module asks PostgreSQL what each role
can actually reach today (`has_*_privilege`, which resolves PUBLIC, membership
and column grants the way the executor will at query time) and compares that
against the declared matrix. The consequence worth stating: "applied after
every migration" is enforced through its observable effect rather than through
bookkeeping — a migration that adds a function leaves it PUBLIC-executable, so
the gate stays red until the grants are re-asserted, while a migration that
adds only an index needs no re-assert and the gate is legitimately green.

Objects the matrix declares but the catalog does not hold yet are skipped, for
the reason `render_table_grants()` skips them: a database standing at an
intermediate version is a downgrade test or a partial upgrade, and the
schema-version half of `check_readiness()` is what fails that case. The
reverse — an object that exists and is not covered by the matrix at all —
stays with `tests/db/test_grant_compliance.py` (#321), which runs as a
superuser in CI: an undeclared table is unreachable by every role, so it is a
policy-coverage defect rather than a live widening, and failing readiness on
it would take a service down for something no attacker can use.

`tests/db/test_grant_compliance.py` reads `information_schema` as a superuser
and is a deliberately independent second implementation; this one reads
`pg_catalog` because it runs at readiness time under `aicc_app`, and
`information_schema.role_table_grants` only shows grants the *current* role is
party to — as `aicc_app` it cannot see the worker's half of the matrix, which
is precisely the half a compromised execution host abuses.
"""

from __future__ import annotations

from dataclasses import dataclass

from command_center.db import roles

__all__ = [
    "GrantViolation",
    "PUBLIC",
    "audit_grants",
    "violation_kinds",
]

#: PostgreSQL's implicit grantee. Accepted by name by the `has_*_privilege`
#: functions, which is what lets the default `EXECUTE` on a new function be
#: asked about in the same way as any role's grant.
PUBLIC = "public"

# The full table privilege vocabulary, asked one by one rather than aggregated,
# so an unexpected `DELETE` or `TRUNCATE` is named in the violation instead of
# hidden inside "does not match".
_TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
_SEQUENCE_PRIVILEGES = ("SELECT", "UPDATE", "USAGE")

_TABLE_KINDS = ("r", "p", "v", "m")  # table, partitioned table, view, matview
_SEQUENCE_KINDS = ("S",)
_CALLABLE_KINDS = ("f", "p")  # function, procedure


@dataclass(frozen=True, slots=True, order=True)
class GrantViolation:
    """One way the live grant graph differs from the declared matrix."""

    kind: str
    role: str
    obj: str
    privilege: str = ""

    def __str__(self) -> str:
        privilege = f" privilege={self.privilege}" if self.privilege else ""
        return f"{self.kind}: role={self.role}{privilege} on={self.obj}"


def violation_kinds(violations: tuple[GrantViolation, ...]) -> list[str]:
    """The distinct kinds present, sorted.

    Exists for `/readyz`, which is commonly unauthenticated: the kinds say what
    is wrong without naming which role reaches which object, and the full list
    goes to the server log and to `python -m command_center.db verify-grants`.
    """
    return sorted({violation.kind for violation in violations})


def audit_grants(conn, schema: str = "public") -> tuple[GrantViolation, ...]:
    """Compare the live grant graph against `roles`. Empty means compliant.

    Read-only, and readable by any role in the cluster: every catalog it
    touches is world-readable and `has_*_privilege` may be asked about a role
    the caller is not a member of. That matters because the caller at readiness
    time is `aicc_app`, which must be able to detect a widening of
    `aicc_worker` without holding any of the worker's rights itself.
    """
    violations: list[GrantViolation] = []
    with conn.cursor() as cur:
        relations = _relations(cur, schema)
        callables = _callables(cur, schema)

        violations.extend(_public_violations(cur, schema, relations, callables))

        cur.execute(
            "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
            (list(roles.GRANTED_ROLES),),
        )
        present = {row[0] for row in cur.fetchall()}

        for role in roles.GRANTED_ROLES:
            if role not in present:
                # Every later check would raise "role does not exist" and abort
                # the audit, turning a missing role into an unverified gate.
                violations.append(GrantViolation("missing_role", role, role))
                continue
            violations.extend(_relation_violations(cur, schema, role, relations))
            violations.extend(_column_violations(cur, schema, role, relations))
            violations.extend(_sequence_violations(cur, schema, role, relations))
            violations.extend(_function_violations(cur, schema, role, callables))

    return tuple(sorted(violations))


# ---------------------------------------------------------------------------
# Catalog reads
# ---------------------------------------------------------------------------


def _relations(cur, schema: str) -> dict[str, tuple[int, str]]:
    """`{name: (oid, relkind)}` for every table, view and sequence in `schema`."""
    cur.execute(
        "SELECT c.relname, c.oid, c.relkind FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relkind::text = ANY(%s)",
        (schema, list(_TABLE_KINDS + _SEQUENCE_KINDS)),
    )
    return {name: (oid, kind) for name, oid, kind in cur.fetchall()}


def _callables(cur, schema: str) -> dict[int, str]:
    """`{oid: rendered signature}` for every function and procedure in `schema`.

    Keyed by oid rather than by name because the matrix grants per signature:
    two overloads are two grant decisions, and a name-keyed comparison would
    read "one of them is granted" as "the declared one is granted".
    """
    cur.execute(
        "SELECT p.oid, p.oid::regprocedure::text FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = %s AND p.prokind::text = ANY(%s)",
        (schema, list(_CALLABLE_KINDS)),
    )
    return dict(cur.fetchall())


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _public_violations(
    cur, schema: str, relations: dict[str, tuple[int, str]], callables: dict[int, str]
) -> list[GrantViolation]:
    """Anything `PUBLIC` can reach in the schema.

    The first of these is the reported defect in its most direct form: no
    function in this schema is meant to be callable by every authenticated
    role, so `PUBLIC` holding `EXECUTE` on any of them means the revoke half of
    `render_table_grants()` has not run since that function was created.
    """
    violations: list[GrantViolation] = []

    for oid in sorted(_executable_by(cur, callables, PUBLIC)):
        violations.append(
            GrantViolation("public_execute", "PUBLIC", callables[oid], "EXECUTE")
        )

    reach = _table_reach(cur, _oids(relations, _TABLE_KINDS), PUBLIC)
    reach.update(_sequence_reach(cur, _oids(relations, _SEQUENCE_KINDS), PUBLIC))
    for name, (oid, _kind) in sorted(relations.items()):
        for privilege in sorted(reach.get(oid, set())):
            violations.append(
                GrantViolation(
                    "public_relation", "PUBLIC", f"{schema}.{name}", privilege
                )
            )

    return violations


def _relation_violations(
    cur, schema: str, role: str, relations: dict[str, tuple[int, str]]
) -> list[GrantViolation]:
    """Table- and view-level privileges, in both directions.

    Column-scoped privileges are deliberately absent from `expected` here:
    `has_table_privilege` answers about the table-level grant only, so the
    worker's column-scoped `UPDATE` on `completion` shows up as a table-level
    `EXTRA` the moment someone widens it to the whole row — which is the
    forged-`review_verdict` channel the carve-out exists to close.
    """
    expected: dict[str, frozenset[str]] = dict(roles.declared_table_privileges(role))
    for view, privileges in roles.VIEW_PRIVILEGES.get(role, {}).items():
        expected[view] = frozenset(privileges)

    violations: list[GrantViolation] = []
    reach = _table_reach(cur, _oids(relations, _TABLE_KINDS), role)
    for name, (oid, kind) in sorted(relations.items()):
        if kind in _SEQUENCE_KINDS:
            continue
        declared = expected.get(name, frozenset())
        actual = reach.get(oid, set())
        for privilege in sorted(declared - actual):
            violations.append(
                GrantViolation("missing_privilege", role, f"{schema}.{name}", privilege)
            )
        for privilege in sorted(actual - declared):
            violations.append(
                GrantViolation("extra_privilege", role, f"{schema}.{name}", privilege)
            )
    return violations


def _column_violations(
    cur, schema: str, role: str, relations: dict[str, tuple[int, str]]
) -> list[GrantViolation]:
    """The exact column set behind every column-scoped privilege.

    Exact in both directions: a missing column breaks the writer, and an extra
    one is a widening — and because `has_column_privilege` reports a
    table-level grant as covering every column, a table-wide `UPDATE` surfaces
    here as the `review_*` columns becoming reachable, named individually.
    """
    violations: list[GrantViolation] = []
    for table, per_privilege in sorted(roles.declared_column_privileges(role).items()):
        if table not in relations:
            continue
        oid = relations[table][0]
        for privilege, columns in sorted(per_privilege.items()):
            cur.execute(
                "SELECT a.attname FROM pg_attribute a "
                "WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped "
                "AND has_column_privilege(%s, a.attrelid, a.attnum, %s)",
                (oid, role, privilege),
            )
            actual = {row[0] for row in cur.fetchall()}
            declared = set(columns)
            for column in sorted(declared - actual):
                violations.append(
                    GrantViolation(
                        "missing_column", role, f"{schema}.{table}.{column}", privilege
                    )
                )
            for column in sorted(actual - declared):
                violations.append(
                    GrantViolation(
                        "extra_column", role, f"{schema}.{table}.{column}", privilege
                    )
                )
    return violations


def _sequence_violations(
    cur, schema: str, role: str, relations: dict[str, tuple[int, str]]
) -> list[GrantViolation]:
    """Identity sequences: `USAGE` where the role may INSERT, nothing elsewhere.

    Without the `USAGE` half an `INSERT` the matrix grants fails at runtime on
    a table whose key is `GENERATED ALWAYS AS IDENTITY`; with the `extra` half,
    a role cannot quietly gain the ability to advance the counters of tables it
    may not write.
    """
    expected = roles.declared_sequence_privileges(role)
    reach = _sequence_reach(cur, _oids(relations, _SEQUENCE_KINDS), role)
    violations: list[GrantViolation] = []
    for name, (oid, kind) in sorted(relations.items()):
        if kind not in _SEQUENCE_KINDS:
            continue
        declared = expected.get(name, frozenset())
        actual = reach.get(oid, set())
        for privilege in sorted(declared - actual):
            violations.append(
                GrantViolation("missing_privilege", role, f"{schema}.{name}", privilege)
            )
        for privilege in sorted(actual - declared):
            violations.append(
                GrantViolation("extra_privilege", role, f"{schema}.{name}", privilege)
            )
    return violations


def _function_violations(
    cur, schema: str, role: str, callables: dict[int, str]
) -> list[GrantViolation]:
    """`EXECUTE`, resolved through the signature the matrix declares.

    `to_regprocedure` does the resolution so that PostgreSQL's own parser
    decides which overload a declared signature names, and returns NULL rather
    than raising when the function does not exist yet.
    """
    if not callables:
        return []

    declared = roles.FUNCTION_PRIVILEGES.get(role, ())
    violations: list[GrantViolation] = []

    # A declared signature that resolves to nothing is declared but not created
    # yet — an intermediate schema version, which the readiness probe fails on
    # separately — so NULL rows drop out here rather than being reported.
    cur.execute(
        "SELECT to_regprocedure(s)::oid FROM unnest(%s::text[]) AS t(s) "
        "WHERE to_regprocedure(s) IS NOT NULL",
        ([f"{schema}.{signature}" for signature in declared],),
    )
    declared_oids = {row[0] for row in cur.fetchall()}
    granted = _executable_by(cur, callables, role)

    for oid in sorted(declared_oids - granted):
        violations.append(
            GrantViolation("missing_execute", role, callables[oid], "EXECUTE")
        )
    for oid in sorted(granted - declared_oids):
        violations.append(
            GrantViolation("extra_execute", role, callables[oid], "EXECUTE")
        )
    return violations


def _oids(relations: dict[str, tuple[int, str]], kinds: tuple[str, ...]) -> list[int]:
    return [oid for _name, (oid, kind) in relations.items() if kind in kinds]


def _executable_by(cur, callables: dict[int, str], grantee: str) -> set[int]:
    """The subset of `callables` that `grantee` may EXECUTE, PUBLIC included.

    `has_function_privilege` is the effective answer rather than the ACL entry:
    it resolves the PUBLIC default and role membership the same way the
    executor does at call time, which is the question that matters — "can this
    credential call it", not "is there a row saying so".
    """
    if not callables:
        return set()
    cur.execute(
        "SELECT t.oid FROM unnest(%s::oid[]) AS t(oid) "
        "WHERE has_function_privilege(%s, t.oid, 'EXECUTE')",
        (list(callables), grantee),
    )
    return {row[0] for row in cur.fetchall()}


def _table_reach(cur, oids: list[int], grantee: str) -> dict[int, set[str]]:
    """`{oid: table-level privileges}`, one round trip for the whole schema."""
    return _reach(cur, "has_table_privilege", _TABLE_PRIVILEGES, oids, grantee)


def _sequence_reach(cur, oids: list[int], grantee: str) -> dict[int, set[str]]:
    """`{oid: sequence privileges}`, one round trip for the whole schema."""
    return _reach(cur, "has_sequence_privilege", _SEQUENCE_PRIVILEGES, oids, grantee)


def _reach(
    cur, function: str, privileges: tuple[str, ...], oids: list[int], grantee: str
) -> dict[int, set[str]]:
    # `function` is one of two literals chosen above, never caller input; the
    # oids, the privilege list and the grantee are all bound parameters.
    if not oids:
        return {}
    cur.execute(
        "SELECT t.oid, p.privilege FROM unnest(%s::oid[]) AS t(oid) "
        "CROSS JOIN unnest(%s::text[]) AS p(privilege) "
        f"WHERE {function}(%s, t.oid, p.privilege)",
        (oids, list(privileges), grantee),
    )
    reach: dict[int, set[str]] = {}
    for oid, privilege in cur.fetchall():
        reach.setdefault(oid, set()).add(privilege)
    return reach
