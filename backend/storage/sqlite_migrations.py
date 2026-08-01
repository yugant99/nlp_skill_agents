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
    definitions = {migration.version: migration.name for migration in migrations}
    if connection.in_transaction:
        current_version = _validate_migration_state(
            connection,
            database_name=database_name,
            latest_version=latest_version,
            definitions=definitions,
            create_ledger=False,
        )
        if current_version < latest_version:
            raise SchemaCompatibilityError(
                f"{database_name} migration requires an inactive connection"
            )
        return current_version

    connection.execute("begin")
    try:
        current_version = _validate_migration_state(
            connection,
            database_name=database_name,
            latest_version=latest_version,
            definitions=definitions,
            create_ledger=False,
        )
    finally:
        connection.rollback()
    if current_version == latest_version:
        return current_version

    while True:
        try:
            connection.execute("begin immediate")
            current_version = _validate_migration_state(
                connection,
                database_name=database_name,
                latest_version=latest_version,
                definitions=definitions,
                create_ledger=True,
            )
            migration = next(
                (
                    candidate
                    for candidate in migrations
                    if candidate.version > current_version
                ),
                None,
            )
            if migration is None:
                connection.commit()
                return current_version
        except Exception:
            connection.rollback()
            raise
        try:
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


def _validate_migration_state(
    connection: sqlite3.Connection,
    *,
    database_name: str,
    latest_version: int,
    definitions: dict[int, str],
    create_ledger: bool,
) -> int:
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
    if create_ledger:
        connection.execute(
            """
            create table if not exists schema_migrations (
              version integer primary key,
              name text not null,
              applied_at text not null
            )
            """
        )
        ledger_exists = True
    recorded_rows = (
        connection.execute(
            """
            select version, name, applied_at
            from schema_migrations order by version
            """
        ).fetchall()
        if ledger_exists
        else []
    )
    recorded = {int(row[0]): str(row[1]) for row in recorded_rows}
    expected_recorded_versions = set(range(1, current_version + 1))
    if set(recorded) != expected_recorded_versions:
        raise SchemaCompatibilityError(
            f"{database_name} migration ledger disagrees with user_version"
        )
    for version, name in recorded.items():
        if definitions.get(version) != name:
            raise SchemaCompatibilityError(
                f"{database_name} migration {version} does not match this application"
            )
    for version, _, applied_at in recorded_rows:
        normalized_applied_at = str(applied_at)
        try:
            parsed_applied_at = datetime.fromisoformat(normalized_applied_at)
        except ValueError as exc:
            raise SchemaCompatibilityError(
                f"{database_name} migration {version} has invalid applied_at"
            ) from exc
        if (
            not normalized_applied_at.strip()
            or len(normalized_applied_at) > 64
            or parsed_applied_at.tzinfo is None
        ):
            raise SchemaCompatibilityError(
                f"{database_name} migration {version} has invalid applied_at"
            )
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
