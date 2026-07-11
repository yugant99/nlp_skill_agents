import sqlite3

import pytest

from backend.storage.sqlite_migrations import (
    Migration,
    SchemaCompatibilityError,
    apply_migrations,
    schema_status,
)


def test_sqlite_migrations_apply_once_in_order_and_record_status(tmp_path) -> None:
    path = tmp_path / "ordered.sqlite3"

    def create_records(connection: sqlite3.Connection) -> None:
        connection.execute("create table records (id text primary key)")

    def add_label(connection: sqlite3.Connection) -> None:
        connection.execute(
            "alter table records add column label text not null default ''"
        )

    migrations = [
        Migration(1, "create-records", create_records),
        Migration(2, "add-record-label", add_label),
    ]
    with sqlite3.connect(path) as connection:
        assert apply_migrations(
            connection,
            database_name="ordered",
            migrations=migrations,
        ) == 2
        assert apply_migrations(
            connection,
            database_name="ordered",
            migrations=migrations,
        ) == 2
        assert [row["name"] for row in schema_status(connection)] == [
            "create-records",
            "add-record-label",
        ]
        assert connection.execute("pragma user_version").fetchone()[0] == 2


def test_sqlite_migration_failure_rolls_back_schema_version_and_ledger(tmp_path) -> None:
    path = tmp_path / "failure.sqlite3"

    def create_records(connection: sqlite3.Connection) -> None:
        connection.execute("create table records (id text primary key)")

    def fail_after_schema_change(connection: sqlite3.Connection) -> None:
        connection.execute("create table partial_records (id text primary key)")
        connection.execute("insert into missing_table values (1)")

    with sqlite3.connect(path) as connection:
        with pytest.raises(SchemaCompatibilityError, match="migration 2"):
            apply_migrations(
                connection,
                database_name="failure",
                migrations=[
                    Migration(1, "create-records", create_records),
                    Migration(2, "fail", fail_after_schema_change),
                ],
            )
        assert connection.execute("pragma user_version").fetchone()[0] == 1
        assert connection.execute(
            """
            select count(*) from sqlite_master
            where type = 'table' and name = 'partial_records'
            """
        ).fetchone()[0] == 0
        assert [row["version"] for row in schema_status(connection)] == [1]


def test_sqlite_migrations_reject_newer_or_conflicting_schema(tmp_path) -> None:
    path = tmp_path / "incompatible.sqlite3"
    migration = Migration(
        1,
        "create-records",
        lambda connection: connection.execute("create table records (id text)"),
    )
    with sqlite3.connect(path) as connection:
        connection.execute("pragma user_version = 2")
        with pytest.raises(SchemaCompatibilityError, match="newer"):
            apply_migrations(
                connection,
                database_name="incompatible",
                migrations=[migration],
            )

    conflict_path = tmp_path / "conflict.sqlite3"
    with sqlite3.connect(conflict_path) as connection:
        apply_migrations(
            connection,
            database_name="conflict",
            migrations=[migration],
        )
        connection.execute(
            "update schema_migrations set name = 'different' where version = 1"
        )
        with pytest.raises(SchemaCompatibilityError, match="does not match"):
            apply_migrations(
                connection,
                database_name="conflict",
                migrations=[migration],
            )

    missing_ledger_path = tmp_path / "missing-ledger.sqlite3"
    with sqlite3.connect(missing_ledger_path) as connection:
        connection.execute("pragma user_version = 1")
        with pytest.raises(SchemaCompatibilityError, match="ledger disagrees"):
            apply_migrations(
                connection,
                database_name="missing ledger",
                migrations=[migration],
            )
        assert connection.execute(
            """
            select count(*) from sqlite_master
            where type = 'table' and name = 'schema_migrations'
            """
        ).fetchone()[0] == 0


def test_sqlite_migrations_do_not_rollback_a_caller_transaction(tmp_path) -> None:
    path = tmp_path / "active.sqlite3"
    migration = Migration(
        1,
        "create-records",
        lambda connection: connection.execute("create table records (id text)"),
    )
    with sqlite3.connect(path) as connection:
        connection.execute("create table caller_records (id text)")
        connection.execute("insert into caller_records values ('pending')")

        with pytest.raises(SchemaCompatibilityError, match="inactive connection"):
            apply_migrations(
                connection,
                database_name="active",
                migrations=[migration],
            )

        assert connection.in_transaction is True
        assert connection.execute(
            "select id from caller_records"
        ).fetchall() == [("pending",)]
