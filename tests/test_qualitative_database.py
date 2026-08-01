import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

import backend.qualitative.database as qualitative_database
from backend.qualitative.database import (
    QualitativeDatabaseConflict,
    QualitativeProjectDatabase,
    new_qualitative_id,
)
from backend.storage.project_archive import ProjectArchiveStore
from backend.storage.sqlite_migrations import (
    Migration,
    SchemaCompatibilityError,
)
from backend.storage.study_store import StudyWorkspaceStore


RESEARCHER_ID = "res_project_owner"


def _create_project(root: Path) -> tuple[str, QualitativeProjectDatabase]:
    study = StudyWorkspaceStore(root).create_study({"name": "Qualitative Study"})
    database = QualitativeProjectDatabase(root, study.id)
    database.initialize(
        researcher_id=RESEARCHER_ID,
        researcher_name="Project Owner",
    )
    return study.id, database


def test_qualitative_database_initializes_once_with_attributable_identity(
    tmp_path: Path,
) -> None:
    project_id, database = _create_project(tmp_path)

    database.initialize(
        researcher_id=RESEARCHER_ID,
        researcher_name="Project Owner",
    )

    assert database.db_path == (
        tmp_path / "studies" / project_id / "qualitative.sqlite3"
    )
    assert [record["name"] for record in database.migration_status()] == [
        "create-qualitative-core-contract"
    ]
    with sqlite3.connect(database.db_path) as connection:
        assert connection.execute("pragma user_version").fetchone()[0] == 1
        assert connection.execute("pragma foreign_key_check").fetchall() == []
        assert connection.execute(
            "select project_id from qualitative_projects"
        ).fetchall() == [(project_id,)]
        assert connection.execute(
            """
            select researcher_id, display_name, role, active from researchers
            """
        ).fetchall() == [
            (RESEARCHER_ID, "Project Owner", "researcher", 1)
        ]
        assert connection.execute(
            "select event_type, actor_id from qualitative_audit_events"
        ).fetchall() == [
            ("qualitative.project.initialized", RESEARCHER_ID)
        ]


def test_qualitative_initialization_retry_uses_persisted_bootstrap_identity(
    tmp_path: Path,
) -> None:
    _, database = _create_project(tmp_path)
    now = datetime.now(UTC).isoformat()
    with database.transaction() as connection:
        connection.execute(
            """
            insert into researchers (
              researcher_id, project_id, display_name, role,
              active, created_at, updated_at
            ) values ('res_future_reviewer', ?, 'Future Reviewer', 'reviewer',
                      1, ?, ?)
            """,
            (database.project_id, now, now),
        )

    database.initialize(
        researcher_id=RESEARCHER_ID,
        researcher_name="Project Owner",
    )

    with sqlite3.connect(database.db_path) as connection:
        assert connection.execute("select count(*) from researchers").fetchone() == (
            2,
        )
        assert connection.execute(
            """
            select actor_id from qualitative_audit_events
            where event_type = 'qualitative.project.initialized'
            """
        ).fetchall() == [(RESEARCHER_ID,)]


def test_qualitative_read_is_guarded_query_only_and_returns_rows(
    tmp_path: Path,
) -> None:
    project_id, database = _create_project(tmp_path)

    with database.read() as connection:
        assert connection.execute("pragma query_only").fetchone()[0] == 1
        project = connection.execute(
            "select project_id, created_at from qualitative_projects"
        ).fetchone()
        assert isinstance(project, sqlite3.Row)
        assert project["project_id"] == project_id
        with pytest.raises(sqlite3.OperationalError):
            connection.execute(
                "insert into qualitative_projects values ('other-project', 'now')"
            )

    with sqlite3.connect(database.db_path) as connection:
        assert connection.execute(
            "select project_id from qualitative_projects"
        ).fetchall() == [(project_id,)]


@pytest.mark.parametrize(
    "tamper_sql",
    [
        "drop table cases",
        "drop trigger prevent_frozen_code_update",
        "drop index cases_by_kind",
        "create table unexpected_qualitative_state (value text)",
    ],
)
def test_qualitative_database_rejects_tampered_schema_objects(
    tmp_path: Path,
    tamper_sql: str,
) -> None:
    _, database = _create_project(tmp_path)
    with sqlite3.connect(database.db_path) as connection:
        connection.execute(tamper_sql)

    with pytest.raises(
        QualitativeDatabaseConflict,
        match="Qualitative database is invalid",
    ):
        database.migration_status()


@pytest.mark.parametrize("path_kind", ["directory", "symlink"])
def test_qualitative_database_rejects_non_regular_paths(
    tmp_path: Path,
    path_kind: str,
) -> None:
    study = StudyWorkspaceStore(tmp_path).create_study(
        {"name": f"Qualitative {path_kind.title()} Path"}
    )
    database = QualitativeProjectDatabase(tmp_path, study.id)
    if path_kind == "directory":
        database.db_path.mkdir()
    else:
        target = tmp_path / "qualitative-target.sqlite3"
        target.write_bytes(b"")
        database.db_path.symlink_to(target)

    with pytest.raises(
        QualitativeDatabaseConflict,
        match="Qualitative database is invalid",
    ):
        database.migration_status()


def test_qualitative_database_rejects_symlinked_project_directory(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside-study"
    outside.mkdir()
    (outside / "study.json").write_text("{}", encoding="utf-8")
    studies_dir = tmp_path / "studies"
    studies_dir.mkdir()
    project_id = "symlinked-study"
    (studies_dir / project_id).symlink_to(outside, target_is_directory=True)
    database = QualitativeProjectDatabase(tmp_path, project_id)

    with pytest.raises(
        QualitativeDatabaseConflict,
        match="study directory is invalid",
    ):
        database.migration_status()

    assert not (outside / "qualitative.sqlite3").exists()
    assert not (outside / "batch_operations.sqlite3").exists()


def test_qualitative_database_rejects_foreign_project_binding(
    tmp_path: Path,
) -> None:
    study = StudyWorkspaceStore(tmp_path).create_study(
        {"name": "Foreign Qualitative Project"}
    )
    database = QualitativeProjectDatabase(tmp_path, study.id)
    database.migration_status()
    with sqlite3.connect(database.db_path) as connection:
        connection.execute(
            "insert into qualitative_projects values ('foreign-project', ?)",
            (datetime.now(UTC).isoformat(),),
        )

    with pytest.raises(
        QualitativeDatabaseConflict,
        match="Qualitative database is invalid",
    ):
        with database.read():
            pass


def test_qualitative_database_rejects_foreign_key_violations(
    tmp_path: Path,
) -> None:
    project_id, database = _create_project(tmp_path)
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(database.db_path) as connection:
        connection.execute(
            """
            insert into cases (
              case_id, project_id, case_kind, label, description,
              created_by, updated_by, created_at, updated_at
            ) values ('cas_invalid_actor', ?, 'participant', 'P1', '',
                      'res_missing', 'res_missing', ?, ?)
            """,
            (project_id, now, now),
        )

    with pytest.raises(
        QualitativeDatabaseConflict,
        match="Qualitative database is invalid",
    ):
        database.migration_status()


def test_qualitative_database_errors_are_content_safe_and_bootstrap_contained(
    tmp_path: Path,
    monkeypatch,
) -> None:
    private_text = "PRIVATE-QUALITATIVE-STORAGE-CONTENT"
    study = StudyWorkspaceStore(tmp_path).create_study(
        {"name": "Content Safe Qualitative Failure"}
    )
    database = QualitativeProjectDatabase(tmp_path, study.id)
    database.db_path.write_bytes(private_text.encode("utf-8"))

    with pytest.raises(QualitativeDatabaseConflict) as corrupt_error:
        database.migration_status()
    assert private_text not in str(corrupt_error.value)

    database.db_path.unlink()

    class BrokenConnection:
        def execute(self, *_args, **_kwargs):
            raise sqlite3.DatabaseError(private_text)

    @contextmanager
    def broken_transaction():
        yield BrokenConnection()

    monkeypatch.setattr(database, "transaction", broken_transaction)
    with pytest.raises(QualitativeDatabaseConflict) as bootstrap_error:
        database.initialize(
            researcher_id=RESEARCHER_ID,
            researcher_name="Project Owner",
        )
    assert private_text not in str(bootstrap_error.value)


def test_qualitative_database_refuses_missing_projects_and_identity_conflicts(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="Invalid qualitative project id"):
        QualitativeProjectDatabase(tmp_path, "../escape")

    missing = QualitativeProjectDatabase(tmp_path, "missing-study")
    with pytest.raises(FileNotFoundError, match="missing-study"):
        missing.initialize(
            researcher_id=RESEARCHER_ID,
            researcher_name="Project Owner",
        )

    _, database = _create_project(tmp_path)
    with pytest.raises(ValueError, match="identity conflicts"):
        database.initialize(
            researcher_id=RESEARCHER_ID,
            researcher_name="Different Person",
        )
    with pytest.raises(ValueError, match="identity conflicts"):
        database.initialize(
            researcher_id="res_different_owner",
            researcher_name="Different Person",
        )
    with pytest.raises(ValueError, match="stable lowercase identifier"):
        database.initialize(
            researcher_id="Project Owner",
            researcher_name="Project Owner",
        )

    audit_study = StudyWorkspaceStore(tmp_path).create_study(
        {"name": "Audit Conflict"}
    )
    audit_database = QualitativeProjectDatabase(tmp_path, audit_study.id)
    audit_database.migration_status()
    now = datetime.now(UTC).isoformat()
    event_id = qualitative_database._bootstrap_event_id(
        audit_study.id,
        RESEARCHER_ID,
    )
    with audit_database.transaction() as connection:
        connection.execute(
            "insert into qualitative_projects values (?, ?)",
            (audit_study.id, now),
        )
        connection.execute(
            """
            insert into researchers (
              researcher_id, project_id, display_name, role,
              active, created_at, updated_at
            ) values (?, ?, 'Project Owner', 'researcher', 1, ?, ?)
            """,
            (RESEARCHER_ID, audit_study.id, now, now),
        )
        connection.execute(
            """
            insert into qualitative_audit_events (
              event_id, project_id, actor_id, event_type,
              subject_type, subject_id, metadata_json, created_at
            ) values (?, ?, ?, 'different.event', 'project', ?, '{}', ?)
            """,
            (event_id, audit_study.id, RESEARCHER_ID, audit_study.id, now),
        )
    with pytest.raises(ValueError, match="audit identity conflicts"):
        audit_database.initialize(
            researcher_id=RESEARCHER_ID,
            researcher_name="Project Owner",
        )


def test_qualitative_schema_failure_rolls_back_partial_migration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    study = StudyWorkspaceStore(tmp_path).create_study({"name": "Migration Study"})

    def fail_after_schema_change(connection: sqlite3.Connection) -> None:
        connection.execute("create table partial_qualitative_records (id text)")
        connection.execute("insert into missing_table values (1)")

    monkeypatch.setattr(
        qualitative_database,
        "QUALITATIVE_MIGRATIONS",
        (
            qualitative_database.QUALITATIVE_MIGRATIONS[0],
            Migration(2, "fail-after-schema-change", fail_after_schema_change),
        ),
    )
    database = QualitativeProjectDatabase(tmp_path, study.id)

    with pytest.raises(SchemaCompatibilityError, match="migration 2"):
        database.migration_status()

    with sqlite3.connect(database.db_path) as connection:
        assert connection.execute("pragma user_version").fetchone()[0] == 1
        assert connection.execute(
            """
            select count(*) from sqlite_master
            where type = 'table' and name = 'partial_qualitative_records'
            """
        ).fetchone()[0] == 0


def test_qualitative_transaction_rolls_back_domain_and_audit_writes(
    tmp_path: Path,
) -> None:
    project_id, database = _create_project(tmp_path)
    now = datetime.now(UTC).isoformat()

    with pytest.raises(RuntimeError, match="stop transaction"):
        with database.transaction() as connection:
            connection.execute(
                """
                insert into cases (
                  case_id, project_id, case_kind, label, description,
                  created_by, updated_by, created_at, updated_at
                ) values ('cas_rollback', ?, 'participant', 'P1', '', ?, ?, ?, ?)
                """,
                (project_id, RESEARCHER_ID, RESEARCHER_ID, now, now),
            )
            connection.execute(
                """
                insert into qualitative_audit_events (
                  event_id, project_id, actor_id, event_type,
                  subject_type, subject_id, metadata_json, created_at
                ) values ('qae_rollback', ?, ?, 'case.created',
                          'case', 'cas_rollback', '{}', ?)
                """,
                (project_id, RESEARCHER_ID, now),
            )
            raise RuntimeError("stop transaction")

    with sqlite3.connect(database.db_path) as connection:
        assert connection.execute("select count(*) from cases").fetchone()[0] == 0
        assert connection.execute(
            "select count(*) from qualitative_audit_events where event_id = 'qae_rollback'"
        ).fetchone()[0] == 0


def test_qualitative_schema_rejects_cross_project_or_unknown_actor_writes(
    tmp_path: Path,
) -> None:
    project_id, database = _create_project(tmp_path)
    now = datetime.now(UTC).isoformat()

    with pytest.raises(sqlite3.IntegrityError, match="belongs to one project"):
        with database.transaction() as connection:
            connection.execute(
                """
                insert into qualitative_projects (project_id, created_at)
                values ('another-project', ?)
                """,
                (now,),
            )
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        with database.transaction() as connection:
            connection.execute(
                """
                insert into cases (
                  case_id, project_id, case_kind, label, description,
                  created_by, updated_by, created_at, updated_at
                ) values ('cas_unknown_actor', ?, 'participant', 'P1', '',
                          'res_missing', 'res_missing', ?, ?)
                """,
                (project_id, now, now),
            )

    with sqlite3.connect(database.db_path) as connection:
        assert connection.execute(
            "select project_id from qualitative_projects"
        ).fetchall() == [(project_id,)]
        assert connection.execute("select count(*) from cases").fetchone()[0] == 0


def test_qualitative_schema_enforces_version_and_audit_immutability(
    tmp_path: Path,
) -> None:
    project_id, database = _create_project(tmp_path)
    now = datetime.now(UTC).isoformat()
    with database.transaction() as connection:
        connection.execute(
            """
            insert into codebooks (
              codebook_id, project_id, title, description,
              created_by, updated_by, created_at, updated_at
            ) values ('cbk_interview', ?, 'Interview codebook', '', ?, ?, ?, ?)
            """,
            (project_id, RESEARCHER_ID, RESEARCHER_ID, now, now),
        )
        connection.execute(
            """
            insert into codebook_versions (
              codebook_version_id, project_id, codebook_id, version_number,
              status, based_on_version_id, created_by, created_at, frozen_at
            ) values ('cbv_interview_1', ?, 'cbk_interview', 1,
                      'draft', null, ?, ?, null)
            """,
            (project_id, RESEARCHER_ID, now),
        )
        connection.execute(
            """
            insert into codes (
              code_id, project_id, codebook_version_id, stable_code_key,
              parent_code_id, label, created_by, created_at, updated_at
            ) values ('cod_access', ?, 'cbv_interview_1', 'access',
                      null, 'Access', ?, ?, ?)
            """,
            (project_id, RESEARCHER_ID, now, now),
        )
        connection.execute(
            """
            update codebook_versions
            set status = 'frozen', frozen_at = ?
            where codebook_version_id = 'cbv_interview_1'
            """,
            (now,),
        )
        connection.execute(
            """
            insert into codebook_versions (
              codebook_version_id, project_id, codebook_id, version_number,
              status, based_on_version_id, created_by, created_at, frozen_at
            ) values ('cbv_interview_2', ?, 'cbk_interview', 2,
                      'draft', 'cbv_interview_1', ?, ?, null)
            """,
            (project_id, RESEARCHER_ID, now),
        )
        connection.execute(
            """
            insert into codes (
              code_id, project_id, codebook_version_id, stable_code_key,
              parent_code_id, label, created_by, created_at, updated_at
            ) values ('cod_draft', ?, 'cbv_interview_2', 'draft',
                      null, 'Draft', ?, ?, ?)
            """,
            (project_id, RESEARCHER_ID, now, now),
        )

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.transaction() as connection:
            connection.execute(
                "update codes set label = 'Changed' where code_id = 'cod_access'"
            )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.transaction() as connection:
            connection.execute(
                """
                update codes set codebook_version_id = 'cbv_interview_1'
                where code_id = 'cod_draft'
                """
            )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.transaction() as connection:
            connection.execute(
                "delete from codes where code_id = 'cod_access'"
            )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.transaction() as connection:
            connection.execute(
                """
                insert into codes (
                  code_id, project_id, codebook_version_id, stable_code_key,
                  parent_code_id, label, created_by, created_at, updated_at
                ) values ('cod_new', ?, 'cbv_interview_1', 'new',
                          null, 'New', ?, ?, ?)
                """,
                (project_id, RESEARCHER_ID, now, now),
            )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.transaction() as connection:
            connection.execute(
                """
                update codebook_versions set frozen_at = ?
                where codebook_version_id = 'cbv_interview_1'
                """,
                (datetime.now(UTC).isoformat(),),
            )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.transaction() as connection:
            connection.execute(
                """
                delete from codebook_versions
                where codebook_version_id = 'cbv_interview_1'
                """
            )

    with database.transaction() as connection:
        connection.execute(
            """
            insert into qualitative_audit_events (
              event_id, project_id, actor_id, event_type,
              subject_type, subject_id, metadata_json, created_at
            ) values ('qae_codebook_frozen', ?, ?, 'codebook.frozen',
                      'codebook_version', 'cbv_interview_1', '{}', ?)
            """,
            (project_id, RESEARCHER_ID, now),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with database.transaction() as connection:
            connection.execute(
                """
                update qualitative_audit_events set event_type = 'changed'
                where event_id = 'qae_codebook_frozen'
                """
            )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with database.transaction() as connection:
            connection.execute(
                """
                delete from qualitative_audit_events
                where event_id = 'qae_codebook_frozen'
                """
            )


def test_qualitative_database_is_preserved_by_project_backup_restore(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    project_id, database = _create_project(source_root)
    now = datetime.now(UTC).isoformat()
    with database.transaction() as connection:
        connection.execute(
            """
            insert into cases (
              case_id, project_id, case_kind, label, description,
              created_by, updated_by, created_at, updated_at
            ) values ('cas_participant_1', ?, 'participant', 'P1', '', ?, ?, ?, ?)
            """,
            (project_id, RESEARCHER_ID, RESEARCHER_ID, now, now),
        )

    archive = ProjectArchiveStore(source_root).create_archive(project_id)
    ProjectArchiveStore(restore_root).restore_archive(archive.archive_path)
    restored = QualitativeProjectDatabase(restore_root, project_id)

    assert restored.migration_status()[-1]["version"] == 1
    with sqlite3.connect(restored.db_path) as connection:
        assert connection.execute(
            "select case_id, label from cases"
        ).fetchall() == [("cas_participant_1", "P1")]


def test_qualitative_ids_use_known_entity_prefixes() -> None:
    assert new_qualitative_id("codebook").startswith("cbk_")
    assert new_qualitative_id("case").startswith("cas_")
    assert new_qualitative_id("audit_event").startswith("qae_")
    with pytest.raises(ValueError, match="Unknown qualitative entity type"):
        new_qualitative_id("unknown")
