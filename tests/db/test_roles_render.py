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

    sql = migrations.discover()[0].up_sql
    identity_tables = set()
    current: str | None = None
    for line in sql.splitlines():
        if line.startswith("CREATE TABLE "):
            current = line.split()[2].rstrip("(")
        elif "GENERATED ALWAYS AS IDENTITY" in line and current:
            identity_tables.add(current)
    assert identity_tables == set(roles.IDENTITY_SEQUENCES)


@pytest.mark.parametrize("bad", ["public; DROP TABLE task", "1schema", "sch-ema"])
def test_identifier_guard_rejects_injection(bad: str) -> None:
    with pytest.raises(ValueError, match="not a safe"):
        roles.render_grants(bad)
