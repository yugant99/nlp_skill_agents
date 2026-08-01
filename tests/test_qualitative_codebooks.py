import json
import sqlite3
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from backend.qualitative.codebooks import (
    CodebookConflictError,
    CodebookImmutableError,
    CodebookNotFoundError,
    CodebookService,
    CodebookValidationError,
)
from backend.qualitative.database import QualitativeProjectDatabase
from backend.storage.sqlite_migrations import SchemaCompatibilityError
from backend.storage.study_store import StudyWorkspaceStore


RESEARCHER_ID = "res_codebook_owner"


def _create_service(
    root: Path,
    *,
    study_name: str = "Codebook Study",
) -> tuple[str, QualitativeProjectDatabase, CodebookService]:
    study = StudyWorkspaceStore(root).create_study({"name": study_name})
    database = QualitativeProjectDatabase(root, study.id)
    database.initialize(
        researcher_id=RESEARCHER_ID,
        researcher_name="Codebook Owner",
    )
    return study.id, database, CodebookService(root, study.id)


def _create_draft(service: CodebookService, title: str = "Interview Themes"):
    codebook = service.create_codebook(
        researcher_id=RESEARCHER_ID,
        title=title,
        description="Researcher-authored themes",
    )
    snapshot = service.create_draft(
        researcher_id=RESEARCHER_ID,
        codebook_id=codebook.codebook_id,
    )
    return codebook, snapshot


def _add_code(
    service: CodebookService,
    codebook_id: str,
    version_id: str,
    *,
    stable_code_key: str,
    label: str,
    parent_code_id: str | None = None,
    sort_order: int = 0,
):
    return service.add_code(
        researcher_id=RESEARCHER_ID,
        codebook_id=codebook_id,
        codebook_version_id=version_id,
        stable_code_key=stable_code_key,
        label=label,
        parent_code_id=parent_code_id,
        definition=f"Definition for {label}",
        inclusion_criteria=f"Include {label}",
        exclusion_criteria=f"Exclude {label}",
        examples=(f"Example {label}",),
        notes=f"Notes for {label}",
        color="#123456",
        sort_order=sort_order,
    )


def _replace_code(
    service: CodebookService,
    codebook_id: str,
    version_id: str,
    code_id: str,
    *,
    label: str,
    parent_code_id: str | None,
    sort_order: int = 0,
):
    return service.update_code(
        researcher_id=RESEARCHER_ID,
        codebook_id=codebook_id,
        codebook_version_id=version_id,
        code_id=code_id,
        label=label,
        parent_code_id=parent_code_id,
        definition=f"Updated {label}",
        inclusion_criteria="Included",
        exclusion_criteria="Excluded",
        examples=("Updated example",),
        notes="Updated notes",
        color="#654321",
        sort_order=sort_order,
    )


def _row_counts(database: QualitativeProjectDatabase) -> tuple[int, int, int, int]:
    with sqlite3.connect(database.db_path) as connection:
        return tuple(
            connection.execute(f"select count(*) from {table}").fetchone()[0]
            for table in (
                "codebooks",
                "codebook_versions",
                "codes",
                "qualitative_audit_events",
            )
        )


def _portable_document() -> dict[str, object]:
    return {
        "format": "nlp-skill-agents.codebook-version",
        "format_version": 1,
        "codebook": {"title": "Portable Themes", "description": "Portable"},
        "version": {"source_version_number": 3, "source_status": "frozen"},
        "codes": [
            {
                "stable_code_key": "child",
                "parent_stable_code_key": "root",
                "label": "Child",
                "definition": "Child definition",
                "inclusion_criteria": "Child inclusion",
                "exclusion_criteria": "Child exclusion",
                "examples": ["Child example"],
                "notes": "Child notes",
                "color": "#222222",
                "sort_order": 0,
            },
            {
                "stable_code_key": "root",
                "parent_stable_code_key": None,
                "label": "Root",
                "definition": "Root definition",
                "inclusion_criteria": "Root inclusion",
                "exclusion_criteria": "Root exclusion",
                "examples": ["Root example"],
                "notes": "Root notes",
                "color": "#111111",
                "sort_order": 0,
            },
        ],
    }


def test_create_list_and_read_return_immutable_deterministic_records(
    tmp_path: Path,
) -> None:
    project_id, _, service = _create_service(tmp_path)
    second = service.create_codebook(
        researcher_id=RESEARCHER_ID,
        title="  beta  ",
    )
    first, draft = _create_draft(service, title="Alpha")

    listed = service.list_codebooks()
    read = service.read_version(
        codebook_id=first.codebook_id,
        codebook_version_id=draft.version.codebook_version_id,
    )

    assert [record.title for record in listed] == ["Alpha", "beta"]
    assert listed[1].codebook_id == second.codebook_id
    assert read.codebook.project_id == project_id
    assert read.version.version_number == 1
    assert read.version.status == "draft"
    assert read.codes == ()
    with pytest.raises(FrozenInstanceError):
        read.version.status = "frozen"


def test_mutations_require_a_known_active_project_researcher(tmp_path: Path) -> None:
    _, database, service = _create_service(tmp_path)

    with pytest.raises(CodebookNotFoundError, match="Researcher"):
        service.create_codebook(researcher_id="res_missing", title="Themes")
    with database.transaction() as connection:
        connection.execute(
            "update researchers set active = 0 where researcher_id = ?",
            (RESEARCHER_ID,),
        )
    with pytest.raises(CodebookConflictError, match="inactive"):
        service.create_codebook(researcher_id=RESEARCHER_ID, title="Themes")
    assert _row_counts(database)[:3] == (0, 0, 0)


def test_missing_project_and_records_fail_visibly(tmp_path: Path) -> None:
    with pytest.raises(CodebookValidationError, match="project id"):
        CodebookService(tmp_path, "../escape")

    service = CodebookService(tmp_path, "missing-study")
    with pytest.raises(CodebookNotFoundError, match="project"):
        service.list_codebooks()

    uninitialized = StudyWorkspaceStore(tmp_path).create_study(
        {"name": "Uninitialized Qualitative Project"}
    )
    with pytest.raises(CodebookNotFoundError, match="not initialized"):
        CodebookService(tmp_path, uninitialized.id).list_codebooks()

    _, _, service = _create_service(tmp_path)
    with pytest.raises(CodebookNotFoundError, match="Codebook"):
        service.create_draft(
            researcher_id=RESEARCHER_ID,
            codebook_id="cbk_missing",
        )


def test_nested_hierarchy_reads_in_deterministic_depth_first_order(
    tmp_path: Path,
) -> None:
    _, _, service = _create_service(tmp_path)
    codebook, draft = _create_draft(service)
    version_id = draft.version.codebook_version_id
    later_root = _add_code(
        service,
        codebook.codebook_id,
        version_id,
        stable_code_key="later",
        label="Later",
        sort_order=2,
    )
    first_root = _add_code(
        service,
        codebook.codebook_id,
        version_id,
        stable_code_key="first",
        label="First",
        sort_order=0,
    )
    _add_code(
        service,
        codebook.codebook_id,
        version_id,
        stable_code_key="z-child",
        label="Zulu child",
        parent_code_id=first_root.code_id,
        sort_order=1,
    )
    alpha_child = _add_code(
        service,
        codebook.codebook_id,
        version_id,
        stable_code_key="a-child",
        label="Alpha child",
        parent_code_id=first_root.code_id,
        sort_order=1,
    )
    _add_code(
        service,
        codebook.codebook_id,
        version_id,
        stable_code_key="grandchild",
        label="Grandchild",
        parent_code_id=alpha_child.code_id,
    )

    snapshot = service.read_version(
        codebook_id=codebook.codebook_id,
        codebook_version_id=version_id,
    )

    assert [code.stable_code_key for code in snapshot.codes] == [
        "first",
        "a-child",
        "grandchild",
        "z-child",
        "later",
    ]
    assert snapshot.codes[-1].code_id == later_root.code_id


def test_duplicate_keys_blank_fields_and_invalid_values_are_rejected(
    tmp_path: Path,
) -> None:
    _, _, service = _create_service(tmp_path)
    codebook, draft = _create_draft(service)
    version_id = draft.version.codebook_version_id
    _add_code(
        service,
        codebook.codebook_id,
        version_id,
        stable_code_key="stable",
        label="Stable",
    )

    with pytest.raises(CodebookConflictError, match="stable_code_key"):
        _add_code(
            service,
            codebook.codebook_id,
            version_id,
            stable_code_key="stable",
            label="Duplicate",
        )
    for field, value in (("stable_code_key", "  "), ("label", "\t")):
        values = {"stable_code_key": "valid", "label": "Valid"}
        values[field] = value
        with pytest.raises(CodebookValidationError, match=field):
            _add_code(
                service,
                codebook.codebook_id,
                version_id,
                **values,
            )
    with pytest.raises(CodebookValidationError, match="sort_order"):
        service.add_code(
            researcher_id=RESEARCHER_ID,
            codebook_id=codebook.codebook_id,
            codebook_version_id=version_id,
            stable_code_key="invalid-order",
            label="Invalid order",
            sort_order=True,
        )
    with pytest.raises(CodebookValidationError, match="examples"):
        service.add_code(
            researcher_id=RESEARCHER_ID,
            codebook_id=codebook.codebook_id,
            codebook_version_id=version_id,
            stable_code_key="invalid-examples",
            label="Invalid examples",
            examples=("valid", 1),
        )


def test_missing_and_cross_version_parents_are_distinguished(tmp_path: Path) -> None:
    _, _, service = _create_service(tmp_path)
    first, first_draft = _create_draft(service, "First")
    second, second_draft = _create_draft(service, "Second")
    parent = _add_code(
        service,
        second.codebook_id,
        second_draft.version.codebook_version_id,
        stable_code_key="parent",
        label="Parent",
    )

    with pytest.raises(CodebookNotFoundError, match="Parent"):
        _add_code(
            service,
            first.codebook_id,
            first_draft.version.codebook_version_id,
            stable_code_key="missing",
            label="Missing",
            parent_code_id="cod_missing",
        )
    with pytest.raises(CodebookValidationError, match="same codebook version"):
        _add_code(
            service,
            first.codebook_id,
            first_draft.version.codebook_version_id,
            stable_code_key="cross-version",
            label="Cross version",
            parent_code_id=parent.code_id,
        )


def test_update_moves_codes_and_rejects_direct_and_indirect_cycles(
    tmp_path: Path,
) -> None:
    _, _, service = _create_service(tmp_path)
    codebook, draft = _create_draft(service)
    version_id = draft.version.codebook_version_id
    root = _add_code(
        service,
        codebook.codebook_id,
        version_id,
        stable_code_key="root",
        label="Root",
    )
    child = _add_code(
        service,
        codebook.codebook_id,
        version_id,
        stable_code_key="child",
        label="Child",
        parent_code_id=root.code_id,
    )
    grandchild = _add_code(
        service,
        codebook.codebook_id,
        version_id,
        stable_code_key="grandchild",
        label="Grandchild",
        parent_code_id=child.code_id,
    )

    with pytest.raises(CodebookValidationError, match="parent itself"):
        _replace_code(
            service,
            codebook.codebook_id,
            version_id,
            child.code_id,
            label="Child",
            parent_code_id=child.code_id,
        )
    with pytest.raises(CodebookValidationError, match="cycle"):
        _replace_code(
            service,
            codebook.codebook_id,
            version_id,
            root.code_id,
            label="Root",
            parent_code_id=grandchild.code_id,
        )

    moved = _replace_code(
        service,
        codebook.codebook_id,
        version_id,
        grandchild.code_id,
        label="Moved grandchild",
        parent_code_id=root.code_id,
        sort_order=2,
    )
    assert moved.parent_code_id == root.code_id
    assert moved.stable_code_key == "grandchild"
    assert moved.examples == ("Updated example",)


def test_freeze_is_atomic_immutable_and_idempotent(tmp_path: Path) -> None:
    _, database, service = _create_service(tmp_path)
    empty_book, empty_draft = _create_draft(service, "Empty")
    with pytest.raises(CodebookValidationError, match="empty"):
        service.freeze_version(
            researcher_id=RESEARCHER_ID,
            codebook_id=empty_book.codebook_id,
            codebook_version_id=empty_draft.version.codebook_version_id,
        )

    codebook, draft = _create_draft(service, "Freezable")
    code = _add_code(
        service,
        codebook.codebook_id,
        draft.version.codebook_version_id,
        stable_code_key="stable",
        label="Stable",
    )
    first = service.freeze_version(
        researcher_id=RESEARCHER_ID,
        codebook_id=codebook.codebook_id,
        codebook_version_id=draft.version.codebook_version_id,
    )
    audit_count = _row_counts(database)[3]
    retry = service.freeze_version(
        researcher_id=RESEARCHER_ID,
        codebook_id=codebook.codebook_id,
        codebook_version_id=draft.version.codebook_version_id,
    )

    assert first.version.status == "frozen"
    assert retry == first
    assert _row_counts(database)[3] == audit_count
    with pytest.raises(CodebookImmutableError):
        _add_code(
            service,
            codebook.codebook_id,
            draft.version.codebook_version_id,
            stable_code_key="late",
            label="Late",
        )
    with pytest.raises(CodebookImmutableError):
        _replace_code(
            service,
            codebook.codebook_id,
            draft.version.codebook_version_id,
            code.code_id,
            label="Changed",
            parent_code_id=None,
        )


def test_derive_requires_frozen_source_and_preserves_hierarchy_with_new_ids(
    tmp_path: Path,
) -> None:
    _, _, service = _create_service(tmp_path)
    codebook, draft = _create_draft(service)
    root = _add_code(
        service,
        codebook.codebook_id,
        draft.version.codebook_version_id,
        stable_code_key="root",
        label="Root",
    )
    _add_code(
        service,
        codebook.codebook_id,
        draft.version.codebook_version_id,
        stable_code_key="child",
        label="Child",
        parent_code_id=root.code_id,
    )
    with pytest.raises(CodebookConflictError, match="frozen"):
        service.derive_draft(
            researcher_id=RESEARCHER_ID,
            codebook_id=codebook.codebook_id,
            based_on_version_id=draft.version.codebook_version_id,
        )
    frozen = service.freeze_version(
        researcher_id=RESEARCHER_ID,
        codebook_id=codebook.codebook_id,
        codebook_version_id=draft.version.codebook_version_id,
    )

    derived = service.derive_draft(
        researcher_id=RESEARCHER_ID,
        codebook_id=codebook.codebook_id,
        based_on_version_id=frozen.version.codebook_version_id,
    )

    assert derived.version.version_number == 2
    assert derived.version.status == "draft"
    assert derived.version.based_on_version_id == frozen.version.codebook_version_id
    assert [code.stable_code_key for code in derived.codes] == ["root", "child"]
    assert {code.code_id for code in derived.codes}.isdisjoint(
        {code.code_id for code in frozen.codes}
    )
    assert derived.codes[1].parent_code_id == derived.codes[0].code_id


def test_portable_export_import_round_trip_preserves_research_content(
    tmp_path: Path,
) -> None:
    _, _, service = _create_service(tmp_path)
    imported = service.import_version(
        researcher_id=RESEARCHER_ID,
        document=_portable_document(),
    )
    exported = service.export_version(
        codebook_id=imported.codebook.codebook_id,
        codebook_version_id=imported.version.codebook_version_id,
    )

    assert imported.version.version_number == 1
    assert imported.version.status == "draft"
    assert [code.stable_code_key for code in imported.codes] == ["root", "child"]
    assert exported["codebook"] == _portable_document()["codebook"]
    assert exported["codes"] == [
        _portable_document()["codes"][1],
        _portable_document()["codes"][0],
    ]
    assert exported["version"] == {
        "source_version_number": 1,
        "source_status": "draft",
    }
    serialized = json.dumps(exported, sort_keys=True)
    for forbidden in (
        imported.codebook.codebook_id,
        imported.version.codebook_version_id,
        RESEARCHER_ID,
        "schema_migrations",
        "local_data/",
    ):
        assert forbidden not in serialized


def test_successful_mutations_append_attributable_audit_sequence(
    tmp_path: Path,
) -> None:
    _, database, service = _create_service(tmp_path)
    codebook, draft = _create_draft(service, "Audited Themes")
    code = _add_code(
        service,
        codebook.codebook_id,
        draft.version.codebook_version_id,
        stable_code_key="audited",
        label="Audited",
    )
    _replace_code(
        service,
        codebook.codebook_id,
        draft.version.codebook_version_id,
        code.code_id,
        label="Audited update",
        parent_code_id=None,
    )
    frozen = service.freeze_version(
        researcher_id=RESEARCHER_ID,
        codebook_id=codebook.codebook_id,
        codebook_version_id=draft.version.codebook_version_id,
    )
    service.derive_draft(
        researcher_id=RESEARCHER_ID,
        codebook_id=codebook.codebook_id,
        based_on_version_id=frozen.version.codebook_version_id,
    )
    service.import_version(
        researcher_id=RESEARCHER_ID,
        document=_portable_document(),
    )

    with sqlite3.connect(database.db_path) as connection:
        events = connection.execute(
            """
            select event_type, actor_id from qualitative_audit_events
            where event_type != 'qualitative.project.initialized'
            order by rowid
            """
        ).fetchall()

    assert events == [
        ("codebook.created", RESEARCHER_ID),
        ("codebook.version.created", RESEARCHER_ID),
        ("codebook.code.created", RESEARCHER_ID),
        ("codebook.code.updated", RESEARCHER_ID),
        ("codebook.version.frozen", RESEARCHER_ID),
        ("codebook.version.derived", RESEARCHER_ID),
        ("codebook.version.imported", RESEARCHER_ID),
    ]


@pytest.mark.parametrize(
    "mutate, message",
    [
        (
            lambda document: document.update({"format": "foreign.codebook"}),
            "document format",
        ),
        (
            lambda document: document.update({"format_version": 2}),
            "format_version",
        ),
        (
            lambda document: document["version"].update(
                {"source_version_number": True}
            ),
            "positive integer",
        ),
        (
            lambda document: document["version"].update(
                {"source_status": "published"}
            ),
            "source_status",
        ),
        (
            lambda document: document.update({"actor_id": "res_untrusted"}),
            "invalid keys",
        ),
        (
            lambda document: document["codes"].append(dict(document["codes"][0])),
            "stable_code_key",
        ),
        (
            lambda document: document["codes"][0].update(
                {"parent_stable_code_key": "missing"}
            ),
            "does not exist",
        ),
        (
            lambda document: document["codes"][1].update(
                {"parent_stable_code_key": "child"}
            ),
            "cycle",
        ),
        (
            lambda document: document["codes"][0].update({"sort_order": True}),
            "sort_order",
        ),
        (
            lambda document: document["codes"][0].update({"examples": [1]}),
            "examples",
        ),
    ],
)
def test_invalid_import_is_fully_validated_before_transaction_and_writes_nothing(
    tmp_path: Path,
    monkeypatch,
    mutate,
    message: str,
) -> None:
    _, database, service = _create_service(tmp_path)
    document = _portable_document()
    mutate(document)
    before = _row_counts(database)
    transaction_opened = False
    original_transaction = service.database.transaction

    def tracked_transaction():
        nonlocal transaction_opened
        transaction_opened = True
        return original_transaction()

    monkeypatch.setattr(service.database, "transaction", tracked_transaction)
    with pytest.raises(CodebookValidationError, match=message):
        service.import_version(
            researcher_id=RESEARCHER_ID,
            document=document,
        )

    assert transaction_opened is False
    assert _row_counts(database) == before


def test_import_rejects_malformed_non_object_document_without_writes(
    tmp_path: Path,
) -> None:
    _, database, service = _create_service(tmp_path)
    before = _row_counts(database)

    with pytest.raises(CodebookValidationError, match="document must be an object"):
        service.import_version(
            researcher_id=RESEARCHER_ID,
            document="{not-json",
        )

    assert _row_counts(database) == before


def test_audit_insertion_failure_rolls_back_domain_mutation_and_is_translated(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _, database, service = _create_service(tmp_path)
    with sqlite3.connect(database.db_path) as connection:
        existing_event_id = connection.execute(
            "select event_id from qualitative_audit_events limit 1"
        ).fetchone()[0]

    def insert_duplicate_audit(connection, **kwargs):
        connection.execute(
            """
            insert into qualitative_audit_events (
              event_id, project_id, actor_id, event_type,
              subject_type, subject_id, metadata_json, created_at
            ) values (?, ?, ?, 'duplicate', 'codebook', 'cbk_duplicate', '{}', ?)
            """,
            (
                existing_event_id,
                service.project_id,
                kwargs["actor_id"],
                kwargs["created_at"],
            ),
        )

    monkeypatch.setattr(service, "_append_audit", insert_duplicate_audit)
    before = _row_counts(database)

    with pytest.raises(CodebookConflictError, match="identity conflicts"):
        service.create_codebook(
            researcher_id=RESEARCHER_ID,
            title="Must roll back",
        )

    assert _row_counts(database) == before


def test_snapshot_rejects_persisted_hierarchy_and_json_corruption(
    tmp_path: Path,
) -> None:
    _, database, service = _create_service(tmp_path)
    codebook, draft = _create_draft(service)
    first = _add_code(
        service,
        codebook.codebook_id,
        draft.version.codebook_version_id,
        stable_code_key="first",
        label="First",
    )
    second = _add_code(
        service,
        codebook.codebook_id,
        draft.version.codebook_version_id,
        stable_code_key="second",
        label="Second",
        parent_code_id=first.code_id,
    )
    with database.transaction() as connection:
        connection.execute(
            "update codes set parent_code_id = ? where code_id = ?",
            (second.code_id, first.code_id),
        )
    with pytest.raises(CodebookConflictError, match="cycle"):
        service.read_version(
            codebook_id=codebook.codebook_id,
            codebook_version_id=draft.version.codebook_version_id,
        )

    with database.transaction() as connection:
        connection.execute(
            "update codes set parent_code_id = null, examples_json = 'not-json' where code_id = ?",
            (first.code_id,),
        )
    with pytest.raises(CodebookConflictError, match="examples"):
        service.read_version(
            codebook_id=codebook.codebook_id,
            codebook_version_id=draft.version.codebook_version_id,
        )

    with database.transaction() as connection:
        connection.execute(
            """
            update codes set examples_json = '[]', label = ? where code_id = ?
            """,
            (sqlite3.Binary(b"invalid-label"), first.code_id),
        )
    with pytest.raises(CodebookConflictError, match="label"):
        service.read_version(
            codebook_id=codebook.codebook_id,
            codebook_version_id=draft.version.codebook_version_id,
        )

    with database.transaction() as connection:
        connection.execute(
            "update codes set label = 'First' where code_id = ?",
            (first.code_id,),
        )
        connection.execute(
            "update codebooks set title = ? where codebook_id = ?",
            (sqlite3.Binary(b"invalid-title"), codebook.codebook_id),
        )
    with pytest.raises(CodebookConflictError, match="title"):
        service.read_version(
            codebook_id=codebook.codebook_id,
            codebook_version_id=draft.version.codebook_version_id,
        )


def test_newer_qualitative_schema_is_refused(tmp_path: Path) -> None:
    _, database, service = _create_service(tmp_path)
    with sqlite3.connect(database.db_path) as connection:
        connection.execute("pragma user_version = 99")

    with pytest.raises(SchemaCompatibilityError, match="newer than supported"):
        service.list_codebooks()
