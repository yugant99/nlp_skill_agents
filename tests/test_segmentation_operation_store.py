import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier

import pytest

from backend.segmentation import pipeline as segmentation_pipeline
from backend.segmentation.pipeline import (
    PatchOperation,
    SegmentationRunStore,
    _segmentation_payload_sha256,
)
from backend.storage.evidence_catalog import EvidenceCatalog
from backend.storage.segmentation_operation_store import (
    SegmentationOperationConflict,
    SegmentationOperationStore,
)
from backend.storage.source_blob_store import SourceBlobStore
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


def _seed_segmentation_run(tmp_path):
    seed_store = SegmentationRunStore(tmp_path / "seed")
    run = seed_store.create_run(
        source_filename="session.txt",
        descript_text="[00:00:00] P: Good morning.\n[00:00:03] Av: Uh yes.",
        rule_ids=[
            "speaker-markers",
            "timestamp-markers",
            "pause-markers",
            "filled-pauses",
        ],
    )
    target_root = tmp_path / "target"
    return SegmentationRunStore(target_root), target_root, run


def test_segmentation_operations_version_and_track_exact_retries(tmp_path) -> None:
    store = SegmentationOperationStore(tmp_path)

    first_operation = _begin_create(store)
    with pytest.raises(SegmentationOperationConflict, match="already running"):
        _begin_create(store)
    store.fail(first_operation, error_type="InterruptedError")
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

    with pytest.raises(SegmentationOperationConflict, match="already running"):
        store.begin(
            run_id="run_one",
            import_id="imp_one",
            operation_kind="patch",
            previous_payload_sha256="a" * 64,
            payload_sha256="b" * 64,
        )
    with pytest.raises(
        SegmentationOperationConflict,
        match="run import identity conflicts",
    ):
        _begin_create(
            store,
            import_id="imp_changed",
            payload_sha256="c" * 64,
        )
    with pytest.raises(
        SegmentationOperationConflict,
        match="import run identity conflicts",
    ):
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


def test_segmentation_operations_serialize_concurrent_exact_starts(tmp_path) -> None:
    store = SegmentationOperationStore(tmp_path)
    operation_id = _begin_create(store)
    store.fail(operation_id, error_type="InterruptedError")

    def begin() -> str:
        return _begin_create(SegmentationOperationStore(tmp_path))

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(begin) for _ in range(2)]
    results = []
    errors = []
    for future in futures:
        try:
            results.append(future.result())
        except Exception as exc:
            errors.append(exc)

    assert results == [operation_id]
    assert len(errors) == 1
    assert isinstance(errors[0], SegmentationOperationConflict)
    assert "already running" in str(errors[0])
    running = store.list_operations(incomplete_only=True)
    assert len(running) == 1
    assert running[0]["status"] == "running"
    assert running[0]["attempt_count"] == 2


def test_segmentation_operations_serialize_distinct_mutations_for_one_run(
    tmp_path,
) -> None:
    store = SegmentationOperationStore(tmp_path)
    store.migration_status()
    barrier = Barrier(2)

    def begin(operation_kind, payload_sha256):
        barrier.wait()
        return store.begin(
            run_id="run_one",
            import_id="imp_one",
            operation_kind=operation_kind,
            previous_payload_sha256="a" * 64,
            payload_sha256=payload_sha256,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(begin, "patch", "b" * 64),
            executor.submit(begin, "verify", "c" * 64),
        ]
    results = []
    errors = []
    for future in futures:
        try:
            results.append(future.result())
        except Exception as exc:
            errors.append(exc)

    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], SegmentationOperationConflict)
    assert "already running" in str(errors[0])
    running = store.list_operations(incomplete_only=True)
    assert len(running) == 1
    assert running[0]["operation_id"] == results[0]
    assert running[0]["operation_kind"] in {"patch", "verify"}


def test_segmentation_operations_refuse_newer_schema(tmp_path) -> None:
    database_path = tmp_path / "segmentation.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("pragma user_version = 99")

    with pytest.raises(SchemaCompatibilityError, match="newer"):
        SegmentationOperationStore(tmp_path).list_operations()


def test_segmentation_persistence_journals_failure_and_exact_retry(
    tmp_path,
    monkeypatch,
) -> None:
    target_store, target_root, run = _seed_segmentation_run(tmp_path)
    original_record_import = EvidenceCatalog.record_import

    def fail_target_import(self, record):
        if self.root == target_root:
            raise OSError("private transcript content must not enter the journal")
        return original_record_import(self, record)

    with monkeypatch.context() as patch:
        patch.setattr(EvidenceCatalog, "record_import", fail_target_import)
        with pytest.raises(OSError, match="private transcript"):
            target_store.persist_run(
                run,
                operation_kind="create",
                expected_previous_payload_sha256="",
            )

    failed = SegmentationOperationStore(target_root).list_operations()[0]
    assert failed["status"] == "failed"
    assert failed["stage"] == "source_blob_stored"
    assert failed["last_error_type"] == "OSError"
    assert "private transcript" not in str(failed)
    assert not (target_root / "segmentation_runs" / f"{run.run_id}.json").exists()

    stored = target_store.persist_run(
        run,
        operation_kind="create",
        expected_previous_payload_sha256="",
    )

    completed = SegmentationOperationStore(target_root).list_operations()[0]
    assert completed["status"] == "completed"
    assert completed["attempt_count"] == 2
    assert len(EvidenceCatalog(target_root).list_imports()) == 1
    assert target_store.load_run(run.run_id) == stored


def test_segmentation_persistence_records_source_blob_failure(
    tmp_path,
    monkeypatch,
) -> None:
    target_store, target_root, run = _seed_segmentation_run(tmp_path)
    original_store = SourceBlobStore.store

    def fail_target_blob(self, content, expected_sha256):
        if self.root == target_root:
            raise OSError("sensitive blob failure")
        return original_store(self, content, expected_sha256)

    with monkeypatch.context() as patch:
        patch.setattr(SourceBlobStore, "store", fail_target_blob)
        with pytest.raises(OSError, match="sensitive blob"):
            target_store.persist_run(
                run,
                operation_kind="create",
                expected_previous_payload_sha256="",
            )

    failed = SegmentationOperationStore(target_root).list_operations()[0]
    assert failed["status"] == "failed"
    assert failed["stage"] == "prepared"
    assert failed["last_error_type"] == "OSError"
    assert "sensitive blob" not in str(failed)

    stored = target_store.persist_run(
        run,
        operation_kind="create",
        expected_previous_payload_sha256="",
    )
    completed = SegmentationOperationStore(target_root).list_operations()[0]
    assert completed["status"] == "completed"
    assert completed["attempt_count"] == 2
    assert target_store.load_run(run.run_id) == stored


def test_segmentation_persistence_includes_specialist_artifacts_in_journal(
    tmp_path,
    monkeypatch,
) -> None:
    target_store, target_root, run = _seed_segmentation_run(tmp_path)
    original_atomic_write_text = segmentation_pipeline.atomic_write_text

    def fail_specialist_write(path, content, **kwargs):
        if str(path).endswith(".html"):
            raise OSError("sensitive packet failure")
        return original_atomic_write_text(path, content, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(
            segmentation_pipeline,
            "atomic_write_text",
            fail_specialist_write,
        )
        with pytest.raises(OSError, match="sensitive packet"):
            target_store.persist_run(
                run,
                operation_kind="create",
                expected_previous_payload_sha256="",
            )

    failed = SegmentationOperationStore(target_root).list_operations()[0]
    assert failed["status"] == "failed"
    assert failed["stage"] == "evidence_cataloged"
    assert failed["last_error_type"] == "OSError"
    assert "sensitive packet" not in str(failed)
    assert not (target_root / "segmentation_runs" / f"{run.run_id}.json").exists()

    target_store.persist_run(
        run,
        operation_kind="create",
        expected_previous_payload_sha256="",
    )
    assert all(
        (target_root / "segmentation_runs" / run.run_id / "specialists" / path).exists()
        for path in ("speaker_turn.html", "timing_pause.html", "repair_overlap.html")
    )


def test_segmentation_persistence_recovers_when_snapshot_precedes_stage_update(
    tmp_path,
    monkeypatch,
) -> None:
    target_store, target_root, run = _seed_segmentation_run(tmp_path)
    original_advance = SegmentationOperationStore.advance

    def fail_snapshot_advance(self, operation_id, stage):
        if self.root == target_root and stage == "snapshot_written":
            raise OSError("process stopped after snapshot write")
        return original_advance(self, operation_id, stage)

    with monkeypatch.context() as patch:
        patch.setattr(
            SegmentationOperationStore,
            "advance",
            fail_snapshot_advance,
        )
        with pytest.raises(OSError, match="process stopped"):
            target_store.persist_run(
                run,
                operation_kind="create",
                expected_previous_payload_sha256="",
            )

    run_path = target_root / "segmentation_runs" / f"{run.run_id}.json"
    assert run_path.exists()
    failed = SegmentationOperationStore(target_root).list_operations()[0]
    assert failed["status"] == "failed"
    assert failed["stage"] == "specialist_artifacts_written"

    target_store.persist_run(
        run,
        operation_kind="create",
        expected_previous_payload_sha256="",
    )
    completed = SegmentationOperationStore(target_root).list_operations()[0]
    assert completed["status"] == "completed"
    assert completed["attempt_count"] == 2


def test_segmentation_persistence_records_snapshot_write_failure(
    tmp_path,
    monkeypatch,
) -> None:
    target_store, target_root, run = _seed_segmentation_run(tmp_path)
    original_atomic_write_text = segmentation_pipeline.atomic_write_text

    def fail_snapshot_write(path, content, **kwargs):
        if Path(path).name == f"{run.run_id}.json":
            raise OSError("sensitive snapshot failure")
        return original_atomic_write_text(path, content, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(
            segmentation_pipeline,
            "atomic_write_text",
            fail_snapshot_write,
        )
        with pytest.raises(OSError, match="sensitive snapshot"):
            target_store.persist_run(
                run,
                operation_kind="create",
                expected_previous_payload_sha256="",
            )

    failed = SegmentationOperationStore(target_root).list_operations()[0]
    assert failed["status"] == "failed"
    assert failed["stage"] == "specialist_artifacts_written"
    assert failed["last_error_type"] == "OSError"
    assert not (target_root / "segmentation_runs" / f"{run.run_id}.json").exists()

    stored = target_store.persist_run(
        run,
        operation_kind="create",
        expected_previous_payload_sha256="",
    )
    completed = SegmentationOperationStore(target_root).list_operations()[0]
    assert completed["status"] == "completed"
    assert completed["attempt_count"] == 2
    assert target_store.load_run(run.run_id) == stored


def test_segmentation_persistence_recovers_when_completion_marker_fails(
    tmp_path,
    monkeypatch,
) -> None:
    target_store, target_root, run = _seed_segmentation_run(tmp_path)
    original_complete = SegmentationOperationStore.complete

    def fail_target_complete(self, operation_id):
        if self.root == target_root:
            raise OSError("completion marker failed")
        return original_complete(self, operation_id)

    with monkeypatch.context() as patch:
        patch.setattr(
            SegmentationOperationStore,
            "complete",
            fail_target_complete,
        )
        with pytest.raises(OSError, match="completion marker"):
            target_store.persist_run(
                run,
                operation_kind="create",
                expected_previous_payload_sha256="",
            )

    failed = SegmentationOperationStore(target_root).list_operations()[0]
    assert failed["status"] == "failed"
    assert failed["stage"] == "snapshot_written"

    target_store.persist_run(
        run,
        operation_kind="create",
        expected_previous_payload_sha256="",
    )
    completed = SegmentationOperationStore(target_root).list_operations()[0]
    assert completed["status"] == "completed"
    assert completed["attempt_count"] == 2


@pytest.mark.parametrize(
    ("failure_point", "failed_stage"),
    [
        ("snapshot_advance", "specialist_artifacts_written"),
        ("complete", "snapshot_written"),
    ],
)
def test_mutable_segmentation_retry_accepts_its_already_applied_target(
    tmp_path,
    monkeypatch,
    failure_point,
    failed_stage,
) -> None:
    store = SegmentationRunStore(tmp_path)
    run = store.create_run(
        source_filename="session.txt",
        descript_text="[00:00:00] P: Preserve the mutable target.",
        rule_ids=["speaker-markers"],
    )
    previous_payload_sha256 = _segmentation_payload_sha256(run)
    updated = replace(run, merged_draft=f"{run.merged_draft}\n")
    target_payload_sha256 = _segmentation_payload_sha256(updated)
    original_advance = SegmentationOperationStore.advance
    original_complete = SegmentationOperationStore.complete

    def fail_snapshot_advance(self, operation_id, stage):
        if self.root == tmp_path and stage == "snapshot_written":
            raise OSError("mutable snapshot stage failed")
        return original_advance(self, operation_id, stage)

    def fail_complete(self, operation_id):
        if self.root == tmp_path:
            raise OSError("mutable completion failed")
        return original_complete(self, operation_id)

    with monkeypatch.context() as patch:
        if failure_point == "snapshot_advance":
            patch.setattr(
                SegmentationOperationStore,
                "advance",
                fail_snapshot_advance,
            )
        else:
            patch.setattr(SegmentationOperationStore, "complete", fail_complete)
        with pytest.raises(OSError, match="mutable"):
            store.persist_run(
                updated,
                operation_kind="rewrite",
                expected_previous_payload_sha256=previous_payload_sha256,
            )

    assert store.load_run(run.run_id) == updated
    failed = next(
        operation
        for operation in SegmentationOperationStore(tmp_path).list_operations()
        if operation["operation_kind"] == "rewrite"
    )
    assert failed["status"] == "failed"
    assert failed["stage"] == failed_stage

    stored = store.persist_run(
        updated,
        operation_kind="rewrite",
        expected_previous_payload_sha256=previous_payload_sha256,
    )

    assert store.load_run(run.run_id) == stored
    completed = next(
        operation
        for operation in SegmentationOperationStore(tmp_path).list_operations()
        if operation["operation_kind"] == "rewrite"
    )
    assert completed["status"] == "completed"
    assert completed["attempt_count"] == 2
    assert completed["previous_payload_sha256"] == previous_payload_sha256
    assert completed["payload_sha256"] == target_payload_sha256


def test_hard_interruption_leaves_visible_running_operation(tmp_path) -> None:
    target_root = tmp_path / "hard-stop"
    script = """
import os
import sys
from backend.segmentation.pipeline import SegmentationRunStore
from backend.storage.evidence_catalog import EvidenceCatalog

def stop_after_blob(self, record):
    os._exit(23)

EvidenceCatalog.record_import = stop_after_blob
SegmentationRunStore(sys.argv[1]).create_run(
    source_filename="hard-stop.txt",
    descript_text="[00:00:00] P: Preserve the visible journal.",
    rule_ids=["speaker-markers"],
)
"""

    process = subprocess.run(
        [sys.executable, "-c", script, str(target_root)],
        cwd=Path(__file__).parents[1],
        check=False,
    )

    assert process.returncode == 23
    operations = SegmentationOperationStore(target_root).list_operations(
        incomplete_only=True
    )
    assert len(operations) == 1
    assert operations[0]["status"] == "running"
    assert operations[0]["stage"] == "source_blob_stored"
    assert not (
        target_root / "segmentation_runs" / f"{operations[0]['run_id']}.json"
    ).exists()


def test_segmentation_persistence_rejects_stale_and_conflicting_snapshots(
    tmp_path,
) -> None:
    store = SegmentationRunStore(tmp_path)
    run = store.create_run(
        source_filename="session.txt",
        descript_text="[00:00:00] P: Good morning.\n[00:00:03] Av: Uh yes.",
        rule_ids=["speaker-markers", "timestamp-markers"],
    )
    stale = store.load_run(run.run_id)
    stale_hash = _segmentation_payload_sha256(stale)
    store.apply_specialist_patches(
        run.run_id,
        specialist_id="speaker_turn",
        patches=[
            PatchOperation(
                operation="event_line",
                event_index=0,
                text="P: Revised greeting.",
                reason="stale-write regression test",
            )
        ],
    )

    with pytest.raises(ValueError, match="changed before persistence"):
        store.persist_run(
            stale,
            operation_kind="verify",
            expected_previous_payload_sha256=stale_hash,
        )

    current = store.load_run(run.run_id)
    current_hash = _segmentation_payload_sha256(current)
    conflicting = replace(current, source_filename="changed.txt")
    with pytest.raises(ValueError, match="identity conflicts"):
        store.persist_run(
            conflicting,
            operation_kind="rewrite",
            expected_previous_payload_sha256=current_hash,
        )

    assert store.load_run(run.run_id) == current
    operations = SegmentationOperationStore(tmp_path).list_operations()
    assert len(operations) == 4
    failed_verify = next(
        operation
        for operation in operations
        if operation["operation_kind"] == "verify"
    )
    assert failed_verify["status"] == "failed"
    assert failed_verify["stage"] == "prepared"
    failed_rewrite = next(
        operation
        for operation in operations
        if operation["operation_kind"] == "rewrite"
    )
    assert failed_rewrite["status"] == "failed"
    assert failed_rewrite["last_error_type"] == "SegmentationSnapshotConflict"


def test_segmentation_persistence_requires_explicit_mutable_predecessor(
    tmp_path,
) -> None:
    store = SegmentationRunStore(tmp_path)
    run = store.create_run(
        source_filename="session.txt",
        descript_text="[00:00:00] P: Keep this snapshot.",
        rule_ids=["speaker-markers"],
    )

    with pytest.raises(ValueError, match="previous payload hash"):
        store.persist_run(
            run,
            operation_kind="rewrite",
            expected_previous_payload_sha256=None,
        )

    assert store.load_run(run.run_id) == run
    operations = SegmentationOperationStore(tmp_path).list_operations()
    assert len(operations) == 1
    assert operations[0]["operation_kind"] == "create"
    assert operations[0]["status"] == "completed"


def test_segmentation_mutations_record_completed_hash_chain(tmp_path) -> None:
    store = SegmentationRunStore(tmp_path)
    created = store.create_run(
        source_filename="session.txt",
        descript_text="[00:00:00] P: Good morning.\n[00:00:03] Av: Uh yes.",
        rule_ids=["speaker-markers", "timestamp-markers"],
    )
    created_hash = _segmentation_payload_sha256(created)
    patched = store.apply_specialist_patches(
        created.run_id,
        specialist_id="speaker_turn",
        patches=[
            PatchOperation(
                operation="event_line",
                event_index=0,
                text="P: Updated greeting.",
                reason="hash-chain proof",
            )
        ],
    )
    patched_hash = _segmentation_payload_sha256(patched)
    verified = store.verify_run(created.run_id)
    verified_hash = _segmentation_payload_sha256(verified)

    operations = {
        operation["operation_kind"]: operation
        for operation in SegmentationOperationStore(tmp_path).list_operations()
    }
    assert set(operations) == {"create", "patch", "verify"}
    assert operations["create"]["previous_payload_sha256"] == ""
    assert operations["create"]["payload_sha256"] == created_hash
    assert operations["patch"]["previous_payload_sha256"] == created_hash
    assert operations["patch"]["payload_sha256"] == patched_hash
    assert operations["verify"]["previous_payload_sha256"] == patched_hash
    assert operations["verify"]["payload_sha256"] == verified_hash
    assert all(operation["status"] == "completed" for operation in operations.values())


def test_segmentation_persistence_rechecks_snapshot_after_claiming_operation(
    tmp_path,
    monkeypatch,
) -> None:
    store = SegmentationRunStore(tmp_path)
    run = store.create_run(
        source_filename="session.txt",
        descript_text="[00:00:00] P: Good morning.\n[00:00:03] Av: Uh yes.",
        rule_ids=["speaker-markers", "timestamp-markers"],
    )
    stale = store.load_run(run.run_id)
    stale_hash = _segmentation_payload_sha256(stale)
    original_begin = SegmentationOperationStore.begin
    competing_write_finished = False

    def begin_after_competing_write(self, **kwargs):
        nonlocal competing_write_finished
        if self.root == tmp_path and not competing_write_finished:
            competing_write_finished = True
            store.apply_specialist_patches(
                run.run_id,
                specialist_id="speaker_turn",
                patches=[
                    PatchOperation(
                        operation="event_line",
                        event_index=0,
                        text="P: Concurrent update wins.",
                        reason="interleaving regression test",
                    )
                ],
            )
        return original_begin(self, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(
            SegmentationOperationStore,
            "begin",
            begin_after_competing_write,
        )
        with pytest.raises(ValueError, match="changed before persistence"):
            store.persist_run(
                stale,
                operation_kind="verify",
                expected_previous_payload_sha256=stale_hash,
            )

    current = store.load_run(run.run_id)
    assert "P: Concurrent update wins." in current.merged_draft
    assert "P: Good morning." not in current.merged_draft
    assert _segmentation_payload_sha256(current) != stale_hash
