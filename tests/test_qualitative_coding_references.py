import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

import backend.qualitative.coding_references as coding_references_module
from backend.evidence.identifiers import (
    cunit_evidence_id,
    passage_evidence_id,
    transcript_evidence_identity,
)
from backend.qualitative.codebooks import CodebookService
from backend.qualitative.coding_references import (
    CodingReferenceConflictError,
    CodingReferenceNotFoundError,
    CodingReferenceService,
    CodingReferenceValidationError,
)
from backend.qualitative.database import QualitativeProjectDatabase
from backend.storage.evidence_catalog import EvidenceCatalog, EvidenceImportRecord
from backend.storage.evidence_target_registry import (
    EvidenceCUnitInput,
    EvidencePassageInput,
    EvidenceTargetRegistry,
)
from backend.storage.study_store import StudyWorkspaceStore
from backend.storage.study_batch_operation_store import (
    StudyBatchOperationConflict,
    StudyBatchOperationStore,
)


OWNER_ID = "res_coding_owner"
SECOND_ID = "res_coding_second"
THIRD_ID = "res_coding_third"
PASSAGE_TEXT = "I came 😊 and I stayed."


@dataclass(frozen=True)
class _CodingFixture:
    project_id: str
    database: QualitativeProjectDatabase
    service: CodingReferenceService
    project_source_id: str
    transcript_revision_id: str
    evidence_set_id: str
    passage_id: str
    cunit_id: str
    codebook_version_id: str
    code_id: str


def _create_fixture(root: Path, *, freeze: bool = True) -> _CodingFixture:
    study = StudyWorkspaceStore(root).create_study({"name": "Coding Study"})
    database = QualitativeProjectDatabase(root, study.id)
    database.initialize(
        researcher_id=OWNER_ID,
        researcher_name="Coding Owner",
    )
    now = datetime.now(UTC).isoformat()
    with database.transaction() as connection:
        connection.executemany(
            """
            insert into researchers (
              researcher_id, project_id, display_name, role,
              active, created_at, updated_at
            ) values (?, ?, ?, 'researcher', 1, ?, ?)
            """,
            (
                (SECOND_ID, study.id, "Second Coder", now, now),
                (THIRD_ID, study.id, "Third Coder", now, now),
            ),
        )

    transcript_text = f"P1: {PASSAGE_TEXT}"
    identity = transcript_evidence_identity(transcript_text)
    project_source_id = "psrc_coding_interview"
    import_record = EvidenceImportRecord(
        import_id="imp_coding_interview",
        run_id="run_coding_interview",
        pipeline="segmentation",
        source_id=identity.source_id,
        source_filename="private-interview.txt",
        source_media_type="text/plain",
        source_blob_sha256="a" * 64,
        transcript_revision_id=identity.transcript_revision_id,
        transcript_sha256=identity.transcript_sha256,
        imported_at="2026-08-01T12:00:00+00:00",
        project_source_id=project_source_id,
        workspace_id=study.id,
    )
    EvidenceCatalog(root).record_import(import_record)
    passage_id = passage_evidence_id(identity.transcript_revision_id, 0)
    cunit_id = cunit_evidence_id(passage_id, 0)
    registry = EvidenceTargetRegistry(root)
    prepared = registry.prepare_complete_set(
        import_id=import_record.import_id,
        workspace_id=study.id,
        project_source_id=project_source_id,
        transcript_revision_id=identity.transcript_revision_id,
        transcript_text=transcript_text,
        producer_kind="cunit_segmentation",
        producer_version=1,
        producer_status="verified",
        review_status="not_domain_validated",
        passages=(
            EvidencePassageInput(
                passage_id=passage_id,
                passage_ordinal=0,
                role="participant",
                text=PASSAGE_TEXT,
                cunits=(
                    EvidenceCUnitInput(
                        cunit_id=cunit_id,
                        cunit_ordinal=0,
                        text="I came 😊",
                    ),
                    EvidenceCUnitInput(
                        cunit_id=cunit_evidence_id(passage_id, 1),
                        cunit_ordinal=1,
                        text="and I stayed.",
                    ),
                ),
            ),
        ),
    )
    registry.register_complete_set(prepared)

    codebooks = CodebookService(root, study.id)
    codebook = codebooks.create_codebook(
        researcher_id=OWNER_ID,
        title="Interview themes",
    )
    draft = codebooks.create_draft(
        researcher_id=OWNER_ID,
        codebook_id=codebook.codebook_id,
    )
    code = codebooks.add_code(
        researcher_id=OWNER_ID,
        codebook_id=codebook.codebook_id,
        codebook_version_id=draft.version.codebook_version_id,
        stable_code_key="persistence",
        label="Persistence",
    )
    if freeze:
        codebooks.freeze_version(
            researcher_id=OWNER_ID,
            codebook_id=codebook.codebook_id,
            codebook_version_id=draft.version.codebook_version_id,
        )

    return _CodingFixture(
        project_id=study.id,
        database=database,
        service=CodingReferenceService(root, study.id),
        project_source_id=project_source_id,
        transcript_revision_id=identity.transcript_revision_id,
        evidence_set_id=prepared.evidence_set_id,
        passage_id=passage_id,
        cunit_id=cunit_id,
        codebook_version_id=draft.version.codebook_version_id,
        code_id=code.code_id,
    )


def _create_reference(
    fixture: _CodingFixture,
    *,
    researcher_id: str = OWNER_ID,
    target_kind: str = "passage",
    cunit_id: str = "",
    start_offset: int = 2,
    end_offset: int = 8,
):
    return fixture.service.create_reference(
        researcher_id=researcher_id,
        project_source_id=fixture.project_source_id,
        transcript_revision_id=fixture.transcript_revision_id,
        evidence_set_id=fixture.evidence_set_id,
        target_kind=target_kind,
        passage_id=fixture.passage_id,
        cunit_id=cunit_id,
        start_offset=start_offset,
        end_offset=end_offset,
        codebook_version_id=fixture.codebook_version_id,
        code_id=fixture.code_id,
    )


def _coding_audits(database: QualitativeProjectDatabase) -> list[sqlite3.Row]:
    with sqlite3.connect(database.db_path) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            select actor_id, event_type, subject_id, metadata_json, created_at
            from qualitative_audit_events
            where subject_type = 'coding_reference'
            order by created_at, event_id
            """
        ).fetchall()


def _insert_raw_reference(
    fixture: _CodingFixture,
    *,
    suffix: str,
    row_overrides: dict[str, object] | None = None,
    metadata_json: str | None = None,
    include_audit: bool = True,
) -> str:
    coding_reference_id = f"cdr_{suffix * 32}"
    values: dict[str, object] = {
        "coding_reference_id": coding_reference_id,
        "project_id": fixture.project_id,
        "project_source_id": fixture.project_source_id,
        "transcript_revision_id": fixture.transcript_revision_id,
        "evidence_set_id": fixture.evidence_set_id,
        "target_kind": "passage",
        "passage_id": fixture.passage_id,
        "cunit_id": "",
        "start_offset": 0,
        "end_offset": 1,
        "codebook_version_id": fixture.codebook_version_id,
        "code_id": fixture.code_id,
        "created_by": OWNER_ID,
        "created_at": datetime.now(UTC).isoformat(),
    }
    values.update(row_overrides or {})
    metadata = metadata_json
    if metadata is None:
        metadata = json.dumps(
            {
                "code_id": values["code_id"],
                "codebook_version_id": values["codebook_version_id"],
                "evidence_set_id": values["evidence_set_id"],
                "target_kind": values["target_kind"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    with sqlite3.connect(fixture.database.db_path) as connection:
        connection.execute("pragma foreign_keys = on")
        connection.execute(
            """
            insert into coding_references (
              coding_reference_id, project_id, project_source_id,
              transcript_revision_id, evidence_set_id, target_kind,
              passage_id, cunit_id, start_offset, end_offset,
              codebook_version_id, code_id, created_by, created_at,
              removed_by, removed_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, null, null)
            """,
            tuple(values.values()),
        )
        if include_audit:
            connection.execute(
                """
                insert into qualitative_audit_events (
                  event_id, project_id, actor_id, event_type,
                  subject_type, subject_id, metadata_json, created_at
                ) values (?, ?, ?, 'coding_reference.created',
                          'coding_reference', ?, ?, ?)
                """,
                (
                    f"qae_{suffix * 32}",
                    fixture.project_id,
                    OWNER_ID,
                    coding_reference_id,
                    metadata,
                    values["created_at"],
                ),
            )
    return coding_reference_id


def test_create_read_list_remove_and_reapply_are_attributable_and_idempotent(
    tmp_path: Path,
) -> None:
    fixture = _create_fixture(tmp_path)
    created = _create_reference(fixture)
    retry = _create_reference(fixture)
    second_coder = _create_reference(fixture, researcher_id=SECOND_ID)

    assert retry == created
    assert second_coder.coding_reference_id != created.coding_reference_id
    assert fixture.service.read_reference(created.coding_reference_id) == created
    assert fixture.service.list_references() == tuple(
        sorted(
            (created, second_coder),
            key=lambda row: (row.created_at, row.coding_reference_id),
        )
    )

    removed = fixture.service.remove_reference(
        researcher_id=SECOND_ID,
        coding_reference_id=created.coding_reference_id,
    )
    removal_retry = fixture.service.remove_reference(
        researcher_id=SECOND_ID,
        coding_reference_id=created.coding_reference_id,
    )
    assert removal_retry == removed
    assert removed.removed_by == SECOND_ID
    assert removed.removed_at is not None
    with pytest.raises(CodingReferenceConflictError, match="different researcher"):
        fixture.service.remove_reference(
            researcher_id=THIRD_ID,
            coding_reference_id=created.coding_reference_id,
        )

    reapplied = _create_reference(fixture)
    assert reapplied.coding_reference_id != created.coding_reference_id
    assert {row.coding_reference_id for row in fixture.service.list_references()} == {
        second_coder.coding_reference_id,
        reapplied.coding_reference_id,
    }
    assert {
        row.coding_reference_id
        for row in fixture.service.list_references(include_removed=True)
    } == {
        created.coding_reference_id,
        second_coder.coding_reference_id,
        reapplied.coding_reference_id,
    }

    audits = _coding_audits(fixture.database)
    assert len(audits) == 4
    created_audit = next(
        row
        for row in audits
        if row["subject_id"] == created.coding_reference_id
        and row["event_type"] == "coding_reference.created"
    )
    removed_audit = next(
        row
        for row in audits
        if row["subject_id"] == created.coding_reference_id
        and row["event_type"] == "coding_reference.removed"
    )
    assert created_audit["actor_id"] == OWNER_ID
    assert created_audit["created_at"] == created.created_at
    assert json.loads(created_audit["metadata_json"]) == {
        "code_id": fixture.code_id,
        "codebook_version_id": fixture.codebook_version_id,
        "evidence_set_id": fixture.evidence_set_id,
        "target_kind": "passage",
    }
    assert removed_audit["actor_id"] == SECOND_ID
    assert removed_audit["created_at"] == removed.removed_at
    assert removed_audit["metadata_json"] == "{}"
    assert PASSAGE_TEXT not in "".join(row["metadata_json"] for row in audits)


def test_removal_cannot_predate_creation_at_trigger_or_strict_read_boundary(
    tmp_path: Path,
) -> None:
    fixture = _create_fixture(tmp_path)
    created = _create_reference(fixture)
    earlier = "2000-01-01T00:00:00+00:00"

    with sqlite3.connect(fixture.database.db_path) as connection:
        connection.execute("pragma foreign_keys = on")
        with pytest.raises(sqlite3.IntegrityError, match="initial removal"):
            connection.execute(
                """
                update coding_references
                set removed_by = ?, removed_at = ?
                where coding_reference_id = ?
                """,
                (SECOND_ID, earlier, created.coding_reference_id),
            )

    assert fixture.service.read_reference(created.coding_reference_id) == created

    with sqlite3.connect(fixture.database.db_path) as connection:
        trigger_sql = connection.execute(
            """
            select sql from sqlite_master
            where type = 'trigger' and name = 'restrict_coding_reference_update'
            """
        ).fetchone()[0]
        assert isinstance(trigger_sql, str)
        connection.execute("drop trigger restrict_coding_reference_update")
        connection.execute(
            """
            update coding_references
            set removed_by = ?, removed_at = ?
            where coding_reference_id = ?
            """,
            (SECOND_ID, earlier, created.coding_reference_id),
        )
        connection.execute(trigger_sql)
        connection.execute(
            """
            insert into qualitative_audit_events (
              event_id, project_id, actor_id, event_type,
              subject_type, subject_id, metadata_json, created_at
            ) values (?, ?, ?, 'coding_reference.removed',
                      'coding_reference', ?, '{}', ?)
            """,
            (
                f"qae_{'7' * 32}",
                fixture.project_id,
                SECOND_ID,
                created.coding_reference_id,
                earlier,
            ),
        )

    with pytest.raises(CodingReferenceConflictError, match="removal timestamp"):
        fixture.service.read_reference(created.coding_reference_id)
    with pytest.raises(CodingReferenceConflictError, match="removal timestamp"):
        fixture.service.validate_project_state()


def test_unicode_cunit_spans_round_trip_without_persisting_excerpt(tmp_path: Path) -> None:
    fixture = _create_fixture(tmp_path)
    created = _create_reference(
        fixture,
        target_kind="cunit",
        cunit_id=fixture.cunit_id,
        start_offset=7,
        end_offset=8,
    )

    assert created.target_kind == "cunit"
    assert created.start_offset == 7
    assert created.end_offset == 8
    assert fixture.service.read_reference(created.coding_reference_id) == created
    with sqlite3.connect(fixture.database.db_path) as connection:
        stored = repr(
            connection.execute(
                "select * from coding_references"
            ).fetchone()
        )
    assert "😊" not in stored
    assert "I came" not in stored


@pytest.mark.parametrize(
    ("overrides", "error_type", "message"),
    [
        ({"start_offset": True}, CodingReferenceValidationError, "positive integer"),
        ({"start_offset": 4, "end_offset": 4}, CodingReferenceValidationError, "positive integer"),
        ({"end_offset": 500}, CodingReferenceValidationError, "outside"),
        (
            {"target_kind": "passage", "cunit_id": f"cun_{'0' * 32}"},
            CodingReferenceValidationError,
            "must not include",
        ),
        (
            {"target_kind": "cunit", "cunit_id": ""},
            CodingReferenceValidationError,
            "cunit_id",
        ),
    ],
)
def test_invalid_span_and_target_inputs_fail_before_writes(
    tmp_path: Path,
    overrides: dict[str, object],
    error_type: type[Exception],
    message: str,
) -> None:
    fixture = _create_fixture(tmp_path)
    arguments: dict[str, object] = {
        "researcher_id": OWNER_ID,
        "project_source_id": fixture.project_source_id,
        "transcript_revision_id": fixture.transcript_revision_id,
        "evidence_set_id": fixture.evidence_set_id,
        "target_kind": "passage",
        "passage_id": fixture.passage_id,
        "cunit_id": "",
        "start_offset": 2,
        "end_offset": 8,
        "codebook_version_id": fixture.codebook_version_id,
        "code_id": fixture.code_id,
    }
    arguments.update(overrides)

    with pytest.raises(error_type, match=message):
        fixture.service.create_reference(**arguments)
    with sqlite3.connect(fixture.database.db_path) as connection:
        assert connection.execute(
            "select count(*) from coding_references"
        ).fetchone() == (0,)


def test_missing_targets_and_draft_codes_fail_with_exact_boundaries(
    tmp_path: Path,
) -> None:
    fixture = _create_fixture(tmp_path)
    with pytest.raises(CodingReferenceNotFoundError, match="Evidence target"):
        fixture.service.create_reference(
            researcher_id=OWNER_ID,
            project_source_id=fixture.project_source_id,
            transcript_revision_id=fixture.transcript_revision_id,
            evidence_set_id=fixture.evidence_set_id,
            target_kind="passage",
            passage_id=f"psg_{'f' * 32}",
            start_offset=0,
            end_offset=1,
            codebook_version_id=fixture.codebook_version_id,
            code_id=fixture.code_id,
        )

    draft = _create_fixture(tmp_path / "draft", freeze=False)
    with pytest.raises(CodingReferenceConflictError, match="frozen"):
        _create_reference(draft)

    with pytest.raises(CodingReferenceNotFoundError, match="Code not found"):
        fixture.service.create_reference(
            researcher_id=OWNER_ID,
            project_source_id=fixture.project_source_id,
            transcript_revision_id=fixture.transcript_revision_id,
            evidence_set_id=fixture.evidence_set_id,
            target_kind="passage",
            passage_id=fixture.passage_id,
            start_offset=0,
            end_offset=1,
            codebook_version_id=fixture.codebook_version_id,
            code_id="cod_missing",
        )


def test_wrong_version_code_is_a_conflict_not_a_missing_code(tmp_path: Path) -> None:
    fixture = _create_fixture(tmp_path)
    codebooks = CodebookService(tmp_path, fixture.project_id)
    second_codebook = codebooks.create_codebook(
        researcher_id=OWNER_ID,
        title="Second codebook",
    )
    second_draft = codebooks.create_draft(
        researcher_id=OWNER_ID,
        codebook_id=second_codebook.codebook_id,
    )
    codebooks.add_code(
        researcher_id=OWNER_ID,
        codebook_id=second_codebook.codebook_id,
        codebook_version_id=second_draft.version.codebook_version_id,
        stable_code_key="other",
        label="Other",
    )
    codebooks.freeze_version(
        researcher_id=OWNER_ID,
        codebook_id=second_codebook.codebook_id,
        codebook_version_id=second_draft.version.codebook_version_id,
    )

    with pytest.raises(CodingReferenceConflictError, match="different codebook"):
        fixture.service.create_reference(
            researcher_id=OWNER_ID,
            project_source_id=fixture.project_source_id,
            transcript_revision_id=fixture.transcript_revision_id,
            evidence_set_id=fixture.evidence_set_id,
            target_kind="passage",
            passage_id=fixture.passage_id,
            start_offset=0,
            end_offset=1,
            codebook_version_id=second_draft.version.codebook_version_id,
            code_id=fixture.code_id,
        )


def test_storage_boundary_failures_use_the_coding_conflict_taxonomy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _create_fixture(tmp_path)
    created = _create_reference(fixture)

    @contextmanager
    def broken_study_guard():
        raise StudyBatchOperationConflict("private journal path")
        yield

    with monkeypatch.context() as patch:
        patch.setattr(fixture.service.database, "read", broken_study_guard)
        with pytest.raises(
            CodingReferenceConflictError,
            match="storage is unavailable or corrupt",
        ):
            fixture.service.read_reference(created.coding_reference_id)

    @contextmanager
    def broken_workspace_lock(_root: Path):
        raise OSError("private workspace path")
        yield

    with monkeypatch.context() as patch:
        patch.setattr(
            "backend.qualitative.coding_references.workspace_mutation_lock",
            broken_workspace_lock,
        )
        with pytest.raises(
            CodingReferenceConflictError,
            match="Evidence target storage is unavailable or invalid",
        ):
            fixture.service.read_reference(created.coding_reference_id)

    with sqlite3.connect(fixture.database.db_path) as connection:
        connection.execute("pragma user_version = 6")
    with pytest.raises(
        CodingReferenceConflictError,
        match="storage is unavailable or corrupt",
    ):
        fixture.service.read_reference(created.coding_reference_id)


def test_coding_reference_runtime_never_nests_study_guard_under_workspace_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _create_fixture(tmp_path)
    workspace_depth = 0
    workspace_entries = 0
    database_entries: list[tuple[str, int]] = []
    study_guard_entries: list[int] = []
    original_workspace_lock = coding_references_module.workspace_mutation_lock
    original_transaction = QualitativeProjectDatabase.transaction
    original_read = QualitativeProjectDatabase.read
    original_study_guard = StudyBatchOperationStore.study_mutation_guard

    @contextmanager
    def tracked_workspace_lock(root):
        nonlocal workspace_depth, workspace_entries
        same_root = Path(root) == tmp_path
        with original_workspace_lock(root):
            if same_root:
                workspace_depth += 1
                workspace_entries += 1
            try:
                yield
            finally:
                if same_root:
                    workspace_depth -= 1

    @contextmanager
    def tracked_transaction(database):
        if database.root == tmp_path:
            database_entries.append(("transaction", workspace_depth))
            assert workspace_depth == 0
        with original_transaction(database) as connection:
            yield connection

    @contextmanager
    def tracked_read(database):
        if database.root == tmp_path:
            database_entries.append(("read", workspace_depth))
            assert workspace_depth == 0
        with original_read(database) as connection:
            yield connection

    @contextmanager
    def tracked_study_guard(store):
        if store.root == tmp_path:
            study_guard_entries.append(workspace_depth)
            assert workspace_depth == 0
        with original_study_guard(store):
            yield

    monkeypatch.setattr(
        coding_references_module,
        "workspace_mutation_lock",
        tracked_workspace_lock,
    )
    monkeypatch.setattr(
        QualitativeProjectDatabase,
        "transaction",
        tracked_transaction,
    )
    monkeypatch.setattr(
        QualitativeProjectDatabase,
        "read",
        tracked_read,
    )
    monkeypatch.setattr(
        StudyBatchOperationStore,
        "study_mutation_guard",
        tracked_study_guard,
    )

    created = _create_reference(fixture)
    assert fixture.service.read_reference(created.coding_reference_id) == created
    assert fixture.service.list_references() == (created,)
    fixture.service.validate_project_state()

    assert workspace_entries >= 4
    assert any(kind == "transaction" for kind, _ in database_entries)
    assert sum(kind == "read" for kind, _ in database_entries) >= 3
    assert all(depth == 0 for _, depth in database_entries)
    assert study_guard_entries
    assert all(depth == 0 for depth in study_guard_entries)
    assert workspace_depth == 0


def test_missing_foreign_and_inactive_researchers_cannot_code_or_remove(
    tmp_path: Path,
) -> None:
    fixture = _create_fixture(tmp_path / "project")
    with pytest.raises(CodingReferenceNotFoundError, match="Researcher"):
        _create_reference(fixture, researcher_id="res_missing")

    foreign = _create_fixture(tmp_path / "foreign")
    foreign_only_id = "res_foreign_only"
    now = datetime.now(UTC).isoformat()
    with foreign.database.transaction() as connection:
        connection.execute(
            """
            insert into researchers (
              researcher_id, project_id, display_name, role,
              active, created_at, updated_at
            ) values (?, ?, 'Foreign Researcher', 'researcher', 1, ?, ?)
            """,
            (foreign_only_id, foreign.project_id, now, now),
        )
    with pytest.raises(CodingReferenceNotFoundError, match="Researcher"):
        _create_reference(fixture, researcher_id=foreign_only_id)

    with fixture.database.transaction() as connection:
        connection.execute(
            """
            update researchers set active = 0, updated_at = ?
            where project_id = ? and researcher_id = ?
            """,
            (now, fixture.project_id, SECOND_ID),
        )
    with pytest.raises(CodingReferenceConflictError, match="inactive"):
        _create_reference(fixture, researcher_id=SECOND_ID)
    created = _create_reference(fixture)
    with pytest.raises(CodingReferenceConflictError, match="inactive"):
        fixture.service.remove_reference(
            researcher_id=SECOND_ID,
            coding_reference_id=created.coding_reference_id,
        )


@pytest.mark.parametrize(
    ("row_overrides", "message"),
    [
        ({"start_offset": "alpha", "end_offset": "omega"}, "start_offset"),
        ({"project_source_id": " padded-source "}, "project_source_id"),
        ({"created_at": "2026-08-01T12:00:00"}, "created_at"),
        ({"created_at": "not-a-timestamp"}, "created_at"),
    ],
)
def test_strict_reads_reject_stored_types_padded_ids_and_bad_timestamps(
    tmp_path: Path,
    row_overrides: dict[str, object],
    message: str,
) -> None:
    fixture = _create_fixture(tmp_path)
    reference_id = _insert_raw_reference(
        fixture,
        suffix="6",
        row_overrides=row_overrides,
    )

    with pytest.raises(CodingReferenceConflictError, match=message):
        fixture.service.read_reference(reference_id)


@pytest.mark.parametrize(
    "metadata_json",
    [
        "{",
        '{"target_kind": "passage", "evidence_set_id": "content"}',
        '{"code_id":"content","extra":"private excerpt"}',
        "[]",
        "NONCANONICAL_EXPECTED",
    ],
)
def test_strict_reads_reject_malformed_noncanonical_and_content_audits(
    tmp_path: Path,
    metadata_json: str,
) -> None:
    fixture = _create_fixture(tmp_path)
    if metadata_json == "NONCANONICAL_EXPECTED":
        metadata_json = json.dumps(
            {
                "target_kind": "passage",
                "evidence_set_id": fixture.evidence_set_id,
                "codebook_version_id": fixture.codebook_version_id,
                "code_id": fixture.code_id,
            }
        )
    reference_id = _insert_raw_reference(
        fixture,
        suffix="7",
        metadata_json=metadata_json,
    )

    with pytest.raises(CodingReferenceConflictError, match="audit history"):
        fixture.service.read_reference(reference_id)


def test_strict_reads_reject_a_coding_reference_without_creation_audit(
    tmp_path: Path,
) -> None:
    fixture = _create_fixture(tmp_path)
    reference_id = _insert_raw_reference(
        fixture,
        suffix="5",
        include_audit=False,
    )

    with pytest.raises(CodingReferenceConflictError, match="audit history"):
        fixture.service.read_reference(reference_id)


def test_migration_triggers_reject_draft_pre_removed_delete_and_updates(
    tmp_path: Path,
) -> None:
    frozen = _create_fixture(tmp_path / "frozen")
    draft = _create_fixture(tmp_path / "draft", freeze=False)
    now = datetime.now(UTC).isoformat()
    base_values = (
        frozen.project_id,
        frozen.project_source_id,
        frozen.transcript_revision_id,
        frozen.evidence_set_id,
        frozen.passage_id,
        frozen.codebook_version_id,
        frozen.code_id,
        OWNER_ID,
        now,
    )
    with sqlite3.connect(frozen.database.db_path) as connection:
        connection.execute("pragma foreign_keys = on")
        with pytest.raises(sqlite3.IntegrityError, match="active when inserted"):
            connection.execute(
                """
                insert into coding_references values (
                  ?, ?, ?, ?, ?, 'passage', ?, '', 0, 1, ?, ?, ?, ?, ?, ?
                )
                """,
                (f"cdr_{'1' * 32}", *base_values, SECOND_ID, now),
            )

    with sqlite3.connect(draft.database.db_path) as connection:
        connection.execute("pragma foreign_keys = on")
        with pytest.raises(sqlite3.IntegrityError, match="frozen"):
            connection.execute(
                """
                insert into coding_references values (
                  ?, ?, ?, ?, ?, 'passage', ?, '', 0, 1, ?, ?, ?, ?, null, null
                )
                """,
                (
                    f"cdr_{'2' * 32}",
                    draft.project_id,
                    draft.project_source_id,
                    draft.transcript_revision_id,
                    draft.evidence_set_id,
                    draft.passage_id,
                    draft.codebook_version_id,
                    draft.code_id,
                    OWNER_ID,
                    now,
                ),
            )

    created = _create_reference(frozen)
    with sqlite3.connect(frozen.database.db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="physically deleted"):
            connection.execute(
                "delete from coding_references where coding_reference_id = ?",
                (created.coding_reference_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="initial removal"):
            connection.execute(
                """
                update coding_references set code_id = code_id
                where coding_reference_id = ?
                """,
                (created.coding_reference_id,),
            )

    removed = frozen.service.remove_reference(
        researcher_id=SECOND_ID,
        coding_reference_id=created.coding_reference_id,
    )
    forbidden_updates = (
        (
            "update coding_references set removed_by = ? where coding_reference_id = ?",
            (THIRD_ID, removed.coding_reference_id),
        ),
        (
            "update coding_references set removed_by = null, removed_at = null "
            "where coding_reference_id = ?",
            (removed.coding_reference_id,),
        ),
        (
            "update coding_references set removed_at = removed_at "
            "where coding_reference_id = ?",
            (removed.coding_reference_id,),
        ),
    )
    for statement, parameters in forbidden_updates:
        with sqlite3.connect(frozen.database.db_path) as connection:
            with pytest.raises(sqlite3.IntegrityError, match="initial removal"):
                connection.execute(statement, parameters)

    active = _create_reference(frozen)
    with sqlite3.connect(frozen.database.db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="initial removal"):
            connection.execute(
                """
                update coding_references set start_offset = start_offset + 1
                where coding_reference_id = ?
                """,
                (active.coding_reference_id,),
            )


def test_audit_failures_roll_back_create_and_remove(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _create_fixture(tmp_path)

    def fail_audit(*args: object, **kwargs: object) -> None:
        raise sqlite3.IntegrityError("injected audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(fixture.service, "_append_audit", fail_audit)
        with pytest.raises(CodingReferenceConflictError, match="constraint"):
            _create_reference(fixture)
    with sqlite3.connect(fixture.database.db_path) as connection:
        assert connection.execute(
            "select count(*) from coding_references"
        ).fetchone() == (0,)

    created = _create_reference(fixture)
    with monkeypatch.context() as patch:
        patch.setattr(fixture.service, "_append_audit", fail_audit)
        with pytest.raises(CodingReferenceConflictError, match="constraint"):
            fixture.service.remove_reference(
                researcher_id=SECOND_ID,
                coding_reference_id=created.coding_reference_id,
            )
    assert fixture.service.read_reference(created.coding_reference_id) == created
    assert len(_coding_audits(fixture.database)) == 1


def test_strict_reads_reject_missing_or_noncanonical_audit_and_stale_offsets(
    tmp_path: Path,
) -> None:
    fixture = _create_fixture(tmp_path)
    created = _create_reference(fixture)
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(fixture.database.db_path) as connection:
        connection.execute(
            """
            insert into qualitative_audit_events (
              event_id, project_id, actor_id, event_type,
              subject_type, subject_id, metadata_json, created_at
            ) values (?, ?, ?, 'coding_reference.created',
                      'coding_reference', ?, ?, ?)
            """,
            (
                f"qae_{'9' * 32}",
                fixture.project_id,
                OWNER_ID,
                created.coding_reference_id,
                '{"unexpected":"content"}',
                created.created_at,
            ),
        )
    with pytest.raises(CodingReferenceConflictError, match="audit history"):
        fixture.service.read_reference(created.coding_reference_id)

    stale = _create_fixture(tmp_path / "stale")
    stale_id = f"cdr_{'8' * 32}"
    metadata = json.dumps(
        {
            "code_id": stale.code_id,
            "codebook_version_id": stale.codebook_version_id,
            "evidence_set_id": stale.evidence_set_id,
            "target_kind": "passage",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    with sqlite3.connect(stale.database.db_path) as connection:
        connection.execute("pragma foreign_keys = on")
        connection.execute(
            """
            insert into coding_references values (
              ?, ?, ?, ?, ?, 'passage', ?, '', 999, 1000,
              ?, ?, ?, ?, null, null
            )
            """,
            (
                stale_id,
                stale.project_id,
                stale.project_source_id,
                stale.transcript_revision_id,
                stale.evidence_set_id,
                stale.passage_id,
                stale.codebook_version_id,
                stale.code_id,
                OWNER_ID,
                now,
            ),
        )
        connection.execute(
            """
            insert into qualitative_audit_events values (
              ?, ?, ?, 'coding_reference.created',
              'coding_reference', ?, ?, ?
            )
            """,
            (
                f"qae_{'8' * 32}",
                stale.project_id,
                OWNER_ID,
                stale_id,
                metadata,
                now,
            ),
        )
    with pytest.raises(CodingReferenceConflictError, match="outside"):
        stale.service.read_reference(stale_id)


def test_project_validation_rejects_unmatched_coding_audit(tmp_path: Path) -> None:
    fixture = _create_fixture(tmp_path)
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(fixture.database.db_path) as connection:
        connection.execute(
            """
            insert into qualitative_audit_events values (
              ?, ?, ?, 'coding_reference.created',
              'coding_reference', ?, '{}', ?
            )
            """,
            (
                f"qae_{'7' * 32}",
                fixture.project_id,
                OWNER_ID,
                f"cdr_{'7' * 32}",
                now,
            ),
        )

    with pytest.raises(CodingReferenceConflictError, match="audit history"):
        fixture.service.validate_project_state()
