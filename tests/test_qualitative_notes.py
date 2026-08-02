import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

import backend.qualitative.notes as notes_module
from backend.evidence.identifiers import (
    cunit_evidence_id,
    passage_evidence_id,
    transcript_evidence_identity,
)
from backend.qualitative.cases import CaseService
from backend.qualitative.codebooks import CodebookService
from backend.qualitative.database import QualitativeProjectDatabase
from backend.qualitative.notes import (
    NoteConflictError,
    NoteNotFoundError,
    NoteService,
    NoteValidationError,
)
from backend.storage.evidence_catalog import EvidenceCatalog, EvidenceImportRecord
from backend.storage.evidence_target_registry import (
    EvidenceCUnitInput,
    EvidencePassageInput,
    EvidenceTargetRegistry,
)
from backend.storage.study_store import StudyWorkspaceStore


OWNER_ID = "res_note_owner"
SECOND_ID = "res_note_second"
PASSAGE_TEXT = "I came 😊 and I stayed."
CUNIT_TEXT = "I came 😊"


@dataclass(frozen=True)
class _NoteFixture:
    project_id: str
    database: QualitativeProjectDatabase
    service: NoteService
    project_source_id: str
    transcript_revision_id: str
    evidence_set_id: str
    passage_id: str
    cunit_id: str
    case_id: str
    frozen_version_id: str
    frozen_code_id: str
    draft_version_id: str
    draft_code_id: str


def _create_fixture(root: Path) -> _NoteFixture:
    study = StudyWorkspaceStore(root).create_study({"name": "Note Study"})
    database = QualitativeProjectDatabase(root, study.id)
    database.initialize(
        researcher_id=OWNER_ID,
        researcher_name="Note Owner",
    )
    now = datetime.now(UTC).isoformat()
    with database.transaction() as connection:
        connection.execute(
            """
            insert into researchers (
              researcher_id, project_id, display_name, role,
              active, created_at, updated_at
            ) values (?, ?, 'Second Researcher', 'researcher', 1, ?, ?)
            """,
            (SECOND_ID, study.id, now, now),
        )

    transcript_text = f"P1: {PASSAGE_TEXT}"
    identity = transcript_evidence_identity(transcript_text)
    project_source_id = "psrc_note_interview"
    import_record = EvidenceImportRecord(
        import_id="imp_note_interview",
        run_id="run_note_interview",
        pipeline="segmentation",
        source_id=identity.source_id,
        source_filename="private-note-interview.txt",
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
                        text=CUNIT_TEXT,
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

    case = CaseService(root, study.id).create_case(
        researcher_id=OWNER_ID,
        case_kind="participant",
        label="Participant A",
    )
    codebooks = CodebookService(root, study.id)
    frozen_codebook = codebooks.create_codebook(
        researcher_id=OWNER_ID,
        title="Frozen note codes",
    )
    frozen_draft = codebooks.create_draft(
        researcher_id=OWNER_ID,
        codebook_id=frozen_codebook.codebook_id,
    )
    frozen_code = codebooks.add_code(
        researcher_id=OWNER_ID,
        codebook_id=frozen_codebook.codebook_id,
        codebook_version_id=frozen_draft.version.codebook_version_id,
        stable_code_key="persistence",
        label="Persistence",
    )
    codebooks.freeze_version(
        researcher_id=OWNER_ID,
        codebook_id=frozen_codebook.codebook_id,
        codebook_version_id=frozen_draft.version.codebook_version_id,
    )
    draft_codebook = codebooks.create_codebook(
        researcher_id=OWNER_ID,
        title="Draft note codes",
    )
    draft = codebooks.create_draft(
        researcher_id=OWNER_ID,
        codebook_id=draft_codebook.codebook_id,
    )
    draft_code = codebooks.add_code(
        researcher_id=OWNER_ID,
        codebook_id=draft_codebook.codebook_id,
        codebook_version_id=draft.version.codebook_version_id,
        stable_code_key="tentative",
        label="Tentative",
    )
    return _NoteFixture(
        project_id=study.id,
        database=database,
        service=NoteService(root, study.id),
        project_source_id=project_source_id,
        transcript_revision_id=identity.transcript_revision_id,
        evidence_set_id=prepared.evidence_set_id,
        passage_id=passage_id,
        cunit_id=cunit_id,
        case_id=case.case_id,
        frozen_version_id=frozen_draft.version.codebook_version_id,
        frozen_code_id=frozen_code.code_id,
        draft_version_id=draft.version.codebook_version_id,
        draft_code_id=draft_code.code_id,
    )


def _targets(fixture: _NoteFixture) -> tuple[dict[str, object], ...]:
    return (
        {"kind": "study"},
        {"kind": "source", "project_source_id": fixture.project_source_id},
        {"kind": "case", "case_id": fixture.case_id},
        {
            "kind": "code",
            "codebook_version_id": fixture.frozen_version_id,
            "code_id": fixture.frozen_code_id,
        },
        {
            "kind": "excerpt",
            "project_source_id": fixture.project_source_id,
            "transcript_revision_id": fixture.transcript_revision_id,
            "evidence_set_id": fixture.evidence_set_id,
            "excerpt_target_kind": "passage",
            "passage_id": fixture.passage_id,
            "start_offset": 0,
            "end_offset": 7,
        },
    )


def _create_study_memo(
    fixture: _NoteFixture,
    *,
    researcher_id: str = OWNER_ID,
    title: str = "Study memo",
    body: str = "Exact memo body.",
):
    return fixture.service.create_note(
        note_kind="memo",
        researcher_id=researcher_id,
        title=title,
        body=body,
        target={"kind": "study"},
    )


def _note_audits(fixture: _NoteFixture, note_id: str) -> list[sqlite3.Row]:
    with sqlite3.connect(fixture.database.db_path) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            select * from qualitative_audit_events
            where subject_id = ? and subject_type in ('memo', 'annotation')
            order by created_at, event_id
            """,
            (note_id,),
        ).fetchall()


def _tamper_immutable_row(
    fixture: _NoteFixture,
    *,
    trigger_name: str,
    statement: str,
    parameters: tuple[object, ...],
    ignore_checks: bool = False,
) -> None:
    with fixture.database.transaction() as connection:
        trigger_sql = connection.execute(
            "select sql from sqlite_master where type = 'trigger' and name = ?",
            (trigger_name,),
        ).fetchone()[0]
        connection.execute(f"drop trigger {trigger_name}")
        if ignore_checks:
            connection.execute("pragma ignore_check_constraints = on")
        connection.execute(statement, parameters)
        if ignore_checks:
            connection.execute("pragma ignore_check_constraints = off")
        connection.execute(trigger_sql)


def test_all_target_kinds_round_trip_for_both_note_kinds_without_copied_evidence(
    tmp_path: Path,
) -> None:
    fixture = _create_fixture(tmp_path)
    created = []
    exact_body = "  Exact e\u0301 body.\r\n  "
    for note_kind in ("memo", "annotation"):
        for index, target in enumerate(_targets(fixture)):
            snapshot = fixture.service.create_note(
                note_kind=note_kind,
                researcher_id=OWNER_ID,
                title=f"  Memo e\u0301 {index}  " if note_kind == "memo" else "",
                body=exact_body,
                target=target,
            )
            created.append(snapshot)
            assert fixture.service.read_note(
                note_kind,
                snapshot.note.note_id,
            ) == snapshot
            assert snapshot.current_revision.body == exact_body
            if note_kind == "memo":
                assert snapshot.current_revision.title == f"Memo e\u0301 {index}"
            else:
                assert snapshot.current_revision.title == ""
            assert snapshot.note.target.kind == target["kind"]

    cunit_annotation = fixture.service.create_note(
        note_kind="annotation",
        researcher_id=OWNER_ID,
        title="",
        body="C-unit context.",
        target={
            "kind": "excerpt",
            "project_source_id": fixture.project_source_id,
            "transcript_revision_id": fixture.transcript_revision_id,
            "evidence_set_id": fixture.evidence_set_id,
            "excerpt_target_kind": "cunit",
            "passage_id": fixture.passage_id,
            "cunit_id": fixture.cunit_id,
            "start_offset": 0,
            "end_offset": len(CUNIT_TEXT),
        },
    )
    assert cunit_annotation.note.target.cunit_id == fixture.cunit_id
    fixture.service.validate_project_state()

    with sqlite3.connect(fixture.database.db_path) as connection:
        note_columns = {
            row[1] for row in connection.execute("pragma table_info(qualitative_notes)")
        }
        metadata = [
            row[0]
            for row in connection.execute(
                """
                select metadata_json from qualitative_audit_events
                where subject_type in ('memo', 'annotation')
                """
            )
        ]
    assert note_columns.isdisjoint(
        {"text", "target_text", "text_sha256", "excerpt_text", "excerpt_sha256"}
    )
    assert all(
        set(json.loads(value))
        <= {"note_revision_id", "revision_number", "target_kind"}
        for value in metadata
    )
    assert PASSAGE_TEXT.encode("utf-8") not in fixture.database.db_path.read_bytes()
    assert len(created) == 10


def test_compare_and_append_revision_retry_noop_and_divergence(
    tmp_path: Path,
) -> None:
    fixture = _create_fixture(tmp_path)
    initial = _create_study_memo(fixture)
    revised = fixture.service.revise_note(
        note_kind="memo",
        note_id=initial.note.note_id,
        researcher_id=SECOND_ID,
        expected_revision_number=1,
        title="  Revised title  ",
        body="  Revised body.\n",
    )
    retry = fixture.service.revise_note(
        note_kind="memo",
        note_id=initial.note.note_id,
        researcher_id=SECOND_ID,
        expected_revision_number=1,
        title=" Revised title ",
        body="  Revised body.\n",
    )
    assert retry == revised
    assert revised.current_revision.revision_number == 2
    assert revised.current_revision.title == "Revised title"
    with pytest.raises(NoteValidationError):
        fixture.service.revise_note(
            note_kind="memo",
            note_id=initial.note.note_id,
            researcher_id=SECOND_ID,
            expected_revision_number=2,
            title="Revised title",
            body="  Revised body.\n",
        )
    with pytest.raises(NoteConflictError):
        fixture.service.revise_note(
            note_kind="memo",
            note_id=initial.note.note_id,
            researcher_id=OWNER_ID,
            expected_revision_number=1,
            title="Divergent",
            body="Divergent body.",
        )
    with pytest.raises(NoteConflictError):
        fixture.service.revise_note(
            note_kind="memo",
            note_id=initial.note.note_id,
            researcher_id=OWNER_ID,
            expected_revision_number=99,
            title="Future",
            body="Future body.",
        )
    revisions, cursor = fixture.service.list_revisions(
        "memo",
        initial.note.note_id,
    )
    assert cursor is None
    assert [revision.revision_number for revision in revisions] == [1, 2]
    assert [row["event_type"] for row in _note_audits(fixture, initial.note.note_id)] == [
        "memo.created",
        "memo.revised",
    ]


def test_concurrent_divergent_same_base_revision_serializes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _create_fixture(tmp_path)
    initial = _create_study_memo(fixture)
    write_barrier = threading.Barrier(2)
    original_write = NoteService._write

    @contextmanager
    def synchronized_write(service):
        write_barrier.wait(timeout=10)
        with original_write(service) as connection:
            yield connection

    monkeypatch.setattr(NoteService, "_write", synchronized_write)

    def revise(actor_id: str, title: str, body: str):
        try:
            return fixture.service.revise_note(
                note_kind="memo",
                note_id=initial.note.note_id,
                researcher_id=actor_id,
                expected_revision_number=1,
                title=title,
                body=body,
            )
        except NoteConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(revise, OWNER_ID, "Owner revision", "Owner body."),
            executor.submit(revise, SECOND_ID, "Second revision", "Second body."),
        )
        results = tuple(future.result(timeout=15) for future in futures)

    conflicts = [result for result in results if isinstance(result, NoteConflictError)]
    successes = [result for result in results if not isinstance(result, Exception)]
    assert len(conflicts) == 1
    assert len(successes) == 1
    assert successes[0].current_revision.revision_number == 2
    with sqlite3.connect(fixture.database.db_path) as connection:
        assert connection.execute(
            "select count(*) from qualitative_note_revisions where note_id = ?",
            (initial.note.note_id,),
        ).fetchone() == (2,)
    assert [row["event_type"] for row in _note_audits(fixture, initial.note.note_id)] == [
        "memo.created",
        "memo.revised",
    ]


def test_tombstone_is_attributable_idempotent_and_collection_scoped(
    tmp_path: Path,
) -> None:
    fixture = _create_fixture(tmp_path)
    initial = _create_study_memo(fixture)
    removed = fixture.service.remove_note(
        note_kind="memo",
        note_id=initial.note.note_id,
        researcher_id=SECOND_ID,
    )
    assert removed.note.removed_by == SECOND_ID
    assert fixture.service.remove_note(
        note_kind="memo",
        note_id=initial.note.note_id,
        researcher_id=SECOND_ID,
    ) == removed
    with pytest.raises(NoteConflictError):
        fixture.service.remove_note(
            note_kind="memo",
            note_id=initial.note.note_id,
            researcher_id=OWNER_ID,
        )
    active, _ = fixture.service.list_notes("memo")
    all_memos, _ = fixture.service.list_notes("memo", include_removed=True)
    assert active == ()
    assert all_memos == (removed,)
    assert fixture.service.read_note("memo", initial.note.note_id) == removed
    assert [row["event_type"] for row in _note_audits(fixture, initial.note.note_id)] == [
        "memo.created",
        "memo.removed",
    ]


def test_note_and_revision_pagination_are_bounded_and_deterministic(
    tmp_path: Path,
) -> None:
    fixture = _create_fixture(tmp_path)
    created = [
        _create_study_memo(fixture, title=f"Memo {index}", body=f"Body {index}")
        for index in range(5)
    ]
    seen = []
    cursor = None
    while True:
        page, cursor = fixture.service.list_notes("memo", limit=2, cursor=cursor)
        assert len(page) <= 2
        seen.extend(snapshot.note.note_id for snapshot in page)
        if cursor is None:
            break
    assert seen == [snapshot.note.note_id for snapshot in created]
    filtered, _ = fixture.service.list_notes(
        "memo",
        target_kind="source",
        cursor=created[1].note.note_id,
    )
    assert filtered == ()
    with pytest.raises(NoteNotFoundError):
        fixture.service.list_notes("memo", cursor=f"mem_{'f' * 32}")

    current = created[0]
    for number in range(1, 5):
        current = fixture.service.revise_note(
            note_kind="memo",
            note_id=current.note.note_id,
            researcher_id=OWNER_ID,
            expected_revision_number=number,
            title=f"Revision {number + 1}",
            body=f"Revision body {number + 1}",
        )
    first_page, revision_cursor = fixture.service.list_revisions(
        "memo",
        current.note.note_id,
        limit=2,
    )
    second_page, next_cursor = fixture.service.list_revisions(
        "memo",
        current.note.note_id,
        limit=2,
        cursor=revision_cursor,
    )
    final_page, final_cursor = fixture.service.list_revisions(
        "memo",
        current.note.note_id,
        limit=2,
        cursor=next_cursor,
    )
    assert [revision.revision_number for revision in first_page + second_page + final_page] == [
        1,
        2,
        3,
        4,
        5,
    ]
    assert final_cursor is None
    with pytest.raises(NoteNotFoundError):
        fixture.service.list_revisions(
            "memo",
            current.note.note_id,
            cursor=f"nrv_{'f' * 32}",
        )


def test_page_and_project_validation_materialization_is_bounded_and_streamed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _create_fixture(tmp_path)
    created = [
        _create_study_memo(fixture, title=f"Memo {index}", body=f"Body {index}")
        for index in range(4)
    ]
    current = created[0]
    for expected in range(1, 5):
        current = fixture.service.revise_note(
            note_kind="memo",
            note_id=current.note.note_id,
            researcher_id=OWNER_ID,
            expected_revision_number=expected,
            title=f"Revision {expected + 1}",
            body=f"Revision body {expected + 1}",
        )

    statements: list[tuple[str, tuple[object, ...]]] = []
    fetchall_statements: list[tuple[str, tuple[object, ...]]] = []
    original_read = NoteService._read

    class CursorProxy:
        def __init__(
            self,
            cursor: sqlite3.Cursor,
            statement: str,
            parameters: tuple[object, ...],
        ) -> None:
            self._cursor = cursor
            self._statement = statement
            self._parameters = parameters

        def __iter__(self):
            return iter(self._cursor)

        def __getattr__(self, name: str):
            return getattr(self._cursor, name)

        def fetchall(self):
            fetchall_statements.append((self._statement, self._parameters))
            return self._cursor.fetchall()

    class ConnectionProxy:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def execute(self, statement: str, parameters: tuple[object, ...] = ()):
            exact_parameters = tuple(parameters)
            statements.append((statement, exact_parameters))
            return CursorProxy(
                self._connection.execute(statement, exact_parameters),
                statement,
                exact_parameters,
            )

        def __getattr__(self, name: str):
            return getattr(self._connection, name)

    @contextmanager
    def tracked_read(service: NoteService):
        with original_read(service) as connection:
            yield ConnectionProxy(connection)

    monkeypatch.setattr(NoteService, "_read", tracked_read)
    notes, _ = fixture.service.list_notes("memo", limit=1)
    revisions, _ = fixture.service.list_revisions(
        "memo",
        current.note.note_id,
        limit=1,
    )
    fixture.service.validate_project_state()

    assert len(notes) == 1
    assert len(revisions) == 1
    assert fetchall_statements
    assert all(" limit ?" in " ".join(statement.split()) for statement, _ in fetchall_statements)
    note_page_queries = [
        (statement, parameters)
        for statement, parameters in statements
        if "select * from qualitative_notes where" in " ".join(statement.split())
        and "order by created_at, note_id limit ?" in " ".join(statement.split())
    ]
    revision_page_queries = [
        (statement, parameters)
        for statement, parameters in statements
        if "select * from qualitative_note_revisions where" in " ".join(statement.split())
        and "order by revision_number, note_revision_id limit ?"
        in " ".join(statement.split())
    ]
    assert len(note_page_queries) == 1
    assert len(revision_page_queries) == 1
    assert note_page_queries[0][1][-1] == 2
    assert revision_page_queries[0][1][-1] == 2
    assert any(
        "select * from qualitative_note_revisions where" in " ".join(statement.split())
        and " limit ?" not in " ".join(statement.split())
        for statement, _ in statements
    )
    assert any(
        "select * from qualitative_audit_events where" in " ".join(statement.split())
        and " limit ?" not in " ".join(statement.split())
        for statement, _ in statements
    )


@pytest.mark.parametrize(
    ("note_kind", "title", "body"),
    (
        ("memo", "   ", "body"),
        ("annotation", "forbidden", "body"),
        ("memo", "valid", "   \n"),
        ("memo", "bad\x00title", "body"),
        ("memo", "valid", "bad\x00body"),
        ("memo", "bad\ud800title", "body"),
        ("memo", "valid", "bad\udfffbody"),
    ),
)
def test_invalid_note_content_is_rejected(
    tmp_path: Path,
    note_kind: str,
    title: str,
    body: str,
) -> None:
    fixture = _create_fixture(tmp_path)
    with pytest.raises(NoteValidationError):
        fixture.service.create_note(
            note_kind=note_kind,
            researcher_id=OWNER_ID,
            title=title,
            body=body,
            target={"kind": "study"},
        )


@pytest.mark.parametrize(
    "target",
    (
        {"kind": "study", "case_id": "cas_extra"},
        {"kind": "source"},
        {"kind": "case", "case_id": "CAS_BAD"},
        {
            "kind": "code",
            "codebook_version_id": "cbv_bad",
            "code_id": "cod_bad",
            "case_id": "cas_hybrid",
        },
        {
            "kind": "excerpt",
            "project_source_id": "psrc_note_interview",
            "transcript_revision_id": "trv_00000000000000000000000000000000",
            "evidence_set_id": "evs_00000000000000000000000000000000",
            "excerpt_target_kind": "passage",
            "passage_id": "psg_00000000000000000000000000000000",
            "cunit_id": "cun_00000000000000000000000000000000",
            "start_offset": 0,
            "end_offset": 1,
        },
        {
            "kind": "excerpt",
            "project_source_id": "psrc_note_interview",
            "transcript_revision_id": "trv_00000000000000000000000000000000",
            "evidence_set_id": "evs_00000000000000000000000000000000",
            "excerpt_target_kind": "cunit",
            "passage_id": "psg_00000000000000000000000000000000",
            "start_offset": 0,
            "end_offset": 1,
        },
        {
            "kind": "excerpt",
            "project_source_id": "psrc_note_interview",
            "transcript_revision_id": "trv_00000000000000000000000000000000",
            "evidence_set_id": "evs_00000000000000000000000000000000",
            "excerpt_target_kind": "passage",
            "passage_id": "psg_00000000000000000000000000000000",
            "start_offset": False,
            "end_offset": 1,
        },
    ),
)
def test_partial_hybrid_and_wrongly_typed_targets_are_rejected(
    tmp_path: Path,
    target: dict[str, object],
) -> None:
    fixture = _create_fixture(tmp_path)
    with pytest.raises(NoteValidationError):
        fixture.service.create_note(
            note_kind="memo",
            researcher_id=OWNER_ID,
            title="Target memo",
            body="Body.",
            target=target,
        )


def test_missing_draft_and_out_of_bounds_dependencies_fail_by_boundary(
    tmp_path: Path,
) -> None:
    fixture = _create_fixture(tmp_path)
    codebooks = CodebookService(tmp_path, fixture.project_id)
    second_codebook = codebooks.create_codebook(
        researcher_id=OWNER_ID,
        title="Second frozen codes",
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
    with pytest.raises(NoteNotFoundError):
        fixture.service.create_note(
            note_kind="memo",
            researcher_id=OWNER_ID,
            title="Missing source",
            body="Body.",
            target={"kind": "source", "project_source_id": "psrc_missing"},
        )
    with pytest.raises(NoteNotFoundError):
        fixture.service.create_note(
            note_kind="memo",
            researcher_id=OWNER_ID,
            title="Missing case",
            body="Body.",
            target={"kind": "case", "case_id": "cas_missing"},
        )
    with pytest.raises(NoteConflictError):
        fixture.service.create_note(
            note_kind="memo",
            researcher_id=OWNER_ID,
            title="Draft code",
            body="Body.",
            target={
                "kind": "code",
                "codebook_version_id": fixture.draft_version_id,
                "code_id": fixture.draft_code_id,
            },
        )
    with pytest.raises(NoteConflictError):
        fixture.service.create_note(
            note_kind="memo",
            researcher_id=OWNER_ID,
            title="Wrong code version",
            body="Body.",
            target={
                "kind": "code",
                "codebook_version_id": second_draft.version.codebook_version_id,
                "code_id": fixture.frozen_code_id,
            },
        )
    with pytest.raises(NoteValidationError):
        fixture.service.create_note(
            note_kind="memo",
            researcher_id=OWNER_ID,
            title="Bad excerpt",
            body="Body.",
            target={
                "kind": "excerpt",
                "project_source_id": fixture.project_source_id,
                "transcript_revision_id": fixture.transcript_revision_id,
                "evidence_set_id": fixture.evidence_set_id,
                "excerpt_target_kind": "passage",
                "passage_id": fixture.passage_id,
                "start_offset": 0,
                "end_offset": len(PASSAGE_TEXT) + 1,
            },
        )


def test_foreign_evidence_targets_fail_and_distinct_sets_remain_exact(
    tmp_path: Path,
) -> None:
    fixture = _create_fixture(tmp_path)
    registry = EvidenceTargetRegistry(tmp_path)
    transcript_text = f"P1: {PASSAGE_TEXT}"
    transcript_identity = transcript_evidence_identity(transcript_text)
    EvidenceCatalog(tmp_path).record_import(
        EvidenceImportRecord(
            import_id="imp_note_interview_second",
            run_id="run_note_interview_second",
            pipeline="segmentation",
            source_id=transcript_identity.source_id,
            source_filename="private-note-interview.txt",
            source_media_type="text/plain",
            source_blob_sha256="a" * 64,
            transcript_revision_id=transcript_identity.transcript_revision_id,
            transcript_sha256=transcript_identity.transcript_sha256,
            imported_at="2026-08-01T12:30:00+00:00",
            project_source_id=fixture.project_source_id,
            workspace_id=fixture.project_id,
        )
    )
    second_set = registry.prepare_complete_set(
        import_id="imp_note_interview_second",
        workspace_id=fixture.project_id,
        project_source_id=fixture.project_source_id,
        transcript_revision_id=fixture.transcript_revision_id,
        transcript_text=transcript_text,
        producer_kind="cunit_segmentation",
        producer_version=1,
        producer_status="verified",
        review_status="not_domain_validated",
        passages=(
            EvidencePassageInput(
                passage_id=fixture.passage_id,
                passage_ordinal=0,
                role="participant",
                text=PASSAGE_TEXT,
                cunits=(
                    EvidenceCUnitInput(
                        cunit_id=fixture.cunit_id,
                        cunit_ordinal=0,
                        text=CUNIT_TEXT,
                    ),
                    EvidenceCUnitInput(
                        cunit_id=cunit_evidence_id(fixture.passage_id, 1),
                        cunit_ordinal=1,
                        text="and I stayed.",
                    ),
                ),
            ),
        ),
    )
    registry.register_complete_set(second_set)
    assert second_set.evidence_set_id != fixture.evidence_set_id

    notes = []
    for evidence_set_id in (fixture.evidence_set_id, second_set.evidence_set_id):
        notes.append(
            fixture.service.create_note(
                note_kind="annotation",
                researcher_id=OWNER_ID,
                title="",
                body=f"Set {evidence_set_id}",
                target={
                    "kind": "excerpt",
                    "project_source_id": fixture.project_source_id,
                    "transcript_revision_id": fixture.transcript_revision_id,
                    "evidence_set_id": evidence_set_id,
                    "excerpt_target_kind": "cunit",
                    "passage_id": fixture.passage_id,
                    "cunit_id": fixture.cunit_id,
                    "start_offset": 0,
                    "end_offset": len(CUNIT_TEXT),
                },
            )
        )
    assert notes[0].note.target.evidence_set_id == fixture.evidence_set_id
    assert notes[1].note.target.evidence_set_id == second_set.evidence_set_id
    assert notes[0].note.target.passage_id == notes[1].note.target.passage_id
    assert notes[0].note.target.cunit_id == notes[1].note.target.cunit_id

    foreign_study = StudyWorkspaceStore(tmp_path).create_study(
        {"name": "Foreign Note Evidence"}
    )
    foreign_text = "P1: Foreign evidence."
    foreign_identity = transcript_evidence_identity(foreign_text)
    foreign_source_id = "psrc_foreign_note"
    foreign_import = EvidenceImportRecord(
        import_id="imp_foreign_note",
        run_id="run_foreign_note",
        pipeline="segmentation",
        source_id=foreign_identity.source_id,
        source_filename="foreign-note.txt",
        source_media_type="text/plain",
        source_blob_sha256="b" * 64,
        transcript_revision_id=foreign_identity.transcript_revision_id,
        transcript_sha256=foreign_identity.transcript_sha256,
        imported_at="2026-08-01T13:00:00+00:00",
        project_source_id=foreign_source_id,
        workspace_id=foreign_study.id,
    )
    EvidenceCatalog(tmp_path).record_import(foreign_import)
    foreign_passage_id = passage_evidence_id(
        foreign_identity.transcript_revision_id,
        0,
    )
    foreign_set = registry.prepare_complete_set(
        import_id=foreign_import.import_id,
        workspace_id=foreign_study.id,
        project_source_id=foreign_source_id,
        transcript_revision_id=foreign_identity.transcript_revision_id,
        transcript_text=foreign_text,
        producer_kind="cunit_segmentation",
        producer_version=1,
        producer_status="verified",
        review_status="not_domain_validated",
        passages=(
            EvidencePassageInput(
                passage_id=foreign_passage_id,
                passage_ordinal=0,
                role="participant",
                text="Foreign evidence.",
            ),
        ),
    )
    registry.register_complete_set(foreign_set)
    for target in (
        {"kind": "source", "project_source_id": foreign_source_id},
        {
            "kind": "excerpt",
            "project_source_id": foreign_source_id,
            "transcript_revision_id": foreign_identity.transcript_revision_id,
            "evidence_set_id": foreign_set.evidence_set_id,
            "excerpt_target_kind": "passage",
            "passage_id": foreign_passage_id,
            "start_offset": 0,
            "end_offset": len("Foreign evidence."),
        },
    ):
        with pytest.raises(NoteConflictError):
            fixture.service.create_note(
                note_kind="memo",
                researcher_id=OWNER_ID,
                title="Foreign target",
                body="Body.",
                target=target,
            )


def test_inactive_mutation_actor_keeps_historical_attribution_readable(
    tmp_path: Path,
) -> None:
    fixture = _create_fixture(tmp_path)
    historical = _create_study_memo(fixture, researcher_id=SECOND_ID)
    with fixture.database.transaction() as connection:
        connection.execute(
            """
            update researchers set active = 0
            where project_id = ? and researcher_id = ?
            """,
            (fixture.project_id, SECOND_ID),
        )
    assert fixture.service.read_note("memo", historical.note.note_id) == historical
    with pytest.raises(NoteConflictError):
        _create_study_memo(fixture, researcher_id=SECOND_ID, title="Blocked")
    with pytest.raises(NoteNotFoundError):
        _create_study_memo(
            fixture,
            researcher_id="res_note_missing",
            title="Missing",
        )


def test_exact_revision_and_removal_retries_survive_actor_deactivation(
    tmp_path: Path,
) -> None:
    fixture = _create_fixture(tmp_path)
    revised_note = _create_study_memo(fixture, title="Revision retry")
    removed_note = _create_study_memo(fixture, title="Removal retry")
    new_write_note = _create_study_memo(fixture, title="New write")
    revised = fixture.service.revise_note(
        note_kind="memo",
        note_id=revised_note.note.note_id,
        researcher_id=SECOND_ID,
        expected_revision_number=1,
        title="Accepted revision",
        body="Accepted body.",
    )
    removed = fixture.service.remove_note(
        note_kind="memo",
        note_id=removed_note.note.note_id,
        researcher_id=SECOND_ID,
    )
    with fixture.database.transaction() as connection:
        connection.execute(
            """
            update researchers set active = 0
            where project_id = ? and researcher_id = ?
            """,
            (fixture.project_id, SECOND_ID),
        )

    assert fixture.service.revise_note(
        note_kind="memo",
        note_id=revised_note.note.note_id,
        researcher_id=SECOND_ID,
        expected_revision_number=1,
        title="Accepted revision",
        body="Accepted body.",
    ) == revised
    assert fixture.service.remove_note(
        note_kind="memo",
        note_id=removed_note.note.note_id,
        researcher_id=SECOND_ID,
    ) == removed
    with pytest.raises(NoteConflictError):
        fixture.service.revise_note(
            note_kind="memo",
            note_id=new_write_note.note.note_id,
            researcher_id=SECOND_ID,
            expected_revision_number=1,
            title="Blocked revision",
            body="Blocked body.",
        )


def test_audit_tampering_and_unmatched_events_fail_strict_validation(
    tmp_path: Path,
) -> None:
    fixture = _create_fixture(tmp_path)
    snapshot = _create_study_memo(fixture)
    original = dict(_note_audits(fixture, snapshot.note.note_id)[0])
    with fixture.database.transaction() as connection:
        delete_trigger = connection.execute(
            """
            select sql from sqlite_master
            where type = 'trigger' and name = 'prevent_qualitative_audit_delete'
            """
        ).fetchone()[0]
        connection.execute("drop trigger prevent_qualitative_audit_delete")
        connection.execute(
            "delete from qualitative_audit_events where event_id = ?",
            (original["event_id"],),
        )
        connection.execute(delete_trigger)
    with pytest.raises(NoteConflictError):
        fixture.service.read_note("memo", snapshot.note.note_id)

    with fixture.database.transaction() as connection:
        connection.execute(
            """
            insert into qualitative_audit_events (
              event_id, project_id, actor_id, event_type,
              subject_type, subject_id, metadata_json, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(
                original[key]
                for key in (
                    "event_id",
                    "project_id",
                    "actor_id",
                    "event_type",
                    "subject_type",
                    "subject_id",
                    "metadata_json",
                    "created_at",
                )
            ),
        )
        metadata = json.loads(original["metadata_json"])
        metadata["body"] = "must not enter audit"
        update_trigger = connection.execute(
            """
            select sql from sqlite_master
            where type = 'trigger' and name = 'prevent_qualitative_audit_update'
            """
        ).fetchone()[0]
        connection.execute("drop trigger prevent_qualitative_audit_update")
        connection.execute(
            "update qualitative_audit_events set metadata_json = ? where event_id = ?",
            (json.dumps(metadata, sort_keys=True, separators=(",", ":")), original["event_id"]),
        )
        connection.execute(update_trigger)
    with pytest.raises(NoteConflictError):
        fixture.service.read_note("memo", snapshot.note.note_id)

    with fixture.database.transaction() as connection:
        update_trigger = connection.execute(
            """
            select sql from sqlite_master
            where type = 'trigger' and name = 'prevent_qualitative_audit_update'
            """
        ).fetchone()[0]
        connection.execute("drop trigger prevent_qualitative_audit_update")
        connection.execute(
            "update qualitative_audit_events set metadata_json = ? where event_id = ?",
            (original["metadata_json"], original["event_id"]),
        )
        connection.execute(update_trigger)
        connection.execute(
            """
            insert into qualitative_audit_events (
              event_id, project_id, actor_id, event_type,
              subject_type, subject_id, metadata_json, created_at
            ) values (?, ?, ?, 'memo_created', 'memos', ?, '{}', ?)
            """,
            (
                f"qae_{'f' * 32}",
                fixture.project_id,
                OWNER_ID,
                f"mem_{'f' * 32}",
                datetime.now(UTC).isoformat(),
            ),
        )
    assert fixture.service.read_note("memo", snapshot.note.note_id) == snapshot
    with pytest.raises(NoteConflictError):
        fixture.service.validate_project_state()


def test_exact_note_subject_with_malformed_audit_types_never_evades_discovery(
    tmp_path: Path,
) -> None:
    fixture = _create_fixture(tmp_path)
    snapshot = _create_study_memo(fixture)
    with fixture.database.transaction() as connection:
        connection.execute(
            """
            insert into qualitative_audit_events (
              event_id, project_id, actor_id, event_type,
              subject_type, subject_id, metadata_json, created_at
            ) values (?, ?, ?, 'memo_created', 'memos', ?, '{}', ?)
            """,
            (
                f"qae_{'e' * 32}",
                fixture.project_id,
                OWNER_ID,
                snapshot.note.note_id,
                datetime.now(UTC).isoformat(),
            ),
        )
    with pytest.raises(NoteConflictError):
        fixture.service.read_note("memo", snapshot.note.note_id)
    with pytest.raises(NoteConflictError):
        fixture.service.validate_project_state()


@pytest.mark.parametrize(
    ("event_type", "subject_type"),
    (
        (sqlite3.Binary(b" MEMO.CREATED "), "unrelated"),
        ("unrelated", sqlite3.Binary(b" ANNOTATION ")),
    ),
)
def test_binary_or_padded_note_audit_markers_never_evade_project_validation(
    tmp_path: Path,
    event_type: object,
    subject_type: object,
) -> None:
    fixture = _create_fixture(tmp_path)
    _create_study_memo(fixture)
    with fixture.database.transaction() as connection:
        connection.execute(
            """
            insert into qualitative_audit_events (
              event_id, project_id, actor_id, event_type,
              subject_type, subject_id, metadata_json, created_at
            ) values (?, ?, ?, ?, ?, 'unrelated_audit', '{}', ?)
            """,
            (
                f"qae_{'d' * 32}",
                fixture.project_id,
                OWNER_ID,
                event_type,
                subject_type,
                datetime.now(UTC).isoformat(),
            ),
        )
    with pytest.raises(NoteConflictError):
        fixture.service.validate_project_state()


def test_audit_failures_roll_back_create_revise_and_remove(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _create_fixture(tmp_path)

    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("injected audit failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(NoteService, "_append_audit", fail_audit)
        with pytest.raises(RuntimeError):
            _create_study_memo(fixture)
    with sqlite3.connect(fixture.database.db_path) as connection:
        assert connection.execute("select count(*) from qualitative_notes").fetchone() == (0,)

    initial = _create_study_memo(fixture)
    with monkeypatch.context() as scoped:
        scoped.setattr(NoteService, "_append_audit", fail_audit)
        with pytest.raises(RuntimeError):
            fixture.service.revise_note(
                note_kind="memo",
                note_id=initial.note.note_id,
                researcher_id=OWNER_ID,
                expected_revision_number=1,
                title="Revision",
                body="Revision body.",
            )
        with pytest.raises(RuntimeError):
            fixture.service.remove_note(
                note_kind="memo",
                note_id=initial.note.note_id,
                researcher_id=OWNER_ID,
            )
    assert fixture.service.read_note("memo", initial.note.note_id) == initial
    assert len(_note_audits(fixture, initial.note.note_id)) == 1


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("title", "  padded stored title  "),
        ("body", " \n "),
    ),
)
def test_direct_sql_invalid_revision_content_is_rejected_on_read(
    tmp_path: Path,
    field_name: str,
    value: str,
) -> None:
    fixture = _create_fixture(tmp_path)
    snapshot = _create_study_memo(fixture)
    with fixture.database.transaction() as connection:
        trigger_sql = connection.execute(
            """
            select sql from sqlite_master
            where type = 'trigger' and name = 'prevent_qualitative_note_revision_update'
            """
        ).fetchone()[0]
        connection.execute("drop trigger prevent_qualitative_note_revision_update")
        connection.execute(
            f"update qualitative_note_revisions set {field_name} = ? where note_id = ?",
            (value, snapshot.note.note_id),
        )
        connection.execute(trigger_sql)
    with pytest.raises(NoteConflictError):
        fixture.service.read_note("memo", snapshot.note.note_id)


@pytest.mark.parametrize(
    "corruption",
    (
        "wrong_integer_storage",
        "padded_revision_id",
        "kind_prefix_disagreement",
        "invalid_timestamp",
        "naive_timestamp",
        "revision_gap",
        "impossible_tombstone",
    ),
)
def test_strict_stored_corruption_matrix_rejects_read_and_project_validation(
    tmp_path: Path,
    corruption: str,
) -> None:
    fixture = _create_fixture(tmp_path)
    route_kind = "memo"
    snapshot = _create_study_memo(fixture)

    if corruption == "wrong_integer_storage":
        _tamper_immutable_row(
            fixture,
            trigger_name="prevent_qualitative_note_revision_update",
            statement=(
                "update qualitative_note_revisions "
                "set revision_number = cast(x'31' as blob) where note_id = ?"
            ),
            parameters=(snapshot.note.note_id,),
            ignore_checks=True,
        )
    elif corruption == "padded_revision_id":
        _tamper_immutable_row(
            fixture,
            trigger_name="prevent_qualitative_note_revision_update",
            statement=(
                "update qualitative_note_revisions "
                "set note_revision_id = ? where note_id = ?"
            ),
            parameters=(
                f" {snapshot.current_revision.note_revision_id} ",
                snapshot.note.note_id,
            ),
        )
    elif corruption == "kind_prefix_disagreement":
        route_kind = "annotation"
        snapshot = fixture.service.create_note(
            note_kind="annotation",
            researcher_id=OWNER_ID,
            title="",
            body="Annotation body.",
            target={"kind": "study"},
        )
        with fixture.database.transaction() as connection:
            trigger_names = (
                "restrict_qualitative_note_update",
                "prevent_qualitative_note_revision_update",
            )
            trigger_sql = tuple(
                connection.execute(
                    """
                    select sql from sqlite_master
                    where type = 'trigger' and name = ?
                    """,
                    (trigger_name,),
                ).fetchone()[0]
                for trigger_name in trigger_names
            )
            for trigger_name in trigger_names:
                connection.execute(f"drop trigger {trigger_name}")
            connection.execute(
                "update qualitative_notes set note_kind = 'memo' where note_id = ?",
                (snapshot.note.note_id,),
            )
            connection.execute(
                """
                update qualitative_note_revisions set title = 'Now a memo'
                where note_id = ?
                """,
                (snapshot.note.note_id,),
            )
            for statement in trigger_sql:
                connection.execute(statement)
    elif corruption in {"invalid_timestamp", "naive_timestamp"}:
        timestamp = (
            "not-a-timestamp"
            if corruption == "invalid_timestamp"
            else "2026-08-01T12:00:00"
        )
        _tamper_immutable_row(
            fixture,
            trigger_name="prevent_qualitative_note_revision_update",
            statement=(
                "update qualitative_note_revisions set created_at = ? "
                "where note_id = ?"
            ),
            parameters=(timestamp, snapshot.note.note_id),
        )
    elif corruption == "revision_gap":
        fixture.service.revise_note(
            note_kind="memo",
            note_id=snapshot.note.note_id,
            researcher_id=OWNER_ID,
            expected_revision_number=1,
            title="Second revision",
            body="Second revision body.",
        )
        _tamper_immutable_row(
            fixture,
            trigger_name="prevent_qualitative_note_revision_update",
            statement=(
                "update qualitative_note_revisions set revision_number = 3 "
                "where note_id = ? and revision_number = 2"
            ),
            parameters=(snapshot.note.note_id,),
        )
    else:
        _tamper_immutable_row(
            fixture,
            trigger_name="restrict_qualitative_note_update",
            statement=(
                "update qualitative_notes set removed_by = ?, removed_at = ? "
                "where note_id = ?"
            ),
            parameters=(
                OWNER_ID,
                "2000-01-01T00:00:00+00:00",
                snapshot.note.note_id,
            ),
        )

    with pytest.raises(NoteConflictError):
        fixture.service.read_note(route_kind, snapshot.note.note_id)
    with pytest.raises(NoteConflictError):
        fixture.service.validate_project_state()


def test_exact_utf8_project_content_budget_is_atomic_and_strict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _create_fixture(tmp_path)
    monkeypatch.setattr(notes_module, "_MAX_PROJECT_CONTENT_BYTES", 6)
    initial = _create_study_memo(fixture, title="A", body="😊")
    with pytest.raises(NoteValidationError):
        fixture.service.revise_note(
            note_kind="memo",
            note_id=initial.note.note_id,
            researcher_id=OWNER_ID,
            expected_revision_number=1,
            title="B",
            body="x",
        )
    assert fixture.service.read_note("memo", initial.note.note_id) == initial
    monkeypatch.setattr(notes_module, "_MAX_PROJECT_CONTENT_BYTES", 4)
    with pytest.raises(NoteConflictError):
        fixture.service.read_note("memo", initial.note.note_id)


def test_external_damage_blocks_reads_and_revisions_but_not_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _create_fixture(tmp_path)
    snapshot = fixture.service.create_note(
        note_kind="annotation",
        researcher_id=OWNER_ID,
        title="",
        body="Source context.",
        target={"kind": "source", "project_source_id": fixture.project_source_id},
    )

    def missing_source(_catalog, _project_source_id):
        raise FileNotFoundError

    monkeypatch.setattr(EvidenceCatalog, "source_history", missing_source)
    with pytest.raises(NoteConflictError):
        fixture.service.read_note("annotation", snapshot.note.note_id)
    with pytest.raises(NoteConflictError):
        fixture.service.revise_note(
            note_kind="annotation",
            note_id=snapshot.note.note_id,
            researcher_id=OWNER_ID,
            expected_revision_number=1,
            title="",
            body="Revised source context.",
        )
    removed = fixture.service.remove_note(
        note_kind="annotation",
        note_id=snapshot.note.note_id,
        researcher_id=OWNER_ID,
    )
    assert removed.note.removed_by == OWNER_ID
