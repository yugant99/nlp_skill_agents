import json
import sqlite3
from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from backend.storage.evidence_catalog import EvidenceCatalog
from backend.storage.audit_log import AuditLogStore
from backend.storage.project_archive import (
    ProjectArchiveConflict,
    ProjectArchiveError,
    ProjectArchiveStore,
)
from backend.storage.source_blob_store import SourceBlobStore
from backend.storage.study_batch_operation_store import StudyBatchOperationStore
from backend.storage.study_store import StudyWorkspaceStore


def _build_study(root: Path) -> tuple[str, str]:
    store = StudyWorkspaceStore(root)
    study = store.create_study({"name": "Archive Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "archive_pack",
            "name": "Archive Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    batch = store.run_text_batch(
        study.id,
        version.version_id,
        [{"source_filename": "session.txt", "content": "P1_c: One.\nP1_p: Two."}],
    )
    run = store.list_batch_runs(study.id, batch.batch_id)[0]
    return study.id, run["source_blob_sha256"]


def test_project_archive_round_trips_study_evidence_and_source_blobs(tmp_path) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, blob_hash = _build_study(source_root)
    source_store = StudyWorkspaceStore(source_root)
    source_batch = source_store.list_batches(study_id)[0]
    source_journal = StudyBatchOperationStore(source_root, study_id)
    source_operation = source_journal.get_operation(source_batch.batch_id)
    source_items = source_journal.list_items(source_batch.batch_id)

    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    with ZipFile(exported.archive_path) as archive:
        assert "study/batch_operations.sqlite3" in archive.namelist()
    restored = ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)

    restored_store = StudyWorkspaceStore(restore_root)
    restored_study = restored_store.list_studies()[0]
    restored_batch = restored_store.load_batch(study_id, source_batch.batch_id)
    restored_journal = StudyBatchOperationStore(restore_root, study_id)
    restored_imports = EvidenceCatalog(restore_root).workspace_import_records(study_id)
    assert exported.archive_path.exists()
    assert len(exported.archive_sha256) == 64
    assert restored.study_id == study_id
    assert restored_study.id == study_id
    assert restored.import_count == 1
    assert restored.blob_count == 1
    assert restored.audit_event_count == 3
    assert [
        event["event_type"]
        for event in AuditLogStore(restore_root).events_for_subject("study", study_id)
    ] == ["study.created", "skill_pack.versioned", "batch.completed"]
    restored_events = AuditLogStore(restore_root).events_for_subject(
        "study", study_id
    )
    assert AuditLogStore(restore_root).import_events(restored_events) == 0
    conflicting_event = {**restored_events[0], "actor": "different-actor"}
    with pytest.raises(ValueError, match="identity conflicts"):
        AuditLogStore(restore_root).import_events([conflicting_event])
    assert restored_imports[0].source_blob_sha256 == blob_hash
    assert SourceBlobStore(restore_root).read_verified(blob_hash) == (
        b"P1_c: One.\nP1_p: Two."
    )
    assert restored_batch.aggregate_dir == (
        restore_root
        / "studies"
        / study_id
        / "batches"
        / source_batch.batch_id
    )
    assert restored_journal.get_operation(source_batch.batch_id) == source_operation
    assert restored_journal.list_items(source_batch.batch_id) == source_items

    replayed = restored_store.run_text_batch(
        study_id,
        source_batch.skill_pack_version_id,
        [
            {
                "source_filename": "session.txt",
                "content": "P1_c: One.\nP1_p: Two.",
            }
        ],
        batch_id=source_batch.batch_id,
    )
    assert replayed == restored_batch
    assert restored_journal.get_operation(source_batch.batch_id)[
        "attempt_count"
    ] == 1
    assert len(
        [
            event
            for event in AuditLogStore(restore_root).list_events(limit=None)
            if event["event_type"] == "batch.completed"
        ]
    ) == 1

    with pytest.raises(FileExistsError):
        ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)


def test_project_archive_refuses_running_study_batch(tmp_path) -> None:
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Running Archive Study"})
    StudyBatchOperationStore(tmp_path, study.id).begin(
        batch_id="batch_20260729080808_77778888",
        skill_pack_version_id="archive_pack-1_0_0",
        skill_pack_sha256="a" * 64,
        request_sha256="b" * 64,
        item_count=0,
        created_at="2026-07-29T08:08:08+00:00",
    )

    with pytest.raises(ProjectArchiveConflict, match="running batch"):
        ProjectArchiveStore(tmp_path).create_archive(study.id)

    assert not list((tmp_path / "backups").glob("*.nlpstudy.zip"))


def test_project_archive_rejects_newer_batch_journal_before_restore_writes(
    tmp_path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    future_archive_path = tmp_path / "future-journal.nlpstudy.zip"
    future_db_path = tmp_path / "future-batch-operations.sqlite3"

    with ZipFile(exported.archive_path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    future_db_path.write_bytes(members["study/batch_operations.sqlite3"])
    with sqlite3.connect(future_db_path) as connection:
        connection.execute("pragma user_version = 99")
    members["study/batch_operations.sqlite3"] = future_db_path.read_bytes()
    manifest = json.loads(members["manifest.json"].decode("utf-8"))
    journal_record = next(
        record
        for record in manifest["members"]
        if record["path"] == "study/batch_operations.sqlite3"
    )
    journal_record["size_bytes"] = len(members["study/batch_operations.sqlite3"])
    journal_record["sha256"] = sha256(
        members["study/batch_operations.sqlite3"]
    ).hexdigest()
    members["manifest.json"] = json.dumps(
        manifest,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    with ZipFile(future_archive_path, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)

    with pytest.raises(ProjectArchiveConflict, match="newer than supported"):
        ProjectArchiveStore(restore_root).restore_archive(future_archive_path)

    assert not (restore_root / "studies" / study_id).exists()
    assert AuditLogStore(restore_root).list_events(limit=None) == []


def test_project_archive_rejects_hash_mismatch_and_unsafe_paths(tmp_path) -> None:
    source_root = tmp_path / "source"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    corrupt_path = tmp_path / "corrupt.zip"
    with ZipFile(exported.archive_path) as original, ZipFile(
        corrupt_path, "w", compression=ZIP_DEFLATED
    ) as corrupt:
        for info in original.infolist():
            content = original.read(info)
            if info.filename == "study/study.json":
                content = bytes([content[0] ^ 1]) + content[1:]
            corrupt.writestr(info.filename, content)
    with pytest.raises(ProjectArchiveError, match="hash mismatch"):
        ProjectArchiveStore(tmp_path / "restore-corrupt").restore_archive(corrupt_path)

    unsafe_path = tmp_path / "unsafe.zip"
    manifest = {
        "format_version": 1,
        "study_id": "archive-study",
        "members": [],
    }
    with ZipFile(unsafe_path, "w") as unsafe:
        unsafe.writestr("manifest.json", json.dumps(manifest))
        unsafe.writestr("../escape.txt", b"escape")
    with pytest.raises(ProjectArchiveError, match="unsafe member path"):
        ProjectArchiveStore(tmp_path / "restore-unsafe").restore_archive(unsafe_path)
    assert not (tmp_path / "escape.txt").exists()

    malformed_path = tmp_path / "malformed.zip"
    malformed_path.write_bytes(b"not a zip")
    with pytest.raises(ProjectArchiveError, match="malformed"):
        ProjectArchiveStore(tmp_path / "restore-malformed").restore_archive(
            malformed_path
        )

    incomplete_path = tmp_path / "incomplete.zip"
    empty_imports = b"[]"
    incomplete_manifest = {
        "format_version": 1,
        "study_id": "archive-study",
        "members": [
            {
                "path": "evidence/imports.json",
                "size_bytes": len(empty_imports),
                "sha256": sha256(empty_imports).hexdigest(),
            }
        ],
    }
    with ZipFile(incomplete_path, "w") as incomplete:
        incomplete.writestr("manifest.json", json.dumps(incomplete_manifest))
        incomplete.writestr("evidence/imports.json", empty_imports)
    with pytest.raises(ProjectArchiveError, match="required members"):
        ProjectArchiveStore(tmp_path / "restore-incomplete").restore_archive(
            incomplete_path
        )
