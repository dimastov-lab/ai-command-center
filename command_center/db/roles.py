"""Least-privilege role inventory for the AICC server database.

This module is the single source of truth for who may touch what. The same
`PRIVILEGES` mapping renders the `GRANT` statements that provision a database
and drives `tests/db/test_role_privileges.py`, which connects *as each role*
and asserts both halves of the matrix — what the role can do and what it must
not. That coupling is deliberate: the defect behind `VOYN-W0-SEC-AUDIT-PG-CRED`
was a grant that no test exercised, so a grant added here without a matching
row in the matrix cannot silently widen access.

Three roles, by what fails if each one is compromised:

* `aicc_migrator` owns the schema and is the only role with DDL. It is used by
  the migration runner and by nothing else at runtime.
* `aicc_app` runs the API and dispatcher: full DML on every domain table, no
  DDL, no ownership, so a SQL-injection foothold in the web layer cannot drop
  or alter a table.
* `aicc_worker` runs on execution hosts, which are the least trusted component
  (they run agent processes against untrusted repository content). It sees
  only the queue and execution tables. Governance data — proposals, council
  motions and votes, audit findings, provenance evidence, the marketplace and
  the model registry — is unreachable from a worker credential, so a
  compromised execution host cannot read or forge the decision record. The
  independent review's verdict lives on `completion`, which a worker does write,
  so the three `review_*` columns are carved out via column-level grants (see
  COLUMN_PRIVILEGES) rather than left inside a table-wide UPDATE.

`DELETE` is granted to no role on any table: this schema is an append/update
ledger, and row removal is a migration-time operation performed by the owner.
"""

from __future__ import annotations

from types import MappingProxyType

__all__ = [
    "APP_ROLE",
    "MIGRATOR_ROLE",
    "WORKER_ROLE",
    "ALL_ROLES",
    "ALL_TABLES",
    "COLUMN_PRIVILEGES",
    "apply_bootstrap",
    "apply_table_grants",
    "IDENTITY_SEQUENCES",
    "PRIVILEGES",
    "render_bootstrap",
    "render_grants",
    "render_table_grants",
    "render_role_creation",
]

MIGRATOR_ROLE = "aicc_migrator"
APP_ROLE = "aicc_app"
WORKER_ROLE = "aicc_worker"

ALL_ROLES = (MIGRATOR_ROLE, APP_ROLE, WORKER_ROLE)

# Every domain table created by 0001_initial.up.sql, plus the runner's own
# bookkeeping table. `test_schema_matches_role_inventory` fails if the database
# and this tuple disagree, so a table added by a later migration cannot end up
# with no declared owner of its access policy.
ALL_TABLES: tuple[str, ...] = (
    "advisor_proposal",
    "audit_finding",
    "audit_run",
    "completion",
    "completion_event",
    "completion_validation",
    "conflict",
    "contact",
    "council_decision",
    "council_event",
    "council_vote",
    "digest_item",
    "market_install_log",
    "market_item",
    "message",
    "model_entry",
    "model_event",
    "motion",
    "networking_invitation",
    "owner_item",
    "proposal",
    "proposal_event",
    "proposal_evidence",
    "provenance_evidence",
    "provider_attempt",
    "queue_entry",
    "report",
    "run",
    "run_event",
    "run_provenance",
    "run_provider_route",
    "schema_migration",
    "session",
    "task",
)

# Tables whose primary key is `bigint GENERATED ALWAYS AS IDENTITY`, mapped to
# the sequence PostgreSQL creates for them. Inserting into these needs USAGE on
# the sequence in addition to INSERT on the table.
IDENTITY_SEQUENCES: MappingProxyType[str, str] = MappingProxyType(
    {
        "completion_event": "completion_event_id_seq",
        "completion_validation": "completion_validation_id_seq",
        "council_event": "council_event_id_seq",
        "model_event": "model_event_id_seq",
        "proposal_event": "proposal_event_id_seq",
        "proposal_evidence": "proposal_evidence_id_seq",
        "run_event": "run_event_id_seq",
    }
)

_APP_DML = frozenset({"SELECT", "INSERT", "UPDATE"})
_WORKER_WRITE = frozenset({"SELECT", "INSERT", "UPDATE"})
_READ = frozenset({"SELECT"})

# Columns of `completion` that record the independent review's outcome. A
# worker writes its own completion row, but table-level UPDATE would also let a
# compromised execution host stamp `review_verdict = 'approved'` on any run —
# forging exactly the decision the review gate exists to make. Granted per
# column instead, so the verdict is writable only by the app.
_REVIEW_COLUMNS = ("review_verdict", "review_run_id", "review_summary")

_COMPLETION_COLUMNS = (
    "run_id", "task_id", "session_id", "project", "repository_path", "branch",
    "base_branch", "head_commit", "remote", "remote_branch", "pull_request_number",
    "pull_request_url", "pull_request_state", "replaced_pull_request_number",
    "replaced_pull_request_url", "merge_commit", "merge_mode", "merge_method",
    "completion_state", "last_reason_code", "requires_human", "is_recoverable",
    "recommended_action", "validation_summary", "policy_json", "last_checked_at",
    "next_retry_at", "retry_count", "recovery_count", "version", "created_at",
    "updated_at", *_REVIEW_COLUMNS,
)

_WORKER_COMPLETION_COLUMNS = tuple(
    column for column in _COMPLETION_COLUMNS if column not in _REVIEW_COLUMNS
)

# role -> table -> privilege -> the columns it is limited to. A privilege listed
# here is granted per column; anything not listed is granted table-wide.
COLUMN_PRIVILEGES: MappingProxyType[str, MappingProxyType[str, MappingProxyType[str, tuple[str, ...]]]] = (
    MappingProxyType(
        {
            WORKER_ROLE: MappingProxyType(
                {
                    "completion": MappingProxyType(
                        {
                            "INSERT": _WORKER_COMPLETION_COLUMNS,
                            "UPDATE": _WORKER_COMPLETION_COLUMNS,
                        }
                    )
                }
            )
        }
    )
)

# The queue and execution tables a worker legitimately writes while running a
# job. Anything absent from this mapping is unreachable for `aicc_worker`.
_WORKER_TABLES: dict[str, frozenset[str]] = {
    "queue_entry": frozenset({"SELECT", "UPDATE"}),  # claims, never enqueues
    "run": _WORKER_WRITE,
    "run_event": _WORKER_WRITE,
    "completion": _WORKER_WRITE,
    "completion_event": _WORKER_WRITE,
    "completion_validation": _WORKER_WRITE,
    "report": _WORKER_WRITE,
    # Read-only context a worker needs to execute the run it claimed.
    "task": _READ,
    "session": _READ,
    "schema_migration": _READ,  # startup compatibility check
}

PRIVILEGES: MappingProxyType[str, MappingProxyType[str, frozenset[str]]] = (
    MappingProxyType(
        {
            # The migrator owns every table, so its DDL rights come from
            # ownership rather than grants; it needs no row-level grants of its
            # own and receives none here.
            MIGRATOR_ROLE: MappingProxyType({}),
            APP_ROLE: MappingProxyType(
                {
                    table: (
                        # The ledger is read by the readiness probe and written
                        # only by the migrator. Table-level write here would let
                        # an injection foothold in the web layer rewrite a
                        # checksum — defeating the one guard that stops two
                        # environments reporting the same version for different
                        # schemas — or fake a version so /readyz reports healthy
                        # against an unmigrated database.
                        _READ if table == "schema_migration" else _APP_DML
                    )
                    for table in ALL_TABLES
                }
            ),
            WORKER_ROLE: MappingProxyType(dict(_WORKER_TABLES)),
        }
    )
)


def render_role_creation(role: str) -> str:
    """SQL creating `role` as a NOLOGIN group if it does not already exist.

    Passwords are never rendered here. Login roles and their secrets are
    provisioned by the operator (or by `docker-compose.server.yml` from the
    environment), so no credential can be committed to this repository or
    leak into a migration log.
    """
    _require_identifier(role)
    return (
        "DO $$\n"
        "BEGIN\n"
        f"    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN\n"
        f"        CREATE ROLE {role} NOLOGIN;\n"
        "    END IF;\n"
        "END\n"
        "$$;"
    )


def render_bootstrap(schema: str = "public") -> list[str]:
    """Cluster- and schema-level setup. Must run as a superuser or schema owner.

    Separate from `render_table_grants()` because the two need different
    rights: `REVOKE ... ON SCHEMA public` requires ownership of the schema,
    which belongs to `postgres`/`pg_database_owner` and not to the migrator,
    while the table grants require ownership of the tables, which the migrator
    has and the superuser should not need to be involved in. Collapsing them
    would force every routine migration to run as a superuser.
    """
    _require_identifier(schema)
    statements: list[str] = [render_role_creation(role) for role in ALL_ROLES]

    # PUBLIC is granted CREATE and USAGE on `public` by default in PostgreSQL
    # below 15, which would let any authenticated role create objects in the
    # application schema. Revoke unconditionally so the policy does not depend
    # on the server's major version.
    statements.append(f"REVOKE ALL ON SCHEMA {schema} FROM PUBLIC;")
    statements.append(f"REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM PUBLIC;")
    statements.append(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {schema} FROM PUBLIC;")
    statements.append(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {schema} FROM PUBLIC;")

    statements.append(f"GRANT USAGE, CREATE ON SCHEMA {schema} TO {MIGRATOR_ROLE};")
    for role in (APP_ROLE, WORKER_ROLE):
        statements.append(f"GRANT USAGE ON SCHEMA {schema} TO {role};")

    return statements


def render_table_grants(schema: str = "public") -> list[str]:
    """Per-table privileges. Runs as the table owner (`aicc_migrator`).

    Idempotent and order-independent by construction — it starts from a clean
    `REVOKE ALL`, so re-running it after a role has been widened by hand puts
    the database back on the declared matrix instead of layering on top of it.
    """
    _require_identifier(schema)
    statements: list[str] = []

    # Re-stripping PUBLIC here rather than only at bootstrap is what makes this
    # cover objects created by *later* migrations: at bootstrap the schema is
    # empty, so `ALL TABLES` matches nothing.
    #
    # FUNCTIONS is the one that matters. PUBLIC gets EXECUTE on every new
    # function by default, so a migration adding a SECURITY DEFINER helper — a
    # normal way to expose a governance query — would otherwise hand
    # `aicc_worker` a route to precisely the tables this matrix excludes.
    #
    # `ALTER DEFAULT PRIVILEGES` would be the tidier mechanism, but it was
    # tested against PostgreSQL 15 and 17 and does not persist a revocation of
    # PUBLIC's built-in function default (no `pg_default_acl` row is stored and
    # new functions still come out with the default ACL), so relying on it would
    # have been a security control that silently does nothing. Re-asserting
    # after every migration — which `command_center.db upgrade` already does —
    # is verifiable, and `test_public_execute_on_new_functions_is_revoked`
    # verifies it.
    statements.append(f"REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM PUBLIC;")
    statements.append(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {schema} FROM PUBLIC;")
    statements.append(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {schema} FROM PUBLIC;")

    for role in (APP_ROLE, WORKER_ROLE):
        statements.append(f"REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM {role};")
        statements.append(
            f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {schema} FROM {role};"
        )

    for role in (APP_ROLE, WORKER_ROLE):
        for table, privileges in sorted(PRIVILEGES[role].items()):
            _require_identifier(table)
            columns = COLUMN_PRIVILEGES.get(role, {}).get(table, {})
            table_wide = sorted(p for p in privileges if p not in columns)
            if table_wide:
                statements.append(
                    f"GRANT {', '.join(table_wide)} ON {schema}.{table} TO {role};"
                )
            for privilege in sorted(p for p in privileges if p in columns):
                for column in columns[privilege]:
                    _require_identifier(column)
                column_list = ", ".join(columns[privilege])
                statements.append(
                    f"GRANT {privilege} ({column_list}) ON {schema}.{table} TO {role};"
                )

    # Identity columns draw from a sequence; INSERT alone is not enough. Granted
    # per sequence rather than schema-wide, so a role still cannot advance the
    # counters of tables it may not write.
    for role in (APP_ROLE, WORKER_ROLE):
        for table, sequence in sorted(IDENTITY_SEQUENCES.items()):
            if "INSERT" not in PRIVILEGES[role].get(table, frozenset()):
                continue
            _require_identifier(sequence)
            statements.append(f"GRANT USAGE ON SEQUENCE {schema}.{sequence} TO {role};")

    return statements


def render_grants(schema: str = "public") -> list[str]:
    """Bootstrap plus table grants, in the order they must be applied."""
    return render_bootstrap(schema) + render_table_grants(schema)


def apply_bootstrap(conn, schema: str = "public") -> int:
    """Execute `render_bootstrap()`. Requires a superuser or the schema owner."""
    return _execute(conn, render_bootstrap(schema))


def apply_table_grants(conn, schema: str = "public") -> int:
    """Execute `render_table_grants()`. Requires ownership of the tables.

    Run after every migration: this is what stops a newly created table from
    shipping with no grants (invisible to the app) or with inherited ones.
    """
    return _execute(conn, render_table_grants(schema))


def _execute(conn, statements: list[str]) -> int:
    with conn.transaction():
        with conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)
    return len(statements)


def _require_identifier(name: str) -> None:
    """Guard the string interpolation above against anything but a plain name."""
    if not name.replace("_", "").isalnum() or not name[0].isalpha():
        raise ValueError(f"{name!r} is not a safe unquoted SQL identifier.")
