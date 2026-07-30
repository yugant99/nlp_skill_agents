import sqlite3

import pytest

from backend.storage.segmentation_operation_store import SegmentationOperationStore
from backend.storage.sqlite_migrations import SchemaCompatibilityError


def _begin_create(
    store: SegmentationOperationStore,
    *,
    run_id: str = "run_one",
    import_id: str = "imp_one",
    payload_sha256: str = "a" * 64,
) -> str:
    return store.begin(
        run_id=run_id,
        import_id=import_id,
        operation_kind="create",
        previous_payload_sha256="",
        payload_sha256=payload_sha256,
    )


def _advance_to_snapshot(
    store: SegmentationOperationStore,
    operation_id: str,
) -> None:
    for stage in (
        "source_blob_stored",
        "evidence_cataloged",
        "specialist_artifacts_written",
        "snapshot_written",
    ):
        store.advance(operation_id, stage)


def test_segmentation_operations_version_and_track_exact_retries(tmp_path) -> None:
    store = SegmentationOperationStore(tmp_path)

    first_operation = _begin_create(store)
    repeated_operation = _begin_create(store)

    assert first_operation == repeated_operation
    operations = store.list_operations()
    assert len(operations) == 1
    assert operations[0]["attempt_count"] == 2
    assert operations[0]["operation_kind"] == "create"
    assert operations[0]["previous_payload_sha256"] == ""
    assert operations[0]["payload_sha256"] == "a" * 64
    assert [item["version"] for item in store.migration_status()] == [1]
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("pragma user_version").fetchone()[0] == 1


def test_segmentation_operations_version_mutable_run_payloads(tmp_path) -> None:
    store = SegmentationOperationStore(tmp_path)
    first_operation = _begin_create(store)
    _advance_to_snapshot(store, first_operation)
    store.complete(first_operation)

    patch_operation = store.begin(
        run_id="run_one",
        import_id="imp_one",
        operation_kind="patch",
        previous_payload_sha256="a" * 64,
        payload_sha256="b" * 64,
    )

    assert patch_operation != first_operation
    operations = store.list_operations()
    assert len(operations) == 2
    assert {item["operation_kind"] for item in operations} == {"create", "patch"}


def test_segmentation_operations_reject_identity_conflicts_and_bad_fields(
    tmp_path,
) -> None:
    store = SegmentationOperationStore(tmp_path)
    _begin_create(store)

    with pytest.raises(RuntimeError, match="already running"):
        store.begin(
            run_id="run_one",
            import_id="imp_one",
            operation_kind="patch",
            previous_payload_sha256="a" * 64,
            payload_sha256="b" * 64,
        )
    with pytest.raises(ValueError, match="run import identity conflicts"):
        _begin_create(
            store,
            import_id="imp_changed",
            payload_sha256="c" * 64,
        )
    with pytest.raises(ValueError, match="import run identity conflicts"):
        _begin_create(
            store,
            run_id="run_changed",
            payload_sha256="c" * 64,
        )
    with pytest.raises(ValueError, match="non-empty"):
        _begin_create(store, run_id=" ")
    with pytest.raises(ValueError, match="Unsupported.*kind"):
        store.begin(
            run_id="run_two",
            import_id="imp_two",
            operation_kind="delete",
            previous_payload_sha256="",
            payload_sha256="d" * 64,
        )
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        _begin_create(store, run_id="run_two", import_id="imp_two", payload_sha256="x")
    with pytest.raises(ValueError, match="cannot have a previous"):
        store.begin(
            run_id="run_two",
            import_id="imp_two",
            operation_kind="create",
            previous_payload_sha256="a" * 64,
            payload_sha256="b" * 64,
        )


def test_segmentation_operations_enforce_transitions(tmp_path) -> None:
    store = SegmentationOperationStore(tmp_path)
    operation_id = _begin_create(store)

    with pytest.raises(ValueError, match="cannot advance"):
        store.advance(operation_id, "evidence_cataloged")
    with pytest.raises(ValueError, match="Unsupported.*stage"):
        store.advance(operation_id, "invented")
    with pytest.raises(ValueError, match="before snapshot_written"):
        store.complete(operation_id)
    with pytest.raises(FileNotFoundError, match="missing"):
        store.advance("missing", "source_blob_stored")

    _advance_to_snapshot(store, operation_id)
    store.complete(operation_id)

    with pytest.raises(RuntimeError, match="not running"):
        store.advance(operation_id, "source_blob_stored")
    with pytest.raises(RuntimeError, match="not running"):
        store.fail(operation_id, error_type="OSError")


def test_segmentation_operations_record_failure_and_retry(tmp_path) -> None:
    store = SegmentationOperationStore(tmp_path)
    operation_id = _begin_create(
        store,
        run_id="run_retry",
        import_id="imp_retry",
        payload_sha256="c" * 64,
    )
    store.advance(operation_id, "source_blob_stored")
    store.fail(operation_id, error_type="OSError")

    failed = SegmentationOperationStore(tmp_path).list_operations(
        incomplete_only=True
    )[0]
    assert failed["status"] == "failed"
    assert failed["stage"] == "source_blob_stored"
    assert failed["last_error_type"] == "OSError"

    assert _begin_create(
        store,
        run_id="run_retry",
        import_id="imp_retry",
        payload_sha256="c" * 64,
    ) == operation_id
    _advance_to_snapshot(store, operation_id)
    store.complete(operation_id)

    completed = store.list_operations()[0]
    assert completed["status"] == "completed"
    assert completed["stage"] == "completed"
    assert completed["attempt_count"] == 2
    assert completed["last_error_type"] == ""
    assert completed["completed_at"]
    assert store.list_operations(incomplete_only=True) == []

    with pytest.raises(ValueError, match="exception class name"):
        store.fail(operation_id, error_type="OSError: transcript content")


def test_segmentation_operations_bound_list_limit(tmp_path) -> None:
    store = SegmentationOperationStore(tmp_path)
    for index in range(3):
        operation_id = _begin_create(
            store,
            run_id=f"run_{index}",
            import_id=f"imp_{index}",
            payload_sha256=f"{index}" * 64,
        )
        _advance_to_snapshot(store, operation_id)
        store.complete(operation_id)

    assert len(store.list_operations(limit=0)) == 1
    assert len(store.list_operations(limit=2)) == 2


def test_segmentation_operations_refuse_newer_schema(tmp_path) -> None:
    database_path = tmp_path / "segmentation.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("pragma user_version = 99")

    with pytest.raises(SchemaCompatibilityError, match="newer"):
        SegmentationOperationStore(tmp_path).list_operations()
