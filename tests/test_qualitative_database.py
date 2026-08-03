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
    apply_migrations,
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


def _insert_study_note(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    note_id: str,
    created_at: str,
    note_kind: str = "memo",
) -> None:
    connection.execute(
        """
        insert into qualitative_notes (
          note_id, project_id, note_kind, target_kind, created_by, created_at
        ) values (?, ?, ?, 'study', ?, ?)
        """,
        (note_id, project_id, note_kind, RESEARCHER_ID, created_at),
    )


def _insert_note_revision(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    note_id: str,
    note_revision_id: str,
    revision_number: int,
    title: str,
    body: str,
    created_at: str,
) -> None:
    connection.execute(
        """
        insert into qualitative_note_revisions (
          note_revision_id, project_id, note_id, revision_number,
          title, body, created_by, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            note_revision_id,
            project_id,
            note_id,
            revision_number,
            title,
            body,
            RESEARCHER_ID,
            created_at,
        ),
    )


def _insert_review_code_fixture(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    created_at: str,
) -> None:
    connection.execute(
        """
        insert into codebooks (
          codebook_id, project_id, title, description,
          created_by, updated_by, created_at, updated_at
        ) values ('cbk_review', ?, 'Review', '', ?, ?, ?, ?)
        """,
        (project_id, RESEARCHER_ID, RESEARCHER_ID, created_at, created_at),
    )
    connection.execute(
        """
        insert into codebook_versions (
          codebook_version_id, project_id, codebook_id, version_number,
          status, based_on_version_id, created_by, created_at, frozen_at
        ) values ('cbv_review_1', ?, 'cbk_review', 1,
                  'draft', null, ?, ?, null)
        """,
        (project_id, RESEARCHER_ID, created_at),
    )
    for code_id, stable_key in (
        ("cod_review", "review"),
        ("cod_review_edited", "review-edited"),
    ):
        connection.execute(
            """
            insert into codes (
              code_id, project_id, codebook_version_id, stable_code_key,
              parent_code_id, label, created_by, created_at, updated_at
            ) values (?, ?, 'cbv_review_1', ?, null, ?, ?, ?, ?)
            """,
            (
                code_id,
                project_id,
                stable_key,
                stable_key,
                RESEARCHER_ID,
                created_at,
                created_at,
            ),
        )
    connection.execute(
        """
        update codebook_versions set status = 'frozen', frozen_at = ?
        where codebook_version_id = 'cbv_review_1'
        """,
        (created_at,),
    )
    connection.execute(
        """
        insert into codebook_versions (
          codebook_version_id, project_id, codebook_id, version_number,
          status, based_on_version_id, created_by, created_at, frozen_at
        ) values ('cbv_review_2', ?, 'cbk_review', 2,
                  'draft', 'cbv_review_1', ?, ?, null)
        """,
        (project_id, RESEARCHER_ID, created_at),
    )
    connection.execute(
        """
        insert into codes (
          code_id, project_id, codebook_version_id, stable_code_key,
          parent_code_id, label, created_by, created_at, updated_at
        ) values ('cod_review_draft', ?, 'cbv_review_2', 'review-draft',
                  null, 'Review draft', ?, ?, ?)
        """,
        (project_id, RESEARCHER_ID, created_at, created_at),
    )


def _insert_agent_suggestion(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    suggestion_id: str,
    origin_key: str,
    created_at: str,
    project_source_id: str = "psrc_review",
    transcript_revision_id: str = "trv_review",
    evidence_set_id: str = "evs_review",
    target_kind: str = "passage",
    passage_id: str = "psg_review",
    cunit_id: str = "",
    start_offset: object = 0,
    end_offset: object = 4,
    codebook_version_id: str = "cbv_review_1",
    code_id: str = "cod_review",
) -> None:
    connection.execute(
        """
        insert into agent_coding_suggestions (
          agent_suggestion_id, project_id,
          origin_kind, origin_id, origin_suggestion_key,
          project_source_id, transcript_revision_id, evidence_set_id,
          target_kind, passage_id, cunit_id, start_offset, end_offset,
          codebook_version_id, code_id, created_by, created_at
        ) values (?, ?, 'synthetic_fixture', 'fixture-run', ?,
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            suggestion_id,
            project_id,
            origin_key,
            project_source_id,
            transcript_revision_id,
            evidence_set_id,
            target_kind,
            passage_id,
            cunit_id,
            start_offset,
            end_offset,
            codebook_version_id,
            code_id,
            RESEARCHER_ID,
            created_at,
        ),
    )


def _insert_coding_reference(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    coding_reference_id: str,
    created_at: str,
    created_by: str = RESEARCHER_ID,
    project_source_id: str = "psrc_review",
    transcript_revision_id: str = "trv_review",
    evidence_set_id: str = "evs_review",
    target_kind: str = "passage",
    passage_id: str = "psg_review",
    cunit_id: str = "",
    start_offset: int = 0,
    end_offset: int = 4,
    codebook_version_id: str = "cbv_review_1",
    code_id: str = "cod_review",
) -> None:
    connection.execute(
        """
        insert into coding_references (
          coding_reference_id, project_id,
          project_source_id, transcript_revision_id, evidence_set_id,
          target_kind, passage_id, cunit_id, start_offset, end_offset,
          codebook_version_id, code_id, created_by, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            coding_reference_id,
            project_id,
            project_source_id,
            transcript_revision_id,
            evidence_set_id,
            target_kind,
            passage_id,
            cunit_id,
            start_offset,
            end_offset,
            codebook_version_id,
            code_id,
            created_by,
            created_at,
        ),
    )


def _insert_reviewer_decision(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    decision_id: str,
    suggestion_id: str,
    decision_number: object,
    decision: str,
    coding_reference_id: str | None,
    created_at: str,
    reviewed_by: str = RESEARCHER_ID,
) -> None:
    connection.execute(
        """
        insert into reviewer_decisions (
          reviewer_decision_id, project_id, agent_suggestion_id,
          decision_number, decision, coding_reference_id,
          reviewed_by, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            decision_id,
            project_id,
            suggestion_id,
            decision_number,
            decision,
            coding_reference_id,
            reviewed_by,
            created_at,
        ),
    )


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
        "create-qualitative-core-contract",
        "add-coding-reference-contract",
        "add-memo-annotation-contract",
        "add-coder-suggestion-review-contract",
        "add-saved-query-contract",
    ]
    with sqlite3.connect(database.db_path) as connection:
        assert connection.execute("pragma user_version").fetchone()[0] == 5
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


def test_qualitative_version_two_upgrades_to_note_contract_without_changing_rows(
    tmp_path: Path,
) -> None:
    study = StudyWorkspaceStore(tmp_path).create_study(
        {"name": "Qualitative Upgrade Study"}
    )
    database = QualitativeProjectDatabase(tmp_path, study.id)
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(database.db_path) as connection:
        connection.execute("pragma foreign_keys = on")
        assert apply_migrations(
            connection,
            database_name="version two qualitative project",
            migrations=qualitative_database.QUALITATIVE_MIGRATIONS[:2],
        ) == 2
        connection.execute(
            "insert into qualitative_projects values (?, ?)",
            (study.id, now),
        )
        connection.execute(
            """
            insert into researchers (
              researcher_id, project_id, display_name, role,
              active, created_at, updated_at
            ) values (?, ?, 'Upgrade Owner', 'researcher', 1, ?, ?)
            """,
            (RESEARCHER_ID, study.id, now, now),
        )
        connection.execute(
            """
            insert into cases (
              case_id, project_id, case_kind, label, description,
              created_by, updated_by, created_at, updated_at
            ) values ('cas_before_note_upgrade', ?, 'participant', 'P1', '',
                      ?, ?, ?, ?)
            """,
            (study.id, RESEARCHER_ID, RESEARCHER_ID, now, now),
        )
        connection.execute(
            """
            insert into qualitative_audit_events (
              event_id, project_id, actor_id, event_type,
              subject_type, subject_id, metadata_json, created_at
            ) values ('qae_before_note_upgrade', ?, ?, 'case.created',
                      'case', 'cas_before_note_upgrade', '{}', ?)
            """,
            (study.id, RESEARCHER_ID, now),
        )

    assert [row["version"] for row in database.migration_status()] == [1, 2, 3, 4, 5]
    with sqlite3.connect(database.db_path) as connection:
        assert connection.execute("pragma user_version").fetchone() == (5,)
        assert connection.execute(
            "select case_id, label from cases"
        ).fetchall() == [("cas_before_note_upgrade", "P1")]
        assert connection.execute(
            "select event_id from qualitative_audit_events"
        ).fetchall() == [("qae_before_note_upgrade",)]
        assert connection.execute(
            """
            select name from sqlite_master
            where type = 'table'
              and name in ('qualitative_notes', 'qualitative_note_revisions')
            order by name
            """
        ).fetchall() == [
            ("qualitative_note_revisions",),
            ("qualitative_notes",),
        ]
        assert connection.execute("pragma foreign_key_check").fetchall() == []


def test_qualitative_version_three_upgrades_to_review_contract_without_data_loss(
    tmp_path: Path,
) -> None:
    study = StudyWorkspaceStore(tmp_path).create_study(
        {"name": "Qualitative Review Upgrade Study"}
    )
    database = QualitativeProjectDatabase(tmp_path, study.id)
    created_at = "2026-08-01T12:00:00+00:00"
    with sqlite3.connect(database.db_path) as connection:
        connection.execute("pragma foreign_keys = on")
        assert apply_migrations(
            connection,
            database_name="version three qualitative project",
            migrations=qualitative_database.QUALITATIVE_MIGRATIONS[:3],
        ) == 3
        connection.execute(
            "insert into qualitative_projects values (?, ?)",
            (study.id, created_at),
        )
        connection.execute(
            """
            insert into researchers (
              researcher_id, project_id, display_name, role,
              active, created_at, updated_at
            ) values (?, ?, 'Upgrade Owner', 'researcher', 1, ?, ?)
            """,
            (RESEARCHER_ID, study.id, created_at, created_at),
        )
        _insert_review_code_fixture(
            connection,
            project_id=study.id,
            created_at=created_at,
        )
        _insert_coding_reference(
            connection,
            project_id=study.id,
            coding_reference_id=f"cdr_{'1' * 32}",
            created_at=created_at,
        )
        note_id = f"mem_{'1' * 32}"
        _insert_study_note(
            connection,
            project_id=study.id,
            note_id=note_id,
            created_at=created_at,
        )
        _insert_note_revision(
            connection,
            project_id=study.id,
            note_id=note_id,
            note_revision_id=f"nrv_{'1' * 32}",
            revision_number=1,
            title="Upgrade memo",
            body="Preserve this revision",
            created_at=created_at,
        )

    assert [row["version"] for row in database.migration_status()] == [1, 2, 3, 4, 5]
    with sqlite3.connect(database.db_path) as connection:
        assert connection.execute("pragma user_version").fetchone() == (5,)
        assert connection.execute(
            "select coding_reference_id from coding_references"
        ).fetchall() == [(f"cdr_{'1' * 32}",)]
        assert connection.execute(
            "select title, body from qualitative_note_revisions"
        ).fetchall() == [("Upgrade memo", "Preserve this revision")]
        assert connection.execute(
            "select count(*) from agent_coding_suggestions"
        ).fetchone() == (0,)
        assert connection.execute(
            "select count(*) from reviewer_decisions"
        ).fetchone() == (0,)
        assert connection.execute("pragma foreign_key_check").fetchall() == []


def test_review_migration_has_exact_columns_indexes_and_triggers(
    tmp_path: Path,
) -> None:
    _, database = _create_project(tmp_path)

    with sqlite3.connect(database.db_path) as connection:
        suggestion_columns = connection.execute(
            "pragma table_info(agent_coding_suggestions)"
        ).fetchall()
        decision_columns = connection.execute(
            "pragma table_info(reviewer_decisions)"
        ).fetchall()
        assert [
            (row[1], row[2].upper(), row[3], row[5])
            for row in suggestion_columns
        ] == [
            ("agent_suggestion_id", "TEXT", 1, 1),
            ("project_id", "TEXT", 1, 0),
            ("origin_kind", "TEXT", 1, 0),
            ("origin_id", "TEXT", 1, 0),
            ("origin_suggestion_key", "TEXT", 1, 0),
            ("project_source_id", "TEXT", 1, 0),
            ("transcript_revision_id", "TEXT", 1, 0),
            ("evidence_set_id", "TEXT", 1, 0),
            ("target_kind", "TEXT", 1, 0),
            ("passage_id", "TEXT", 1, 0),
            ("cunit_id", "TEXT", 1, 0),
            ("start_offset", "INTEGER", 1, 0),
            ("end_offset", "INTEGER", 1, 0),
            ("codebook_version_id", "TEXT", 1, 0),
            ("code_id", "TEXT", 1, 0),
            ("created_by", "TEXT", 1, 0),
            ("created_at", "TEXT", 1, 0),
        ]
        assert [
            (row[1], row[2].upper(), row[3], row[5])
            for row in decision_columns
        ] == [
            ("reviewer_decision_id", "TEXT", 1, 1),
            ("project_id", "TEXT", 1, 0),
            ("agent_suggestion_id", "TEXT", 1, 0),
            ("decision_number", "INTEGER", 1, 0),
            ("decision", "TEXT", 1, 0),
            ("coding_reference_id", "TEXT", 0, 0),
            ("reviewed_by", "TEXT", 1, 0),
            ("created_at", "TEXT", 1, 0),
        ]

        objects = connection.execute(
            """
            select type, name from sqlite_master
            where tbl_name in ('agent_coding_suggestions', 'reviewer_decisions')
              and name not like 'sqlite_autoindex_%'
              and type in ('index', 'trigger')
            order by type, name
            """
        ).fetchall()
        assert objects == [
            ("index", "agent_coding_suggestions_by_code_created"),
            ("index", "agent_coding_suggestions_by_created"),
            ("index", "agent_coding_suggestions_by_source_created"),
            ("trigger", "prevent_agent_coding_suggestion_delete"),
            ("trigger", "prevent_agent_coding_suggestion_update"),
            ("trigger", "prevent_reviewer_decision_delete"),
            ("trigger", "prevent_reviewer_decision_update"),
            ("trigger", "reject_reviewer_decision_after_terminal"),
            ("trigger", "require_frozen_agent_coding_suggestion_version"),
            ("trigger", "require_matching_reviewer_decision_candidate"),
            ("trigger", "require_monotonic_reviewer_decision_time"),
            ("trigger", "require_sequential_reviewer_decision"),
            ("trigger", "require_valid_reviewer_decision_result"),
        ]


def test_qualitative_version_four_upgrades_to_saved_query_contract_without_data_loss(
    tmp_path: Path,
) -> None:
    study = StudyWorkspaceStore(tmp_path).create_study(
        {"name": "Saved Query Upgrade Study"}
    )
    database = QualitativeProjectDatabase(tmp_path, study.id)
    created_at = "2026-08-02T12:00:00+00:00"
    suggestion_id = f"ags_{'1' * 32}"
    with sqlite3.connect(database.db_path) as connection:
        connection.execute("pragma foreign_keys = on")
        assert apply_migrations(
            connection,
            database_name="version four saved query upgrade",
            migrations=qualitative_database.QUALITATIVE_MIGRATIONS[:4],
        ) == 4
        connection.execute(
            "insert into qualitative_projects values (?, ?)",
            (study.id, created_at),
        )
        connection.execute(
            """
            insert into researchers (
              researcher_id, project_id, display_name, role,
              active, created_at, updated_at
            ) values (?, ?, 'Upgrade Owner', 'researcher', 1, ?, ?)
            """,
            (RESEARCHER_ID, study.id, created_at, created_at),
        )
        _insert_review_code_fixture(
            connection,
            project_id=study.id,
            created_at=created_at,
        )
        _insert_agent_suggestion(
            connection,
            project_id=study.id,
            suggestion_id=suggestion_id,
            origin_key="upgrade",
            created_at=created_at,
        )

    assert [row["version"] for row in database.migration_status()] == [
        1,
        2,
        3,
        4,
        5,
    ]
    with sqlite3.connect(database.db_path) as connection:
        assert connection.execute("pragma user_version").fetchone() == (5,)
        assert connection.execute(
            "select agent_suggestion_id from agent_coding_suggestions"
        ).fetchall() == [(suggestion_id,)]
        assert connection.execute("select count(*) from saved_queries").fetchone() == (
            0,
        )
        assert connection.execute("pragma foreign_key_check").fetchall() == []


def test_saved_query_migration_has_exact_columns_indexes_and_triggers(
    tmp_path: Path,
) -> None:
    _, database = _create_project(tmp_path)

    with sqlite3.connect(database.db_path) as connection:
        columns = connection.execute("pragma table_info(saved_queries)").fetchall()
        assert [(row[1], row[2].upper(), row[3], row[5]) for row in columns] == [
            ("saved_query_id", "TEXT", 1, 1),
            ("project_id", "TEXT", 1, 0),
            ("title", "TEXT", 1, 0),
            ("query_kind", "TEXT", 1, 0),
            ("definition_version", "INTEGER", 1, 0),
            ("filters_json", "TEXT", 1, 0),
            ("request_sha256", "TEXT", 1, 0),
            ("created_by", "TEXT", 1, 0),
            ("created_at", "TEXT", 1, 0),
        ]
        objects = connection.execute(
            """
            select type, name from sqlite_master
            where tbl_name = 'saved_queries'
              and name not like 'sqlite_autoindex_%'
              and type in ('index', 'trigger')
            order by type, name
            """
        ).fetchall()
        assert objects == [
            ("index", "saved_queries_by_created"),
            ("index", "saved_queries_by_creator_created"),
            ("trigger", "prevent_saved_query_delete"),
            ("trigger", "prevent_saved_query_update"),
        ]


def test_saved_query_migration_enforces_identity_bounds_and_immutability(
    tmp_path: Path,
) -> None:
    project_id, database = _create_project(tmp_path)
    saved_query_id = f"qry_{'1' * 32}"
    values = (
        saved_query_id,
        project_id,
        "All active coding",
        "coding_reference_filter",
        1,
        '{"code_id":null,"codebook_version_id":null,"created_by":null,'
        '"include_removed":false,"project_source_id":null}',
        "2" * 64,
        RESEARCHER_ID,
        "2026-08-02T12:00:00+00:00",
    )
    insert_sql = """
        insert into saved_queries (
          saved_query_id, project_id, title, query_kind,
          definition_version, filters_json, request_sha256,
          created_by, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    with sqlite3.connect(database.db_path) as connection:
        connection.execute("pragma foreign_keys = on")
        connection.execute(insert_sql, values)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "update saved_queries set title = title where saved_query_id = ?",
                (saved_query_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "delete from saved_queries where saved_query_id = ?",
                (saved_query_id,),
            )

    invalid_replacements = {
        0: "qry_not_hex_________________________",
        2: " padded ",
        3: "sql",
        4: 1.5,
        5: b"{}",
        6: "A" * 64,
        8: "not-a-timestamp",
    }
    for index, replacement in invalid_replacements.items():
        candidate = list(values)
        candidate[0] = f"qry_{index + 2:032x}"
        candidate[index] = replacement
        with sqlite3.connect(database.db_path) as connection:
            connection.execute("pragma foreign_keys = on")
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(insert_sql, tuple(candidate))


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
        "drop table qualitative_note_revisions",
        "drop trigger restrict_qualitative_note_update",
        "drop index qualitative_notes_by_kind_created",
        "drop table reviewer_decisions",
        "drop trigger prevent_agent_coding_suggestion_update",
        "drop index agent_coding_suggestions_by_created",
        "drop table saved_queries",
        "drop trigger prevent_saved_query_update",
        "drop index saved_queries_by_created",
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
    study = StudyWorkspaceStore(tmp_path).create_study(
        {"name": "Qualitative Migration Rollback"}
    )
    database = QualitativeProjectDatabase(tmp_path, study.id)
    with sqlite3.connect(database.db_path) as connection:
        assert apply_migrations(
            connection,
            database_name="version two qualitative rollback",
            migrations=qualitative_database.QUALITATIVE_MIGRATIONS[:2],
        ) == 2

    def fail_after_schema_change(connection: sqlite3.Connection) -> None:
        connection.execute("create table partial_qualitative_records (id text)")
        connection.execute("insert into missing_table values (1)")

    monkeypatch.setattr(
        qualitative_database,
        "QUALITATIVE_MIGRATIONS",
        (
            *qualitative_database.QUALITATIVE_MIGRATIONS[:2],
            Migration(3, "fail-after-schema-change", fail_after_schema_change),
        ),
    )

    with pytest.raises(SchemaCompatibilityError, match="migration 3"):
        database.migration_status()

    with sqlite3.connect(database.db_path) as connection:
        assert connection.execute("pragma user_version").fetchone()[0] == 2
        assert connection.execute(
            "select version from schema_migrations order by version"
        ).fetchall() == [(1,), (2,)]
        assert connection.execute(
            """
            select count(*) from sqlite_master
            where type = 'table' and name = 'partial_qualitative_records'
            """
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            select count(*) from sqlite_master
            where type = 'table' and name = 'qualitative_notes'
            """
        ).fetchone()[0] == 0


def test_review_schema_failure_rolls_back_partial_migration_four(
    tmp_path: Path,
    monkeypatch,
) -> None:
    study = StudyWorkspaceStore(tmp_path).create_study(
        {"name": "Review Migration Rollback"}
    )
    database = QualitativeProjectDatabase(tmp_path, study.id)
    with sqlite3.connect(database.db_path) as connection:
        assert apply_migrations(
            connection,
            database_name="version three review rollback",
            migrations=qualitative_database.QUALITATIVE_MIGRATIONS[:3],
        ) == 3

    def fail_after_review_schema_change(connection: sqlite3.Connection) -> None:
        connection.execute("create table partial_review_records (id text)")
        connection.execute("insert into missing_table values (1)")

    monkeypatch.setattr(
        qualitative_database,
        "QUALITATIVE_MIGRATIONS",
        (
            *qualitative_database.QUALITATIVE_MIGRATIONS[:3],
            Migration(
                4,
                "fail-review-schema-change",
                fail_after_review_schema_change,
            ),
        ),
    )

    with pytest.raises(SchemaCompatibilityError, match="migration 4"):
        database.migration_status()

    with sqlite3.connect(database.db_path) as connection:
        assert connection.execute("pragma user_version").fetchone()[0] == 3
        assert connection.execute(
            "select version from schema_migrations order by version"
        ).fetchall() == [(1,), (2,), (3,)]
        assert connection.execute(
            """
            select count(*) from sqlite_master
            where type = 'table' and name = 'partial_review_records'
            """
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            select count(*) from sqlite_master
            where type = 'table' and name = 'agent_coding_suggestions'
            """
        ).fetchone()[0] == 0


def test_saved_query_schema_failure_rolls_back_partial_migration_five(
    tmp_path: Path,
    monkeypatch,
) -> None:
    study = StudyWorkspaceStore(tmp_path).create_study(
        {"name": "Saved Query Migration Rollback"}
    )
    database = QualitativeProjectDatabase(tmp_path, study.id)
    with sqlite3.connect(database.db_path) as connection:
        assert apply_migrations(
            connection,
            database_name="version four saved query rollback",
            migrations=qualitative_database.QUALITATIVE_MIGRATIONS[:4],
        ) == 4

    def fail_after_saved_query_schema_change(connection: sqlite3.Connection) -> None:
        connection.execute("create table partial_saved_query_records (id text)")
        connection.execute("insert into missing_table values (1)")

    monkeypatch.setattr(
        qualitative_database,
        "QUALITATIVE_MIGRATIONS",
        (
            *qualitative_database.QUALITATIVE_MIGRATIONS[:4],
            Migration(
                5,
                "fail-saved-query-schema-change",
                fail_after_saved_query_schema_change,
            ),
        ),
    )

    with pytest.raises(SchemaCompatibilityError, match="migration 5"):
        database.migration_status()

    with sqlite3.connect(database.db_path) as connection:
        assert connection.execute("pragma user_version").fetchone()[0] == 4
        assert connection.execute(
            "select version from schema_migrations order by version"
        ).fetchall() == [(1,), (2,), (3,), (4,)]
        assert connection.execute(
            """
            select count(*) from sqlite_master
            where type = 'table' and name = 'partial_saved_query_records'
            """
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            select count(*) from sqlite_master
            where type = 'table' and name = 'saved_queries'
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


def test_review_migration_enforces_agent_suggestion_storage_rules(
    tmp_path: Path,
) -> None:
    project_id, database = _create_project(tmp_path)
    created_at = "2026-08-01T12:00:00+00:00"
    passage_id = f"ags_{'1' * 32}"
    cunit_id = f"ags_{'2' * 32}"
    with database.transaction() as connection:
        _insert_review_code_fixture(
            connection,
            project_id=project_id,
            created_at=created_at,
        )

    with sqlite3.connect(database.db_path) as connection:
        connection.execute("pragma foreign_keys = on")
        _insert_agent_suggestion(
            connection,
            project_id=project_id,
            suggestion_id=passage_id,
            origin_key="passage-1",
            created_at=created_at,
        )
        _insert_agent_suggestion(
            connection,
            project_id=project_id,
            suggestion_id=cunit_id,
            origin_key="cunit-1",
            created_at=created_at,
            target_kind="cunit",
            cunit_id="cun_review",
        )

        with pytest.raises(sqlite3.IntegrityError, match="frozen"):
            _insert_agent_suggestion(
                connection,
                project_id=project_id,
                suggestion_id=f"ags_{'3' * 32}",
                origin_key="draft-code",
                created_at=created_at,
                codebook_version_id="cbv_review_2",
                code_id="cod_review_draft",
            )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
            _insert_agent_suggestion(
                connection,
                project_id=project_id,
                suggestion_id=f"ags_{'4' * 32}",
                origin_key="invalid-cunit",
                created_at=created_at,
                target_kind="cunit",
                cunit_id="",
            )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
            _insert_agent_suggestion(
                connection,
                project_id=project_id,
                suggestion_id=f"ags_{'5' * 32}",
                origin_key="non-integer-offset",
                created_at=created_at,
                start_offset=0.5,
            )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
            _insert_agent_suggestion(
                connection,
                project_id=project_id,
                suggestion_id=f"ags_{'6' * 32}",
                origin_key="invalid-offset-order",
                created_at=created_at,
                start_offset=4,
                end_offset=4,
            )
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint"):
            _insert_agent_suggestion(
                connection,
                project_id=project_id,
                suggestion_id=f"ags_{'7' * 32}",
                origin_key="passage-1",
                created_at=created_at,
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                """
                update agent_coding_suggestions set end_offset = 3
                where agent_suggestion_id = ?
                """,
                (passage_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "delete from agent_coding_suggestions where agent_suggestion_id = ?",
                (passage_id,),
            )

        assert connection.execute(
            """
            select target_kind, cunit_id, typeof(start_offset), typeof(end_offset)
            from agent_coding_suggestions order by agent_suggestion_id
            """
        ).fetchall() == [
            ("passage", "", "integer", "integer"),
            ("cunit", "cun_review", "integer", "integer"),
        ]


def test_review_migration_enforces_decision_sequence_terminal_and_immutability(
    tmp_path: Path,
) -> None:
    project_id, database = _create_project(tmp_path)
    suggested_at = "2026-08-01T12:00:00+00:00"
    result_at = "2026-08-01T12:01:00+00:00"
    deferred_at = "2026-08-01T12:02:00+00:00"
    accepted_at = "2026-08-01T12:03:00+00:00"
    suggestion_id = f"ags_{'8' * 32}"
    reference_id = f"cdr_{'8' * 32}"
    deferred_id = f"rvd_{'8' * 32}"
    accepted_id = f"rvd_{'9' * 32}"
    with database.transaction() as connection:
        _insert_review_code_fixture(
            connection,
            project_id=project_id,
            created_at=suggested_at,
        )
        _insert_agent_suggestion(
            connection,
            project_id=project_id,
            suggestion_id=suggestion_id,
            origin_key="decision-chain",
            created_at=suggested_at,
        )
        _insert_coding_reference(
            connection,
            project_id=project_id,
            coding_reference_id=reference_id,
            created_at=result_at,
        )

    with sqlite3.connect(database.db_path) as connection:
        connection.execute("pragma foreign_keys = on")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
            _insert_reviewer_decision(
                connection,
                project_id=project_id,
                decision_id=f"rvd_{'1' * 32}",
                suggestion_id=suggestion_id,
                decision_number=1,
                decision="accepted",
                coding_reference_id=None,
                created_at=deferred_at,
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_reviewer_decision(
                connection,
                project_id=project_id,
                decision_id=f"rvd_{'2' * 32}",
                suggestion_id=suggestion_id,
                decision_number=1.5,
                decision="deferred",
                coding_reference_id=None,
                created_at=deferred_at,
            )
        _insert_reviewer_decision(
            connection,
            project_id=project_id,
            decision_id=deferred_id,
            suggestion_id=suggestion_id,
            decision_number=1,
            decision="deferred",
            coding_reference_id=None,
            created_at=deferred_at,
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_reviewer_decision(
                connection,
                project_id=project_id,
                decision_id=f"rvd_{'3' * 32}",
                suggestion_id=suggestion_id,
                decision_number=3,
                decision="deferred",
                coding_reference_id=None,
                created_at=accepted_at,
            )
        with pytest.raises(sqlite3.IntegrityError, match="timestamp"):
            _insert_reviewer_decision(
                connection,
                project_id=project_id,
                decision_id=f"rvd_{'4' * 32}",
                suggestion_id=suggestion_id,
                decision_number=2,
                decision="deferred",
                coding_reference_id=None,
                created_at="2026-08-01T11:59:00+00:00",
            )
        _insert_reviewer_decision(
            connection,
            project_id=project_id,
            decision_id=accepted_id,
            suggestion_id=suggestion_id,
            decision_number=2,
            decision="accepted",
            coding_reference_id=reference_id,
            created_at=accepted_at,
        )
        with pytest.raises(sqlite3.IntegrityError, match="terminal"):
            _insert_reviewer_decision(
                connection,
                project_id=project_id,
                decision_id=f"rvd_{'5' * 32}",
                suggestion_id=suggestion_id,
                decision_number=3,
                decision="deferred",
                coding_reference_id=None,
                created_at="2026-08-01T12:04:00+00:00",
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                """
                update reviewer_decisions set decision = 'rejected'
                where reviewer_decision_id = ?
                """,
                (deferred_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "delete from reviewer_decisions where reviewer_decision_id = ?",
                (deferred_id,),
            )

        assert connection.execute(
            """
            select decision_number, decision, coding_reference_id
            from reviewer_decisions order by decision_number
            """
        ).fetchall() == [
            (1, "deferred", None),
            (2, "accepted", reference_id),
        ]


def test_review_migration_enforces_accept_edit_result_and_chronology_rules(
    tmp_path: Path,
) -> None:
    project_id, database = _create_project(tmp_path)
    suggested_at = "2026-08-01T12:00:00+00:00"
    decision_before_result = "2026-08-01T12:00:30+00:00"
    result_at = "2026-08-01T12:01:00+00:00"
    later_suggestion_at = "2026-08-01T12:02:00+00:00"
    decision_at = "2026-08-01T12:03:00+00:00"
    exact_reference_id = f"cdr_{'a' * 32}"
    edited_reference_id = f"cdr_{'b' * 32}"
    unrelated_reference_id = f"cdr_{'c' * 32}"
    other_reference_id = f"cdr_{'d' * 32}"
    other_researcher_id = "res_other_reviewer"

    with database.transaction() as connection:
        _insert_review_code_fixture(
            connection,
            project_id=project_id,
            created_at=suggested_at,
        )
        connection.execute(
            """
            insert into researchers (
              researcher_id, project_id, display_name, role,
              active, created_at, updated_at
            ) values (?, ?, 'Other Reviewer', 'reviewer', 1, ?, ?)
            """,
            (
                other_researcher_id,
                project_id,
                suggested_at,
                suggested_at,
            ),
        )
        for suffix, origin_key in (
            ("a", "accepted-exact"),
            ("b", "edited-exact-invalid"),
            ("c", "edited-valid"),
            ("d", "accepted-mismatch"),
            ("e", "edited-unrelated"),
            ("f", "wrong-owner"),
        ):
            _insert_agent_suggestion(
                connection,
                project_id=project_id,
                suggestion_id=f"ags_{suffix * 32}",
                origin_key=origin_key,
                created_at=suggested_at,
            )
        _insert_agent_suggestion(
            connection,
            project_id=project_id,
            suggestion_id=f"ags_{'1a' * 16}",
            origin_key="preexisting-result",
            created_at=later_suggestion_at,
        )
        _insert_agent_suggestion(
            connection,
            project_id=project_id,
            suggestion_id=f"ags_{'1b' * 16}",
            origin_key="decision-before-result",
            created_at=suggested_at,
            end_offset=3,
        )
        _insert_coding_reference(
            connection,
            project_id=project_id,
            coding_reference_id=exact_reference_id,
            created_at=result_at,
        )
        _insert_coding_reference(
            connection,
            project_id=project_id,
            coding_reference_id=edited_reference_id,
            created_at=result_at,
            end_offset=3,
        )
        _insert_coding_reference(
            connection,
            project_id=project_id,
            coding_reference_id=unrelated_reference_id,
            created_at=result_at,
            project_source_id="psrc_unrelated",
        )
        _insert_coding_reference(
            connection,
            project_id=project_id,
            coding_reference_id=other_reference_id,
            created_at=result_at,
            created_by=other_researcher_id,
        )

    with sqlite3.connect(database.db_path) as connection:
        connection.execute("pragma foreign_keys = on")
        _insert_reviewer_decision(
            connection,
            project_id=project_id,
            decision_id=f"rvd_{'a' * 32}",
            suggestion_id=f"ags_{'a' * 32}",
            decision_number=1,
            decision="accepted",
            coding_reference_id=exact_reference_id,
            created_at=decision_at,
        )
        with pytest.raises(sqlite3.IntegrityError, match="candidate"):
            _insert_reviewer_decision(
                connection,
                project_id=project_id,
                decision_id=f"rvd_{'b' * 32}",
                suggestion_id=f"ags_{'b' * 32}",
                decision_number=1,
                decision="edited",
                coding_reference_id=exact_reference_id,
                created_at=decision_at,
            )
        _insert_reviewer_decision(
            connection,
            project_id=project_id,
            decision_id=f"rvd_{'c' * 32}",
            suggestion_id=f"ags_{'c' * 32}",
            decision_number=1,
            decision="edited",
            coding_reference_id=edited_reference_id,
            created_at=decision_at,
        )
        with pytest.raises(sqlite3.IntegrityError, match="candidate"):
            _insert_reviewer_decision(
                connection,
                project_id=project_id,
                decision_id=f"rvd_{'d' * 32}",
                suggestion_id=f"ags_{'d' * 32}",
                decision_number=1,
                decision="accepted",
                coding_reference_id=edited_reference_id,
                created_at=decision_at,
            )
        with pytest.raises(sqlite3.IntegrityError, match="candidate"):
            _insert_reviewer_decision(
                connection,
                project_id=project_id,
                decision_id=f"rvd_{'e' * 32}",
                suggestion_id=f"ags_{'e' * 32}",
                decision_number=1,
                decision="edited",
                coding_reference_id=unrelated_reference_id,
                created_at=decision_at,
            )
        with pytest.raises(sqlite3.IntegrityError, match="result"):
            _insert_reviewer_decision(
                connection,
                project_id=project_id,
                decision_id=f"rvd_{'f' * 32}",
                suggestion_id=f"ags_{'f' * 32}",
                decision_number=1,
                decision="accepted",
                coding_reference_id=other_reference_id,
                created_at=decision_at,
            )
        with pytest.raises(sqlite3.IntegrityError, match="result"):
            _insert_reviewer_decision(
                connection,
                project_id=project_id,
                decision_id=f"rvd_{'1' * 32}",
                suggestion_id=f"ags_{'1a' * 16}",
                decision_number=1,
                decision="accepted",
                coding_reference_id=exact_reference_id,
                created_at=decision_at,
            )
        with pytest.raises(sqlite3.IntegrityError, match="result"):
            _insert_reviewer_decision(
                connection,
                project_id=project_id,
                decision_id=f"rvd_{'2' * 32}",
                suggestion_id=f"ags_{'1b' * 16}",
                decision_number=1,
                decision="accepted",
                coding_reference_id=edited_reference_id,
                created_at=decision_before_result,
            )

        connection.execute(
            """
            update coding_references set removed_by = ?, removed_at = ?
            where coding_reference_id = ?
            """,
            (other_researcher_id, decision_at, other_reference_id),
        )
        with pytest.raises(sqlite3.IntegrityError, match="result"):
            _insert_reviewer_decision(
                connection,
                project_id=project_id,
                decision_id=f"rvd_{'3' * 32}",
                suggestion_id=f"ags_{'f' * 32}",
                decision_number=1,
                decision="accepted",
                coding_reference_id=other_reference_id,
                reviewed_by=other_researcher_id,
                created_at="2026-08-01T12:04:00+00:00",
            )

        assert connection.execute(
            "select decision from reviewer_decisions order by reviewer_decision_id"
        ).fetchall() == [("accepted",), ("edited",)]


def test_note_migration_enforces_exact_target_shapes_and_frozen_codes(
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
            ) values ('cbk_notes', ?, 'Notes', '', ?, ?, ?, ?)
            """,
            (project_id, RESEARCHER_ID, RESEARCHER_ID, now, now),
        )
        connection.execute(
            """
            insert into codebook_versions (
              codebook_version_id, project_id, codebook_id, version_number,
              status, based_on_version_id, created_by, created_at, frozen_at
            ) values ('cbv_notes_1', ?, 'cbk_notes', 1,
                      'draft', null, ?, ?, null)
            """,
            (project_id, RESEARCHER_ID, now),
        )
        connection.execute(
            """
            insert into codes (
              code_id, project_id, codebook_version_id, stable_code_key,
              parent_code_id, label, created_by, created_at, updated_at
            ) values ('cod_notes', ?, 'cbv_notes_1', 'notes',
                      null, 'Notes', ?, ?, ?)
            """,
            (project_id, RESEARCHER_ID, now, now),
        )

    with sqlite3.connect(database.db_path) as connection:
        connection.execute("pragma foreign_keys = on")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
            connection.execute(
                """
                insert into qualitative_notes (
                  note_id, project_id, note_kind, target_kind,
                  project_source_id, created_by, created_at
                ) values (?, ?, 'memo', 'study', 'psrc_hybrid', ?, ?)
                """,
                (f"mem_{'1' * 32}", project_id, RESEARCHER_ID, now),
            )
        with pytest.raises(sqlite3.IntegrityError, match="frozen"):
            connection.execute(
                """
                insert into qualitative_notes (
                  note_id, project_id, note_kind, target_kind,
                  codebook_version_id, code_id, created_by, created_at
                ) values (?, ?, 'memo', 'code', 'cbv_notes_1', 'cod_notes', ?, ?)
                """,
                (f"mem_{'2' * 32}", project_id, RESEARCHER_ID, now),
            )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
            connection.execute(
                """
                insert into qualitative_notes (
                  note_id, project_id, note_kind, target_kind,
                  project_source_id, transcript_revision_id, evidence_set_id,
                  excerpt_target_kind, passage_id, start_offset, end_offset,
                  created_by, created_at
                ) values (?, ?, 'annotation', 'excerpt', 'psrc_excerpt',
                          ?, ?, 'passage', ?, 'not-an-integer', 4, ?, ?)
                """,
                (
                    f"ann_{'3' * 32}",
                    project_id,
                    f"trv_{'3' * 32}",
                    f"evs_{'3' * 32}",
                    f"psg_{'3' * 32}",
                    RESEARCHER_ID,
                    now,
                ),
            )
        connection.execute(
            """
            update codebook_versions set status = 'frozen', frozen_at = ?
            where codebook_version_id = 'cbv_notes_1'
            """,
            (now,),
        )
        connection.execute(
            """
            insert into qualitative_notes (
              note_id, project_id, note_kind, target_kind,
              codebook_version_id, code_id, created_by, created_at
            ) values (?, ?, 'memo', 'code', 'cbv_notes_1', 'cod_notes', ?, ?)
            """,
            (f"mem_{'4' * 32}", project_id, RESEARCHER_ID, now),
        )

    with sqlite3.connect(database.db_path) as connection:
        assert connection.execute(
            "select target_kind, codebook_version_id, code_id from qualitative_notes"
        ).fetchall() == [("code", "cbv_notes_1", "cod_notes")]
        assert connection.execute("pragma foreign_key_check").fetchall() == []


def test_note_migration_enforces_append_only_revisions_and_one_way_removal(
    tmp_path: Path,
) -> None:
    project_id, database = _create_project(tmp_path)
    created_at = "2026-08-01T12:00:00+00:00"
    later = "2026-08-01T12:01:00+00:00"
    note_id = f"mem_{'5' * 32}"
    revision_id = f"nrv_{'5' * 32}"
    with sqlite3.connect(database.db_path) as connection:
        connection.execute("pragma foreign_keys = on")
        _insert_study_note(
            connection,
            project_id=project_id,
            note_id=note_id,
            created_at=created_at,
        )
        _insert_note_revision(
            connection,
            project_id=project_id,
            note_id=note_id,
            note_revision_id=revision_id,
            revision_number=1,
            title="Research memo",
            body="Initial interpretation",
            created_at=created_at,
        )

        with pytest.raises(sqlite3.IntegrityError, match="sequence"):
            _insert_note_revision(
                connection,
                project_id=project_id,
                note_id=note_id,
                note_revision_id=f"nrv_{'6' * 32}",
                revision_number=3,
                title="Third",
                body="Skipped one revision",
                created_at=later,
            )
        with pytest.raises(sqlite3.IntegrityError, match="timestamp"):
            _insert_note_revision(
                connection,
                project_id=project_id,
                note_id=note_id,
                note_revision_id=f"nrv_{'7' * 32}",
                revision_number=2,
                title="Earlier",
                body="Time moved backwards",
                created_at="2026-08-01T11:59:00+00:00",
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                """
                update qualitative_note_revisions set body = 'changed'
                where note_revision_id = ?
                """,
                (revision_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "delete from qualitative_note_revisions where note_revision_id = ?",
                (revision_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="initial removal"):
            connection.execute(
                "update qualitative_notes set note_kind = 'annotation' where note_id = ?",
                (note_id,),
            )

        connection.execute(
            """
            update qualitative_notes set removed_by = ?, removed_at = ?
            where note_id = ?
            """,
            (RESEARCHER_ID, later, note_id),
        )
        with pytest.raises(sqlite3.IntegrityError, match="removed"):
            _insert_note_revision(
                connection,
                project_id=project_id,
                note_id=note_id,
                note_revision_id=f"nrv_{'8' * 32}",
                revision_number=2,
                title="After removal",
                body="Must not be stored",
                created_at=later,
            )
        with pytest.raises(sqlite3.IntegrityError, match="initial removal"):
            connection.execute(
                """
                update qualitative_notes set removed_by = removed_by
                where note_id = ?
                """,
                (note_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="physically deleted"):
            connection.execute(
                "delete from qualitative_notes where note_id = ?",
                (note_id,),
            )

        orphan_id = f"ann_{'9' * 32}"
        _insert_study_note(
            connection,
            project_id=project_id,
            note_id=orphan_id,
            created_at=created_at,
            note_kind="annotation",
        )
        with pytest.raises(sqlite3.IntegrityError, match="initial removal"):
            connection.execute(
                """
                update qualitative_notes set removed_by = ?, removed_at = ?
                where note_id = ?
                """,
                (RESEARCHER_ID, later, orphan_id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="active when inserted"):
            connection.execute(
                """
                insert into qualitative_notes (
                  note_id, project_id, note_kind, target_kind,
                  created_by, created_at, removed_by, removed_at
                ) values (?, ?, 'memo', 'study', ?, ?, ?, ?)
                """,
                (
                    f"mem_{'a' * 32}",
                    project_id,
                    RESEARCHER_ID,
                    created_at,
                    RESEARCHER_ID,
                    later,
                ),
            )


def test_note_migration_enforces_revision_identity_and_content_bounds(
    tmp_path: Path,
) -> None:
    project_id, database = _create_project(tmp_path)
    created_at = "2026-08-01T12:00:00+00:00"
    annotation_id = f"ann_{'b' * 32}"
    memo_id = f"mem_{'c' * 32}"
    with sqlite3.connect(database.db_path) as connection:
        connection.execute("pragma foreign_keys = on")
        _insert_study_note(
            connection,
            project_id=project_id,
            note_id=annotation_id,
            created_at=created_at,
            note_kind="annotation",
        )
        with pytest.raises(sqlite3.IntegrityError, match="content"):
            _insert_note_revision(
                connection,
                project_id=project_id,
                note_id=annotation_id,
                note_revision_id=f"nrv_{'b' * 32}",
                revision_number=1,
                title="Annotations are untitled",
                body="Context",
                created_at=created_at,
            )
        with pytest.raises(sqlite3.IntegrityError, match="content"):
            _insert_note_revision(
                connection,
                project_id=project_id,
                note_id=annotation_id,
                note_revision_id=f"nrv_{'d' * 32}",
                revision_number=1,
                title="",
                body="",
                created_at=created_at,
            )
        with pytest.raises(sqlite3.IntegrityError, match="content"):
            _insert_note_revision(
                connection,
                project_id=project_id,
                note_id=annotation_id,
                note_revision_id=f"nrv_{'e' * 32}",
                revision_number=1,
                title="",
                body="contains\0nul",
                created_at=created_at,
            )
        _insert_note_revision(
            connection,
            project_id=project_id,
            note_id=annotation_id,
            note_revision_id=f"nrv_{'f' * 32}",
            revision_number=1,
            title="",
            body="Context",
            created_at=created_at,
        )

        _insert_study_note(
            connection,
            project_id=project_id,
            note_id=memo_id,
            created_at=created_at,
        )
        with pytest.raises(sqlite3.IntegrityError, match="content"):
            _insert_note_revision(
                connection,
                project_id=project_id,
                note_id=memo_id,
                note_revision_id=f"nrv_{'1' * 32}",
                revision_number=1,
                title="x" * 513,
                body="Body",
                created_at=created_at,
            )
        with pytest.raises(sqlite3.IntegrityError, match="content"):
            _insert_note_revision(
                connection,
                project_id=project_id,
                note_id=memo_id,
                note_revision_id=f"nrv_{'2' * 32}",
                revision_number=1,
                title="Memo",
                body="x" * 262_145,
                created_at=created_at,
            )
        with pytest.raises(sqlite3.IntegrityError, match="initial"):
            _insert_note_revision(
                connection,
                project_id=project_id,
                note_id=memo_id,
                note_revision_id=f"nrv_{'3' * 32}",
                revision_number=1,
                title="Memo",
                body="Body",
                created_at="2026-08-01T12:00:01+00:00",
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

    assert restored.migration_status()[-1]["version"] == 5
    with sqlite3.connect(restored.db_path) as connection:
        assert connection.execute(
            "select case_id, label from cases"
        ).fetchall() == [("cas_participant_1", "P1")]


def test_qualitative_ids_use_known_entity_prefixes() -> None:
    assert new_qualitative_id("codebook").startswith("cbk_")
    assert new_qualitative_id("case").startswith("cas_")
    assert new_qualitative_id("coding_reference").startswith("cdr_")
    assert new_qualitative_id("agent_suggestion").startswith("ags_")
    assert new_qualitative_id("reviewer_decision").startswith("rvd_")
    assert new_qualitative_id("memo").startswith("mem_")
    assert new_qualitative_id("annotation").startswith("ann_")
    assert new_qualitative_id("note_revision").startswith("nrv_")
    assert new_qualitative_id("saved_query").startswith("qry_")
    assert new_qualitative_id("audit_event").startswith("qae_")
    with pytest.raises(ValueError, match="Unknown qualitative entity type"):
        new_qualitative_id("unknown")
