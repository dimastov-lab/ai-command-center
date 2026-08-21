"""Two role patches landing in sequence, which is the arrangement neither one's own suite covers.

`tests/db/test_roles_render.py` proves the merge helpers union correctly and
that the shipped matrix says what it should. It proves it against synthetic
dicts, though, which is the same blind spot the defect has: a patch checked in
isolation is checked in the one arrangement where it is right.

The defect (VOYN-W0-AICC-ROLES-LAYER-MERGE): the SRV-04b slice (the queue-claim
protocol, `f9bb889`) and the SRV-03 slice (host enrolment, `ec9fb22`) each
declare a role's whole surface, and a role patch opens with
`REVOKE ALL ... FROM <role>` so that re-running it puts the database back on the
declared matrix rather than layering on top of it. That opening REVOKE is
correct and worth keeping — but it means a patch is a *complete replacement*,
so the thing it replaces from has to be the union of every slice. It was not
required to be. Land SRV-04b, then land SRV-03, and the second one's REVOKE
takes away the first one's grants: no error, no warning, both slices' suites
green, because each is right alone.

For `aicc_worker` that is not a narrowing, it is an emptying. The worker holds
no table privilege at all on the claim protocol — its entire reach is EXECUTE on
the four functions — so a replacement patch leaves it able to execute nothing,
and the queue stops being claimable by anyone.

So these tests apply patches, in sequence, to a catalog: build the SQL each
slice would ship on its own, run it statement by statement through an ACL model,
and ask afterwards whether the first slice's grants are still there.
"""

from __future__ import annotations

import re

import pytest

from command_center.db import roles

SCHEMA = "public"

#: The roles the matrix grants to. Derived from the public inventory rather
#: than reaching for the module's own private tuple: the migrator is excluded
#: because its rights come from owning the tables, not from a grant, so it has
#: no surface for a patch to replace.
GRANTED_ROLES = tuple(r for r in roles.ALL_ROLES if r != roles.MIGRATOR_ROLE)


# ---------------------------------------------------------------------------
# An ACL model, small enough to trust
# ---------------------------------------------------------------------------
# It understands only the statement shapes `render_table_grants()` emits. That
# is the point: it is not a PostgreSQL emulator, it is a record of who holds
# what after a list of GRANTs and REVOKEs, and it is what lets the sequencing
# question be asked without a database on every machine that runs the suite.


class _Catalog:
    """`{(role, object): {privilege}}`, mutated by GRANT and REVOKE.

    Views live in the same class as tables because PostgreSQL puts them there:
    `REVOKE ALL ON ALL TABLES IN SCHEMA public FROM aicc_app` strips the app's
    read on `work_attempt_public` too. Modelling views separately would hide
    half of what a replacement patch takes away — and it is the half that
    matters, since the redacted views are the *only* read path onto the three
    tables granted to nobody.
    """

    #: The object classes `REVOKE ALL ON ALL <class>` names, and what each
    #: sweeps. "TABLES" covers views; "FUNCTIONS" covers only functions.
    _CLASS_OF = {
        "table": "TABLES",
        "view": "TABLES",
        "sequence": "SEQUENCES",
        "function": "FUNCTIONS",
    }

    _REVOKE_ALL = re.compile(
        r"^REVOKE ALL ON ALL (TABLES|SEQUENCES|FUNCTIONS) IN SCHEMA (\w+) FROM (\w+);$"
    )
    _GRANT = re.compile(
        r"^GRANT (.+) ON (?:(FUNCTION|SEQUENCE) )?([\w.()\s,]+?) TO (\w+);$"
    )

    def __init__(self) -> None:
        self._held: dict[tuple[str, str], set[str]] = {}

    def apply(self, statements: list[str]) -> None:
        for statement in statements:
            self._apply_one(statement)

    def _apply_one(self, statement: str) -> None:
        revoke = self._REVOKE_ALL.match(statement)
        if revoke is not None:
            object_class, schema, role = revoke.groups()
            assert schema == SCHEMA
            for key in list(self._held):
                if key[0] == role and self._class_of(key[1]) == object_class:
                    del self._held[key]
            return

        grant = self._GRANT.match(statement)
        if grant is not None:
            privileges, _keyword, target, role = grant.groups()
            self._held.setdefault((role, target.strip()), set()).update(
                self._split_privileges(privileges)
            )
            return

        # Bootstrap statements (schema USAGE, role membership, DO blocks) are
        # not table privileges and are not what this model is about. Anything
        # else is a shape the model has not been taught, and silently ignoring
        # it is how a test like this comes to assert nothing.
        assert self._is_ignorable(statement), f"unmodelled statement: {statement}"

    @staticmethod
    def _is_ignorable(statement: str) -> bool:
        return (
            statement.startswith("REVOKE")
            or statement.startswith("DO $$")
            or " ON SCHEMA " in statement
            or statement.startswith("GRANT pg_signal_backend")
            or bool(re.match(r"^GRANT \w+ TO \w+ ", statement))
        )

    @staticmethod
    def _split_privileges(privileges: str) -> set[str]:
        """`SELECT, UPDATE` -> two tokens; `UPDATE (a, b)` -> one per column.

        Column-scoped grants are kept per column rather than collapsed to
        `UPDATE`, so that a patch which replaces the worker's column list with
        a shorter one is visible here as the narrowing it is.
        """
        scoped = re.match(r"^(\w+) \((.+)\)$", privileges.strip())
        if scoped is not None:
            privilege, columns = scoped.groups()
            return {f"{privilege}({column.strip()})" for column in columns.split(",")}
        return {p.strip() for p in privileges.split(",")}

    def _class_of(self, target: str) -> str:
        if "(" in target:
            return "FUNCTIONS"
        if target.rsplit(".", 1)[-1].endswith("_seq"):
            return "SEQUENCES"
        return "TABLES"

    def privileges(self, role: str, target: str) -> set[str]:
        return set(self._held.get((role, f"{SCHEMA}.{target}"), set()))

    def executes(self, role: str) -> set[str]:
        return {
            target.split(".", 1)[1]
            for (held_role, target), privileges in self._held.items()
            if held_role == role and "EXECUTE" in privileges
        }


def _render_patch(
    role: str,
    *,
    tables: dict[str, frozenset[str]] | None = None,
    views: dict[str, frozenset[str]] | None = None,
    functions: tuple[str, ...] = (),
) -> list[str]:
    """The SQL one slice ships for one role, in the shape the real patches take.

    A faithful miniature of `render_table_grants()`: the same opening
    `REVOKE ALL ... FROM <role>` that makes a patch idempotent and
    order-independent, followed by that slice's own declared surface — and
    nothing about any other slice, because a slice does not know about the ones
    that come after it. Reproducing the REVOKE is the whole point; without it
    the patches would merely layer and there would be no defect to test.
    """
    statements = [
        f"REVOKE ALL ON ALL TABLES IN SCHEMA {SCHEMA} FROM {role};",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {SCHEMA} FROM {role};",
        f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {SCHEMA} FROM {role};",
    ]
    for relation, privileges in sorted({**(tables or {}), **(views or {})}.items()):
        if privileges:
            statements.append(
                f"GRANT {', '.join(sorted(privileges))} ON {SCHEMA}.{relation} TO {role};"
            )
    for signature in functions:
        statements.append(f"GRANT EXECUTE ON FUNCTION {SCHEMA}.{signature} TO {role};")
    return statements


# The two slices of the finding, each rendered as it would have shipped alone.
# The table maps are the module's own contribution constants, so these are not a
# paraphrase of the patches — they are the patches' declared surfaces.

WORKER_QUEUE_PATCH = _render_patch(  # SRV-04b, f9bb889
    roles.WORKER_ROLE,
    tables=roles._WORKER_TABLES,
    functions=roles._WORKER_FUNCTIONS,
)

WORKER_ENROLMENT_PATCH = _render_patch(  # SRV-03, ec9fb22
    roles.WORKER_ROLE,
    tables=roles._WORKER_ENROLMENT_TABLES,
    functions=roles._WORKER_ENROLMENT_FUNCTIONS,
)

APP_QUEUE_PATCH = _render_patch(  # SRV-04b, f9bb889
    roles.APP_ROLE,
    tables=roles._APP_QUEUE_TABLES,
    views=roles._APP_QUEUE_VIEWS,
    functions=roles._APP_FUNCTIONS,
)

APP_ENROLMENT_PATCH = _render_patch(  # SRV-03, ec9fb22
    roles.APP_ROLE,
    tables=roles._APP_ENROLMENT_TABLES,
    views=roles._APP_ENROLMENT_VIEWS,
    functions=roles._APP_ENROLMENT_FUNCTIONS,
)


# ---------------------------------------------------------------------------
# The finding, executed
# ---------------------------------------------------------------------------


def test_each_patch_is_correct_on_its_own() -> None:
    """The premise: neither slice is wrong, which is why neither suite is red.

    Stated first because it is what makes the next test a finding rather than a
    bug report against one of the two slices.
    """
    alone = _Catalog()
    alone.apply(WORKER_QUEUE_PATCH)
    assert alone.executes(roles.WORKER_ROLE) == set(roles._WORKER_FUNCTIONS)

    other = _Catalog()
    other.apply(WORKER_ENROLMENT_PATCH)
    assert other.executes(roles.WORKER_ROLE) == set(roles._WORKER_ENROLMENT_FUNCTIONS)


def test_the_second_patch_silently_revokes_the_first() -> None:
    """The defect: land both, and the worker can execute nothing it could before.

    No statement fails. The catalog ends in a state PostgreSQL is perfectly
    happy with. The queue simply stops being claimable, and the first evidence
    is a production worker that cannot take work.
    """
    catalog = _Catalog()
    catalog.apply(WORKER_QUEUE_PATCH)
    catalog.apply(WORKER_ENROLMENT_PATCH)

    survived = catalog.executes(roles.WORKER_ROLE)
    assert survived == set(roles._WORKER_ENROLMENT_FUNCTIONS), "the loss, reproduced"
    assert not survived & set(roles._WORKER_FUNCTIONS)
    assert "queue_claim(text, text, integer)" not in survived


def test_the_second_patch_also_takes_the_control_planes_redacted_views() -> None:
    """And on the app it reaches further than tables, because views share the class.

    `REVOKE ALL ON ALL TABLES` sweeps views too, so the enrolment patch takes
    away `work_attempt_public` — the only read path onto `work_attempt`, which
    is granted to nobody because it holds `claim_token_hash`. The control plane
    is left unable to see its own queue's attempts at all.
    """
    catalog = _Catalog()
    catalog.apply(APP_QUEUE_PATCH)
    catalog.apply(APP_ENROLMENT_PATCH)

    assert catalog.privileges(roles.APP_ROLE, "work_attempt_public") == set()
    assert catalog.privileges(roles.APP_ROLE, "work_item") == set()


# ---------------------------------------------------------------------------
# The fix, stated as the property that must hold
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", GRANTED_ROLES)
def test_the_shipped_matrix_carries_every_slices_grants(role: str) -> None:
    """One replacement, rendered from the union: nothing any slice declared is missing.

    This is what "merge, not patch-as-replacement" buys. The renderer still
    opens with `REVOKE ALL` — the property is not that the SQL stopped
    replacing, it is that what it replaces from is the union of every slice.
    """
    catalog = _Catalog()
    catalog.apply(roles.render_table_grants(SCHEMA))

    for table, privileges in roles.PRIVILEGES[role].items():
        assert catalog.privileges(role, table) >= _table_wide(role, table, privileges)
    for view, privileges in roles.VIEW_PRIVILEGES.get(role, {}).items():
        assert catalog.privileges(role, view) == set(privileges)
    assert catalog.executes(role) == set(roles.FUNCTION_PRIVILEGES.get(role, ()))


def _table_wide(role: str, table: str, privileges: frozenset[str]) -> set[str]:
    """`privileges` minus the ones granted per column, which render differently."""
    scoped = roles.COLUMN_PRIVILEGES.get(role, {}).get(table, {})
    return {p for p in privileges if p not in scoped}


def test_the_worker_keeps_the_claim_protocol_once_enrolment_has_landed() -> None:
    """The regression, named: SRV-04b's six-function surface after SRV-03 shipped.

    Both slices have landed in `main`. If the composition were ever rewritten as
    a replacement, this is the assertion that goes red — where each slice's own
    suite would stay green.
    """
    catalog = _Catalog()
    catalog.apply(roles.render_table_grants(SCHEMA))

    executable = catalog.executes(roles.WORKER_ROLE)
    assert executable >= set(roles._WORKER_FUNCTIONS)
    assert executable >= set(roles._WORKER_ENROLMENT_FUNCTIONS)


def test_re_running_the_matrix_is_idempotent() -> None:
    """Applying the replacement twice is applying it once.

    The opening `REVOKE ALL` is what makes this true, and it is why the
    replacement shape is worth keeping rather than trading for additive
    patches: a role widened by hand is put back on the declared matrix.
    """
    once, twice = _Catalog(), _Catalog()
    once.apply(roles.render_table_grants(SCHEMA))
    twice.apply(roles.render_table_grants(SCHEMA))
    twice.apply(roles.render_table_grants(SCHEMA))

    for role in GRANTED_ROLES:
        assert once.executes(role) == twice.executes(role)
        for table in roles.ALL_TABLES:
            assert once.privileges(role, table) == twice.privileges(role, table)


# ---------------------------------------------------------------------------
# The same mistake, in the two surfaces that are not dicts
# ---------------------------------------------------------------------------


def test_a_replacing_function_contribution_empties_the_role() -> None:
    """Why EXECUTE gets its own merge helper rather than a bare `+`.

    The worker holds no table privilege at all on the claim protocol and the
    operator may write nothing anywhere, so for both of them EXECUTE is not a
    convenience over a table route — it is the only route. Replacement there
    does not narrow a role, it empties it, and `b` type-checks exactly as well
    as `a + b`.
    """
    naive = roles._WORKER_ENROLMENT_FUNCTIONS
    assert "queue_claim(text, text, integer)" not in naive, "the mistake, reproduced"

    merged = roles.merge_function_privileges(
        roles._WORKER_FUNCTIONS, roles._WORKER_ENROLMENT_FUNCTIONS
    )
    assert set(merged) == set(roles._WORKER_FUNCTIONS) | set(
        roles._WORKER_ENROLMENT_FUNCTIONS
    )


def test_merging_function_contributions_is_order_stable_and_grants_once() -> None:
    """A signature two slices both name is granted once, in first-seen order.

    Order because the rendered SQL is compared statement-by-statement
    elsewhere; once because a duplicated GRANT is harmless against PostgreSQL
    but makes a rendered patch overstate how wide a role's surface is.
    """
    first = ("a(text)", "b(text)")
    second = ("b(text)", "c(text)")

    assert roles.merge_function_privileges(first, second) == (
        "a(text)",
        "b(text)",
        "c(text)",
    )


def test_the_operator_can_only_act_through_execute() -> None:
    """The premise of the test above, asserted rather than assumed.

    The operator reads two enrolment tables and writes none, so every act this
    role exists for — minting a ticket, revoking one, retiring a host — is
    reachable only by EXECUTE. Replace that tuple and the admission lever is
    gone, while the role still looks provisioned.
    """
    writes = {"INSERT", "UPDATE", "DELETE", "TRUNCATE"}
    for table, privileges in roles.PRIVILEGES[roles.OPERATOR_ROLE].items():
        assert not (privileges & writes), table
    assert roles.FUNCTION_PRIVILEGES[roles.OPERATOR_ROLE]


# ---------------------------------------------------------------------------
# The guard that outlives these two slices
# ---------------------------------------------------------------------------
# The tests above name SRV-03 and SRV-04b. The defect is not about those two
# slices, it is about every pair of slices that will ever declare a surface for
# the same role — so the guard has to find contributions rather than list them.
# A slice whose constant is declared and then wired by replacement (or not
# wired at all) is exactly what disappears silently, and it is what these turn
# red.

_CONTRIBUTION = re.compile(
    r"^_(APP|WORKER|OPERATOR)(?:_[A-Z]+)*_(TABLES|VIEWS|FUNCTIONS)$"
)

_ROLE_OF = {
    "APP": roles.APP_ROLE,
    "WORKER": roles.WORKER_ROLE,
    "OPERATOR": roles.OPERATOR_ROLE,
}


def _contributions(kind: str) -> list[tuple[str, str, object]]:
    """`(constant name, role, value)` for every declared contribution of `kind`."""
    found = []
    for name, value in vars(roles).items():
        match = _CONTRIBUTION.match(name)
        if match is not None and match.group(2) == kind:
            found.append((name, _ROLE_OF[match.group(1)], value))
    return found


@pytest.mark.parametrize("kind", ["TABLES", "VIEWS", "FUNCTIONS"])
def test_every_declared_contribution_survives_into_the_shipped_surface(
    kind: str,
) -> None:
    shipped = {
        "TABLES": roles.PRIVILEGES,
        "VIEWS": roles.VIEW_PRIVILEGES,
        "FUNCTIONS": roles.FUNCTION_PRIVILEGES,
    }[kind]

    contributions = _contributions(kind)
    # Anti-vacuity: the discovery is by naming convention, so a rename that
    # stopped matching would otherwise turn this test into a no-op that passes.
    assert len(contributions) >= 3, f"discovery found almost no {kind} contributions"

    for name, role, declared in contributions:
        surface = shipped[role]
        if kind == "FUNCTIONS":
            missing = set(declared) - set(surface)
            assert not missing, f"{name} lost {sorted(missing)} from {role}"
            continue
        for relation, privileges in declared.items():
            assert relation in surface, f"{name} lost {relation} from {role}"
            assert surface[relation] >= privileges, (
                f"{name} was narrowed on {relation} for {role}: "
                f"{sorted(privileges - surface[relation])} missing"
            )


def test_the_discovery_finds_the_two_contributions_of_the_finding() -> None:
    """The convention the guard above depends on, pinned to the actual defect."""
    names = {name for name, _role, _value in _contributions("FUNCTIONS")}
    assert {"_WORKER_FUNCTIONS", "_WORKER_ENROLMENT_FUNCTIONS"} <= names
