"""The cutover, as two switches with different reversibility (SRV-07g).

Every plan this migration has carried until now spoke of *the* cutover, as one
event: parity goes green, the authority becomes PostgreSQL, done. That framing
hides the only fact about it that matters operationally — the step is two steps,
and they are not the same kind of step.

**Stage one moves reads.** Its rollback is a flag. Writes keep going to SQLite
and are mirrored forward the whole time, so SQLite stays a complete system of
record, and going back costs nothing and loses nothing. Its preconditions are
about *confidence*, not about recovery: a parity gate that passed, seven
identity sequences resynced and proven by a native insert, and a fresh backup
for the failure nobody predicted. It is soaked under a continuous gate, and any
divergence that is not lag puts reads back.

**Stage two moves writes, and it is not symmetrical.** A row written natively
into PostgreSQL has no counterpart in SQLite and nothing forward-facing will
ever give it one. Recovery by restoring the backup is *forward-only*: it
recovers the database as it stood before the switch and loses every row written
after it. For a task queue that is the wrong recovery, not a degraded one — the
rows it drops are the work the system accepted and promised to run. So stage two
has a precondition stage one does not: a reverse mirror carrying PostgreSQL back
into SQLite for the length of the soak, built from the same declarations
(`command_center/db/reverse_mirror.py`), and this module refuses to advance
without proof that it works.

Stage two itself is executed under SRV-09. What lives here is the machine that
decides whether either switch may be thrown, and what throwing it back costs.

## Why the flag is a file

`CutoverFlag` persists to a JSON file, not to a table.

Rolling reads back is the thing you do when PostgreSQL is what is wrong. A flag
stored in PostgreSQL would have to be read out of the database you are rolling
away from, and written to it — so the one moment the flag exists for is the one
moment it might be unreachable. A file next to the runtime database is readable
and writable with the server down, which is the property that makes "rollback is
a flag" true rather than aspirational.

Absence reads as `DUAL_WRITE`. A missing flag file means the safe stage, never
the advanced one: a lost, truncated or not-yet-created file must not be able to
promote reads onto a seam nobody proved.

## Why the identity proof is not a one-time step

The parity gate resyncs the seven `GENERATED ALWAYS AS IDENTITY` sequences past
the largest mirrored id and proves it with an insert that takes the sequence's
own next value. That proof is true at the moment it is made and *decays through
stage one*: during stage one SQLite keeps assigning ids and the forward mirror
keeps writing them explicitly, which leaves the sequence untouched while
`max(id)` climbs away from it. By the end of a week's soak the sequence is
behind again, and the first native write after stage two would collide.

So the soak re-runs the whole gate, identity included, on every poll, and stage
two demands its own fresh proof rather than pointing at stage one's. The cost is
one surrogate id burned per identity table per poll — the proof's insert is
rolled back, but sequences are not transactional — which over any realistic soak
is a rounding error against `bigint` and is the price of the step being proven by
the operation that actually fails.

## What the soak tolerates, which is one thing, and not by subtraction

The parity gate's tolerance is zero and its docstring is explicit that this is
only honest against a quiesced system. A soak is the opposite of quiesced. The
gate names the single divergence shape that has a benign reading under traffic —
`missing`, a row written to the authority moments ago that the mirror has not
received yet, which the next write repairs — and says lag explains nothing about
the other three.

This module holds that line exactly. `fields`, `unexpected` and `unreadable` put
reads back on the first poll that sees them. A `missing` row is not tolerated
either; it is *confirmed*: the keys are remembered, and if the same key is still
divergent on the next poll it was never lag and reads go back. A count is never
subtracted from and no threshold is configurable, because a threshold is how "0
divergences" becomes "few enough divergences" — the sentence the gate was built
to make unsayable.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from command_center.db import parity_gate, reverse_mirror

__all__ = [
    "BackupEvidence",
    "CutoverFlag",
    "CutoverRecord",
    "CutoverRefused",
    "MAX_BACKUP_AGE_SECONDS",
    "MIN_SOAK_SECONDS",
    "Precondition",
    "SoakVerdict",
    "Stage",
    "identity_tables",
    "latest_backup",
    "read_preconditions",
    "soak",
    "write_preconditions",
]


class Stage(str, Enum):
    """Where the cutover is, named by what each stage actually changed.

    A `str` enum so the persisted flag is legible to an operator with `cat` and
    to anything that reads the file without importing this module — the flag has
    to be readable when the process that writes it is the thing that is broken.
    """

    #: Reads and writes on SQLite; every write mirrored forward. The state the
    #: migration has been in since SRV-01b, and the one an absent flag means.
    DUAL_WRITE = "dual_write"
    #: Stage one: reads served from PostgreSQL, writes still SQLite-first and
    #: mirrored forward. Reversible by flag, losing nothing.
    READS_POSTGRES = "reads_postgres"
    #: Stage two: writes native to PostgreSQL. Reversible only while the reverse
    #: mirror carries those rows back into SQLite.
    WRITES_POSTGRES = "writes_postgres"


class CutoverRefused(RuntimeError):
    """A stage change that was refused, carrying the unmet preconditions.

    An exception rather than a `False` return: advancing the cutover is not a
    query, and a caller that ignores a returned boolean advances a migration by
    forgetting to check one. `refusals` is the list an operator has to act on.
    """

    def __init__(self, message: str, refusals: tuple[Precondition, ...] = ()) -> None:
        super().__init__(message)
        self.refusals = refusals


@dataclass(frozen=True)
class Precondition:
    """One thing that had to be true, whether it was, and what was measured.

    `detail` is written to be readable in a refusal without a second lookup: it
    names the number that failed, not the rule that was broken.
    """

    name: str
    satisfied: bool
    detail: str

    def render(self) -> str:
        return f"[{'ok' if self.satisfied else 'NO'}] {self.name}: {self.detail}"


# --- the flag ----------------------------------------------------------------


@dataclass(frozen=True)
class CutoverRecord:
    """The persisted flag: which stage, since when, on whose authority, and why.

    `evidence` holds the machine-readable report the stage was granted on — the
    parity gate's `as_dict()`, the soak's verdict, the reverse mirror's proof.
    Kept with the flag rather than in a separate log because the question asked
    six months later is "what was true when this was switched", and an answer
    that lives in a different file is an answer that was rotated away.
    """

    stage: Stage
    since: str
    operator: str
    reason: str
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "stage": self.stage.value,
            "since": self.since,
            "operator": self.operator,
            "reason": self.reason,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> CutoverRecord:
        return cls(
            stage=Stage(payload["stage"]),
            since=str(payload.get("since", "")),
            operator=str(payload.get("operator", "")),
            reason=str(payload.get("reason", "")),
            evidence=dict(payload.get("evidence") or {}),
        )


class CutoverFlag:
    """The two-stage flag, on disk, with the transitions it will and will not make.

    Read is total and never raises: a missing, empty, truncated or unparseable
    flag reads as `DUAL_WRITE`. That is not leniency about corruption, it is the
    direction the failure has to point — the alternative is a torn write that
    leaves reads pointed at PostgreSQL because the file could not be understood.
    A file that could not be parsed is reported through `damaged()`, so the
    condition is visible without ever being load-bearing.

    Write is atomic: a sibling temporary file and `os.replace`, which is what
    makes the paragraph above about *torn* writes true rather than hopeful.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._damaged = ""

    # --- state ---------------------------------------------------------

    def read(self) -> CutoverRecord:
        self._damaged = ""
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return CutoverRecord.from_dict(payload)
        except FileNotFoundError:
            return CutoverRecord(Stage.DUAL_WRITE, "", "", "no cutover flag; SQLite is authority")
        except Exception as exc:  # noqa: BLE001 - degraded to the safe stage, never raised
            self._damaged = f"{type(exc).__name__}: {exc}"
            return CutoverRecord(
                Stage.DUAL_WRITE,
                "",
                "",
                f"the cutover flag at {self.path} could not be read ({self._damaged}); "
                "reads stay on SQLite",
            )

    def damaged(self) -> str:
        """Why the last `read()` fell back to `DUAL_WRITE`, or `''`.

        Separate from `read()` because the fallback must never be a failure the
        caller has to handle — a read path that raises when the flag is unreadable
        is a read path that goes down with the flag.
        """
        return self._damaged

    @property
    def stage(self) -> Stage:
        return self.read().stage

    def _commit(self, record: CutoverRecord) -> CutoverRecord:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".partial")
        temporary.write_text(json.dumps(record.as_dict(), indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.path)
        return record

    # --- stage one: reads ----------------------------------------------

    def advance_reads(
        self,
        *,
        report: parity_gate.GateReport,
        backup: BackupEvidence | None,
        operator: str,
        reason: str = "",
        now: datetime | None = None,
    ) -> CutoverRecord:
        """Move reads onto PostgreSQL, or refuse and say which precondition failed.

        Takes the gate *report object* rather than a verdict an operator pasted
        in. The number that gates this switch has to be the one the gate
        produced in this process, on this database, minutes ago — a `PARITY_PASS`
        typed into a form is a claim about a run nobody here witnessed.
        """
        current = self.read().stage
        if current is Stage.READS_POSTGRES:
            return self.read()
        if current is not Stage.DUAL_WRITE:
            raise CutoverRefused(
                f"reads cannot be advanced from {current.value}: stage two is already in "
                "effect, and going back to stage one is a rollback, not an advance."
            )
        checks = read_preconditions(report=report, backup=backup, now=now)
        _refuse_unless_all(checks, "reads may not move to PostgreSQL")
        return self._commit(
            CutoverRecord(
                stage=Stage.READS_POSTGRES,
                since=_stamp(now),
                operator=operator,
                reason=reason or "stage one: reads moved to PostgreSQL",
                evidence={
                    "preconditions": [check.render() for check in checks],
                    "parity": report.as_dict(),
                    "backup": backup.as_dict() if backup else None,
                },
            )
        )

    def rollback_reads(
        self,
        *,
        operator: str,
        reason: str,
        evidence: dict | None = None,
        now: datetime | None = None,
    ) -> CutoverRecord:
        """Put reads back on SQLite. Unconditional, by design.

        No preconditions, no database access, no gate. This is the property that
        makes stage one reversible at all: writes never stopped going to SQLite,
        so there is nothing to reconcile on the way back and nothing to check
        before going. A rollback that first has to prove something is a rollback
        that cannot be run when the thing it would prove is what broke.

        Callable from `WRITES_POSTGRES` too, and it lands on `DUAL_WRITE` — but
        `rollback_writes` is what a stage-two operator wants, because *that* one
        drains the reverse mirror first. This one moves the flag and nothing
        else; from stage two on its own it is the forward-only path, and the
        refusal in `rollback_writes` explains what that costs.
        """
        return self._commit(
            CutoverRecord(
                stage=Stage.DUAL_WRITE,
                since=_stamp(now),
                operator=operator,
                reason=reason,
                evidence=dict(evidence or {}),
            )
        )

    # --- stage two: writes ---------------------------------------------

    def advance_writes(
        self,
        *,
        report: parity_gate.GateReport,
        soak_verdict: SoakVerdict,
        proof: reverse_mirror.Proof,
        backup: BackupEvidence | None,
        operator: str,
        reason: str = "",
        now: datetime | None = None,
    ) -> CutoverRecord:
        """Move writes onto PostgreSQL — the irreversible half, made reversible.

        Refuses unless the reverse mirror has been *proven* to carry PostgreSQL's
        rows back into SQLite, because without it this switch has no rollback at
        all, only a restore that discards everything written after it.
        """
        current = self.read().stage
        if current is Stage.WRITES_POSTGRES:
            return self.read()
        if current is not Stage.READS_POSTGRES:
            raise CutoverRefused(
                f"writes cannot be advanced from {current.value}: stage one moves reads "
                "and is soaked first, so that the seam serving reads is the one that has "
                "been under observation before it starts serving writes."
            )
        checks = write_preconditions(
            report=report, soak_verdict=soak_verdict, proof=proof, backup=backup, now=now
        )
        _refuse_unless_all(checks, "writes may not move to PostgreSQL")
        return self._commit(
            CutoverRecord(
                stage=Stage.WRITES_POSTGRES,
                since=_stamp(now),
                operator=operator,
                reason=reason or "stage two: writes moved to PostgreSQL",
                evidence={
                    "preconditions": [check.render() for check in checks],
                    "parity": report.as_dict(),
                    "soak": soak_verdict.as_dict(),
                    "reverse_mirror": {
                        "obligation": reverse_mirror.OBLIGATION.render(),
                        "rows_carried": proof.carry.rows,
                        "would_lose": proof.losses,
                    },
                    "backup": backup.as_dict() if backup else None,
                },
            )
        )

    def rollback_writes(
        self,
        *,
        authority: parity_gate.Authority,
        connection_factory: Callable[[], Any] | None = None,
        root: Path | None = None,
        operator: str,
        reason: str,
        to: Stage = Stage.READS_POSTGRES,
        now: datetime | None = None,
    ) -> CutoverRecord:
        """Drain the reverse mirror, then put writes back on SQLite.

        The drain comes first and the flag moves second, and that order is the
        whole of the safety. Moving the flag first would freeze PostgreSQL at
        whatever it held and then copy it — which is the same rows, but with a
        window in between during which a write lands in a store the flag says is
        no longer authoritative and the drain has already passed.

        Refuses when the drain does not prove itself, and the refusal names the
        number that matters: how many rows a rollback would lose. Refusing here
        is not obstruction — the operator can still call `rollback_reads`, which
        moves the flag and nothing else. What the refusal buys is that doing so
        is a decision with the loss in front of it, rather than a rollback that
        looked complete.

        Lands on `READS_POSTGRES` by default rather than `DUAL_WRITE`: writes go
        back to SQLite and are mirrored forward again, which is exactly stage
        one, and stage one is the state that was soaked. An operator who wants
        the whole way back passes `to=Stage.DUAL_WRITE`.
        """
        current = self.read().stage
        if current is not Stage.WRITES_POSTGRES:
            raise CutoverRefused(
                f"writes are not on PostgreSQL (stage is {current.value}); there is nothing "
                "to roll back."
            )
        proof = reverse_mirror.prove(
            authority=authority, connection_factory=connection_factory, root=root
        )
        if not proof.safe:
            losses = proof.losses
            raise CutoverRefused(
                "the reverse mirror cannot account for what PostgreSQL holds, so this "
                "rollback would lose rows: "
                + (
                    f"{losses} row(s) written in PostgreSQL have no counterpart in SQLite"
                    if losses
                    else "the count could not be established"
                )
                + ". Restoring the backup instead recovers the database as it stood before "
                "stage two and loses everything written since — for the task queue, work "
                "that was accepted and promised. Fix the carry-back, or roll back reads "
                "only (`rollback_reads`) with the loss in view.\n"
                + proof.render(),
                refusals=(
                    Precondition(
                        "reverse-mirror carry-back",
                        False,
                        proof.render(),
                    ),
                ),
            )
        return self._commit(
            CutoverRecord(
                stage=to,
                since=_stamp(now),
                operator=operator,
                reason=reason,
                evidence={
                    "rows_carried_back": proof.carry.rows,
                    "would_have_lost": proof.losses,
                    "reverse_mirror": reverse_mirror.OBLIGATION.render(),
                },
            )
        )


# --- preconditions -----------------------------------------------------------

#: How old the backup behind a cutover switch may be.
#:
#: One hour, and the reasoning is different at each stage. At stage one the
#: backup is for the failure nobody predicted — writes are still going to SQLite,
#: so it is not the rollback path — and an hour keeps it a real artefact of this
#: cutover rather than last night's. At stage two it is the *forward-only* path,
#: and the window between the backup and the switch is exactly what a
#: forward-only recovery discards. Deliberately the same number at both, because
#: an operator learning a second, looser rule at the more dangerous switch is how
#: the looser rule wins.
MAX_BACKUP_AGE_SECONDS = 3600

#: How long stage one must be soaked before stage two may be considered.
#:
#: 24 hours: long enough to span a full daily cycle of this system's own
#: schedules — the nightly backup, the queue reaper's timer, the planner and
#: review ticks — so that the seam has served reads for every kind of load the
#: deployment generates, not only the kind that happens on a weekday afternoon.
MIN_SOAK_SECONDS = 24 * 60 * 60

#: The seven `GENERATED ALWAYS AS IDENTITY` tables of `docs/srv01b-schema-map.md`.
#: Not a constant here — see `identity_tables()`.
EXPECTED_IDENTITY_TABLES = 7


def identity_tables() -> tuple[str, ...]:
    """The identity tables, derived from the declarations rather than listed.

    The schema map counts seven and names them, and a copy of that list here
    would be a copy that a later migration can make wrong without failing
    anything. `tests/db/test_two_stage_cutover.py` pins the seven names against
    this function, so adding an eighth identity table is a reviewed change to a
    test rather than a silent widening of what "all sequences proven" means.
    """
    return tuple(
        table for table, subject in parity_gate.subjects().items() if subject.spec.identity
    )


def read_preconditions(
    *,
    report: parity_gate.GateReport,
    backup: BackupEvidence | None,
    now: datetime | None = None,
) -> tuple[Precondition, ...]:
    """The three things that must hold before reads move. All of them, or none.

    Returned as a list rather than a boolean so a refusal can say which one
    failed and with what number. An operator told only "refused" reruns the
    thing that already worked.
    """
    checks: list[Precondition] = []

    total = report.divergence_total
    checks.append(
        Precondition(
            "PARITY_PASS",
            report.passed and total == 0,
            (
                f"the gate passed with {total} divergences over {len(report.results)} tables"
                if report.passed and total == 0
                else "the gate did not pass: "
                + (
                    "the divergence total was never established (a mirror was unreadable), "
                    "so there is no zero to advance on"
                    if total is None
                    else f"{total} divergence(s), "
                    + ", ".join(
                        f"{shape}={count}"
                        for shape, count in report.shape_totals().items()
                        if count
                    )
                )
                + (
                    f"; tables never reached: {', '.join(report.unreached)}"
                    if report.unreached
                    else ""
                )
            ),
        )
    )

    expected = set(identity_tables())
    proven = {
        result.identity.table for result in report.results if result.identity is not None
    }
    checks.append(
        Precondition(
            "identity sequences resynced and proven",
            bool(expected) and expected <= proven,
            (
                f"{len(proven & expected)}/{len(expected)} sequences resynced and proven by a "
                "native insert"
                + (
                    ""
                    if expected <= proven
                    else f"; unproven: {', '.join(sorted(expected - proven))} — the first "
                    "native write after the cutover collides with a mirrored id"
                )
            ),
        )
    )

    checks.append(_backup_precondition(backup, now))
    return tuple(checks)


def write_preconditions(
    *,
    report: parity_gate.GateReport,
    soak_verdict: SoakVerdict,
    proof: reverse_mirror.Proof,
    backup: BackupEvidence | None,
    now: datetime | None = None,
) -> tuple[Precondition, ...]:
    """Everything stage one needed, freshly, plus the two things only stage two needs.

    The parity and identity checks are re-run rather than inherited. Stage one's
    proof decayed the moment stage one started — SQLite kept assigning ids that
    the mirror wrote explicitly, leaving the sequence behind `max(id)` again —
    so pointing at it here would be citing a measurement that this stage's own
    duration invalidated.
    """
    checks = list(read_preconditions(report=report, backup=backup, now=now))

    checks.append(
        Precondition(
            "stage one soaked",
            soak_verdict.passed and soak_verdict.seconds >= MIN_SOAK_SECONDS,
            (
                f"{soak_verdict.seconds:.0f}s of soak over {soak_verdict.polls} polls, "
                f"{soak_verdict.transient_missing} lagging row(s) that the next write repaired"
                if soak_verdict.passed
                else f"the soak ended by rolling reads back: {soak_verdict.reason}"
            )
            + (
                ""
                if soak_verdict.seconds >= MIN_SOAK_SECONDS
                else f" — below the {MIN_SOAK_SECONDS}s minimum"
            ),
        )
    )

    losses = proof.losses
    checks.append(
        Precondition(
            "reverse mirror proven",
            proof.safe,
            (
                f"{proof.carry.rows} rows carried back; a rollback right now would lose "
                f"{losses} — {reverse_mirror.OBLIGATION.render()}"
                if proof.safe
                else "without it stage two has no rollback, only a restore that discards "
                "everything written after the switch.\n" + proof.render()
            ),
        )
    )
    return tuple(checks)


def _backup_precondition(backup: BackupEvidence | None, now: datetime | None) -> Precondition:
    if backup is None:
        return Precondition(
            "fresh backup",
            False,
            "no verified backup archive was found. `scripts/aicc_pg_backup.sh --out-dir ... "
            "--verify` writes one; `scripts/aicc_pg_restore.sh` restores it side by side in "
            "about 5.4s (measured, SRV-08b).",
        )
    age = backup.age_seconds(now)
    fresh = age <= MAX_BACKUP_AGE_SECONDS
    return Precondition(
        "fresh backup",
        fresh and backup.checksum_matches,
        f"{backup.path.name}, {backup.size_bytes} bytes, {age:.0f}s old"
        + ("" if fresh else f" — older than the {MAX_BACKUP_AGE_SECONDS}s limit")
        + ("" if backup.checksum_matches else f" — {backup.checksum_detail}"),
    )


def _refuse_unless_all(checks: Iterable[Precondition], what: str) -> None:
    unmet = tuple(check for check in checks if not check.satisfied)
    if not unmet:
        return
    raise CutoverRefused(
        f"{what}:\n" + "\n".join(f"  {check.render()}" for check in unmet), unmet
    )


# --- backup evidence ---------------------------------------------------------


@dataclass(frozen=True)
class BackupEvidence:
    """A backup archive on disk, and whether it is one.

    `scripts/aicc_pg_backup.sh` writes `aicc-<db>-<stamp>.dump` beside a
    `.sha256` sidecar, and renames from `.partial` only on success. All three
    facts are checked here, because each failure produces a file that looks like
    a backup: a `.partial` left by a crashed run, an archive whose sidecar is
    missing, and an archive whose bytes no longer match the sidecar.

    The digest is recomputed rather than trusted. It is the same check
    `aicc_pg_restore.sh` runs before it writes anything, moved to the moment the
    decision is made instead of the moment the recovery is attempted — a backup
    first discovered to be corrupt during a restore is a backup that was never
    there.
    """

    path: Path
    taken_at: float
    size_bytes: int
    checksum_matches: bool
    checksum_detail: str = ""

    def age_seconds(self, now: datetime | None = None) -> float:
        reference = now.timestamp() if now is not None else time.time()
        return max(0.0, reference - self.taken_at)

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "taken_at": datetime.fromtimestamp(self.taken_at).isoformat(timespec="seconds"),
            "size_bytes": self.size_bytes,
            "checksum_matches": self.checksum_matches,
            "checksum_detail": self.checksum_detail,
        }


def latest_backup(directory: Path, database: str) -> BackupEvidence | None:
    """The newest finished archive for `database`, checksum verified, or `None`.

    `.partial` files are not candidates at any age: the backup script renames
    only on success, so a `.partial` is a run that did not finish, and the
    newest file in the directory is exactly what a crashed run leaves behind.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return None
    archives = sorted(
        (path for path in directory.glob(f"aicc-{database}-*.dump") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not archives:
        return None
    archive = archives[0]
    matches, detail = _checksum_verdict(archive)
    stat = archive.stat()
    return BackupEvidence(archive, stat.st_mtime, stat.st_size, matches, detail)


def _checksum_verdict(archive: Path) -> tuple[bool, str]:
    sidecar = archive.with_name(archive.name + ".sha256")
    try:
        recorded = sidecar.read_text(encoding="utf-8").split()[0].strip().lower()
    except FileNotFoundError:
        return False, f"no {sidecar.name} beside it, so nothing states what it should contain"
    except (IndexError, UnicodeDecodeError) as exc:
        return False, f"{sidecar.name} does not hold a digest ({type(exc).__name__}: {exc})"
    digest = hashlib.sha256()
    with archive.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    actual = digest.hexdigest()
    if actual != recorded:
        return False, f"its bytes hash to {actual[:16]}…, the sidecar records {recorded[:16]}…"
    return True, f"sha256 {actual[:16]}… matches {sidecar.name}"


# --- the soak ----------------------------------------------------------------


@dataclass(frozen=True)
class SoakVerdict:
    """How a soak ended, and the evidence for it."""

    passed: bool
    polls: int
    seconds: float
    #: Rows the mirror had not received yet when a poll looked, and had by the
    #: next one. Reported rather than swallowed: it is the size of the dual-write
    #: lag, which is a fact about the deployment worth knowing before stage two.
    transient_missing: int
    rolled_back: bool
    reason: str
    findings: tuple[parity_gate.Finding, ...] = ()

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "polls": self.polls,
            "seconds": round(self.seconds, 1),
            "transient_missing": self.transient_missing,
            "rolled_back": self.rolled_back,
            "reason": self.reason,
            "findings": [
                {"table": finding.table, "check": finding.check, "detail": finding.detail}
                for finding in self.findings
            ],
        }

    def render(self) -> str:
        lines = [
            f"soak: {'PASS' if self.passed else 'ROLLED BACK'}",
            f"  polls              {self.polls}",
            f"  elapsed            {self.seconds:.0f}s",
            f"  lagging rows seen  {self.transient_missing} (repaired by the next write)",
            f"  reason             {self.reason}",
        ]
        for finding in self.findings:
            lines.append(f"  {finding.table}: {finding.check}: {finding.detail}")
        return "\n".join(lines)


def soak(
    *,
    flag: CutoverFlag,
    authority: parity_gate.Authority,
    connection_factory: Callable[[], Any] | None = None,
    duration_seconds: float = MIN_SOAK_SECONDS,
    poll_seconds: float = 60.0,
    operator: str = "soak",
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    on_poll: Callable[[int, parity_gate.GateReport], None] | None = None,
) -> SoakVerdict:
    """Run the gate continuously through stage one, and put reads back if it reddens.

    The loop is the second half of what makes stage one safe. The preconditions
    prove parity at one instant against a quiesced system; the soak asks the same
    question repeatedly against a live one, which is the only way to learn that
    a write path mirrors under real traffic rather than under a rehearsal.

    On anything but lag, it calls `rollback_reads` itself. Not a page, not a
    metric — the rollback is free and loses nothing, so the correct response to
    an unexplained divergence is to take it, and leaving that to a human at 3am
    means it happens after the divergence has been served to somebody.

    `clock`, `sleep` and `on_poll` are injected so the loop is testable without
    waiting. That is also why the duration is checked against `clock()` rather
    than counted in polls: a poll that takes longer than its interval must not
    shorten the soak.
    """
    started = clock()
    polls = 0
    transient = 0
    outstanding: dict[str, set] = {}

    while True:
        report = parity_gate.run(
            authority=authority, connection_factory=connection_factory, prove_identity=True
        )
        polls += 1
        if on_poll is not None:
            on_poll(polls, report)

        verdict = _poll_verdict(report, outstanding)
        transient += verdict.repaired
        if verdict.rollback:
            elapsed = clock() - started
            flag.rollback_reads(
                operator=operator,
                reason=verdict.reason,
                evidence={"parity": report.as_dict(), "polls": polls, "seconds": round(elapsed, 1)},
            )
            return SoakVerdict(
                passed=False,
                polls=polls,
                seconds=elapsed,
                transient_missing=transient,
                rolled_back=True,
                reason=verdict.reason,
                findings=report.findings,
            )

        elapsed = clock() - started
        if elapsed >= duration_seconds:
            return SoakVerdict(
                passed=True,
                polls=polls,
                seconds=elapsed,
                transient_missing=transient,
                rolled_back=False,
                reason=f"{polls} consecutive clean polls over {elapsed:.0f}s",
            )
        sleep(poll_seconds)


@dataclass(frozen=True)
class _PollVerdict:
    rollback: bool
    reason: str
    repaired: int


def _poll_verdict(report: parity_gate.GateReport, outstanding: dict[str, set]) -> _PollVerdict:
    """Decide one poll, and carry the unconfirmed `missing` keys to the next.

    `outstanding` is mutated deliberately: it is the memory that turns "a row is
    missing" into "a row is *still* missing", which is the only question lag can
    answer differently from loss. A key seen missing twice running was never lag,
    whatever the count around it says.
    """
    if report.unreached:
        return _PollVerdict(
            True,
            "the gate did not reach " + ", ".join(report.unreached) + "; a table nobody "
            "compared cannot be reported as agreeing",
            0,
        )

    hard: list[str] = []
    still_missing: list[str] = []
    repaired = 0
    seen_now: dict[str, set] = {}

    for result in report.results:
        previous = outstanding.get(result.table, set())
        if result.divergences is None:
            hard.append(
                f"{result.table}: the mirror could not be read, so nothing was compared"
            )
            seen_now[result.table] = previous
            continue

        missing_keys = set()
        for finding in result.findings:
            if finding.check != parity_gate.CHECK_DIVERGENCE:
                hard.append(f"{result.table}: {finding.check}: {finding.detail}")
                continue
            for record in finding.records:
                found = parity_gate.shape(record)
                if found == parity_gate.SHAPE_MISSING:
                    missing_keys.add(record["id"])
                else:
                    hard.append(
                        f"{result.table}: {found} divergence on {record['id']!r} "
                        f"({', '.join(record['fields'])}) — lag explains a row the mirror has "
                        "not received yet, and nothing else"
                    )
        confirmed = missing_keys & previous
        if confirmed:
            still_missing.append(
                f"{result.table}: {len(confirmed)} row(s) still absent from the mirror one "
                f"poll later, first {sorted(confirmed)[0]!r} — a row the next write did not "
                "repair was never lag"
            )
        repaired += len(previous - missing_keys)
        seen_now[result.table] = missing_keys

    outstanding.clear()
    outstanding.update(seen_now)

    if hard:
        return _PollVerdict(True, "; ".join(hard[:3]), repaired)
    if still_missing:
        return _PollVerdict(True, "; ".join(still_missing[:3]), repaired)
    return _PollVerdict(False, "", repaired)


def _stamp(now: datetime | None) -> str:
    """A timestamp in the shape every other timestamp in this application has.

    Naive local, second precision, no offset — what `models.iso_now()` emits and
    what `mirror_support.to_instant` is written to interpret. Spelled out rather
    than imported so `command_center/db/` keeps not depending on the application
    package, which is the same reason `parity_gate` reads SQLite with a bare
    `SELECT`.
    """
    return (now or datetime.now()).isoformat(timespec="seconds")
