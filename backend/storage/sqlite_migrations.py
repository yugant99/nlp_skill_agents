from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime


class SchemaCompatibilityError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


def apply_migrations(
    connection: sqlite3.Connection,
    *,
    database_name: str,
    migrations: Sequence[Migration],
) -> int:
    _validate_definitions(migrations)
    latest_version = migrations[-1].version if migrations else 0
    current_version = int(connection.execute("pragma user_version").fetchone()[0])
    if current_version > latest_version:
        raise SchemaCompatibilityError(
            f"{database_name} schema version {current_version} is newer than "
            f"supported version {latest_version}"
        )
    ledger_exists = connection.execute(
        """
        select 1 from sqlite_master
        where type = 'table' and name = 'schema_migrations'
        """
    ).fetchone()
    if current_version and ledger_exists is None:
        raise SchemaCompatibilityError(
            f"{database_name} migration ledger disagrees with user_version"
        )
    connection.execute(
        """
        create table if not exists schema_migrations (
          version integer primary key,
          name text not null,
          applied_at text not null
        )
        """
    )
    recorded = {
        int(row[0]): str(row[1])
        for row in connection.execute(
            "select version, name from schema_migrations order by version"
        )
    }
    expected_recorded_versions = set(range(1, current_version + 1))
    if set(recorded) != expected_recorded_versions:
        raise SchemaCompatibilityError(
            f"{database_name} migration ledger disagrees with user_version"
        )
    definitions = {migration.version: migration.name for migration in migrations}
    for version, name in recorded.items():
        if definitions.get(version) != name:
            raise SchemaCompatibilityError(
                f"{database_name} migration {version} does not match this application"
            )

    for migration in migrations:
        if migration.version <= current_version:
            continue
        if connection.in_transaction:
            raise SchemaCompatibilityError(
                f"{database_name} migration requires an inactive connection"
            )
        try:
            connection.execute("begin immediate")
            migration.apply(connection)
            connection.execute(
                """
                insert into schema_migrations (version, name, applied_at)
                values (?, ?, ?)
                """,
                (
                    migration.version,
                    migration.name,
                    datetime.now(UTC).isoformat(),
                ),
            )
            connection.execute(f"pragma user_version = {migration.version}")
            connection.commit()
        except Exception as exc:
            connection.rollback()
            raise SchemaCompatibilityError(
                f"{database_name} migration {migration.version} ({migration.name}) failed"
            ) from exc
        current_version = migration.version
    return current_version


def schema_status(connection: sqlite3.Connection) -> list[dict[str, object]]:
    exists = connection.execute(
        """
        select 1 from sqlite_master
        where type = 'table' and name = 'schema_migrations'
        """
    ).fetchone()
    if exists is None:
        return []
    return [
        {"version": int(row[0]), "name": str(row[1]), "applied_at": str(row[2])}
        for row in connection.execute(
            "select version, name, applied_at from schema_migrations order by version"
        )
    ]


def add_text_column_if_missing(
    connection: sqlite3.Connection,
    *,
    table: str,
    column: str,
) -> None:
    columns = {
        str(row[1]) for row in connection.execute(f"pragma table_info({table})")
    }
    if column not in columns:
        connection.execute(
            f"alter table {table} add column {column} text not null default ''"
        )


def _validate_definitions(migrations: Sequence[Migration]) -> None:
    versions = [migration.version for migration in migrations]
    if versions != list(range(1, len(migrations) + 1)):
        raise ValueError("Migration versions must be contiguous and start at 1")
    if any(not migration.name.strip() for migration in migrations):
        raise ValueError("Migration names must be non-empty")
