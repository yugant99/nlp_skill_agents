import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from backend.storage.sqlite_migrations import SchemaCompatibilityError
from backend.storage.study_batch_operation_store import (
    StudyBatchOperationConflict,
    StudyBatchOperationStore,
)
from backend.storage.study_store import StudyWorkspaceStore


BATCH_ID = "batch_20260729000000_a1b2c3d4"
CREATED_AT = "2026-07-29T00:00:00+00:00"


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
    for stage in (
        "items_processed",
        "aggregate_json_written",
        "csv_exports_written",
        "batch_manifest_written",
        "audit_recorded",
    ):
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
    assert [item["version"] for item in store.migration_status()] == [1]
    assert store.db_path == (
        tmp_path / "studies" / "study-one" / "batch_operations.sqlite3"
    )
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("pragma user_version").fetchone()[0] == 1


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
    for stage in (
        "aggregate_json_written",
        "csv_exports_written",
        "batch_manifest_written",
        "audit_recorded",
    ):
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
        for stage in (
            "items_processed",
            "aggregate_json_written",
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
