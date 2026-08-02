import json
import sqlite3
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import pytest

import backend.qualitative.cases as cases_module
from backend.qualitative.cases import (
    CaseConflictError,
    CaseNotFoundError,
    CaseService,
    CaseValidationError,
)
from backend.qualitative.database import QualitativeProjectDatabase
from backend.storage.evidence_catalog import EvidenceCatalog, EvidenceImportRecord
from backend.storage.sqlite_migrations import SchemaCompatibilityError
from backend.storage.study_store import StudyWorkspaceStore


RESEARCHER_ID = "res_case_owner"
SECOND_RESEARCHER_ID = "res_case_second"


class _IntSubclass(int):
    pass


def _create_service(
    root: Path,
) -> tuple[str, QualitativeProjectDatabase, CaseService]:
    study = StudyWorkspaceStore(root).create_study({"name": "Case Study"})
    database = QualitativeProjectDatabase(root, study.id)
    database.initialize(
        researcher_id=RESEARCHER_ID,
        researcher_name="Case Owner",
    )
    return study.id, database, CaseService(root, study.id)


def _create_case(service: CaseService, label: str = "P1"):
    return service.create_case(
        researcher_id=RESEARCHER_ID,
        case_kind="participant",
        label=label,
        description="Exact description",
    )


def _create_definition(
    service: CaseService,
    *,
    key: str,
    value_type: str,
    allowed_values=(),
    required: bool = False,
):
    return service.create_attribute_definition(
        researcher_id=RESEARCHER_ID,
        attribute_key=key,
        label=f"Label {key}",
        value_type=value_type,
        allowed_values=allowed_values,
        required=required,
    )


def _record_source(
    root: Path,
    *,
    project_source_id: str,
    workspace_id: str,
    source_filename: str = "sensitive-interview.wav",
) -> None:
    EvidenceCatalog(root).record_import(
        EvidenceImportRecord(
            import_id=f"import_{project_source_id}",
            run_id=f"run_{project_source_id}",
            pipeline="test-pipeline",
            source_id=f"source_{project_source_id}",
            source_filename=source_filename,
            source_media_type="audio/wav",
            source_blob_sha256="a" * 64,
            transcript_revision_id=f"revision_{project_source_id}",
            transcript_sha256="b" * 64,
            imported_at=datetime.now(UTC).isoformat(),
            project_source_id=project_source_id,
            workspace_id=workspace_id,
        )
    )


def _audit_rows(database: QualitativeProjectDatabase) -> list[sqlite3.Row]:
    with sqlite3.connect(database.db_path) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            select actor_id, event_type, subject_type, subject_id, metadata_json
            from qualitative_audit_events
            where event_type != 'qualitative.project.initialized'
            order by rowid
            """
        ).fetchall()


def _domain_and_audit_snapshot(
    database: QualitativeProjectDatabase,
) -> dict[str, tuple[tuple[object, ...], ...]]:
    tables = (
        "cases",
        "attribute_definitions",
        "case_attribute_values",
        "source_case_links",
        "qualitative_audit_events",
    )
    with sqlite3.connect(database.db_path) as connection:
        return {
            table: tuple(connection.execute(f"select * from {table} order by rowid"))
            for table in tables
        }


def _add_second_researcher(database: QualitativeProjectDatabase) -> None:
    now = datetime.now(UTC).isoformat()
    with database.transaction() as connection:
        connection.execute(
            """
            insert into researchers (
              researcher_id, project_id, display_name, role,
              active, created_at, updated_at
            ) values (?, ?, 'Second Researcher', 'researcher', 1, ?, ?)
            """,
            (SECOND_RESEARCHER_ID, database.project_id, now, now),
        )


def test_cases_are_immutable_and_read_in_deterministic_order(tmp_path: Path) -> None:
    project_id, database, service = _create_service(tmp_path)
    created = {
        kind: service.create_case(
            researcher_id=RESEARCHER_ID,
            case_kind=kind,
            label=f"  {kind.title()}  ",
            description=f"description for {kind}",
        )
        for kind in ("timepoint", "session", "participant", "dyad", "condition")
    }

    updated = service.update_case(
        researcher_id=RESEARCHER_ID,
        case_id=created["participant"].case_id,
        case_kind="participant",
        label="  alpha  ",
        description="  description whitespace stays  ",
    )
    snapshot = service.read_case(updated.case_id)
    listed = service.list_cases()

    assert snapshot.case == updated
    assert snapshot.case.project_id == project_id
    assert snapshot.case.label == "alpha"
    assert snapshot.case.description == "  description whitespace stays  "
    assert snapshot.attribute_values == ()
    assert snapshot.project_source_ids == ()
    assert [record.case_kind for record in listed] == [
        "condition",
        "dyad",
        "participant",
        "session",
        "timepoint",
    ]
    with pytest.raises(FrozenInstanceError):
        snapshot.case.label = "changed"

    rows = _audit_rows(database)
    assert [row["event_type"] for row in rows] == [
        "case.created",
        "case.created",
        "case.created",
        "case.created",
        "case.created",
        "case.updated",
    ]
    assert json.loads(rows[-1]["metadata_json"]) == {}
    assert all(row["actor_id"] == RESEARCHER_ID for row in rows)


def test_case_input_missing_records_and_researcher_state_fail_visibly(
    tmp_path: Path,
) -> None:
    with pytest.raises(CaseValidationError, match="project id"):
        CaseService(tmp_path, "../escape")

    missing = CaseService(tmp_path, "missing-study")
    with pytest.raises(CaseNotFoundError, match="project"):
        missing.list_cases()

    uninitialized = StudyWorkspaceStore(tmp_path).create_study(
        {"name": "Uninitialized"}
    )
    with pytest.raises(CaseNotFoundError, match="not initialized"):
        CaseService(tmp_path, uninitialized.id).list_cases()

    _, database, service = _create_service(tmp_path)
    with pytest.raises(CaseValidationError, match="case_kind"):
        service.create_case(
            researcher_id=RESEARCHER_ID,
            case_kind="person",
            label="P1",
        )
    with pytest.raises(CaseValidationError, match="label"):
        service.create_case(
            researcher_id=RESEARCHER_ID,
            case_kind="participant",
            label="   ",
        )
    with pytest.raises(CaseNotFoundError, match="Researcher"):
        service.create_case(
            researcher_id="res_missing",
            case_kind="participant",
            label="P1",
        )
    with pytest.raises(CaseNotFoundError, match="Case"):
        service.read_case("cas_missing")

    with database.transaction() as connection:
        connection.execute(
            "update researchers set active = 0 where researcher_id = ?",
            (RESEARCHER_ID,),
        )
    with pytest.raises(CaseConflictError, match="inactive"):
        service.create_case(
            researcher_id=RESEARCHER_ID,
            case_kind="participant",
            label="P2",
        )


def test_all_typed_values_round_trip_replace_clear_and_order(tmp_path: Path) -> None:
    _, database, service = _create_service(tmp_path)
    case = _create_case(service)
    definitions = {
        "z_text": _create_definition(service, key="z_text", value_type="text"),
        "number": _create_definition(service, key="number", value_type="number"),
        "boolean": _create_definition(
            service,
            key="boolean",
            value_type="boolean",
        ),
        "date": _create_definition(
            service,
            key="date",
            value_type="date",
            required=True,
        ),
        "category": _create_definition(
            service,
            key="category",
            value_type="categorical",
            allowed_values=("  Alpha  ", "Beta Value"),
        ),
    }
    inputs = {
        "z_text": "  exact text  ",
        "number": 7,
        "boolean": False,
        "date": "2026-08-01",
        "category": "Alpha",
    }
    created = {
        key: service.set_attribute_value(
            researcher_id=RESEARCHER_ID,
            case_id=case.case_id,
            attribute_definition_id=definition.attribute_definition_id,
            value=inputs[key],
        )
        for key, definition in definitions.items()
    }

    assert definitions["category"].allowed_values == ("Alpha", "Beta Value")
    assert definitions["date"].required is True
    assert {key: record.value for key, record in created.items()} == inputs
    assert [value.attribute_key for value in service.read_case(case.case_id).attribute_values] == [
        "boolean",
        "category",
        "date",
        "number",
        "z_text",
    ]
    focused = service.read_attribute_value(
        case_id=case.case_id,
        attribute_definition_id=definitions["number"].attribute_definition_id,
    )
    replaced = service.set_attribute_value(
        researcher_id=RESEARCHER_ID,
        case_id=case.case_id,
        attribute_definition_id=definitions["number"].attribute_definition_id,
        value=1.25,
    )
    assert focused.value == 7
    assert replaced.value == 1.25
    assert type(replaced.value) is float
    assert replaced.created_at == focused.created_at

    assert (
        service.clear_attribute_value(
            researcher_id=RESEARCHER_ID,
            case_id=case.case_id,
            attribute_definition_id=definitions["date"].attribute_definition_id,
        )
        is None
    )
    with pytest.raises(CaseNotFoundError, match="value"):
        service.read_attribute_value(
            case_id=case.case_id,
            attribute_definition_id=definitions["date"].attribute_definition_id,
        )
    with pytest.raises(CaseNotFoundError, match="value"):
        service.clear_attribute_value(
            researcher_id=RESEARCHER_ID,
            case_id=case.case_id,
            attribute_definition_id=definitions["date"].attribute_definition_id,
        )

    events = [row["event_type"] for row in _audit_rows(database)]
    assert events.count("case.attribute_value.set") == 5
    assert events.count("case.attribute_value.replaced") == 1
    assert events.count("case.attribute_value.cleared") == 1


def test_huge_integer_round_trips_without_float_conversion(tmp_path: Path) -> None:
    _, _, service = _create_service(tmp_path)
    case = _create_case(service)
    definition = _create_definition(service, key="huge", value_type="number")
    huge_integer = 10**1000

    stored = service.set_attribute_value(
        researcher_id=RESEARCHER_ID,
        case_id=case.case_id,
        attribute_definition_id=definition.attribute_definition_id,
        value=huge_integer,
    )
    read = service.read_attribute_value(
        case_id=case.case_id,
        attribute_definition_id=definition.attribute_definition_id,
    )

    assert stored.value == huge_integer
    assert read.value == huge_integer
    assert type(read.value) is int


@pytest.mark.parametrize(
    ("value_type", "allowed_values", "required", "message"),
    [
        ("unknown", (), False, "value_type"),
        ("categorical", (), False, "require allowed_values"),
        ("categorical", ("A", " A "), False, "unique"),
        ("categorical", (" ",), False, "empty"),
        ("text", ("A",), False, "only for categorical"),
        ("text", "not-a-list", False, "sequence"),
        ("text", (), 1, "boolean"),
    ],
)
def test_invalid_attribute_definitions_are_rejected(
    tmp_path: Path,
    value_type,
    allowed_values,
    required,
    message: str,
) -> None:
    _, _, service = _create_service(tmp_path)
    with pytest.raises(CaseValidationError, match=message):
        service.create_attribute_definition(
            researcher_id=RESEARCHER_ID,
            attribute_key="attribute",
            label="Attribute",
            value_type=value_type,
            allowed_values=allowed_values,
            required=required,
        )


def test_definition_keys_are_unique_and_definitions_are_create_only(
    tmp_path: Path,
) -> None:
    _, _, service = _create_service(tmp_path)
    first = _create_definition(
        service,
        key="  consent_date  ",
        value_type="date",
    )
    second = _create_definition(service, key="Alpha", value_type="text")
    third = _create_definition(service, key="alpha", value_type="boolean")

    with pytest.raises(CaseConflictError, match="already exists"):
        _create_definition(service, key="consent_date", value_type="date")
    assert first.attribute_key == "consent_date"
    assert [definition.attribute_key for definition in service.list_attribute_definitions()] == [
        "Alpha",
        "alpha",
        "consent_date",
    ]
    assert second.attribute_definition_id != third.attribute_definition_id
    assert not hasattr(service, "update_attribute_definition")
    assert not hasattr(service, "delete_attribute_definition")


@pytest.mark.parametrize(
    ("value_type", "allowed_values", "value"),
    [
        ("text", (), None),
        ("number", (), True),
        ("number", (), "1"),
        ("number", (), _IntSubclass(1)),
        ("number", (), float("nan")),
        ("number", (), float("inf")),
        ("boolean", (), 1),
        ("boolean", (), "true"),
        ("date", (), "2026-02-30"),
        ("date", (), "20260801"),
        ("categorical", ("Alpha",), "alpha"),
        ("categorical", ("Alpha",), None),
    ],
)
def test_wrong_typed_attribute_values_are_rejected(
    tmp_path: Path,
    value_type: str,
    allowed_values,
    value,
) -> None:
    _, _, service = _create_service(tmp_path)
    case = _create_case(service)
    definition = _create_definition(
        service,
        key="attribute",
        value_type=value_type,
        allowed_values=allowed_values,
    )
    with pytest.raises(CaseValidationError):
        service.set_attribute_value(
            researcher_id=RESEARCHER_ID,
            case_id=case.case_id,
            attribute_definition_id=definition.attribute_definition_id,
            value=value,
        )


def test_each_repeated_value_set_is_an_attributed_mutation(tmp_path: Path) -> None:
    _, database, service = _create_service(tmp_path)
    case = _create_case(service)
    definition = _create_definition(service, key="note", value_type="text")
    first = service.set_attribute_value(
        researcher_id=RESEARCHER_ID,
        case_id=case.case_id,
        attribute_definition_id=definition.attribute_definition_id,
        value="same",
    )
    second = service.set_attribute_value(
        researcher_id=RESEARCHER_ID,
        case_id=case.case_id,
        attribute_definition_id=definition.attribute_definition_id,
        value="same",
    )

    assert first.value == second.value == "same"
    rows = _audit_rows(database)[-2:]
    assert [row["event_type"] for row in rows] == [
        "case.attribute_value.set",
        "case.attribute_value.replaced",
    ]
    expected_metadata = {
        "attribute_definition_id": definition.attribute_definition_id,
        "value_type": "text",
    }
    assert [json.loads(row["metadata_json"]) for row in rows] == [
        expected_metadata,
        expected_metadata,
    ]
    assert "same" not in rows[0]["metadata_json"]
    assert "note" not in rows[0]["metadata_json"]


@pytest.mark.parametrize(
    "mutation",
    [
        "create_case",
        "update_case",
        "create_definition",
        "value_insert",
        "value_replace",
        "value_clear",
        "source_link",
        "source_unlink",
    ],
)
def test_audit_failure_rolls_back_every_paired_domain_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    project_id, database, service = _create_service(tmp_path)
    case = None
    definition = None
    source_id = "psrc_audit_rollback"

    if mutation in {
        "update_case",
        "value_insert",
        "value_replace",
        "value_clear",
        "source_link",
        "source_unlink",
    }:
        case = _create_case(service)
    if mutation in {"value_insert", "value_replace", "value_clear"}:
        definition = _create_definition(service, key="rollback", value_type="text")
    if mutation in {"value_replace", "value_clear"}:
        service.set_attribute_value(
            researcher_id=RESEARCHER_ID,
            case_id=case.case_id,
            attribute_definition_id=definition.attribute_definition_id,
            value="before",
        )
    if mutation in {"source_link", "source_unlink"}:
        _record_source(
            tmp_path,
            project_source_id=source_id,
            workspace_id=project_id,
        )
    if mutation == "source_unlink":
        service.link_source(
            researcher_id=RESEARCHER_ID,
            case_id=case.case_id,
            project_source_id=source_id,
        )

    before = _domain_and_audit_snapshot(database)

    def fail_audit(*args, **kwargs):
        raise sqlite3.IntegrityError("UNIQUE constraint failed")

    monkeypatch.setattr(service, "_append_audit", fail_audit)
    with pytest.raises(CaseConflictError, match="conflicts"):
        if mutation == "create_case":
            _create_case(service)
        elif mutation == "update_case":
            service.update_case(
                researcher_id=RESEARCHER_ID,
                case_id=case.case_id,
                case_kind="session",
                label="Changed",
                description="changed",
            )
        elif mutation == "create_definition":
            _create_definition(service, key="rollback", value_type="text")
        elif mutation in {"value_insert", "value_replace"}:
            service.set_attribute_value(
                researcher_id=RESEARCHER_ID,
                case_id=case.case_id,
                attribute_definition_id=definition.attribute_definition_id,
                value="after",
            )
        elif mutation == "value_clear":
            service.clear_attribute_value(
                researcher_id=RESEARCHER_ID,
                case_id=case.case_id,
                attribute_definition_id=definition.attribute_definition_id,
            )
        elif mutation == "source_link":
            service.link_source(
                researcher_id=RESEARCHER_ID,
                case_id=case.case_id,
                project_source_id=source_id,
            )
        else:
            service.unlink_source(
                researcher_id=RESEARCHER_ID,
                case_id=case.case_id,
                project_source_id=source_id,
            )

    assert _domain_and_audit_snapshot(database) == before


def test_source_link_retry_actor_conflict_unlink_and_audit_safety(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, database, service = _create_service(tmp_path)
    case = _create_case(service)
    source_id = "psrc_interview_1"
    _record_source(
        tmp_path,
        project_source_id=source_id,
        workspace_id=project_id,
    )
    _add_second_researcher(database)

    link = service.link_source(
        researcher_id=RESEARCHER_ID,
        case_id=case.case_id,
        project_source_id=source_id,
    )
    retry = service.link_source(
        researcher_id=RESEARCHER_ID,
        case_id=case.case_id,
        project_source_id=source_id,
    )
    with pytest.raises(CaseConflictError, match="different actor"):
        service.link_source(
            researcher_id=SECOND_RESEARCHER_ID,
            case_id=case.case_id,
            project_source_id=source_id,
        )

    assert retry == link
    assert link.project_id == project_id
    assert link.project_source_id == source_id
    assert set(link.__dataclass_fields__) == {
        "project_id",
        "project_source_id",
        "case_id",
        "linked_by",
        "created_at",
    }
    linked_events = [
        row for row in _audit_rows(database) if row["event_type"] == "case.source.linked"
    ]
    assert len(linked_events) == 1
    assert json.loads(linked_events[0]["metadata_json"]) == {
        "project_source_id": source_id
    }
    assert "sensitive-interview.wav" not in linked_events[0]["metadata_json"]

    def catalog_must_not_be_read(*args, **kwargs):
        raise AssertionError("unlink consulted the evidence catalog")

    monkeypatch.setattr(EvidenceCatalog, "source_history", catalog_must_not_be_read)
    assert (
        service.unlink_source(
            researcher_id=RESEARCHER_ID,
            case_id=case.case_id,
            project_source_id=source_id,
        )
        is None
    )
    with pytest.raises(CaseNotFoundError, match="link"):
        service.unlink_source(
            researcher_id=RESEARCHER_ID,
            case_id=case.case_id,
            project_source_id=source_id,
        )


def test_source_links_reject_invalid_missing_and_foreign_sources(tmp_path: Path) -> None:
    project_id, _, service = _create_service(tmp_path)
    case = _create_case(service)

    for invalid in ("", " source", "source "):
        with pytest.raises(CaseValidationError, match="project_source_id"):
            service.link_source(
                researcher_id=RESEARCHER_ID,
                case_id=case.case_id,
                project_source_id=invalid,
            )
    with pytest.raises(CaseNotFoundError, match="source"):
        service.link_source(
            researcher_id=RESEARCHER_ID,
            case_id=case.case_id,
            project_source_id="psrc_missing",
        )

    _record_source(
        tmp_path,
        project_source_id="psrc_foreign",
        workspace_id=f"foreign-{project_id}",
    )
    with pytest.raises(CaseConflictError, match="different project"):
        service.link_source(
            researcher_id=RESEARCHER_ID,
            case_id=case.case_id,
            project_source_id="psrc_foreign",
        )


def test_source_catalog_lock_is_released_before_qualitative_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, _, service = _create_service(tmp_path)
    case = _create_case(service)
    source_id = "psrc_lock_order"
    _record_source(
        tmp_path,
        project_source_id=source_id,
        workspace_id=project_id,
    )
    state = {"workspace_locked": False}
    original_transaction = service.database.transaction

    @contextmanager
    def tracked_workspace_lock(root):
        assert state["workspace_locked"] is False
        state["workspace_locked"] = True
        try:
            yield
        finally:
            state["workspace_locked"] = False

    @contextmanager
    def tracked_transaction():
        assert state["workspace_locked"] is False
        with original_transaction() as connection:
            yield connection

    monkeypatch.setattr(cases_module, "workspace_mutation_lock", tracked_workspace_lock)
    monkeypatch.setattr(service.database, "transaction", tracked_transaction)

    service.link_source(
        researcher_id=RESEARCHER_ID,
        case_id=case.case_id,
        project_source_id=source_id,
    )


def test_existing_non_regular_evidence_catalog_is_rejected(tmp_path: Path) -> None:
    _, _, service = _create_service(tmp_path)
    case = _create_case(service)
    (tmp_path / "evidence.sqlite3").mkdir()

    with pytest.raises(CaseConflictError, match="storage"):
        service.link_source(
            researcher_id=RESEARCHER_ID,
            case_id=case.case_id,
            project_source_id="psrc_any",
        )


def test_workspace_lock_enter_failure_is_content_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, service = _create_service(tmp_path)
    case = _create_case(service)

    @contextmanager
    def failing_workspace_lock(root):
        raise OSError("/sensitive/workspace/lock/path")
        yield

    monkeypatch.setattr(cases_module, "workspace_mutation_lock", failing_workspace_lock)

    with pytest.raises(
        CaseConflictError,
        match="Evidence source storage is unavailable or invalid",
    ) as error:
        service.link_source(
            researcher_id=RESEARCHER_ID,
            case_id=case.case_id,
            project_source_id="psrc_lock_failure",
        )
    assert "sensitive" not in str(error.value)


def test_padded_actor_is_canonical_for_mutations_audits_and_link_retry(
    tmp_path: Path,
) -> None:
    project_id, database, service = _create_service(tmp_path)
    padded_actor = f"  {RESEARCHER_ID}  "
    case = service.create_case(
        researcher_id=padded_actor,
        case_kind="participant",
        label="Padded actor case",
    )
    updated = service.update_case(
        researcher_id=padded_actor,
        case_id=case.case_id,
        case_kind="participant",
        label="Updated padded actor case",
        description="updated",
    )
    definition = service.create_attribute_definition(
        researcher_id=padded_actor,
        attribute_key="actor_note",
        label="Actor note",
        value_type="text",
    )
    value = service.set_attribute_value(
        researcher_id=padded_actor,
        case_id=case.case_id,
        attribute_definition_id=definition.attribute_definition_id,
        value="canonical",
    )
    source_id = "psrc_padded_actor"
    _record_source(
        tmp_path,
        project_source_id=source_id,
        workspace_id=project_id,
    )
    link = service.link_source(
        researcher_id=padded_actor,
        case_id=case.case_id,
        project_source_id=source_id,
    )
    retry = service.link_source(
        researcher_id=padded_actor,
        case_id=case.case_id,
        project_source_id=source_id,
    )

    assert case.created_by == RESEARCHER_ID
    assert updated.updated_by == RESEARCHER_ID
    assert definition.created_by == definition.updated_by == RESEARCHER_ID
    assert value.updated_by == RESEARCHER_ID
    assert link.linked_by == RESEARCHER_ID
    assert retry == link
    audit_rows = _audit_rows(database)
    assert all(row["actor_id"] == RESEARCHER_ID for row in audit_rows)
    assert [row["event_type"] for row in audit_rows].count("case.source.linked") == 1


@pytest.mark.parametrize(
    ("table", "column", "replacement", "reader"),
    [
        ("cases", "label", " padded ", "cases"),
        (
            "attribute_definitions",
            "allowed_values_json",
            '[" padded "]',
            "definitions",
        ),
        (
            "attribute_definitions",
            "allowed_values_json",
            "9" * 5000,
            "definitions",
        ),
        ("case_attribute_values", "value_json", "NaN", "value"),
        (
            "case_attribute_values",
            "value_json",
            "[" * 1100 + "0" + "]" * 1100,
            "value",
        ),
        ("source_case_links", "project_source_id", " bad ", "case"),
    ],
)
def test_malformed_stored_case_domain_rows_fail_visibly(
    tmp_path: Path,
    table: str,
    column: str,
    replacement: str,
    reader: str,
) -> None:
    project_id, database, service = _create_service(tmp_path)
    case = _create_case(service)
    definition = _create_definition(service, key="note", value_type="text")
    service.set_attribute_value(
        researcher_id=RESEARCHER_ID,
        case_id=case.case_id,
        attribute_definition_id=definition.attribute_definition_id,
        value="valid",
    )
    source_id = "psrc_tamper"
    _record_source(
        tmp_path,
        project_source_id=source_id,
        workspace_id=project_id,
    )
    service.link_source(
        researcher_id=RESEARCHER_ID,
        case_id=case.case_id,
        project_source_id=source_id,
    )

    with sqlite3.connect(database.db_path) as connection:
        connection.execute(f"update {table} set {column} = ?", (replacement,))

    with pytest.raises(CaseConflictError):
        if reader == "cases":
            service.list_cases()
        elif reader == "definitions":
            service.list_attribute_definitions()
        elif reader == "value":
            service.read_attribute_value(
                case_id=case.case_id,
                attribute_definition_id=definition.attribute_definition_id,
            )
        else:
            service.read_case(case.case_id)


def test_validate_project_state_checks_rows_sources_and_lock_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, _, service = _create_service(tmp_path)
    case = _create_case(service)
    definition = _create_definition(service, key="number", value_type="number")
    service.set_attribute_value(
        researcher_id=RESEARCHER_ID,
        case_id=case.case_id,
        attribute_definition_id=definition.attribute_definition_id,
        value=3.5,
    )
    source_id = "psrc_restore"
    _record_source(
        tmp_path,
        project_source_id=source_id,
        workspace_id=project_id,
    )
    service.link_source(
        researcher_id=RESEARCHER_ID,
        case_id=case.case_id,
        project_source_id=source_id,
    )
    state = {"reading": False}
    original_read = service.database.read
    original_lock = cases_module.workspace_mutation_lock

    @contextmanager
    def tracked_read():
        state["reading"] = True
        try:
            with original_read() as connection:
                yield connection
        finally:
            state["reading"] = False

    @contextmanager
    def tracked_workspace_lock(root):
        assert state["reading"] is False
        with original_lock(root):
            yield

    monkeypatch.setattr(service.database, "read", tracked_read)
    monkeypatch.setattr(cases_module, "workspace_mutation_lock", tracked_workspace_lock)

    assert service.validate_project_state() is None


def test_validate_project_state_allows_schema_only_database(tmp_path: Path) -> None:
    study = StudyWorkspaceStore(tmp_path).create_study({"name": "Legacy Study"})
    service = CaseService(tmp_path, study.id)

    assert service.validate_project_state() is None


def test_validate_project_state_preserves_newer_schema_error(tmp_path: Path) -> None:
    _, database, service = _create_service(tmp_path)
    with sqlite3.connect(database.db_path) as connection:
        connection.execute("pragma user_version = 5")

    with pytest.raises(SchemaCompatibilityError, match="newer"):
        service.validate_project_state()


def test_validate_project_state_rejects_unavailable_stored_source(tmp_path: Path) -> None:
    project_id, _, service = _create_service(tmp_path)
    case = _create_case(service)
    source_id = "psrc_removed_from_catalog"
    _record_source(
        tmp_path,
        project_source_id=source_id,
        workspace_id=project_id,
    )
    service.link_source(
        researcher_id=RESEARCHER_ID,
        case_id=case.case_id,
        project_source_id=source_id,
    )
    with sqlite3.connect(tmp_path / "evidence.sqlite3") as connection:
        connection.execute("pragma foreign_keys = off")
        connection.execute(
            "delete from source_imports where project_source_id = ?",
            (source_id,),
        )
        connection.execute(
            "delete from source_revisions where project_source_id = ?",
            (source_id,),
        )
        connection.execute(
            "delete from project_sources where project_source_id = ?",
            (source_id,),
        )

    with pytest.raises(CaseConflictError, match="unavailable evidence"):
        service.validate_project_state()
