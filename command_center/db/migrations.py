"""Forward/backward migration runner for the AICC server database.

Migrations are plain SQL files in `sql/`, named `NNNN_slug.up.sql` with a
matching `NNNN_slug.down.sql`. Applied versions are recorded in
`schema_migration`.

Two properties matter more than the file format:

*Serialization.* Every run takes a session-level `pg_advisory_lock` before it
looks at the ledger. Two servers starting at the same moment — the normal case
for a rolling deploy — would otherwise both read "0001 not applied" and both
try to create the same tables; the second would fail mid-DDL and leave the
operator diagnosing a crash loop instead of a queued migration.

*Atomicity.* Each migration and its ledger row are written in one transaction,
so an interrupted migration leaves either the old schema or the new one, never
a half-applied schema that claims to be complete. PostgreSQL has transactional
DDL, which is what makes this possible at all — it is why the ledger is not
written by a separate statement after the fact.

Down-migrations are first-class rather than a comment saying "restore from
backup": the acceptance criterion for this task requires that the schema can be
moved forward *and* back under test, and a downgrade path that is never
executed is a downgrade path that does not work.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "Migration",
    "MigrationError",
    "applied_versions",
    "current_version",
    "discover",
    "downgrade",
    "ensure_ledger",
    "upgrade",
]

_LOG = logging.getLogger(__name__)

SQL_DIR = Path(__file__).resolve().parent / "sql"

# Derived from 'aicc-schema-migration'; any constant works as long as it is
# stable and unlikely to collide with another advisory lock in this database.
ADVISORY_LOCK_KEY = 0x4149_4343_5343_484D

_NAME_RE = re.compile(r"^(?P<version>\d{4})_(?P<slug>[a-z0-9_]+)\.(?P<direction>up|down)\.sql$")

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migration (
    version     integer PRIMARY KEY,
    slug        text        NOT NULL,
    checksum    text        NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now(),
    applied_by  text        NOT NULL DEFAULT current_user
)
"""


class MigrationError(RuntimeError):
    """Raised when the migration set or the database state is inconsistent."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    slug: str
    up_path: Path
    down_path: Path

    @property
    def up_sql(self) -> str:
        return self.up_path.read_text(encoding="utf-8")

    @property
    def down_sql(self) -> str:
        return self.down_path.read_text(encoding="utf-8")

    @property
    def checksum(self) -> str:
        import hashlib

        return hashlib.sha256(self.up_sql.encode("utf-8")).hexdigest()


def discover(sql_dir: Path | None = None) -> tuple[Migration, ...]:
    """Load the migration set, rejecting gaps, duplicates and missing downgrades."""
    directory = SQL_DIR if sql_dir is None else sql_dir
    ups: dict[int, tuple[str, Path]] = {}
    downs: dict[int, Path] = {}

    for path in sorted(directory.glob("*.sql")):
        match = _NAME_RE.match(path.name)
        if match is None:
            raise MigrationError(
                f"{path.name!r} does not match NNNN_slug.(up|down).sql; migration "
                "files are ordered by name, so an unparseable name would be applied "
                "in an undefined position."
            )
        version = int(match["version"])
        if match["direction"] == "up":
            if version in ups:
                raise MigrationError(f"Duplicate up-migration for version {version}.")
            ups[version] = (match["slug"], path)
        else:
            downs[version] = path

    migrations: list[Migration] = []
    for version in sorted(ups):
        slug, up_path = ups[version]
        down_path = downs.get(version)
        if down_path is None:
            raise MigrationError(
                f"Migration {version:04d}_{slug} has no down-migration. Every "
                "migration must be reversible under test."
            )
        migrations.append(
            Migration(version=version, slug=slug, up_path=up_path, down_path=down_path)
        )

    expected = list(range(1, len(migrations) + 1))
    if [m.version for m in migrations] != expected:
        raise MigrationError(
            f"Migration versions must be contiguous from 0001; got "
            f"{[m.version for m in migrations]}."
        )
    return tuple(migrations)


def ensure_ledger(conn) -> None:
    """Create `schema_migration` if absent. Requires DDL rights (migrator role)."""
    with conn.cursor() as cur:
        cur.execute(_LEDGER_DDL)


def applied_versions(conn) -> tuple[int, ...]:
    """Versions recorded as applied, oldest first.

    Read-only on purpose: the readiness probe runs this as `aicc_app`, which
    has no DDL rights, so creating the ledger here would turn a health check
    into a permission error.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migration ORDER BY version")
        return tuple(row[0] for row in cur.fetchall())


def current_version(conn) -> int:
    """Highest applied version, or 0 on a fresh database."""
    versions = applied_versions(conn)
    return versions[-1] if versions else 0


def upgrade(conn, *, target: int | None = None, sql_dir: Path | None = None) -> tuple[int, ...]:
    """Apply every pending migration up to `target`. Returns versions applied."""
    migrations = discover(sql_dir)
    ceiling = migrations[-1].version if target is None else target

    with _advisory_lock(conn):
        ensure_ledger(conn)
        already = set(applied_versions(conn))
        _verify_checksums(conn, migrations)
        applied: list[int] = []
        for migration in migrations:
            if migration.version in already or migration.version > ceiling:
                continue
            _LOG.info("applying migration %04d_%s", migration.version, migration.slug)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(migration.up_sql)
                    cur.execute(
                        "INSERT INTO schema_migration (version, slug, checksum) "
                        "VALUES (%s, %s, %s)",
                        (migration.version, migration.slug, migration.checksum),
                    )
            applied.append(migration.version)
        return tuple(applied)


def downgrade(conn, *, target: int, sql_dir: Path | None = None) -> tuple[int, ...]:
    """Revert applied migrations down to (and including) version > `target`."""
    if target < 0:
        raise MigrationError(f"Downgrade target must be >= 0; got {target}.")
    migrations = {m.version: m for m in discover(sql_dir)}

    with _advisory_lock(conn):
        ensure_ledger(conn)
        reverted: list[int] = []
        for version in sorted(applied_versions(conn), reverse=True):
            if version <= target:
                break
            migration = migrations.get(version)
            if migration is None:
                raise MigrationError(
                    f"Database has migration {version} applied but no file defines "
                    "it; refusing to guess how to revert it."
                )
            _LOG.info("reverting migration %04d_%s", version, migration.slug)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(migration.down_sql)
                    cur.execute("DELETE FROM schema_migration WHERE version = %s", (version,))
            reverted.append(version)
        return tuple(reverted)


def _verify_checksums(conn, migrations) -> None:
    """Fail if an already-applied migration file has been edited since.

    Editing an applied migration is the quiet way to make two environments
    disagree about what the schema is while both report the same version.
    """
    by_version = {m.version: m for m in migrations}
    with conn.cursor() as cur:
        cur.execute("SELECT version, checksum FROM schema_migration ORDER BY version")
        rows = cur.fetchall()
    for version, checksum in rows:
        migration = by_version.get(version)
        if migration is None:
            continue
        if migration.checksum != checksum:
            raise MigrationError(
                f"Migration {version:04d}_{migration.slug} was modified after it was "
                f"applied (recorded {checksum[:12]}…, file {migration.checksum[:12]}…). "
                "Add a new migration instead of editing an applied one."
            )


class _advisory_lock:
    """Session-level advisory lock held for the duration of a migration run."""

    def __init__(self, conn) -> None:
        self._conn = conn

    def __enter__(self):
        with self._conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        return self

    def __exit__(self, *exc_info) -> None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
