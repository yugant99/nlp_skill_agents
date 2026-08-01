import json
import sqlite3
from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from backend.storage.audit_log import AuditLogStore
from backend.storage.evidence_catalog import EvidenceCatalog, EvidenceImportRecord
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


def _rewrite_archive_journal(
    archive_path: Path,
    output_path: Path,
    journal_path: Path,
) -> None:
    with ZipFile(archive_path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    members["study/batch_operations.sqlite3"] = journal_path.read_bytes()
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
    with ZipFile(output_path, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def _rewrite_archive_members(
    archive_path: Path,
    output_path: Path,
    mutate,
) -> None:
    with ZipFile(archive_path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(members.pop("manifest.json").decode("utf-8"))
    mutate(members)
    manifest["members"] = [
        {
            "path": name,
            "size_bytes": len(content),
            "sha256": sha256(content).hexdigest(),
        }
        for name, content in sorted(members.items())
    ]
    with ZipFile(output_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
        )
        for name, content in sorted(members.items()):
            archive.writestr(name, content)


def _write_archive(
    archive_path: Path,
    *,
    study_id: str,
    members: dict[str, bytes],
) -> None:
    manifest = {
        "format_version": 1,
        "study_id": study_id,
        "created_at": "2026-07-30T12:00:00+00:00",
        "members": [
            {
                "path": name,
                "size_bytes": len(content),
                "sha256": sha256(content).hexdigest(),
            }
            for name, content in sorted(members.items())
        ],
    }
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, content in members.items():
            archive.writestr(name, content)


def _rewrite_archive_manifest(
    archive_path: Path,
    output_path: Path,
    mutate,
) -> None:
    with ZipFile(archive_path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(members["manifest.json"].decode("utf-8"))
    members["manifest.json"] = json.dumps(mutate(manifest)).encode("utf-8")
    with ZipFile(output_path, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def _destination_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.name != ".workspace-mutation.lock"
    }


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


@pytest.mark.parametrize("missing_dependency", ["batch_manifest", "csv"])
def test_project_archive_refuses_corrupt_completed_source_snapshot(
    tmp_path,
    missing_dependency,
) -> None:
    study_id, _ = _build_study(tmp_path)
    study_dir = tmp_path / "studies" / study_id
    if missing_dependency == "batch_manifest":
        target = next(study_dir.glob("batches/*/batch.json"))
    else:
        target = next(study_dir.glob("batches/*/*.csv"))
    target.unlink()

    with pytest.raises(ProjectArchiveConflict, match="Completed study batch"):
        ProjectArchiveStore(tmp_path).create_archive(study_id)

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


@pytest.mark.parametrize(
    "missing_dependency",
    [
        "batch_manifest",
        "run",
        "evidence",
        "audit",
        "skill_pack",
        "skill_pack_metadata",
        "skill_pack_audit",
        "csv",
    ],
)
def test_project_archive_rejects_incomplete_completed_batch_dependencies(
    tmp_path,
    missing_dependency,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    forged_archive_path = tmp_path / f"missing-{missing_dependency}.nlpstudy.zip"

    def remove_dependency(members):
        if missing_dependency == "batch_manifest":
            target = next(
                name
                for name in members
                if name.startswith("study/batches/") and name.endswith("/batch.json")
            )
            members.pop(target)
        elif missing_dependency == "run":
            target = next(
                name
                for name in members
                if "/runs/" in name and name.endswith(".json")
            )
            members.pop(target)
        elif missing_dependency == "evidence":
            members["evidence/imports.json"] = b"[]"
            for name in list(members):
                if name.startswith("blobs/"):
                    members.pop(name)
        elif missing_dependency == "audit":
            audit_events = json.loads(members["evidence/audit.json"])
            members["evidence/audit.json"] = json.dumps(
                [
                    event
                    for event in audit_events
                    if event["event_type"] != "batch.completed"
                ],
                indent=2,
                sort_keys=True,
            ).encode("utf-8")
        elif missing_dependency == "skill_pack":
            target = next(
                name
                for name in members
                if name.startswith("study/skill_packs/")
                and name.endswith(".json")
                and not name.endswith(".metadata.json")
            )
            payload = json.loads(members[target])
            payload["name"] = "Forged Skill Pack"
            members[target] = json.dumps(payload, indent=2).encode("utf-8")
        elif missing_dependency == "skill_pack_metadata":
            target = next(
                name
                for name in members
                if name.startswith("study/skill_packs/")
                and name.endswith(".metadata.json")
            )
            members.pop(target)
        elif missing_dependency == "skill_pack_audit":
            audit_events = json.loads(members["evidence/audit.json"])
            members["evidence/audit.json"] = json.dumps(
                [
                    event
                    for event in audit_events
                    if event["event_type"] != "skill_pack.versioned"
                ],
                indent=2,
                sort_keys=True,
            ).encode("utf-8")
        else:
            target = next(
                name
                for name in members
                if name.startswith("study/batches/") and name.endswith(".csv")
            )
            members.pop(target)

    _rewrite_archive_members(
        exported.archive_path,
        forged_archive_path,
        remove_dependency,
    )

    with pytest.raises(
        ProjectArchiveError,
        match="completed batch artifacts are invalid",
    ):
        ProjectArchiveStore(restore_root).restore_archive(forged_archive_path)

    assert not (restore_root / "studies" / study_id).exists()
    assert AuditLogStore(restore_root).list_events(limit=None) == []
    assert not (restore_root / "source_blobs").exists()


def test_project_archive_rejects_private_invalid_migration_timestamp(
    tmp_path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    forged_archive_path = tmp_path / "private-ledger.nlpstudy.zip"
    forged_db_path = tmp_path / "private-ledger.sqlite3"
    with ZipFile(exported.archive_path) as archive:
        forged_db_path.write_bytes(
            archive.read("study/batch_operations.sqlite3")
        )
    with sqlite3.connect(forged_db_path) as connection:
        connection.execute(
            "update schema_migrations set applied_at = 'PRIVATE-CONTENT'"
        )
    _rewrite_archive_journal(
        exported.archive_path,
        forged_archive_path,
        forged_db_path,
    )

    with pytest.raises(
        ProjectArchiveConflict,
        match="invalid applied_at",
    ) as error:
        ProjectArchiveStore(restore_root).restore_archive(forged_archive_path)

    assert "PRIVATE-CONTENT" not in str(error.value)
    assert not (restore_root / "studies" / study_id).exists()


@pytest.mark.parametrize(
    "tamper_sql",
    [
        """
        create trigger forged_history_delete
        after insert on study_batch_operations
        begin
          delete from study_batch_operations where batch_id != new.batch_id;
        end
        """,
        "drop trigger study_batch_operation_items_index_guard",
    ],
)
def test_project_archive_rejects_forged_batch_journal_schema(
    tmp_path,
    tamper_sql,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    forged_archive_path = tmp_path / "forged-schema.nlpstudy.zip"
    forged_db_path = tmp_path / "forged-schema.sqlite3"
    with ZipFile(exported.archive_path) as archive:
        forged_db_path.write_bytes(
            archive.read("study/batch_operations.sqlite3")
        )
    with sqlite3.connect(forged_db_path) as connection:
        connection.executescript(tamper_sql)
    _rewrite_archive_journal(
        exported.archive_path,
        forged_archive_path,
        forged_db_path,
    )

    with pytest.raises(ProjectArchiveError, match="journal is invalid"):
        ProjectArchiveStore(restore_root).restore_archive(forged_archive_path)

    assert not (restore_root / "studies" / study_id).exists()
    assert AuditLogStore(restore_root).list_events(limit=None) == []
    assert not (restore_root / "source_blobs").exists()


@pytest.mark.parametrize(
    "tamper_sql",
    [
        "update study_batch_operation_items set run_id = '../escape'",
        "update study_batch_operations set updated_at = 'PRIVATE-CONTENT'",
        """
        update study_batch_operations
        set status = 'failed', stage = 'prepared', completed_at = '',
            last_error_type = 'PRIVATE-CONTENT'
        """,
        """
        update study_batch_operations
        set status = 'running', stage = 'prepared', completed_at = '',
            last_error_type = ''
        """,
        "update study_batch_operation_items set run_id = 'CON'",
        """
        update study_batch_operations set item_count = 2;
        update study_batch_operation_items set run_id = 'Run';
        insert into study_batch_operation_items (
          batch_id, item_index, item_request_sha256,
          run_id, import_id, project_source_id,
          source_blob_sha256, transcript_sha256,
          transcript_revision_id, run_payload_sha256,
          stage, last_error_type, created_at, updated_at
        )
        select batch_id, 1, item_request_sha256,
               'run', 'casefold-import', project_source_id,
               source_blob_sha256, transcript_sha256,
               transcript_revision_id, run_payload_sha256,
               stage, last_error_type, created_at, updated_at
        from study_batch_operation_items where item_index = 0
        """,
        """
        update study_batch_operations
        set item_count = 1.5, attempt_count = 1.5
        """,
        "update study_batch_operation_items set item_index = 0.5",
        """
        update study_batch_operations
        set aggregate_payload_sha256 =
          '0000000000000000000000000000000000000000000000000000000000000000'
        """,
        """
        update study_batch_operations
        set skill_pack_version_id = 'other-9_9_9'
        """,
    ],
)
def test_project_archive_rejects_invalid_batch_journal_rows(
    tmp_path,
    tamper_sql,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    forged_archive_path = tmp_path / "forged-rows.nlpstudy.zip"
    forged_db_path = tmp_path / "forged-rows.sqlite3"
    with ZipFile(exported.archive_path) as archive:
        forged_db_path.write_bytes(
            archive.read("study/batch_operations.sqlite3")
        )
    with sqlite3.connect(forged_db_path) as connection:
        connection.executescript(tamper_sql)
    _rewrite_archive_journal(
        exported.archive_path,
        forged_archive_path,
        forged_db_path,
    )

    with pytest.raises(ProjectArchiveError, match="journal is invalid"):
        ProjectArchiveStore(restore_root).restore_archive(forged_archive_path)

    assert not (restore_root / "studies" / study_id).exists()
    assert AuditLogStore(restore_root).list_events(limit=None) == []
    assert not (restore_root / "source_blobs").exists()


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
        "created_at": "2026-07-30T12:00:00+00:00",
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


def test_project_archive_rejects_symlinked_study_root_before_journal_access(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    external_root = tmp_path / "external"
    study_id, _ = _build_study(external_root)
    (source_root / "studies").mkdir(parents=True)
    (source_root / "studies" / study_id).symlink_to(
        external_root / "studies" / study_id,
        target_is_directory=True,
    )

    with pytest.raises(ProjectArchiveError, match="root cannot be a symbolic link"):
        ProjectArchiveStore(source_root).create_archive(study_id)

    assert not (external_root / "studies" / study_id / ".archive.lock").exists()
    assert not (source_root / "backups").exists()


def test_project_archive_backup_names_do_not_collide(tmp_path: Path) -> None:
    study_id, _ = _build_study(tmp_path)
    store = ProjectArchiveStore(tmp_path)

    first = store.create_archive(study_id)
    second = store.create_archive(study_id)

    assert first.archive_path != second.archive_path
    assert first.archive_path.is_file()
    assert second.archive_path.is_file()


def test_project_archive_rejects_semantically_invalid_unreferenced_skill_pack(
    tmp_path: Path,
) -> None:
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Invalid Unreferenced Pack"})
    store.add_skill_pack_version(
        study.id,
        {
            "id": "invalid_unreferenced_pack",
            "version": "1.0.0",
            "metrics": [],
        },
        validate=False,
    )

    with pytest.raises(ProjectArchiveConflict, match="semantically invalid"):
        ProjectArchiveStore(tmp_path).create_archive(study.id)

    assert not list((tmp_path / "backups").glob("*.nlpstudy.zip"))


def test_project_archive_restores_format_v1_legacy_batch_without_journal(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    legacy_archive = tmp_path / "legacy-format-v1.nlpstudy.zip"

    def remove_journal(members):
        members.pop("study/batch_operations.sqlite3")

    _rewrite_archive_members(
        exported.archive_path,
        legacy_archive,
        remove_journal,
    )

    ProjectArchiveStore(restore_root).restore_archive(legacy_archive)

    restored_batches = StudyWorkspaceStore(restore_root).list_batches(study_id)
    assert len(restored_batches) == 1
    assert StudyWorkspaceStore(restore_root).list_batch_runs(
        study_id,
        restored_batches[0].batch_id,
    )


def test_project_archive_round_trips_original_pre_audit_batch_generation(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    source_store = StudyWorkspaceStore(source_root)
    batch = source_store.list_batches(study_id)[0]
    run_path = next((batch.aggregate_dir / "runs").glob("*.json"))
    run_payload = json.loads(run_path.read_text(encoding="utf-8"))
    run_path.write_text(
        json.dumps(
            {
                key: run_payload[key]
                for key in (
                    "run_id",
                    "source_filename",
                    "created_at",
                    "turn_count",
                    "results",
                )
            }
        ),
        encoding="utf-8",
    )
    aggregate_path = batch.aggregate_dir / "aggregate_results.json"
    aggregate_payload = json.loads(aggregate_path.read_text(encoding="utf-8"))
    aggregate_payload.pop("study_schema")
    aggregate_path.write_text(json.dumps(aggregate_payload), encoding="utf-8")
    (source_root / "audit" / "events.jsonl").write_text("", encoding="utf-8")
    (source_root / "studies" / study_id / "batch_operations.sqlite3").unlink()

    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    restored = ProjectArchiveStore(restore_root).restore_archive(
        exported.archive_path
    )

    batches = StudyWorkspaceStore(restore_root).list_batches(study_id)
    assert restored.study_id == study_id
    assert len(batches) == 1
    assert StudyWorkspaceStore(restore_root).list_batch_runs(
        study_id,
        batches[0].batch_id,
    )


@pytest.mark.parametrize("catalog_workspace", ["legacy", "local-default"])
@pytest.mark.parametrize("destination_blob_state", ["absent", "corrupt"])
def test_project_archive_preserves_import_v1_catalog_without_original_blob(
    tmp_path: Path,
    catalog_workspace: str,
    destination_blob_state: str,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    source_store = StudyWorkspaceStore(source_root)
    batch = source_store.list_batches(study_id)[0]
    run_path = next((batch.aggregate_dir / "runs").glob("*.json"))
    run_payload = json.loads(run_path.read_text(encoding="utf-8"))
    project_source_id = run_payload["project_source_id"]
    for field_name in (
        "project_source_id",
        "parent_transcript_revision_id",
        "workspace_id",
    ):
        run_payload.pop(field_name)
    run_path.write_text(json.dumps(run_payload), encoding="utf-8")
    catalog = EvidenceCatalog(source_root)
    with sqlite3.connect(catalog.db_path) as connection:
        connection.execute(
            """
            update project_sources set workspace_id = ?
            where project_source_id = ?
            """,
            (catalog_workspace, project_source_id),
        )
    SourceBlobStore(source_root).blob_path(
        run_payload["source_blob_sha256"]
    ).unlink()
    (source_root / "studies" / study_id / "batch_operations.sqlite3").unlink()

    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    with ZipFile(exported.archive_path) as archive:
        archived_imports = json.loads(archive.read("evidence/imports.json"))
        unretained_blobs = json.loads(
            archive.read("evidence/unretained_blobs.json")
        )
        assert f"blobs/{run_payload['source_blob_sha256']}.blob" not in (
            archive.namelist()
        )
    if destination_blob_state == "corrupt":
        destination_blob = SourceBlobStore(restore_root).blob_path(
            run_payload["source_blob_sha256"]
        )
        destination_blob.parent.mkdir(parents=True, exist_ok=True)
        destination_blob.write_bytes(b"corrupt")
        with pytest.raises(ProjectArchiveError, match="destination"):
            ProjectArchiveStore(restore_root).restore_archive(
                exported.archive_path
            )
        assert not (restore_root / "studies" / study_id).exists()
        return
    restored = ProjectArchiveStore(restore_root).restore_archive(
        exported.archive_path
    )

    restored_imports = EvidenceCatalog(restore_root).workspace_import_records(
        study_id
    )
    assert archived_imports[0]["import_id"] == run_payload["import_id"]
    assert archived_imports[0]["workspace_id"] == study_id
    assert unretained_blobs == [run_payload["source_blob_sha256"]]
    assert restored.import_count == 1
    assert restored.blob_count == 0
    assert restored_imports[0].import_id == run_payload["import_id"]
    assert StudyWorkspaceStore(restore_root).list_batch_runs(
        study_id,
        batch.batch_id,
    )


@pytest.mark.parametrize("marker_state", ["unreferenced", "journal-backed"])
def test_project_archive_rejects_invalid_unretained_blob_marker(
    tmp_path: Path,
    marker_state: str,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, digest = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    tampered_archive = tmp_path / f"unretained-{marker_state}.nlpstudy.zip"

    def mutate(members):
        marked_digest = "0" * 64 if marker_state == "unreferenced" else digest
        members["evidence/unretained_blobs.json"] = json.dumps(
            [marked_digest]
        ).encode("utf-8")
        if marker_state == "journal-backed":
            members.pop(f"blobs/{digest}.blob")

    _rewrite_archive_members(
        exported.archive_path,
        tampered_archive,
        mutate,
    )

    with pytest.raises(ProjectArchiveError):
        ProjectArchiveStore(restore_root).restore_archive(tampered_archive)

    assert not (restore_root / "studies" / study_id).exists()


def test_project_archive_rejects_unretained_marker_for_non_batch_import(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study = StudyWorkspaceStore(source_root).create_study(
        {"name": "Ordinary Evidence Archive Study"}
    )
    content = b"ordinary retained source"
    digest = sha256(content).hexdigest()
    SourceBlobStore(source_root).store(content, digest)
    EvidenceCatalog(source_root).record_import(
        EvidenceImportRecord(
            import_id="imp_ordinary_archive_evidence",
            run_id="run_ordinary_archive_evidence",
            pipeline="analysis",
            source_id=f"src_{digest[:32]}",
            source_filename="ordinary.txt",
            source_media_type="text/plain",
            source_blob_sha256=digest,
            transcript_revision_id=f"trv_{digest[:32]}",
            transcript_sha256=digest,
            imported_at="2026-08-01T12:00:00+00:00",
            project_source_id="psrc_ordinary_archive_evidence",
            workspace_id=study.id,
        )
    )
    exported = ProjectArchiveStore(source_root).create_archive(study.id)
    tampered_archive = tmp_path / "ordinary-unretained.nlpstudy.zip"

    def mutate(members):
        members.pop(f"blobs/{digest}.blob")
        members["evidence/unretained_blobs.json"] = json.dumps([digest]).encode(
            "utf-8"
        )

    _rewrite_archive_members(
        exported.archive_path,
        tampered_archive,
        mutate,
    )

    with pytest.raises(ProjectArchiveError, match="artifacts are invalid"):
        ProjectArchiveStore(restore_root).restore_archive(tampered_archive)

    assert not (restore_root / "studies" / study.id).exists()


@pytest.mark.parametrize(
    "artifact_kind",
    ["aggregate", "run", "identity", "csv", "audit"],
)
def test_project_archive_rejects_tampered_pre_journal_batch_on_backup(
    tmp_path: Path,
    artifact_kind: str,
) -> None:
    study_id, _ = _build_study(tmp_path)
    study_dir = tmp_path / "studies" / study_id
    batch = StudyWorkspaceStore(tmp_path).list_batches(study_id)[0]
    (study_dir / "batch_operations.sqlite3").unlink()
    if artifact_kind == "aggregate":
        (batch.aggregate_dir / "aggregate_results.json").write_text(
            json.dumps({"results": [], "failures": []}),
            encoding="utf-8",
        )
    elif artifact_kind == "run":
        next((batch.aggregate_dir / "runs").glob("*.json")).write_text(
            json.dumps({"run_id": "tampered"}),
            encoding="utf-8",
        )
    elif artifact_kind == "identity":
        run_path = next((batch.aggregate_dir / "runs").glob("*.json"))
        run_payload = json.loads(run_path.read_text(encoding="utf-8"))
        run_payload["import_id"] = "import_tampered"
        run_path.write_text(json.dumps(run_payload), encoding="utf-8")
    elif artifact_kind == "csv":
        next(batch.aggregate_dir.glob("*.csv")).write_text(
            "tampered\n",
            encoding="utf-8",
        )
    else:
        events_path = tmp_path / "audit" / "events.jsonl"
        events = AuditLogStore(tmp_path).list_events(limit=None)
        events_path.write_text(
            "".join(
                json.dumps(event) + "\n"
                for event in events
                if event.get("event_type") != "batch.completed"
            ),
            encoding="utf-8",
        )

    with pytest.raises(ProjectArchiveConflict, match="Legacy completed"):
        ProjectArchiveStore(tmp_path).create_archive(study_id)

    assert not list((tmp_path / "backups").glob("*.nlpstudy.zip"))


@pytest.mark.parametrize(
    "artifact_kind",
    ["aggregate", "run", "identity", "csv", "audit"],
)
def test_project_archive_rejects_tampered_pre_journal_batch_on_restore(
    tmp_path: Path,
    artifact_kind: str,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    tampered_archive = tmp_path / f"legacy-{artifact_kind}.nlpstudy.zip"

    def mutate(members):
        members.pop("study/batch_operations.sqlite3")
        if artifact_kind == "aggregate":
            aggregate_name = "study/batches/" + next(
                name.split("/")[2]
                for name in members
                if name.endswith("/aggregate_results.json")
            ) + "/aggregate_results.json"
            members[aggregate_name] = b"[]"
        elif artifact_kind == "run":
            run_name = next(
                name
                for name in members
                if "/runs/" in name and name.endswith(".json")
            )
            members[run_name] = json.dumps({"run_id": "tampered"}).encode(
                "utf-8"
            )
        elif artifact_kind == "identity":
            run_name = next(
                name
                for name in members
                if "/runs/" in name and name.endswith(".json")
            )
            run_payload = json.loads(members[run_name])
            run_payload["import_id"] = "import_tampered"
            members[run_name] = json.dumps(run_payload).encode("utf-8")
        elif artifact_kind == "csv":
            csv_name = next(name for name in members if name.endswith(".csv"))
            members[csv_name] = b"tampered\n"
        else:
            events = json.loads(members["evidence/audit.json"])
            members["evidence/audit.json"] = json.dumps(
                [
                    event
                    for event in events
                    if event.get("event_type") != "batch.completed"
                ]
            ).encode("utf-8")

    _rewrite_archive_members(
        exported.archive_path,
        tampered_archive,
        mutate,
    )

    with pytest.raises(ProjectArchiveError, match="artifacts are invalid"):
        ProjectArchiveStore(restore_root).restore_archive(tampered_archive)

    assert not (restore_root / "studies" / study_id).exists()


def test_project_archive_rejects_hash_aligned_non_object_run_snapshot(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    source_store = StudyWorkspaceStore(source_root)
    batch = source_store.list_batches(study_id)[0]
    item = StudyBatchOperationStore(source_root, study_id).list_items(
        batch.batch_id
    )[0]
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    tampered_archive = tmp_path / "hash-aligned-non-object-run.nlpstudy.zip"
    journal_copy = tmp_path / "tampered-journal.sqlite3"
    with ZipFile(exported.archive_path) as archive:
        journal_copy.write_bytes(
            archive.read("study/batch_operations.sqlite3")
        )
    with sqlite3.connect(journal_copy) as connection:
        connection.execute(
            """
            update study_batch_operation_items
            set run_payload_sha256 = ?
            where batch_id = ? and item_index = ?
            """,
            (sha256(b"[]").hexdigest(), batch.batch_id, item["item_index"]),
        )

    def mutate(members):
        members["study/batch_operations.sqlite3"] = journal_copy.read_bytes()
        run_name = next(
            name
            for name in members
            if name.endswith(f"/runs/{item['run_id']}.json")
        )
        members[run_name] = b"[]"

    _rewrite_archive_members(
        exported.archive_path,
        tampered_archive,
        mutate,
    )

    with pytest.raises(ProjectArchiveError, match="artifacts are invalid"):
        ProjectArchiveStore(restore_root).restore_archive(tampered_archive)

    assert not (restore_root / "studies" / study_id).exists()


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "C:/study/study.json",
        "study/C:/study.json",
        "study//study.json",
        "study/./study.json",
        "study/folder../study.json",
        "study/folder /study.json",
        "study/CON/study.json",
        "study/con.txt",
        "study/COM¹.txt",
        "study/LPT³.txt",
        "study/question?.json",
        "study/star*.json",
        'study/quote".json',
        "study/pipe|.json",
        "study/less<.json",
        "study/control\x01.json",
        f"study/{'a' * 256}/study.json",
        f"study/{'😀' * 128}/study.json",
        "study/e\u0301/study.json",
    ],
)
def test_project_archive_rejects_nonportable_member_paths(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    archive_path = tmp_path / "unsafe-portable.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format_version": 1,
                    "study_id": "portable-study",
                    "created_at": "2026-07-30T12:00:00+00:00",
                    "members": [],
                }
            ),
        )
        archive.writestr(unsafe_name, b"unsafe")

    with pytest.raises(ProjectArchiveError, match="unsafe member path"):
        ProjectArchiveStore(tmp_path / "restore").restore_archive(archive_path)


def test_project_archive_rejects_file_directory_member_collisions(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "file-directory-collision.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", b"{}")
        archive.writestr("study/a", b"file")
        archive.writestr("study/a/b", b"child")

    with pytest.raises(ProjectArchiveError, match="file-directory-colliding"):
        ProjectArchiveStore(tmp_path / "restore").restore_archive(archive_path)


@pytest.mark.parametrize(
    ("header_offset", "field_value", "message"),
    [
        (8, 99, "unsupported compression"),
        (6, 1, "encrypted member"),
    ],
)
def test_project_archive_rejects_unsupported_zip_member_features(
    tmp_path: Path,
    header_offset: int,
    field_value: int,
    message: str,
) -> None:
    archive_path = tmp_path / "unsupported-member.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", b"{}")
    archive_bytes = bytearray(archive_path.read_bytes())
    local_header = archive_bytes.index(b"PK\x03\x04")
    central_header = archive_bytes.index(b"PK\x01\x02")
    archive_bytes[
        local_header + header_offset : local_header + header_offset + 2
    ] = field_value.to_bytes(2, "little")
    central_offset = header_offset + 2
    archive_bytes[
        central_header + central_offset : central_header + central_offset + 2
    ] = field_value.to_bytes(2, "little")
    archive_path.write_bytes(archive_bytes)

    with pytest.raises(ProjectArchiveError, match=message):
        ProjectArchiveStore(tmp_path / "restore").restore_archive(archive_path)


def test_project_archive_translates_zip_member_read_errors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive_path = tmp_path / "unreadable-member.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", b"{}")

    def fail_read(self, name, pwd=None):
        raise RuntimeError("injected ZIP read failure")

    monkeypatch.setattr(ZipFile, "read", fail_read)

    with pytest.raises(ProjectArchiveError, match="member data is malformed"):
        ProjectArchiveStore(tmp_path / "restore").restore_archive(archive_path)


def test_project_archive_rejects_casefold_colliding_member_paths(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "casefold-collision.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format_version": 1,
                    "study_id": "portable-study",
                    "created_at": "2026-07-30T12:00:00+00:00",
                    "members": [],
                }
            ),
        )
        archive.writestr("study/File.json", b"one")
        archive.writestr("study/file.json", b"two")

    with pytest.raises(ProjectArchiveError, match="case-colliding"):
        ProjectArchiveStore(tmp_path / "restore").restore_archive(archive_path)


def test_project_archive_rejects_overlong_legacy_study_id_before_writes(
    tmp_path: Path,
) -> None:
    study_id = "s" * 97
    archive_path = tmp_path / "overlong-study.zip"
    members = {
        "study/study.json": json.dumps(
            {
                "id": study_id,
                "name": "Overlong Legacy Study",
                "description": "",
                "created_at": "2026-07-30T12:00:00+00:00",
            }
        ).encode("utf-8"),
        "evidence/imports.json": b"[]",
        "evidence/audit.json": b"[]",
    }
    _write_archive(archive_path, study_id=study_id, members=members)
    restore_root = tmp_path / "restore-overlong"

    with pytest.raises(ProjectArchiveError, match="Invalid study id"):
        ProjectArchiveStore(restore_root).restore_archive(archive_path)

    assert not restore_root.exists()


@pytest.mark.parametrize(
    ("mutate_manifest", "message"),
    [
        (lambda manifest: [], "manifest is malformed"),
        (
            lambda manifest: {**manifest, "unexpected": True},
            "manifest is malformed",
        ),
        (
            lambda manifest: {**manifest, "format_version": True},
            "Unsupported archive format version",
        ),
        (
            lambda manifest: {**manifest, "created_at": None},
            "manifest timestamp is invalid",
        ),
        (
            lambda manifest: {**manifest, "members": [None]},
            "manifest member is invalid",
        ),
        (
            lambda manifest: {
                **manifest,
                "members": [
                    {**manifest["members"][0], "size_bytes": "1"},
                    *manifest["members"][1:],
                ],
            },
            "member size is invalid",
        ),
        (
            lambda manifest: {
                **manifest,
                "members": [
                    {**manifest["members"][0], "sha256": 7},
                    *manifest["members"][1:],
                ],
            },
            "member hash is invalid",
        ),
    ],
)
def test_project_archive_rejects_malformed_manifest_shapes(
    tmp_path: Path,
    mutate_manifest,
    message: str,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    malformed_archive = tmp_path / "malformed-manifest.zip"
    _rewrite_archive_manifest(
        exported.archive_path,
        malformed_archive,
        mutate_manifest,
    )

    with pytest.raises(ProjectArchiveError, match=message):
        ProjectArchiveStore(restore_root).restore_archive(malformed_archive)

    assert not (restore_root / "studies" / study_id).exists()


def test_project_archive_translates_malformed_study_json(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    malformed_archive = tmp_path / "malformed-study-json.zip"

    def mutate(members):
        members["study/study.json"] = b"{not-json"

    _rewrite_archive_members(
        exported.archive_path,
        malformed_archive,
        mutate,
    )

    with pytest.raises(ProjectArchiveError, match="study record is malformed"):
        ProjectArchiveStore(restore_root).restore_archive(malformed_archive)

    assert not (restore_root / "studies" / study_id).exists()


@pytest.mark.parametrize(
    ("member_name", "mutate_payload", "message"),
    [
        (
            "study/study.json",
            lambda payload: {**payload, "name": 7},
            "study record is malformed",
        ),
        (
            "evidence/imports.json",
            lambda payload: [
                {**payload[0], "source_blob_sha256": "A" * 64},
                *payload[1:],
            ],
            "evidence SHA-256 is invalid",
        ),
        (
            "evidence/imports.json",
            lambda payload: [
                {**payload[0], "imported_at": "2026-07-30T12:00:00"},
                *payload[1:],
            ],
            "evidence import timestamp is invalid",
        ),
        (
            "evidence/imports.json",
            lambda payload: [
                {**payload[0], "source_filename": "x" * 4097},
                *payload[1:],
            ],
            "evidence records are malformed",
        ),
        (
            "evidence/audit.json",
            lambda payload: [
                {**payload[0], "metadata": []},
                *payload[1:],
            ],
            "audit records are malformed",
        ),
        (
            "evidence/audit.json",
            lambda payload: [
                {**payload[0], "created_at": "not-a-timestamp"},
                *payload[1:],
            ],
            "audit event timestamp is invalid",
        ),
    ],
)
def test_project_archive_validates_imported_record_types_and_bounds(
    tmp_path: Path,
    member_name: str,
    mutate_payload,
    message: str,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    malformed_archive = tmp_path / "malformed-record.zip"

    def mutate(members):
        payload = json.loads(members[member_name].decode("utf-8"))
        members[member_name] = json.dumps(mutate_payload(payload)).encode("utf-8")

    _rewrite_archive_members(
        exported.archive_path,
        malformed_archive,
        mutate,
    )

    with pytest.raises(ProjectArchiveError, match=message):
        ProjectArchiveStore(restore_root).restore_archive(malformed_archive)

    assert not (restore_root / "studies" / study_id).exists()


@pytest.mark.parametrize("conflict_kind", ["evidence", "audit", "blob"])
def test_project_archive_preflights_destination_conflicts_without_partial_writes(
    tmp_path: Path,
    conflict_kind: str,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    with ZipFile(exported.archive_path) as archive:
        import_payload = json.loads(archive.read("evidence/imports.json"))[0]
        audit_payload = json.loads(archive.read("evidence/audit.json"))[0]

    if conflict_kind == "evidence":
        EvidenceCatalog(restore_root).record_import(
            EvidenceImportRecord(
                **{**import_payload, "run_id": f"{import_payload['run_id']}-other"}
            )
        )
    elif conflict_kind == "audit":
        AuditLogStore(restore_root).import_events(
            [{**audit_payload, "actor": "conflicting-actor"}]
        )
    else:
        blob_path = SourceBlobStore(restore_root).blob_path(
            import_payload["source_blob_sha256"]
        )
        blob_path.parent.mkdir(parents=True)
        blob_path.write_bytes(b"corrupt destination blob")
    before = _destination_files(restore_root)

    with pytest.raises(ProjectArchiveError, match="conflicts with destination"):
        ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)

    assert _destination_files(restore_root) == before
    assert not (restore_root / "studies" / study_id).exists()


def test_project_archive_rolls_back_destination_after_late_publish_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    destination_store = StudyWorkspaceStore(restore_root)
    destination_study = destination_store.create_study(
        {"name": "Existing Destination"}
    )
    destination_version = destination_store.add_skill_pack_version(
        destination_study.id,
        {
            "id": "destination_pack",
            "name": "Destination Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    destination_store.run_text_batch(
        destination_study.id,
        destination_version.version_id,
        [{"source_filename": "existing.txt", "content": "CG: One.\nP: Two."}],
    )
    before = _destination_files(restore_root)
    archive_store = ProjectArchiveStore(restore_root)

    def fail_publish(stage_dir, study_dir):
        raise OSError("injected late publish failure")

    monkeypatch.setattr(archive_store, "_publish_study", fail_publish)

    with pytest.raises(ProjectArchiveError, match="could not be committed"):
        archive_store.restore_archive(exported.archive_path)

    assert _destination_files(restore_root) == before
    assert not (restore_root / "studies" / study_id).exists()


def test_project_archive_translates_sqlite_import_errors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)

    def fail_import(self, record):
        raise sqlite3.InterfaceError("injected binding failure")

    monkeypatch.setattr(EvidenceCatalog, "record_import", fail_import)

    with pytest.raises(ProjectArchiveError, match="artifacts are invalid"):
        ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)

    assert not (restore_root / "studies" / study_id).exists()
