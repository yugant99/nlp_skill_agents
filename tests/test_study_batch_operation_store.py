import json
import sqlite3
from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FutureTimeoutError,
)
from hashlib import sha256
from pathlib import Path
from threading import Barrier, Event

import pytest

from backend.storage.sqlite_migrations import (
    SchemaCompatibilityError,
    apply_migrations,
)
from backend.storage.study_batch_operation_store import (
    STUDY_BATCH_OPERATION_MIGRATIONS,
    StudyBatchOperationConflict,
    StudyBatchOperationStore,
    validate_study_batch_operation_database,
)
from backend.storage.study_store import StudyWorkspaceStore


BATCH_ID = "batch_20260729000000_a1b2c3d4"
CREATED_AT = "2026-07-29T00:00:00+00:00"


def _canonical_json_sha256(payload: object) -> str:
    return sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _v1_aggregate_payload(*, batch_id: str = BATCH_ID) -> dict:
    return {
        "study_id": "study-one",
        "batch_id": batch_id,
        "skill_pack_version_id": "pack-1_0_0",
        "study_schema": None,
        "created_at": CREATED_AT,
        "run_count": 0,
        "failure_count": 0,
        "failures": [],
        "results": [],
    }


def _create_populated_v1_journal(
    tmp_path: Path,
    *,
    status: str = "completed",
    aggregate_payload: dict | None = None,
    malformed_aggregate: bool = False,
) -> Path:
    db_path = tmp_path / "batch_operations.sqlite3"
    with sqlite3.connect(db_path) as connection:
        apply_migrations(
            connection,
            database_name="study batch operations v1 fixture",
            migrations=STUDY_BATCH_OPERATION_MIGRATIONS[:1],
        )
        connection.execute(
            """
            insert into study_batch_operations (
              batch_id, study_id, skill_pack_version_id, skill_pack_sha256,
              request_sha256, item_count, audit_event_id, status, stage,
              attempt_count, last_error_type, created_at, updated_at,
              completed_at
            ) values (?, 'study-one', 'pack-1_0_0', ?, ?, 0, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                BATCH_ID,
                "f" * 64,
                "a" * 64,
                sha256(
                    f"batch.completed\0study-one\0{BATCH_ID}".encode("utf-8")
                ).hexdigest(),
                status,
                "completed" if status == "completed" else "prepared",
                "" if status == "completed" else "InterruptedError",
                CREATED_AT,
                CREATED_AT,
                CREATED_AT if status == "completed" else "",
            ),
        )
    if malformed_aggregate or aggregate_payload is not None:
        aggregate_path = (
            tmp_path / "batches" / BATCH_ID / "aggregate_results.json"
        )
        aggregate_path.parent.mkdir(parents=True)
        aggregate_path.write_text(
            "not-json"
            if malformed_aggregate
            else json.dumps(aggregate_payload),
            encoding="utf-8",
        )
    return db_path


def _promote_to_original_v2(db_path: Path) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute("begin immediate")
        connection.execute(
            """
            alter table study_batch_operations
            add column aggregate_payload_sha256 text not null default ''
              check (
                aggregate_payload_sha256 = ''
                or length(aggregate_payload_sha256) = 64
              )
            """
        )
        connection.execute(
            """
            insert into schema_migrations (version, name, applied_at)
            values (2, 'add-study-batch-aggregate-hash', ?)
            """,
            (CREATED_AT,),
        )
        connection.execute("pragma user_version = 2")
        connection.commit()


def _store(tmp_path: Path) -> StudyBatchOperationStore:
    StudyWorkspaceStore(tmp_path).create_study(
        {"id": "study-one", "name": "Study One"}
    )
    return StudyBatchOperationStore(tmp_path, "study-one")


def _begin(store: StudyBatchOperationStore) -> str:
    return store.begin(
        batch_id=BATCH_ID,
        skill_pack_version_id="pack-1_0_0",
        skill_pack_sha256="f" * 64,
        request_sha256="a" * 64,
        item_count=1,
        created_at=CREATED_AT,
    )


def _reserve_item(
    store: StudyBatchOperationStore,
    *,
    item_index: int = 0,
) -> dict:
    return store.reserve_item(
        BATCH_ID,
        item_index=item_index,
        item_request_sha256=f"{item_index + 1}" * 64,
        run_id=f"run_{item_index}",
        import_id=f"imp_{item_index}",
        project_source_id=f"psrc_{item_index}",
        source_blob_sha256="b" * 64,
        transcript_sha256="c" * 64,
        transcript_revision_id=f"trv_{item_index}",
        created_at=CREATED_AT,
    )


def _advance_item(store: StudyBatchOperationStore) -> None:
    store.record_analysis_completed(
        BATCH_ID,
        0,
        run_payload_sha256="d" * 64,
    )
    for stage in (
        "source_blob_stored",
        "evidence_cataloged",
        "snapshot_written",
        "completed",
    ):
        store.advance_item(BATCH_ID, 0, stage)


def _advance_operation(store: StudyBatchOperationStore) -> None:
    store.advance(BATCH_ID, "items_processed")
    store.record_aggregate_written(
        BATCH_ID,
        aggregate_payload_sha256="e" * 64,
    )
    for stage in ("csv_exports_written", "batch_manifest_written", "audit_recorded"):
        store.advance(BATCH_ID, stage)


def test_study_batch_operations_version_and_track_exact_retries(tmp_path) -> None:
    store = _store(tmp_path)

    assert _begin(store) == BATCH_ID
    with pytest.raises(StudyBatchOperationConflict, match="already running"):
        _begin(store)
    store.fail(BATCH_ID, error_type="InterruptedError")
    assert _begin(store) == BATCH_ID

    operation = store.get_operation(BATCH_ID)
    assert operation["status"] == "running"
    assert operation["stage"] == "prepared"
    assert operation["attempt_count"] == 2
    assert operation["request_sha256"] == "a" * 64
    assert operation["skill_pack_sha256"] == "f" * 64
    assert len(operation["audit_event_id"]) == 64
    assert [item["version"] for item in store.migration_status()] == [1, 2, 3]
    assert store.db_path == (
        tmp_path / "studies" / "study-one" / "batch_operations.sqlite3"
    )
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("pragma user_version").fetchone()[0] == 3


def test_study_batch_archive_guard_serializes_new_operations(tmp_path) -> None:
    store = _store(tmp_path)
    guard_entered = Event()
    release_guard = Event()

    def hold_archive_guard() -> None:
        with store.archive_snapshot_guard():
            guard_entered.set()
            assert release_guard.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=2) as executor:
        guard_future = executor.submit(hold_archive_guard)
        assert guard_entered.wait(timeout=5)
        begin_future = executor.submit(_begin, store)
        with pytest.raises(FutureTimeoutError):
            begin_future.result(timeout=0.1)
        release_guard.set()
        guard_future.result(timeout=5)
        assert begin_future.result(timeout=5) == BATCH_ID

    assert store.get_operation(BATCH_ID)["status"] == "running"


def test_study_batch_archive_guard_serializes_study_metadata_writes(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    workspace_store = StudyWorkspaceStore(tmp_path)
    guard_entered = Event()
    release_guard = Event()

    def hold_archive_guard() -> None:
        with store.archive_snapshot_guard():
            guard_entered.set()
            assert release_guard.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=2) as executor:
        guard_future = executor.submit(hold_archive_guard)
        assert guard_entered.wait(timeout=5)
        schema_future = executor.submit(
            workspace_store.save_study_schema,
            "study-one",
            {"participant_count": 2, "week_count": 1},
        )
        with pytest.raises(FutureTimeoutError):
            schema_future.result(timeout=0.1)
        release_guard.set()
        guard_future.result(timeout=5)
        schema = schema_future.result(timeout=5)

    assert schema.participant_count == 2
    assert (tmp_path / "studies" / "study-one" / "study_schema.json").is_file()


def test_study_batch_begin_reports_long_archive_lock_as_conflict(tmp_path) -> None:
    store = _store(tmp_path)
    guard_entered = Event()
    release_guard = Event()

    def hold_archive_guard() -> None:
        with store.archive_snapshot_guard():
            guard_entered.set()
            assert release_guard.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=1) as executor:
        guard_future = executor.submit(hold_archive_guard)
        assert guard_entered.wait(timeout=5)
        try:
            with pytest.raises(StudyBatchOperationConflict, match="journal is busy"):
                _begin(store)
        finally:
            release_guard.set()
        guard_future.result(timeout=5)

    with pytest.raises(FileNotFoundError):
        store.get_operation(BATCH_ID)


def test_study_batch_operations_reject_conflicts_and_bad_identity(tmp_path) -> None:
    store = _store(tmp_path)
    _begin(store)
    store.fail(BATCH_ID, error_type="OSError")

    with pytest.raises(StudyBatchOperationConflict, match="identity conflicts"):
        store.begin(
            batch_id=BATCH_ID,
            skill_pack_version_id="changed-1_0_0",
            skill_pack_sha256="f" * 64,
            request_sha256="a" * 64,
            item_count=1,
            created_at=CREATED_AT,
        )
    with pytest.raises(StudyBatchOperationConflict, match="identity conflicts"):
        store.begin(
            batch_id=BATCH_ID,
            skill_pack_version_id="pack-1_0_0",
            skill_pack_sha256="9" * 64,
            request_sha256="a" * 64,
            item_count=1,
            created_at=CREATED_AT,
        )
    with pytest.raises(ValueError, match="generated study batch"):
        store.begin(
            batch_id="batch-bad",
            skill_pack_version_id="pack-1_0_0",
            skill_pack_sha256="f" * 64,
            request_sha256="a" * 64,
            item_count=1,
            created_at=CREATED_AT,
        )
    with pytest.raises(ValueError, match="normalized version"):
        store.begin(
            batch_id="batch_20260729000001_a1b2c3d4",
            skill_pack_version_id="../pack.json",
            skill_pack_sha256="f" * 64,
            request_sha256="a" * 64,
            item_count=1,
            created_at=CREATED_AT,
        )
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        store.begin(
            batch_id="batch_20260729000001_a1b2c3d4",
            skill_pack_version_id="pack-1_0_0",
            skill_pack_sha256="f" * 64,
            request_sha256="private transcript",
            item_count=1,
            created_at=CREATED_AT,
        )
    with pytest.raises(ValueError, match="normalized study"):
        StudyBatchOperationStore(tmp_path, "../other")
    with pytest.raises(FileNotFoundError, match="missing-study"):
        StudyBatchOperationStore(tmp_path, "missing-study").migration_status()


def test_study_batch_item_identity_and_transitions_are_replay_safe(tmp_path) -> None:
    store = _store(tmp_path)
    _begin(store)
    prepared = _reserve_item(store)

    assert prepared["stage"] == "prepared"
    assert _reserve_item(store) == prepared
    with pytest.raises(StudyBatchOperationConflict, match="item identity conflicts"):
        store.reserve_item(
            BATCH_ID,
            item_index=0,
            item_request_sha256="1" * 64,
            run_id="run_changed",
            import_id="imp_0",
            project_source_id="psrc_0",
            source_blob_sha256="b" * 64,
            transcript_sha256="c" * 64,
            transcript_revision_id="trv_0",
            created_at=CREATED_AT,
        )
    with pytest.raises(ValueError, match="cannot advance"):
        store.advance_item(BATCH_ID, 0, "evidence_cataloged")
    with pytest.raises(ValueError, match="Unsupported"):
        store.advance_item(BATCH_ID, 0, "analysis_completed")

    _advance_item(store)
    store.advance_item(BATCH_ID, 0, "source_blob_stored")
    store.fail(BATCH_ID, error_type="OSError")
    _begin(store)

    replayed = store.get_item(BATCH_ID, 0)
    assert replayed is not None
    assert replayed["stage"] == "completed"
    assert _reserve_item(store)["stage"] == "completed"
    _advance_item(store)
    _advance_operation(store)
    store.complete(BATCH_ID)

    completed = store.get_operation(BATCH_ID)
    assert completed["status"] == "completed"
    assert completed["stage"] == "completed"
    assert completed["attempt_count"] == 2
    assert store.list_operations(incomplete_only=True) == []

    assert _begin(store) == BATCH_ID
    assert store.get_operation(BATCH_ID)["attempt_count"] == 2


def test_study_batch_operation_requires_items_before_completion(tmp_path) -> None:
    store = _store(tmp_path)
    _begin(store)
    _reserve_item(store)

    with pytest.raises(ValueError, match="unprocessed items"):
        store.advance(BATCH_ID, "items_processed")
    with pytest.raises(ValueError, match="before audit_recorded"):
        store.complete(BATCH_ID)
    with pytest.raises(ValueError, match="exception class name"):
        store.fail(BATCH_ID, error_type="OSError: transcript content")


def test_study_batch_operations_record_rejected_items_without_content(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    store.begin(
        batch_id=BATCH_ID,
        skill_pack_version_id="pack-1_0_0",
        skill_pack_sha256="f" * 64,
        request_sha256="a" * 64,
        item_count=2,
        created_at=CREATED_AT,
    )
    _reserve_item(store, item_index=0)
    _reserve_item(store, item_index=1)

    first = store.reject_item(
        BATCH_ID,
        item_index=0,
        item_request_sha256="1" * 64,
        error_type="ValueError",
    )
    second = store.reject_item(
        BATCH_ID,
        item_index=1,
        item_request_sha256="2" * 64,
        error_type="KeyError",
    )

    assert first["stage"] == "rejected"
    assert first["last_error_type"] == "ValueError"
    assert second["last_error_type"] == "KeyError"
    assert "source_filename" not in first
    assert "content" not in first
    assert "metadata" not in first
    store.advance(BATCH_ID, "items_processed")
    store.record_aggregate_written(
        BATCH_ID,
        aggregate_payload_sha256="e" * 64,
    )
    for stage in ("csv_exports_written", "batch_manifest_written", "audit_recorded"):
        store.advance(BATCH_ID, stage)
    store.complete(BATCH_ID)

    with sqlite3.connect(store.db_path) as connection:
        persisted = str(
            connection.execute(
                "select * from study_batch_operation_items order by item_index"
            ).fetchall()
        )
    assert "private transcript" not in persisted
    assert "participant-name.txt" not in persisted


def test_study_batch_operations_serialize_concurrent_exact_starts(tmp_path) -> None:
    store = _store(tmp_path)
    _begin(store)
    store.fail(BATCH_ID, error_type="InterruptedError")

    def begin() -> str:
        return _begin(StudyBatchOperationStore(tmp_path, "study-one"))

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(begin) for _ in range(2)]
    results = []
    errors = []
    for future in futures:
        try:
            results.append(future.result())
        except Exception as exc:
            errors.append(exc)

    assert results == [BATCH_ID]
    assert len(errors) == 1
    assert isinstance(errors[0], StudyBatchOperationConflict)
    assert store.list_operations(incomplete_only=True)[0]["attempt_count"] == 2


def test_study_batch_operations_serialize_distinct_concurrent_cold_starts(
    tmp_path,
) -> None:
    StudyWorkspaceStore(tmp_path).create_study(
        {"id": "study-one", "name": "Study One"}
    )
    db_path = tmp_path / "studies" / "study-one" / "batch_operations.sqlite3"
    assert not db_path.exists()
    barrier = Barrier(2)
    batch_ids = [
        "batch_20260729000001_a1b2c3d4",
        "batch_20260729000002_a1b2c3d4",
    ]

    def begin(batch_id: str) -> str:
        barrier.wait(timeout=5)
        return StudyBatchOperationStore(tmp_path, "study-one").begin(
            batch_id=batch_id,
            skill_pack_version_id="pack-1_0_0",
            skill_pack_sha256="f" * 64,
            request_sha256=sha256(batch_id.encode("utf-8")).hexdigest(),
            item_count=0,
            created_at=CREATED_AT,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(begin, batch_ids))

    assert results == batch_ids
    operations = StudyBatchOperationStore(
        tmp_path,
        "study-one",
    ).list_operations()
    assert {operation["batch_id"] for operation in operations} == set(batch_ids)


def test_study_batch_operations_serialize_conflicting_concurrent_starts(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    store.migration_status()
    barrier = Barrier(2)

    def begin(request_sha256):
        barrier.wait()
        return StudyBatchOperationStore(tmp_path, "study-one").begin(
            batch_id=BATCH_ID,
            skill_pack_version_id="pack-1_0_0",
            skill_pack_sha256="f" * 64,
            request_sha256=request_sha256,
            item_count=1,
            created_at=CREATED_AT,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(begin, "a" * 64),
            executor.submit(begin, "9" * 64),
        ]
    results = []
    errors = []
    for future in futures:
        try:
            results.append(future.result())
        except Exception as exc:
            errors.append(exc)

    assert results == [BATCH_ID]
    assert len(errors) == 1
    assert isinstance(errors[0], StudyBatchOperationConflict)
    assert store.list_operations(incomplete_only=True)[0]["attempt_count"] == 1


def test_study_batch_database_rejects_invalid_states_and_item_indexes(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    _begin(store)
    _reserve_item(store)

    with sqlite3.connect(store.db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                update study_batch_operations
                set status = 'completed'
                where batch_id = ?
                """,
                (BATCH_ID,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                update study_batch_operations
                set status = 'failed'
                where batch_id = ?
                """,
                (BATCH_ID,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                update study_batch_operation_items
                set stage = 'rejected'
                where batch_id = ? and item_index = 0
                """,
                (BATCH_ID,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                update study_batch_operation_items
                set stage = 'analysis_completed', run_payload_sha256 = 'bad'
                where batch_id = ? and item_index = 0
                """,
                (BATCH_ID,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                insert into study_batch_operation_items (
                  batch_id, item_index, item_request_sha256,
                  run_id, import_id, project_source_id,
                  source_blob_sha256, transcript_sha256,
                  transcript_revision_id, run_payload_sha256,
                  stage, last_error_type, created_at, updated_at
                ) values (?, 1, ?, 'run_other', 'imp_other', 'psrc_other',
                          ?, ?, 'trv_other', '', 'prepared', '', ?, ?)
                """,
                (
                    BATCH_ID,
                    "8" * 64,
                    "7" * 64,
                    "6" * 64,
                    CREATED_AT,
                    CREATED_AT,
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                update study_batch_operation_items set item_index = 1
                where batch_id = ? and item_index = 0
                """,
                (BATCH_ID,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                update study_batch_operations set item_count = 0
                where batch_id = ?
                """,
                (BATCH_ID,),
            )


def test_study_batch_operations_bound_results_and_refuse_newer_schema(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    for index in range(3):
        batch_id = f"batch_2026072900000{index}_a1b2c3d4"
        store.begin(
            batch_id=batch_id,
            skill_pack_version_id="pack-1_0_0",
            skill_pack_sha256="f" * 64,
            request_sha256=f"{index}" * 64,
            item_count=0,
            created_at=CREATED_AT,
        )
        store.advance(batch_id, "items_processed")
        store.record_aggregate_written(
            batch_id,
            aggregate_payload_sha256="e" * 64,
        )
        for stage in (
            "csv_exports_written",
            "batch_manifest_written",
            "audit_recorded",
        ):
            store.advance(batch_id, stage)
        store.complete(batch_id)

    assert len(store.list_operations(limit=0)) == 1
    assert len(store.list_operations(limit=2)) == 2

    future_store = _store(tmp_path / "future")
    with sqlite3.connect(future_store.db_path) as connection:
        connection.execute("pragma user_version = 99")
    with pytest.raises(SchemaCompatibilityError, match="newer"):
        future_store.list_operations()


def test_study_batch_archive_validation_upgrades_canonical_v1_schema(
    tmp_path,
) -> None:
    db_path = tmp_path / "canonical-v1.sqlite3"
    with sqlite3.connect(db_path) as connection:
        apply_migrations(
            connection,
            database_name="study batch operations v1 fixture",
            migrations=STUDY_BATCH_OPERATION_MIGRATIONS[:1],
        )

    validate_study_batch_operation_database(db_path, "study-one")

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("pragma user_version").fetchone()[0] == 3


def test_study_batch_v1_schema_signature_is_frozen() -> None:
    with sqlite3.connect(":memory:") as connection:
        apply_migrations(
            connection,
            database_name="study batch operations v1 fixture",
            migrations=STUDY_BATCH_OPERATION_MIGRATIONS[:1],
        )
        signature = [
            (
                str(row[0]),
                str(row[1]),
                str(row[2]),
                " ".join(str(row[3] or "").split()),
            )
            for row in connection.execute(
                """
                select type, name, tbl_name, sql from sqlite_master
                where name not like 'sqlite_%'
                order by type, name
                """
            )
        ]

    assert _canonical_json_sha256(signature) == (
        "49957732d9853fa0b0b34921b8dca6f4992c25c7bf9072766f506e06a16d77e5"
    )


def test_study_batch_archive_validation_backfills_completed_v1_journal(
    tmp_path,
) -> None:
    aggregate_payload = _v1_aggregate_payload()
    db_path = _create_populated_v1_journal(
        tmp_path,
        aggregate_payload=aggregate_payload,
    )

    validate_study_batch_operation_database(db_path, "study-one")

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("pragma user_version").fetchone()[0] == 3
        assert connection.execute(
            """
            select aggregate_payload_sha256 from study_batch_operations
            where batch_id = ?
            """,
            (BATCH_ID,),
        ).fetchone()[0] == _canonical_json_sha256(aggregate_payload)


@pytest.mark.parametrize(
    ("aggregate_payload", "malformed_aggregate"),
    [
        (None, False),
        (None, True),
        (_v1_aggregate_payload(batch_id="batch_20260729000001_a1b2c3d4"), False),
        (
            {
                **_v1_aggregate_payload(),
                "skill_pack_version_id": "other-9_9_9",
            },
            False,
        ),
        (
            {
                **_v1_aggregate_payload(),
                "created_at": "2040-01-01T00:00:00+00:00",
            },
            False,
        ),
        ({**_v1_aggregate_payload(), "run_count": 1}, False),
        ({**_v1_aggregate_payload(), "failure_count": 1}, False),
    ],
)
def test_study_batch_archive_validation_keeps_invalid_completed_v1_atomic(
    tmp_path,
    aggregate_payload,
    malformed_aggregate,
) -> None:
    db_path = _create_populated_v1_journal(
        tmp_path,
        aggregate_payload=aggregate_payload,
        malformed_aggregate=malformed_aggregate,
    )

    with pytest.raises(SchemaCompatibilityError, match="migration 2"):
        validate_study_batch_operation_database(db_path, "study-one")

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("pragma user_version").fetchone()[0] == 1
        assert "aggregate_payload_sha256" not in {
            str(row[1])
            for row in connection.execute(
                "pragma table_info(study_batch_operations)"
            )
        }


def test_study_batch_archive_validation_upgrades_failed_v1_without_aggregate(
    tmp_path,
) -> None:
    db_path = _create_populated_v1_journal(tmp_path, status="failed")

    validate_study_batch_operation_database(db_path, "study-one")

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("pragma user_version").fetchone()[0] == 3
        assert connection.execute(
            """
            select aggregate_payload_sha256 from study_batch_operations
            where batch_id = ?
            """,
            (BATCH_ID,),
        ).fetchone()[0] == ""


def test_study_batch_archive_validation_repairs_completed_original_v2(
    tmp_path,
) -> None:
    aggregate_payload = _v1_aggregate_payload()
    db_path = _create_populated_v1_journal(
        tmp_path,
        aggregate_payload=aggregate_payload,
    )
    _promote_to_original_v2(db_path)

    validate_study_batch_operation_database(db_path, "study-one")

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("pragma user_version").fetchone()[0] == 3
        assert connection.execute(
            """
            select aggregate_payload_sha256 from study_batch_operations
            where batch_id = ?
            """,
            (BATCH_ID,),
        ).fetchone()[0] == _canonical_json_sha256(aggregate_payload)


def test_study_batch_archive_validation_keeps_original_v2_repair_atomic(
    tmp_path,
) -> None:
    db_path = _create_populated_v1_journal(
        tmp_path,
        aggregate_payload=_v1_aggregate_payload(),
    )
    _promote_to_original_v2(db_path)
    (
        tmp_path / "batches" / BATCH_ID / "aggregate_results.json"
    ).unlink()

    with pytest.raises(SchemaCompatibilityError, match="migration 3"):
        validate_study_batch_operation_database(db_path, "study-one")

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("pragma user_version").fetchone()[0] == 2
        assert connection.execute(
            """
            select aggregate_payload_sha256 from study_batch_operations
            where batch_id = ?
            """,
            (BATCH_ID,),
        ).fetchone()[0] == ""


def test_study_batch_archive_validation_rejects_forged_v1_schema(
    tmp_path,
) -> None:
    db_path = tmp_path / "forged-v1.sqlite3"
    with sqlite3.connect(db_path) as connection:
        apply_migrations(
            connection,
            database_name="study batch operations v1 fixture",
            migrations=STUDY_BATCH_OPERATION_MIGRATIONS[:1],
        )
        connection.execute(
            """
            create trigger forged_history_delete
            after insert on study_batch_operations
            begin
              delete from study_batch_operations where batch_id != new.batch_id;
            end
            """
        )

    with pytest.raises(ValueError, match="schema definition is invalid"):
        validate_study_batch_operation_database(db_path, "study-one")
