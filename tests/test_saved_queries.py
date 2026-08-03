import base64
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from threading import Barrier

import pytest

import backend.qualitative.saved_queries as saved_query_module
from backend.qualitative.codebooks import CodebookService
from backend.qualitative.database import QualitativeProjectDatabase
from backend.qualitative.research_reviews import ResearchReviewService
from backend.qualitative.saved_queries import (
    SavedQueryConflictError,
    SavedQueryNotFoundError,
    SavedQueryService,
    SavedQueryValidationError,
)
from backend.storage.evidence_catalog import EvidenceCatalog, EvidenceImportRecord
from backend.storage.study_store import StudyWorkspaceStore


OWNER_ID = "res_saved_query_owner"
QUERY_ID = f"qry_{'1' * 32}"


@dataclass(frozen=True)
class SavedQueryFixture:
    root: Path
    project_id: str
    database: QualitativeProjectDatabase
    service: SavedQueryService


@pytest.fixture
def fixture(tmp_path: Path) -> SavedQueryFixture:
    study = StudyWorkspaceStore(tmp_path).create_study(
        {"name": "Saved Query Study"}
    )
    database = QualitativeProjectDatabase(tmp_path, study.id)
    database.initialize(
        researcher_id=OWNER_ID,
        researcher_name="Saved Query Owner",
    )
    return SavedQueryFixture(
        root=tmp_path,
        project_id=study.id,
        database=database,
        service=SavedQueryService(tmp_path, study.id),
    )


def _definition(**overrides: object) -> dict[str, object]:
    filters: dict[str, object] = {
        "project_source_id": None,
        "codebook_version_id": None,
        "code_id": None,
        "created_by": None,
        "include_removed": False,
    }
    filters.update(overrides)
    return {
        "kind": "coding_reference_filter",
        "version": 1,
        "filters": filters,
    }


def _create(
    fixture: SavedQueryFixture,
    *,
    saved_query_id: str = QUERY_ID,
    researcher_id: str = OWNER_ID,
    title: str = "Active coding by owner",
    definition: dict[str, object] | None = None,
):
    return fixture.service.create_saved_query(
        saved_query_id=saved_query_id,
        researcher_id=researcher_id,
        title=title,
        definition=_definition() if definition is None else definition,
    )


def _record_source(
    fixture: SavedQueryFixture,
    *,
    project_source_id: str = "psrc_saved_query",
    workspace_id: str | None = None,
    suffix: str = "1",
) -> str:
    EvidenceCatalog(fixture.root).record_import(
        EvidenceImportRecord(
            import_id=f"imp_saved_query_{suffix}",
            run_id=f"run_saved_query_{suffix}",
            pipeline="saved-query-test",
            source_id=f"src_saved_query_{suffix}",
            source_filename=f"source-{suffix}.txt",
            source_media_type="text/plain",
            source_blob_sha256=suffix * 64,
            transcript_revision_id=f"trv_{suffix * 32}",
            transcript_sha256=("a" if suffix != "a" else "b") * 64,
            imported_at="2026-08-02T12:00:00+00:00",
            project_source_id=project_source_id,
            workspace_id=workspace_id or fixture.project_id,
        )
    )
    return project_source_id


def _create_code(fixture: SavedQueryFixture, *, stable_key: str = "theme"):
    service = CodebookService(fixture.root, fixture.project_id)
    codebook = service.create_codebook(
        researcher_id=OWNER_ID,
        title=f"Codebook {stable_key}",
    )
    version = service.create_draft(
        researcher_id=OWNER_ID,
        codebook_id=codebook.codebook_id,
    )
    code = service.add_code(
        researcher_id=OWNER_ID,
        codebook_id=codebook.codebook_id,
        codebook_version_id=version.version.codebook_version_id,
        stable_code_key=stable_key,
        label=stable_key.title(),
    )
    return version.version.codebook_version_id, code.code_id


def _register_researcher(
    fixture: SavedQueryFixture,
    researcher_id: str,
) -> None:
    ResearchReviewService(fixture.root, fixture.project_id).create_researcher(
        actor_id=OWNER_ID,
        researcher_id=researcher_id,
        display_name="Second Coder",
        role="researcher",
    )


def _canonical_request_digest(
    fixture: SavedQueryFixture,
    *,
    saved_query_id: str,
    researcher_id: str,
    title: str,
    definition: dict[str, object],
) -> str:
    payload = {
        "definition": definition,
        "project_id": fixture.project_id,
        "researcher_id": researcher_id,
        "saved_query_id": saved_query_id,
        "title": title,
    }
    content = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return sha256(b"nlp-skill-agents.saved-query-create.v1\0" + content).hexdigest()


def _replace_immutable_row(
    fixture: SavedQueryFixture,
    set_clause: str,
    parameters: tuple[object, ...],
) -> None:
    with sqlite3.connect(fixture.database.db_path) as connection:
        connection.execute("pragma ignore_check_constraints = on")
        trigger_sql = connection.execute(
            """
            select sql from sqlite_master
            where type = 'trigger' and name = 'prevent_saved_query_update'
            """
        ).fetchone()[0]
        connection.execute("drop trigger prevent_saved_query_update")
        connection.execute(
            f"update saved_queries set {set_clause} where saved_query_id = ?",
            (*parameters, QUERY_ID),
        )
        connection.execute(trigger_sql)


def _delete_query_audit(fixture: SavedQueryFixture) -> None:
    with sqlite3.connect(fixture.database.db_path) as connection:
        trigger_sql = connection.execute(
            """
            select sql from sqlite_master
            where type = 'trigger' and name = 'prevent_qualitative_audit_delete'
            """
        ).fetchone()[0]
        connection.execute("drop trigger prevent_qualitative_audit_delete")
        connection.execute(
            "delete from qualitative_audit_events where subject_id = ?",
            (QUERY_ID,),
        )
        connection.execute(trigger_sql)


def _cursor_payload(cursor: str) -> dict[str, object]:
    padding = "=" * ((4 - len(cursor) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(cursor + padding))


def _encode_cursor_payload(payload: object, *, compact: bool = True) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":") if compact else None,
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def test_create_read_and_exact_retry_are_canonical_and_attributable(
    fixture: SavedQueryFixture,
) -> None:
    definition = _definition()
    created = _create(fixture, definition=definition)
    retried = _create(fixture, definition=definition)
    loaded = fixture.service.read_saved_query(QUERY_ID)

    assert created == retried == loaded
    assert created.project_id == fixture.project_id
    assert created.title == "Active coding by owner"
    assert created.definition.kind == "coding_reference_filter"
    assert created.definition.version == 1
    assert created.definition.filters.include_removed is False

    expected_filters = json.dumps(
        definition["filters"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    expected_digest = _canonical_request_digest(
        fixture,
        saved_query_id=QUERY_ID,
        researcher_id=OWNER_ID,
        title="Active coding by owner",
        definition=definition,
    )
    with sqlite3.connect(fixture.database.db_path) as connection:
        assert connection.execute(
            """
            select filters_json, request_sha256 from saved_queries
            where saved_query_id = ?
            """,
            (QUERY_ID,),
        ).fetchone() == (expected_filters, expected_digest)
        assert connection.execute(
            """
            select actor_id, event_type, subject_type, subject_id,
                   metadata_json, created_at
            from qualitative_audit_events
            where subject_id = ?
            """,
            (QUERY_ID,),
        ).fetchall() == [
            (
                OWNER_ID,
                "saved_query.created",
                "saved_query",
                QUERY_ID,
                '{"definition_version":1,"query_kind":"coding_reference_filter"}',
                created.created_at,
            )
        ]


def test_divergent_identity_retry_conflicts_without_second_event(
    fixture: SavedQueryFixture,
) -> None:
    _create(fixture)

    for changes in (
        {"title": "Different title"},
        {"definition": _definition(include_removed=True)},
        {"researcher_id": "res_different_actor"},
    ):
        with pytest.raises(SavedQueryConflictError, match="identity conflicts"):
            _create(fixture, **changes)

    with sqlite3.connect(fixture.database.db_path) as connection:
        assert connection.execute("select count(*) from saved_queries").fetchone() == (
            1,
        )
        assert connection.execute(
            "select count(*) from qualitative_audit_events where subject_id = ?",
            (QUERY_ID,),
        ).fetchone() == (1,)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("saved_query_id", "qry_invalid"),
        ("researcher_id", "Owner"),
        ("title", ""),
        ("title", " padded "),
        ("title", "x" * 257),
        ("definition", {}),
        ("definition", {"kind": "sql", "version": 1, "filters": {}}),
        (
            "definition",
            {"kind": "coding_reference_filter", "version": True, "filters": {}},
        ),
        ("definition", _definition(include_removed="false")),
        ("definition", _definition(project_source_id=" psrc_bad ")),
    ],
)
def test_create_rejects_noncanonical_or_coercive_inputs(
    fixture: SavedQueryFixture,
    field: str,
    value: object,
) -> None:
    request: dict[str, object] = {
        "saved_query_id": QUERY_ID,
        "researcher_id": OWNER_ID,
        "title": "Active coding by owner",
        "definition": _definition(),
    }
    request[field] = value
    with pytest.raises(SavedQueryValidationError):
        fixture.service.create_saved_query(**request)


def test_new_create_requires_active_actor_but_exact_retry_does_not(
    fixture: SavedQueryFixture,
) -> None:
    with pytest.raises(SavedQueryNotFoundError, match="Researcher"):
        _create(fixture, researcher_id="res_missing_actor")

    created = _create(fixture)
    with fixture.database.transaction() as connection:
        connection.execute(
            "update researchers set active = 0 where researcher_id = ?",
            (OWNER_ID,),
        )

    assert _create(fixture) == created
    with pytest.raises(SavedQueryConflictError, match="inactive"):
        _create(fixture, saved_query_id=f"qry_{'2' * 32}")


def test_source_and_local_filter_dependencies_are_project_owned(
    fixture: SavedQueryFixture,
) -> None:
    source_id = _record_source(fixture)
    version_id, code_id = _create_code(fixture)
    coder_id = "res_saved_query_coder"
    _register_researcher(fixture, coder_id)

    record = _create(
        fixture,
        definition=_definition(
            project_source_id=source_id,
            codebook_version_id=version_id,
            code_id=code_id,
            created_by=coder_id,
            include_removed=True,
        ),
    )
    assert record.definition.filters.project_source_id == source_id
    assert record.definition.filters.code_id == code_id

    with pytest.raises(SavedQueryNotFoundError):
        _create(
            fixture,
            saved_query_id=f"qry_{'2' * 32}",
            definition=_definition(project_source_id="psrc_missing"),
        )
    _record_source(
        fixture,
        project_source_id="psrc_foreign",
        workspace_id="another-study",
        suffix="2",
    )
    with pytest.raises(SavedQueryNotFoundError):
        _create(
            fixture,
            saved_query_id=f"qry_{'3' * 32}",
            definition=_definition(project_source_id="psrc_foreign"),
        )
    for saved_query_id, filters in (
        (f"qry_{'4' * 32}", {"codebook_version_id": "cbv_missing"}),
        (f"qry_{'5' * 32}", {"code_id": "cod_missing"}),
        (f"qry_{'6' * 32}", {"created_by": "res_missing"}),
    ):
        with pytest.raises(SavedQueryNotFoundError):
            _create(
                fixture,
                saved_query_id=saved_query_id,
                definition=_definition(**filters),
            )

    other_version, _ = _create_code(fixture, stable_key="other")
    with pytest.raises(SavedQueryNotFoundError):
        _create(
            fixture,
            saved_query_id=f"qry_{'7' * 32}",
            definition=_definition(
                codebook_version_id=other_version,
                code_id=code_id,
            ),
        )


def test_audit_failure_rolls_back_query_and_event(
    fixture: SavedQueryFixture,
) -> None:
    with fixture.database.transaction() as connection:
        connection.execute(
            """
            create trigger fail_saved_query_audit
            before insert on qualitative_audit_events
            when new.event_type = 'saved_query.created'
            begin
              select raise(abort, 'forced saved-query audit failure');
            end
            """
        )

    with pytest.raises(SavedQueryConflictError):
        _create(fixture)

    with sqlite3.connect(fixture.database.db_path) as connection:
        assert connection.execute("select count(*) from saved_queries").fetchone() == (
            0,
        )
        assert connection.execute(
            "select count(*) from qualitative_audit_events where subject_id = ?",
            (QUERY_ID,),
        ).fetchone() == (0,)


def test_missing_sole_audit_is_a_stored_state_conflict(
    fixture: SavedQueryFixture,
) -> None:
    _create(fixture)
    _delete_query_audit(fixture)

    with pytest.raises(SavedQueryConflictError, match="audit identity"):
        fixture.service.read_saved_query(QUERY_ID)


@pytest.mark.parametrize(
    ("set_clause", "parameters"),
    [
        ("title = ?", (" padded ",)),
        ("filters_json = ?", ('{"project_source_id":null}',)),
        (
            "filters_json = ?",
            (
                '{"code_id":null,"code_id":null,"codebook_version_id":null,'
                '"created_by":null,"include_removed":false,'
                '"project_source_id":null}',
            ),
        ),
        ("request_sha256 = ?", ("0" * 64,)),
        ("created_at = ?", ("2026-08-02T12:00:00Z",)),
        ("filters_json = ?", ("[" * 900 + "0" + "]" * 900,)),
    ],
)
def test_reads_reject_corrupt_or_noncanonical_stored_rows(
    fixture: SavedQueryFixture,
    set_clause: str,
    parameters: tuple[object, ...],
) -> None:
    _create(fixture)
    _replace_immutable_row(fixture, set_clause, parameters)

    with pytest.raises(SavedQueryConflictError):
        fixture.service.read_saved_query(QUERY_ID)


@pytest.mark.parametrize(
    ("event_type", "subject_type", "subject_id", "metadata"),
    [
        ("saved_query.created", "saved_query", QUERY_ID, "{}"),
        (" saved_query.created ", "saved_query", f"qry_{'8' * 32}", "{}"),
        (b"saved_query.created\xff", b"saved_query\xff", b"qry_bad\xff", b"{}"),
        (
            "saved_query.created",
            "saved_query",
            f"qry_{'9' * 32}",
            '{"definition_version":1,"query_kind":"coding_reference_filter",'
            '"title":"private"}',
        ),
    ],
)
def test_reads_reject_duplicate_unmatched_binary_or_content_bearing_audits(
    fixture: SavedQueryFixture,
    event_type: object,
    subject_type: object,
    subject_id: object,
    metadata: object,
) -> None:
    created = _create(fixture)
    with sqlite3.connect(fixture.database.db_path) as connection:
        connection.execute(
            """
            insert into qualitative_audit_events (
              event_id, project_id, actor_id, event_type,
              subject_type, subject_id, metadata_json, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"qae_{'a' * 32}",
                fixture.project_id,
                OWNER_ID,
                event_type,
                subject_type,
                subject_id,
                metadata,
                created.created_at,
            ),
        )

    with pytest.raises(SavedQueryConflictError):
        fixture.service.read_saved_query(QUERY_ID)


def test_hidden_query_corruption_cannot_be_filtered_out(
    fixture: SavedQueryFixture,
) -> None:
    other_id = "res_saved_query_other"
    _register_researcher(fixture, other_id)
    _create(fixture)
    _create(
        fixture,
        saved_query_id=f"qry_{'2' * 32}",
        researcher_id=other_id,
        title="Other coder query",
    )
    _replace_immutable_row(fixture, "request_sha256 = ?", ("0" * 64,))

    with pytest.raises(SavedQueryConflictError):
        fixture.service.list_saved_queries(created_by=other_id)


def test_bounded_pagination_is_deterministic_and_filter_bound(
    fixture: SavedQueryFixture,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        saved_query_module,
        "_utc_now",
        lambda: "2026-08-02T12:00:00+00:00",
    )
    for digit in ("1", "2", "3"):
        _create(
            fixture,
            saved_query_id=f"qry_{digit * 32}",
            title=f"Query {digit}",
        )

    first = fixture.service.list_saved_queries(limit=1)
    second = fixture.service.list_saved_queries(limit=1, cursor=first.next_cursor)
    third = fixture.service.list_saved_queries(limit=1, cursor=second.next_cursor)

    assert [page.saved_queries[0].saved_query_id for page in (first, second, third)] == [
        f"qry_{'1' * 32}",
        f"qry_{'2' * 32}",
        f"qry_{'3' * 32}",
    ]
    assert first.next_cursor is not None
    assert second.next_cursor is not None
    assert third.next_cursor is None
    with pytest.raises(SavedQueryValidationError):
        fixture.service.list_saved_queries(
            created_by=OWNER_ID,
            cursor=first.next_cursor,
        )


@pytest.mark.parametrize("divergent", [False, True])
def test_concurrent_same_id_creates_converge_or_conflict_atomically(
    fixture: SavedQueryFixture,
    monkeypatch,
    divergent: bool,
) -> None:
    source_id = _record_source(fixture)
    barrier = Barrier(2)
    original = SavedQueryService._validate_source_ids

    def synchronized_preflight(
        self,
        source_ids,
        *,
        missing_is_not_found: bool,
    ):
        if missing_is_not_found:
            barrier.wait(timeout=5)
        return original(
            self,
            source_ids,
            missing_is_not_found=missing_is_not_found,
        )

    monkeypatch.setattr(
        SavedQueryService,
        "_validate_source_ids",
        synchronized_preflight,
    )
    titles = (
        "Concurrent exact",
        "Concurrent divergent" if divergent else "Concurrent exact",
    )

    def create(title: str):
        return fixture.service.create_saved_query(
            saved_query_id=QUERY_ID,
            researcher_id=OWNER_ID,
            title=title,
            definition=_definition(project_source_id=source_id),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(create, title) for title in titles]
        outcomes: list[object] = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=10))
            except SavedQueryConflictError as exc:
                outcomes.append(exc)

    records = [outcome for outcome in outcomes if not isinstance(outcome, Exception)]
    conflicts = [
        outcome for outcome in outcomes if isinstance(outcome, SavedQueryConflictError)
    ]
    if divergent:
        assert len(records) == 1
        assert len(conflicts) == 1
    else:
        assert len(records) == 2
        assert not conflicts
        assert records[0] == records[1]
    with sqlite3.connect(fixture.database.db_path) as connection:
        assert connection.execute("select count(*) from saved_queries").fetchone() == (
            1,
        )
        assert connection.execute(
            "select count(*) from qualitative_audit_events where subject_id = ?",
            (QUERY_ID,),
        ).fetchone() == (1,)

@pytest.mark.parametrize(
    "cursor",
    [
        "bad=",
        "*",
        "a" * 4097,
        base64.urlsafe_b64encode(b"{}" * 1600).decode("ascii").rstrip("="),
    ],
)
def test_list_rejects_malformed_or_oversized_cursors(
    fixture: SavedQueryFixture,
    cursor: str,
) -> None:
    with pytest.raises(SavedQueryValidationError):
        fixture.service.list_saved_queries(cursor=cursor)


def test_list_rejects_cross_project_unknown_duplicate_and_noncanonical_cursors(
    fixture: SavedQueryFixture,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        saved_query_module,
        "_utc_now",
        lambda: "2026-08-02T12:00:00+00:00",
    )
    _create(fixture)
    _create(
        fixture,
        saved_query_id=f"qry_{'2' * 32}",
        title="Second query",
    )
    cursor = fixture.service.list_saved_queries(limit=1).next_cursor
    assert cursor is not None
    payload = _cursor_payload(cursor)

    cross_project = dict(payload)
    cross_project["project_id"] = "other-study"
    unknown_key = dict(payload)
    unknown_key["unknown"] = 1
    noncanonical = _encode_cursor_payload(payload, compact=False)
    decoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    duplicate = base64.urlsafe_b64encode(
        f'{decoded[:-1]},"version":1}}'.encode("utf-8")
    ).decode("ascii").rstrip("=")

    for invalid in (
        _encode_cursor_payload(cross_project),
        _encode_cursor_payload(unknown_key),
        noncanonical,
        duplicate,
    ):
        with pytest.raises(SavedQueryValidationError):
            fixture.service.list_saved_queries(cursor=invalid)


def test_list_rejects_deeply_nested_cursor_without_raw_recursion_error(
    fixture: SavedQueryFixture,
) -> None:
    nested = "[" * 1100 + "0" + "]" * 1100
    cursor = base64.urlsafe_b64encode(nested.encode("utf-8")).decode(
        "ascii"
    ).rstrip("=")

    with pytest.raises(SavedQueryValidationError, match="cursor"):
        fixture.service.list_saved_queries(cursor=cursor)


def test_cursor_rejects_missing_anchor_and_revalidates_anchor_source(
    fixture: SavedQueryFixture,
    monkeypatch,
) -> None:
    source_id = _record_source(fixture)
    _create(
        fixture,
        definition=_definition(project_source_id=source_id),
    )
    _create(
        fixture,
        saved_query_id=f"qry_{'2' * 32}",
        title="Second query",
    )
    first = fixture.service.list_saved_queries(limit=1)
    assert first.next_cursor is not None

    original = EvidenceCatalog.source_history

    def missing_source(self, project_source_id: str):
        if project_source_id == source_id:
            raise FileNotFoundError(project_source_id)
        return original(self, project_source_id)

    monkeypatch.setattr(EvidenceCatalog, "source_history", missing_source)
    with pytest.raises(SavedQueryConflictError, match="source is unavailable"):
        fixture.service.list_saved_queries(limit=1, cursor=first.next_cursor)

    payload = {
        "anchor": {
            "created_at": "2026-08-02T12:00:00+00:00",
            "saved_query_id": f"qry_{'f' * 32}",
        },
        "endpoint": "saved_queries",
        "filters": {"created_by": None},
        "project_id": fixture.project_id,
        "version": 1,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).decode("ascii").rstrip("=")
    monkeypatch.setattr(EvidenceCatalog, "source_history", original)
    with pytest.raises(SavedQueryNotFoundError, match="anchor"):
        fixture.service.list_saved_queries(cursor=encoded)


def test_exact_retry_succeeds_at_capacity_but_new_create_fails(
    fixture: SavedQueryFixture,
    monkeypatch,
) -> None:
    monkeypatch.setattr(saved_query_module, "_MAX_PROJECT_QUERIES", 1)
    created = _create(fixture)

    assert _create(fixture) == created
    with pytest.raises(SavedQueryValidationError, match="capacity"):
        _create(
            fixture,
            saved_query_id=f"qry_{'2' * 32}",
            title="Over capacity",
        )


def test_reads_reject_stored_family_over_capacity(
    fixture: SavedQueryFixture,
    monkeypatch,
) -> None:
    _create(fixture)
    _create(
        fixture,
        saved_query_id=f"qry_{'2' * 32}",
        title="Second query",
    )
    monkeypatch.setattr(saved_query_module, "_MAX_PROJECT_QUERIES", 1)

    with pytest.raises(SavedQueryConflictError, match="capacity"):
        fixture.service.read_saved_query(QUERY_ID)


@pytest.mark.parametrize(
    "foreign_project_id",
    ["other-study", sqlite3.Binary(b"foreign-study")],
)
def test_reads_reject_foreign_or_binary_project_rows_hidden_from_project_filters(
    fixture: SavedQueryFixture,
    foreign_project_id: object,
) -> None:
    _create(fixture)
    with sqlite3.connect(fixture.database.db_path) as connection:
        connection.execute("pragma foreign_keys = off")
        connection.execute(
            """
            insert into saved_queries (
              saved_query_id, project_id, title, query_kind,
              definition_version, filters_json, request_sha256,
              created_by, created_at
            ) values (?, ?, 'Foreign query', 'coding_reference_filter',
                      1, ?, ?, ?, '2026-08-02T12:00:00+00:00')
            """,
            (
                f"qry_{'f' * 32}",
                foreign_project_id,
                json.dumps(
                    _definition()["filters"],
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "0" * 64,
                OWNER_ID,
            ),
        )

    with pytest.raises(SavedQueryConflictError):
        fixture.service.read_saved_query(QUERY_ID)


def test_exact_retry_maps_disappeared_stored_source_to_conflict(
    fixture: SavedQueryFixture,
    monkeypatch,
) -> None:
    source_id = _record_source(fixture)
    _create(
        fixture,
        definition=_definition(project_source_id=source_id),
    )
    monkeypatch.setattr(
        EvidenceCatalog,
        "source_history",
        lambda self, project_source_id: (_ for _ in ()).throw(
            FileNotFoundError(project_source_id)
        ),
    )

    with pytest.raises(SavedQueryConflictError, match="source is unavailable"):
        _create(
            fixture,
            definition=_definition(project_source_id=source_id),
        )


def test_validate_project_state_rechecks_stored_source_ownership(
    fixture: SavedQueryFixture,
    monkeypatch,
) -> None:
    source_id = _record_source(fixture)
    _create(
        fixture,
        definition=_definition(project_source_id=source_id),
    )

    monkeypatch.setattr(
        EvidenceCatalog,
        "source_history",
        lambda self, project_source_id: (_ for _ in ()).throw(
            FileNotFoundError(project_source_id)
        ),
    )
    with pytest.raises(SavedQueryConflictError, match="source is unavailable"):
        fixture.service.validate_project_state()
