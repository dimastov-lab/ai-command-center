"""The rendered grant matrix, checked without a database.

`test_role_privileges.py` proves the database enforces this matrix. These tests
prove the matrix says what it is supposed to say — cheap, and they run on every
machine, including ones with no PostgreSQL available.
"""

from __future__ import annotations

import pytest

from command_center.db import roles


def test_public_is_stripped_of_schema_privileges() -> None:
    statements = roles.render_grants()
    assert "REVOKE ALL ON SCHEMA public FROM PUBLIC;" in statements
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;" in statements


def test_only_the_migrator_may_create_objects() -> None:
    statements = roles.render_grants()
    creators = [s for s in statements if "CREATE ON SCHEMA" in s]
    assert creators == ["GRANT USAGE, CREATE ON SCHEMA public TO aicc_migrator;"]


def test_no_role_is_granted_delete() -> None:
    """This schema is an append/update ledger; deletes are an owner operation."""
    assert not [s for s in roles.render_grants() if "DELETE" in s]


def test_no_role_is_granted_truncate_or_ddl() -> None:
    for forbidden in ("TRUNCATE", "REFERENCES", "TRIGGER"):
        assert not [s for s in roles.render_grants() if forbidden in s], forbidden


def test_worker_cannot_reach_governance_tables() -> None:
    """A compromised execution host must not read or forge the decision record."""
    off_limits = {
        "proposal",
        "proposal_event",
        "proposal_evidence",
        "provenance_evidence",
        "run_provenance",
        "motion",
        "council_vote",
        "council_decision",
        "council_event",
        "audit_run",
        "audit_finding",
        "market_item",
        "market_install_log",
        "model_entry",
        "model_event",
    }
    granted = set(roles.PRIVILEGES[roles.WORKER_ROLE])
    assert not (granted & off_limits)


def test_worker_cannot_enqueue_work() -> None:
    """Workers claim queue entries; only the dispatcher creates them."""
    assert "INSERT" not in roles.PRIVILEGES[roles.WORKER_ROLE]["queue_entry"]


QUEUE_TABLES = ("work_item", "work_attempt", "work_result", "work_event")


def test_no_role_holds_a_table_privilege_on_the_claim_protocol() -> None:
    """The exclusivity argument is a property of the grant graph, not of a WHERE.

    If any role could `UPDATE work_item` directly there would be a second route
    to a claim, and `queue_claim()` would stop being the only place the
    exclusivity has to hold.
    """
    for role in (roles.APP_ROLE, roles.WORKER_ROLE):
        for table in QUEUE_TABLES:
            assert not {
                p for p in roles.PRIVILEGES[role][table] if p in {"INSERT", "UPDATE"}
            }, f"{role} may write {table}"

    # `work_attempt` holds the capability itself and is readable by nobody.
    for role in (roles.APP_ROLE, roles.WORKER_ROLE):
        assert roles.PRIVILEGES[role]["work_attempt"] == frozenset()


def test_the_worker_reaches_the_queue_only_through_the_four_protocol_steps() -> None:
    granted = {s.split("(")[0] for s in roles.FUNCTION_PRIVILEGES[roles.WORKER_ROLE]}
    assert granted == {"queue_claim", "queue_heartbeat", "queue_complete", "queue_fail"}
    assert roles.VIEW_PRIVILEGES[roles.WORKER_ROLE] == {}


def test_the_control_plane_cannot_claim() -> None:
    """Dispatch is not execution.

    Granting the app `queue_claim()` would restore the shape the claim protocol
    exists to remove: a privileged process recording an executor it was merely
    told about.
    """
    granted = {s.split("(")[0] for s in roles.FUNCTION_PRIVILEGES[roles.APP_ROLE]}
    assert "queue_claim" not in granted
    assert granted == {"queue_enqueue", "queue_reap", "queue_redrive"}


def test_the_worker_can_no_longer_write_the_queue_mirror() -> None:
    """`queue_entry` is a mirror; a claim written there is lost on the next sync."""
    assert roles.PRIVILEGES[roles.WORKER_ROLE]["queue_entry"] == frozenset({"SELECT"})


def test_internal_queue_helpers_are_granted_to_nobody() -> None:
    """`_queue_audit` and `_queue_owns` are SECURITY DEFINER over everything."""
    for role, signatures in roles.FUNCTION_PRIVILEGES.items():
        assert not [s for s in signatures if s.startswith("_")], role


def test_function_grants_are_revoked_before_they_are_reapplied() -> None:
    """Otherwise re-running the matrix widens rather than replaces."""
    statements = roles.render_table_grants()
    for role in (roles.APP_ROLE, roles.WORKER_ROLE):
        revoke = f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM {role};"
        assert revoke in statements
        first_grant = next(
            i for i, s in enumerate(statements) if s.startswith("GRANT EXECUTE ON FUNCTION")
            and s.endswith(f"TO {role};")
        )
        assert statements.index(revoke) < first_grant


@pytest.mark.parametrize("bad", ["queue_claim(text; DROP TABLE task)", "queue_claim(text"])
def test_function_signature_guard_rejects_injection(bad: str) -> None:
    with pytest.raises(ValueError):
        roles._require_function_signature(bad)


def test_a_worker_host_role_is_a_login_member_of_the_worker_group() -> None:
    """Per-host identity with no new machinery, and no password in the SQL."""
    statement = roles.render_worker_host_role("aicc_worker_host_a")[0]
    assert "CREATE ROLE aicc_worker_host_a LOGIN IN ROLE aicc_worker;" in statement
    assert "PASSWORD" not in statement


def test_app_covers_every_table() -> None:
    assert set(roles.PRIVILEGES[roles.APP_ROLE]) == set(roles.ALL_TABLES)


def test_migrator_holds_no_table_grants() -> None:
    """Its rights come from ownership, so a stray grant here would be a smell."""
    assert roles.PRIVILEGES[roles.MIGRATOR_ROLE] == {}


def test_sequence_usage_accompanies_every_insert_grant() -> None:
    statements = roles.render_grants()
    for role in (roles.APP_ROLE, roles.WORKER_ROLE):
        for table, sequence in roles.IDENTITY_SEQUENCES.items():
            expected = f"GRANT USAGE ON SEQUENCE public.{sequence} TO {role};"
            if "INSERT" in roles.PRIVILEGES[role].get(table, frozenset()):
                assert expected in statements, f"{role} inserts into {table}"
            else:
                assert expected not in statements, f"{role} must not advance {sequence}"


def test_identity_sequences_match_the_ddl() -> None:
    """Sequence names are guessed from PostgreSQL's naming rule; verify the guess."""
    from command_center.db import migrations

    identity_tables = set()
    for migration in migrations.discover():
        current: str | None = None
        for line in migration.up_sql.splitlines():
            if line.startswith("CREATE TABLE "):
                current = line.split()[2].rstrip("(")
            elif "GENERATED ALWAYS AS IDENTITY" in line and current:
                identity_tables.add(current)
    assert identity_tables == set(roles.IDENTITY_SEQUENCES)


@pytest.mark.parametrize("bad", ["public; DROP TABLE task", "1schema", "sch-ema"])
def test_identifier_guard_rejects_injection(bad: str) -> None:
    with pytest.raises(ValueError, match="not a safe"):
        roles.render_grants(bad)
