import json
from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from backend.storage.evidence_catalog import EvidenceCatalog
from backend.storage.project_archive import ProjectArchiveError, ProjectArchiveStore
from backend.storage.source_blob_store import SourceBlobStore
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

    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    restored = ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)

    restored_study = StudyWorkspaceStore(restore_root).list_studies()[0]
    restored_imports = EvidenceCatalog(restore_root).workspace_import_records(study_id)
    assert exported.archive_path.exists()
    assert len(exported.archive_sha256) == 64
    assert restored.study_id == study_id
    assert restored_study.id == study_id
    assert restored.import_count == 1
    assert restored.blob_count == 1
    assert restored_imports[0].source_blob_sha256 == blob_hash
    assert SourceBlobStore(restore_root).read_verified(blob_hash) == (
        b"P1_c: One.\nP1_p: Two."
    )

    with pytest.raises(FileExistsError):
        ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)


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
