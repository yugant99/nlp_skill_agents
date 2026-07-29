import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

import backend.qualitative.database as qualitative_database
from backend.qualitative.database import (
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
    with pytest.raises(ValueError, match="stable lowercase identifier"):
        database.initialize(
            researcher_id="Project Owner",
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

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.transaction() as connection:
            connection.execute(
                "update codes set label = 'Changed' where code_id = 'cod_access'"
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
