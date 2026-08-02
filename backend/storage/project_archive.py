from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import unicodedata
import zlib
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from uuid import uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from backend.qualitative.cases import (
    CaseConflictError,
    CaseNotFoundError,
    CaseService,
    CaseValidationError,
)
from backend.qualitative.coding_references import (
    CodingReferenceConflictError,
    CodingReferenceNotFoundError,
    CodingReferenceService,
    CodingReferenceValidationError,
)
from backend.qualitative.database import QualitativeDatabaseConflict
from backend.qualitative.notes import (
    NoteConflictError,
    NoteNotFoundError,
    NoteService,
    NoteValidationError,
)
from backend.segmentation.adjudicator import adjudicate_cunit_boundaries
from backend.segmentation.models import RawTranscriptEvent
from backend.storage.atomic import atomic_binary_writer, atomic_write_bytes
from backend.storage.audit_log import AuditLogStore
from backend.storage.evidence_catalog import EvidenceCatalog, EvidenceImportRecord
from backend.storage.evidence_target_registry import (
    EvidenceCUnitInput,
    EvidencePassageInput,
    EvidenceSetSnapshot,
    EvidenceTargetBlobConflict,
    EvidenceTargetConflictError,
    EvidenceTargetNotFoundError,
    EvidenceTargetRegistry,
    EvidenceTargetValidationError,
    PreparedEvidenceSet,
    prepare_complete_evidence_set,
)
from backend.storage.evidence_text_blob_store import (
    EvidenceTextBlobIntegrityError,
    EvidenceTextBlobStore,
)
from backend.storage.source_blob_store import (
    SourceBlobIntegrityError,
    SourceBlobStore,
)
from backend.storage.sqlite_migrations import SchemaCompatibilityError
from backend.storage.study_batch_operation_store import (
    StudyBatchOperationConflict,
    StudyBatchOperationStore,
    validate_study_batch_operation_database,
)
from backend.storage.study_store import (
    MAX_STUDY_ID_LENGTH,
    StudyBatchSnapshotConflict,
    StudySkillPackVersionConflict,
    StudyWorkspace,
    StudyWorkspaceStore,
)
from backend.storage.workspace_lock import (
    WorkspaceLockError,
    workspace_mutation_lock,
)


ARCHIVE_FORMAT_VERSION = 2
SUPPORTED_ARCHIVE_FORMAT_VERSIONS = {1, 2}
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_FILE_BYTES = 512 * 1024 * 1024
_STUDY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_AUDIT_EVENT_ID_PATTERN = re.compile(r"^(?:[0-9a-f]{32}|[0-9a-f]{64})$")
_WINDOWS_DEVICE_NAME = re.compile(
    r"^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])$",
    re.IGNORECASE,
)
_WINDOWS_INVALID_FILENAME_CHARACTER = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SUPPORTED_ARCHIVE_COMPRESSION_TYPES = {ZIP_STORED, ZIP_DEFLATED}
MAX_ARCHIVE_MEMBER_PATH_LENGTH = 1024
MAX_ARCHIVE_MEMBER_COMPONENT_UTF16_UNITS = 255
MAX_EVIDENCE_IDENTIFIER_LENGTH = 256
MAX_EVIDENCE_FILENAME_LENGTH = 4096
MAX_EVIDENCE_LABEL_LENGTH = 256
MAX_TIMESTAMP_LENGTH = 64
_TARGET_EXPORT_FORMAT = "nlp-skill-agents.evidence-target-export"
_TARGET_EXPORT_FORMAT_VERSION = 1
_TARGET_SET_FORMAT = "nlp-skill-agents.evidence-target-set"
_TARGET_SET_FORMAT_VERSION = 1
_TARGET_EXPORT_FIELDS = {"format", "format_version", "workspace_id", "sets"}
_TARGET_SET_FIELDS = {
    "format",
    "format_version",
    "evidence_set_id",
    "snapshot_sha256",
    "created_at",
    "import_id",
    "workspace_id",
    "project_source_id",
    "transcript_revision_id",
    "transcript_text_sha256",
    "producer_kind",
    "producer_version",
    "producer_status",
    "review_status",
    "passage_count",
    "cunit_count",
    "passages",
}
_TARGET_PASSAGE_FIELDS = {
    "passage_id",
    "passage_ordinal",
    "role",
    "text_sha256",
    "text_length",
    "cunits",
}
_TARGET_CUNIT_FIELDS = {
    "cunit_id",
    "cunit_ordinal",
    "text_sha256",
    "text_length",
}
_EVIDENCE_REQUIRED_FIELDS = {
    "import_id",
    "run_id",
    "pipeline",
    "source_id",
    "source_filename",
    "source_media_type",
    "source_blob_sha256",
    "transcript_revision_id",
    "transcript_sha256",
    "imported_at",
    "project_source_id",
    "parent_transcript_revision_id",
    "workspace_id",
}
_AUDIT_REQUIRED_FIELDS = {
    "id",
    "event_type",
    "subject_type",
    "subject_id",
    "actor",
    "metadata",
    "created_at",
}


@dataclass(frozen=True)
class ProjectArchiveExport:
    study_id: str
    archive_path: Path
    archive_sha256: str
    member_count: int
    created_at: str


@dataclass(frozen=True)
class ProjectRestoreResult:
    study_id: str
    study_dir: Path
    import_count: int
    blob_count: int
    audit_event_count: int


@dataclass(frozen=True)
class _ParsedArchivePayload:
    imports: tuple[EvidenceImportRecord, ...]
    audit_events: tuple[dict[str, object], ...]
    unretained_blob_digests: frozenset[str]
    source_blob_names: frozenset[str]
    evidence_texts: tuple[tuple[str, str], ...]
    evidence_sets: tuple[PreparedEvidenceSet, ...]


class ProjectArchiveError(ValueError):
    pass


class ProjectArchiveConflict(ProjectArchiveError):
    pass


class ProjectArchiveStore:
    def __init__(self, root: Path | str = "local_data") -> None:
        self.root = Path(root)
        self.studies_dir = self.root / "studies"
        self.backups_dir = self.root / "backups"
        self.catalog = EvidenceCatalog(self.root)
        self.blobs = SourceBlobStore(self.root)
        self.audit = AuditLogStore(self.root)

    def create_archive(self, study_id: str) -> ProjectArchiveExport:
        _validate_study_id(study_id)
        _validate_optional_destination_directory(self.root)
        _validate_optional_destination_directory(self.studies_dir)
        _validate_audit_paths(self.audit)
        study_dir = self.studies_dir / study_id
        if study_dir.is_symlink():
            raise ProjectArchiveError("Study archive root cannot be a symbolic link")
        if not (study_dir / "study.json").is_file():
            raise FileNotFoundError(study_id)
        try:
            with StudyBatchOperationStore(
                self.root,
                study_id,
            ).archive_snapshot_guard():
                with workspace_mutation_lock(self.root):
                    validation_store = StudyWorkspaceStore(self.root)
                    compatibility = (
                        validation_store.validate_completed_batch_snapshots(
                            study_id
                        )
                    )
                    validation_store.validate_skill_pack_versions(
                        study_id,
                        legacy_unaudited_versions=(
                            compatibility.legacy_unaudited_versions
                        ),
                    )
                    return self._create_archive_snapshot(
                        study_id,
                        study_dir,
                        legacy_import_ids=compatibility.legacy_import_ids,
                    )
        except (
            CodingReferenceConflictError,
            CodingReferenceNotFoundError,
            CodingReferenceValidationError,
            EvidenceTargetBlobConflict,
            EvidenceTargetConflictError,
            EvidenceTargetNotFoundError,
            EvidenceTargetValidationError,
            EvidenceTextBlobIntegrityError,
            FileNotFoundError,
            NoteConflictError,
            NoteNotFoundError,
            NoteValidationError,
            OSError,
            SchemaCompatibilityError,
            SourceBlobIntegrityError,
            sqlite3.Error,
            StudyBatchOperationConflict,
            StudyBatchSnapshotConflict,
            StudySkillPackVersionConflict,
            ValueError,
        ) as exc:
            raise ProjectArchiveConflict(str(exc)) from exc

    def _create_archive_snapshot(
        self,
        study_id: str,
        study_dir: Path,
        *,
        legacy_import_ids: frozenset[str],
    ) -> ProjectArchiveExport:
        _validate_audit_paths(self.audit)
        if self.catalog.db_path.is_symlink():
            raise ProjectArchiveError(
                "Study archive dependencies cannot contain symbolic links"
            )
        qualitative_database_path = study_dir / "qualitative.sqlite3"
        try:
            if (
                qualitative_database_path.exists()
                or qualitative_database_path.is_symlink()
            ) and not stat.S_ISREG(qualitative_database_path.lstat().st_mode):
                raise ProjectArchiveError(
                    "Qualitative database must be a non-symlink regular file"
                )
        except OSError as exc:
            raise ProjectArchiveError(
                "Qualitative database path is unavailable or invalid"
            ) from exc
        created_at = datetime.now(UTC).isoformat()
        members: dict[str, bytes] = {}
        for path in sorted(study_dir.rglob("*")):
            if path.is_symlink():
                raise ProjectArchiveError("Study archive cannot contain symbolic links")
            if path.is_file():
                relative = path.relative_to(study_dir).as_posix()
                members[f"study/{relative}"] = path.read_bytes()

        imports_by_id = {
            record.import_id: record
            for record in self.catalog.workspace_import_records(study_id)
        }
        if legacy_import_ids:
            for workspace_id in ("legacy", "local-default"):
                for record in self.catalog.workspace_import_records(workspace_id):
                    if record.import_id in legacy_import_ids:
                        imports_by_id[record.import_id] = replace(
                            record,
                            workspace_id=study_id,
                        )
        imports = [imports_by_id[import_id] for import_id in sorted(imports_by_id)]
        members["evidence/imports.json"] = json.dumps(
            [asdict(record) for record in imports],
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        members["evidence/audit.json"] = json.dumps(
            self.audit.events_for_subject("study", study_id),
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        records_by_blob: dict[str, list[EvidenceImportRecord]] = {}
        for record in imports:
            records_by_blob.setdefault(record.source_blob_sha256, []).append(record)
        unretained_blobs: list[str] = []
        for digest, blob_records in sorted(records_by_blob.items()):
            try:
                members[f"blobs/{digest}.blob"] = self.blobs.read_verified(digest)
            except FileNotFoundError:
                if not all(
                    record.import_id in legacy_import_ids
                    for record in blob_records
                ):
                    raise
                unretained_blobs.append(digest)
        if unretained_blobs:
            members["evidence/unretained_blobs.json"] = json.dumps(
                unretained_blobs,
                indent=2,
            ).encode("utf-8")

        evidence_registry = EvidenceTargetRegistry(self.root)
        evidence_sets = evidence_registry.workspace_snapshot(study_id)
        archived_import_ids = {record.import_id for record in imports}
        if any(
            snapshot.import_id not in archived_import_ids
            for snapshot in evidence_sets
        ):
            raise ProjectArchiveError(
                "Study evidence targets are not closed over archived imports"
            )
        members["evidence/targets.json"] = _evidence_target_document(
            study_id,
            evidence_sets,
        )
        evidence_text_digests = {
            digest
            for snapshot in evidence_sets
            for digest in snapshot.text_blob_sha256s
        }
        for digest in sorted(evidence_text_digests):
            text = evidence_registry.text_blobs.read_verified(digest)
            members[f"evidence_text_blobs/{digest}.utf8"] = text.encode("utf-8")

        parsed_payload = _parse_archive_payload(
            members,
            format_version=ARCHIVE_FORMAT_VERSION,
            study_id=study_id,
        )
        with tempfile.TemporaryDirectory(
            prefix=f".{study_id}.archive-validation."
        ) as validation_directory:
            _stage_and_validate_archive(
                Path(validation_directory),
                study_id,
                members,
                parsed_payload,
                format_version=ARCHIVE_FORMAT_VERSION,
            )

        _validate_member_names(["manifest.json", *members])

        manifest = {
            "format_version": ARCHIVE_FORMAT_VERSION,
            "study_id": study_id,
            "created_at": created_at,
            "members": [
                {
                    "path": name,
                    "size_bytes": len(content),
                    "sha256": sha256(content).hexdigest(),
                }
                for name, content in sorted(members.items())
            ],
        }
        manifest_content = json.dumps(
            manifest,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        _enforce_archive_budget({"manifest.json": manifest_content, **members})
        _prepare_non_symlink_directory(self.backups_dir)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        archive_path = (
            self.backups_dir
            / f"{study_id}-{timestamp}-{uuid4().hex[:8]}.nlpstudy.zip"
        )
        with atomic_binary_writer(archive_path) as archive_file:
            with ZipFile(archive_file, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr(
                    "manifest.json",
                    manifest_content,
                )
                for name, content in sorted(members.items()):
                    archive.writestr(name, content)
        return ProjectArchiveExport(
            study_id=study_id,
            archive_path=archive_path,
            archive_sha256=sha256(archive_path.read_bytes()).hexdigest(),
            member_count=len(members) + 1,
            created_at=created_at,
        )

    def restore_archive(self, archive_path: Path | str) -> ProjectRestoreResult:
        archive_path = Path(archive_path)
        if archive_path.stat().st_size > MAX_ARCHIVE_FILE_BYTES:
            raise ProjectArchiveError("Archive exceeds file size limit")
        try:
            with ZipFile(archive_path) as archive:
                members, manifest = _verified_archive_members(archive)
            members.pop("manifest.json")
        except (BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProjectArchiveError("Archive is malformed") from exc
        study_id = manifest["study_id"]
        if not isinstance(study_id, str):
            raise ProjectArchiveError("Archive manifest study id is invalid")
        _validate_study_id(study_id)
        study_dir = self.studies_dir / study_id
        if study_dir.exists() or study_dir.is_symlink():
            raise FileExistsError(study_id)
        format_version = int(manifest["format_version"])
        parsed_payload = _parse_archive_payload(
            members,
            format_version=format_version,
            study_id=study_id,
        )

        root_existed = _validate_optional_destination_directory(self.root)
        studies_existed = _validate_optional_destination_directory(
            self.studies_dir,
            parent_must_exist=root_existed,
        )
        lock_path = self.root.resolve(strict=False) / ".workspace-mutation.lock"
        lock_existed = lock_path.exists() or lock_path.is_symlink()
        stage_parent = self.root if root_existed else self.root.parent
        try:
            stage_root = Path(
                tempfile.mkdtemp(
                    prefix=f".{study_id}.restore.",
                    dir=stage_parent,
                )
            )
        except OSError as exc:
            raise ProjectArchiveError(
                "Archive restore staging directory is unavailable"
            ) from exc
        restored = False
        try:
            stage_dir = _stage_and_validate_archive(
                stage_root,
                study_id,
                members,
                parsed_payload,
                format_version=format_version,
            )

            with workspace_mutation_lock(self.root):
                _prepare_non_symlink_directory(self.root)
                _prepare_non_symlink_directory(self.studies_dir)
                if study_dir.exists() or study_dir.is_symlink():
                    raise FileExistsError(study_id)
                imported_audit_count = self._preflight_destination_state(
                    stage_root,
                    parsed_payload,
                    members,
                )
                self._commit_restore(
                    stage_root,
                    stage_dir,
                    study_dir,
                    parsed_payload,
                    members,
                )
                restored = True
        except WorkspaceLockError as exc:
            raise ProjectArchiveError(
                "Archive destination workspace lock is invalid"
            ) from exc
        finally:
            shutil.rmtree(stage_root, ignore_errors=True)
            if not restored:
                try:
                    _cleanup_restore_ancestors(
                        self.root,
                        root_existed=root_existed,
                        studies_existed=studies_existed,
                        lock_path=lock_path,
                        lock_existed=lock_existed,
                    )
                except OSError as exc:
                    raise ProjectArchiveError(
                        "Archive restore failed and destination rollback was incomplete"
                    ) from exc
        return ProjectRestoreResult(
            study_id=study_id,
            study_dir=study_dir,
            import_count=len(parsed_payload.imports),
            blob_count=len(parsed_payload.source_blob_names),
            audit_event_count=imported_audit_count,
        )

    def _preflight_destination_state(
        self,
        stage_root: Path,
        payload: _ParsedArchivePayload,
        members: dict[str, bytes],
    ) -> int:
        preflight_root = stage_root / "destination-preflight"
        try:
            if self.catalog.db_path.is_symlink():
                raise ValueError("Destination evidence database is a symbolic link")
            if self.catalog.db_path.exists():
                _copy_sqlite_database(
                    self.catalog.db_path,
                    preflight_root / "evidence.sqlite3",
                )
            _validate_audit_paths(self.audit)
            if self.audit.events_path.exists():
                atomic_write_bytes(
                    preflight_root / "audit" / "events.jsonl",
                    self.audit.events_path.read_bytes(),
                )
            for blob_name in sorted(payload.source_blob_names):
                digest = PurePosixPath(blob_name).stem
                destination = self.blobs.blob_path(digest)
                _validate_destination_blob_path(self.root, destination)
                if destination.exists() or destination.is_symlink():
                    stored_content = self.blobs.read_verified(digest)
                    if stored_content != members[blob_name]:
                        raise ValueError("Destination source blob conflicts")
            for digest in sorted(payload.unretained_blob_digests):
                destination = self.blobs.blob_path(digest)
                _validate_destination_blob_path(self.root, destination)
                if destination.exists() or destination.is_symlink():
                    self.blobs.read_verified(digest)

            destination_text_store = EvidenceTextBlobStore(self.root)
            for digest, text in payload.evidence_texts:
                destination = destination_text_store.blob_path(digest)
                _validate_destination_blob_path(self.root, destination)
                if destination.exists() or destination.is_symlink():
                    if destination_text_store.read_verified(digest) != text:
                        raise ValueError("Destination evidence text blob conflicts")

            preflight_catalog = EvidenceCatalog(preflight_root)
            with preflight_catalog.read():
                pass
            _restore_imports(preflight_catalog, list(payload.imports))
            preflight_text_store = EvidenceTextBlobStore(preflight_root)
            for digest, text in payload.evidence_texts:
                preflight_text_store.store(text, digest)
            preflight_registry = EvidenceTargetRegistry(preflight_root)
            for evidence_set in payload.evidence_sets:
                preflight_registry.register_complete_set(evidence_set)
            return AuditLogStore(preflight_root).import_events(
                list(payload.audit_events)
            )
        except (
            EvidenceTargetBlobConflict,
            EvidenceTargetConflictError,
            EvidenceTargetNotFoundError,
            EvidenceTargetValidationError,
            EvidenceTextBlobIntegrityError,
            OSError,
            SourceBlobIntegrityError,
            sqlite3.Error,
            TypeError,
            ValueError,
        ) as exc:
            raise ProjectArchiveError(
                "Archive evidence conflicts with destination"
            ) from exc
        except SchemaCompatibilityError as exc:
            raise ProjectArchiveConflict(str(exc)) from exc

    def _commit_restore(
        self,
        stage_root: Path,
        stage_dir: Path,
        study_dir: Path,
        payload: _ParsedArchivePayload,
        members: dict[str, bytes],
    ) -> None:
        preflight_root = stage_root / "destination-preflight"
        preflight_catalog = preflight_root / "evidence.sqlite3"
        preflight_audit = preflight_root / "audit" / "events.jsonl"
        catalog_existed = self.catalog.db_path.exists() or self.catalog.db_path.is_symlink()
        audit_existed = self.audit.events_path.exists() or self.audit.events_path.is_symlink()
        catalog_before = self.catalog.db_path.read_bytes() if catalog_existed else None
        audit_before = self.audit.events_path.read_bytes() if audit_existed else None
        created_blobs: list[Path] = []
        created_directories: set[Path] = set()
        try:
            for blob_name in sorted(payload.source_blob_names):
                digest = PurePosixPath(blob_name).stem
                blob_path = self.blobs.blob_path(digest)
                existed = blob_path.exists() or blob_path.is_symlink()
                created_directories.update(
                    _missing_parent_directories(blob_path, self.root)
                )
                if not existed:
                    created_blobs.append(blob_path)
                self.blobs.store(members[blob_name], digest)

            text_store = EvidenceTextBlobStore(self.root)
            for digest, text in payload.evidence_texts:
                blob_path = text_store.blob_path(digest)
                existed = blob_path.exists() or blob_path.is_symlink()
                created_directories.update(
                    _missing_parent_directories(blob_path, self.root)
                )
                if not existed:
                    created_blobs.append(blob_path)
                text_store.store(text, digest)
            if preflight_catalog.exists():
                atomic_write_bytes(
                    self.catalog.db_path,
                    preflight_catalog.read_bytes(),
                )
            if preflight_audit.exists():
                if not self.audit.audit_dir.exists():
                    created_directories.add(self.audit.audit_dir)
                atomic_write_bytes(
                    self.audit.events_path,
                    preflight_audit.read_bytes(),
                )
            self._publish_study(stage_dir, study_dir)
        except BaseException as exc:
            try:
                if (
                    not stage_dir.exists()
                    and not stage_dir.is_symlink()
                    and (study_dir.exists() or study_dir.is_symlink())
                ):
                    _remove_published_study(study_dir)
                _restore_file_snapshot(self.catalog.db_path, catalog_before)
                _restore_file_snapshot(self.audit.events_path, audit_before)
                for blob_path in created_blobs:
                    blob_path.unlink(missing_ok=True)
                for directory in sorted(
                    created_directories,
                    key=lambda path: len(path.parts),
                    reverse=True,
                ):
                    if directory.exists() or directory.is_symlink():
                        directory.rmdir()
            except BaseException as rollback_exc:
                raise ProjectArchiveError(
                    "Archive restore failed and destination rollback was incomplete"
                ) from rollback_exc
            if isinstance(exc, (FileExistsError, ProjectArchiveError)):
                raise
            raise ProjectArchiveError(
                "Archive restore could not be committed"
            ) from exc

    def _publish_study(self, stage_dir: Path, study_dir: Path) -> None:
        os.replace(stage_dir, study_dir)


def _verified_archive_members(
    archive: ZipFile,
) -> tuple[dict[str, bytes], dict[str, object]]:
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise ProjectArchiveError("Archive contains too many members")
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise ProjectArchiveError("Archive contains duplicate members")
    for info in infos:
        _validate_member(info)
    _validate_member_names(names)
    if sum(info.file_size for info in infos) > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
        raise ProjectArchiveError("Archive exceeds uncompressed size limit")
    if "manifest.json" not in names:
        raise ProjectArchiveError("Archive manifest is missing")
    try:
        members = {info.filename: archive.read(info) for info in infos}
    except (
        BadZipFile,
        EOFError,
        NotImplementedError,
        OSError,
        RuntimeError,
        zlib.error,
    ) as exc:
        raise ProjectArchiveError("Archive member data is malformed") from exc
    manifest = _strict_json_loads(
        members["manifest.json"],
        "Archive manifest is malformed",
    )
    if not isinstance(manifest, dict):
        raise ProjectArchiveError("Archive manifest is malformed")
    if set(manifest) != {"format_version", "study_id", "created_at", "members"}:
        raise ProjectArchiveError("Archive manifest is malformed")
    if (
        isinstance(manifest["format_version"], bool)
        or not isinstance(manifest["format_version"], int)
        or manifest["format_version"] not in SUPPORTED_ARCHIVE_FORMAT_VERSIONS
    ):
        raise ProjectArchiveError("Unsupported archive format version")
    if not isinstance(manifest.get("study_id"), str):
        raise ProjectArchiveError("Archive manifest study id is invalid")
    _validate_timestamp(manifest.get("created_at"), "manifest timestamp")
    declared = manifest.get("members")
    if not isinstance(declared, list):
        raise ProjectArchiveError("Archive manifest members are invalid")
    records: dict[str, dict[str, object]] = {}
    for record in declared:
        if not isinstance(record, dict) or set(record) != {
            "path",
            "size_bytes",
            "sha256",
        }:
            raise ProjectArchiveError("Archive manifest member is invalid")
        path = record["path"]
        size_bytes = record["size_bytes"]
        digest = record["sha256"]
        if not isinstance(path, str):
            raise ProjectArchiveError("Archive manifest member path is invalid")
        _validate_member_name(path)
        if (
            isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes < 0
            or size_bytes > MAX_ARCHIVE_FILE_BYTES
        ):
            raise ProjectArchiveError("Archive manifest member size is invalid")
        if not isinstance(digest, str) or not _SHA256_PATTERN.fullmatch(digest):
            raise ProjectArchiveError("Archive manifest member hash is invalid")
        if path in records:
            raise ProjectArchiveError("Archive manifest contains duplicate members")
        records[path] = record
    actual_names = set(members) - {"manifest.json"}
    if set(records) != actual_names or len(records) != len(declared):
        raise ProjectArchiveError("Archive members do not match manifest")
    required_members = {
        "study/study.json",
        "evidence/imports.json",
        "evidence/audit.json",
    }
    if not required_members.issubset(actual_names):
        raise ProjectArchiveError("Archive required members are missing")
    format_version = int(manifest["format_version"])
    target_members = {
        name
        for name in actual_names
        if name == "evidence/targets.json"
        or name.startswith("evidence_text_blobs/")
    }
    if format_version == 1 and target_members:
        raise ProjectArchiveError(
            "Format version 1 cannot contain evidence target members"
        )
    if format_version == 2 and "evidence/targets.json" not in actual_names:
        raise ProjectArchiveError("Archive required members are missing")
    for name, record in records.items():
        content = members[name]
        if record["size_bytes"] != len(content):
            raise ProjectArchiveError(f"Archive member size mismatch: {name}")
        if record["sha256"] != sha256(content).hexdigest():
            raise ProjectArchiveError(f"Archive member hash mismatch: {name}")
    return members, manifest


def _validate_member(info: ZipInfo) -> None:
    _validate_member_name(info.filename)
    if info.file_size < 0 or info.file_size > MAX_ARCHIVE_FILE_BYTES:
        raise ProjectArchiveError("Archive member exceeds file size limit")
    if info.is_dir():
        raise ProjectArchiveError("Archive contains an unsafe member path")
    if info.flag_bits & 0x1:
        raise ProjectArchiveError("Archive contains an encrypted member")
    if info.compress_type not in _SUPPORTED_ARCHIVE_COMPRESSION_TYPES:
        raise ProjectArchiveError("Archive contains unsupported compression")
    unix_mode = (info.external_attr >> 16) & 0o170000
    if unix_mode == 0o120000:
        raise ProjectArchiveError("Archive contains a symbolic link")
    if unix_mode not in (0, 0o100000):
        raise ProjectArchiveError("Archive contains an unsupported member type")


def _validate_member_names(names: list[str]) -> None:
    folded: set[str] = set()
    for name in names:
        _validate_member_name(name)
        key = unicodedata.normalize("NFC", name).casefold()
        if key in folded:
            raise ProjectArchiveError("Archive contains case-colliding members")
        folded.add(key)
    for key in folded:
        parts = key.split("/")
        if any(
            "/".join(parts[:index]) in folded
            for index in range(1, len(parts))
        ):
            raise ProjectArchiveError(
                "Archive contains file-directory-colliding members"
            )


def _validate_member_name(name: str) -> None:
    if (
        not name
        or len(name) > MAX_ARCHIVE_MEMBER_PATH_LENGTH
        or unicodedata.normalize("NFC", name) != name
        or "\\" in name
        or ":" in name
        or "//" in name
    ):
        raise ProjectArchiveError("Archive contains an unsafe member path")
    path = PurePosixPath(name)
    if path.is_absolute() or path.as_posix() != name:
        raise ProjectArchiveError("Archive contains an unsafe member path")
    for part in path.parts:
        try:
            component_utf16_units = len(part.encode("utf-16-le")) // 2
        except UnicodeEncodeError as exc:
            raise ProjectArchiveError(
                "Archive contains an unsafe member path"
            ) from exc
        if (
            part in {"", ".", ".."}
            or part.endswith((".", " "))
            or _WINDOWS_INVALID_FILENAME_CHARACTER.search(part)
            or component_utf16_units > MAX_ARCHIVE_MEMBER_COMPONENT_UTF16_UNITS
        ):
            raise ProjectArchiveError("Archive contains an unsafe member path")
        if _WINDOWS_DEVICE_NAME.fullmatch(part.split(".", 1)[0].rstrip(". ")):
            raise ProjectArchiveError("Archive contains an unsafe member path")


def _safe_extraction_target(stage_dir: Path, relative: PurePosixPath) -> Path:
    if not relative.parts:
        raise ProjectArchiveError("Archive contains an unsafe member path")
    root = stage_dir.resolve()
    target = stage_dir.joinpath(*relative.parts)
    if not target.resolve(strict=False).is_relative_to(root):
        raise ProjectArchiveError("Archive contains an unsafe member path")
    return target


def _evidence_target_document(
    study_id: str,
    snapshots: tuple[EvidenceSetSnapshot, ...],
) -> bytes:
    records = []
    for snapshot in sorted(snapshots, key=lambda item: item.evidence_set_id):
        records.append(
            {
                **snapshot.to_manifest(),
                "evidence_set_id": snapshot.evidence_set_id,
                "snapshot_sha256": snapshot.snapshot_sha256,
                "created_at": snapshot.created_at,
            }
        )
    return json.dumps(
        {
            "format": _TARGET_EXPORT_FORMAT,
            "format_version": _TARGET_EXPORT_FORMAT_VERSION,
            "workspace_id": study_id,
            "sets": records,
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _parse_archive_payload(
    members: dict[str, bytes],
    *,
    format_version: int,
    study_id: str,
) -> _ParsedArchivePayload:
    imports = _parse_evidence_imports(
        members["evidence/imports.json"],
        study_id,
    )
    unretained_blob_digests = _parse_unretained_blob_digests(
        members.get("evidence/unretained_blobs.json", b"[]")
    )
    audit_events = _parse_audit_events(
        members["evidence/audit.json"],
        study_id,
    )
    expected_source_blob_names = {
        f"blobs/{record.source_blob_sha256}.blob"
        for record in imports
        if record.source_blob_sha256 not in unretained_blob_digests
    }
    referenced_source_digests = {
        record.source_blob_sha256 for record in imports
    }
    if not unretained_blob_digests.issubset(referenced_source_digests):
        raise ProjectArchiveError(
            "Archive unretained blob set does not match evidence imports"
        )
    actual_source_blob_names = {
        name for name in members if name.startswith("blobs/")
    }
    if actual_source_blob_names != expected_source_blob_names:
        raise ProjectArchiveError(
            "Archive blob set does not match evidence imports"
        )

    evidence_texts: dict[str, str] = {}
    evidence_sets: tuple[PreparedEvidenceSet, ...] = ()
    if format_version == 2:
        evidence_sets, evidence_texts = _parse_evidence_targets(
            members["evidence/targets.json"],
            members,
            study_id=study_id,
            imports=imports,
        )
    elif format_version != 1:
        raise ProjectArchiveError("Unsupported archive format version")

    return _ParsedArchivePayload(
        imports=tuple(imports),
        audit_events=tuple(audit_events),
        unretained_blob_digests=frozenset(unretained_blob_digests),
        source_blob_names=frozenset(actual_source_blob_names),
        evidence_texts=tuple(sorted(evidence_texts.items())),
        evidence_sets=evidence_sets,
    )


def _parse_evidence_targets(
    content: bytes,
    members: dict[str, bytes],
    *,
    study_id: str,
    imports: list[EvidenceImportRecord],
) -> tuple[tuple[PreparedEvidenceSet, ...], dict[str, str]]:
    document = _strict_json_loads(
        content,
        "Archive evidence target records are malformed",
    )
    if not isinstance(document, dict) or set(document) != _TARGET_EXPORT_FIELDS:
        raise ProjectArchiveError("Archive evidence target records are malformed")
    if (
        document["format"] != _TARGET_EXPORT_FORMAT
        or type(document["format_version"]) is not int
        or document["format_version"] != _TARGET_EXPORT_FORMAT_VERSION
        or document["workspace_id"] != study_id
        or not isinstance(document["sets"], list)
    ):
        raise ProjectArchiveError("Archive evidence target records are malformed")

    records = document["sets"]
    record_ids: list[str] = []
    required_text_digests: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != _TARGET_SET_FIELDS:
            raise ProjectArchiveError(
                "Archive evidence target records are malformed"
            )
        record_id = record["evidence_set_id"]
        if not isinstance(record_id, str):
            raise ProjectArchiveError(
                "Archive evidence target records are malformed"
            )
        record_ids.append(record_id)
        required_text_digests.add(
            _archive_digest(record["transcript_text_sha256"])
        )
        passages = record["passages"]
        if not isinstance(passages, list):
            raise ProjectArchiveError(
                "Archive evidence target records are malformed"
            )
        for passage_index, passage in enumerate(passages):
            if (
                not isinstance(passage, dict)
                or set(passage) != _TARGET_PASSAGE_FIELDS
                or type(passage["passage_ordinal"]) is not int
                or passage["passage_ordinal"] != passage_index
                or type(passage["text_length"]) is not int
                or passage["text_length"] < 0
                or not isinstance(passage["cunits"], list)
            ):
                raise ProjectArchiveError(
                    "Archive evidence target records are malformed"
                )
            required_text_digests.add(_archive_digest(passage["text_sha256"]))
            for cunit_index, cunit in enumerate(passage["cunits"]):
                if (
                    not isinstance(cunit, dict)
                    or set(cunit) != _TARGET_CUNIT_FIELDS
                    or type(cunit["cunit_ordinal"]) is not int
                    or cunit["cunit_ordinal"] != cunit_index
                    or type(cunit["text_length"]) is not int
                    or cunit["text_length"] < 0
                ):
                    raise ProjectArchiveError(
                        "Archive evidence target records are malformed"
                    )
                required_text_digests.add(_archive_digest(cunit["text_sha256"]))
    if len(record_ids) != len(set(record_ids)) or record_ids != sorted(record_ids):
        raise ProjectArchiveError("Archive evidence target records are malformed")

    expected_text_names = {
        f"evidence_text_blobs/{digest}.utf8"
        for digest in required_text_digests
    }
    actual_text_names = {
        name for name in members if name.startswith("evidence_text_blobs/")
    }
    if actual_text_names != expected_text_names:
        raise ProjectArchiveError(
            "Archive evidence text blob closure is incomplete or excessive"
        )
    texts: dict[str, str] = {}
    for digest in sorted(required_text_digests):
        blob = members[f"evidence_text_blobs/{digest}.utf8"]
        if sha256(blob).hexdigest() != digest:
            raise ProjectArchiveError("Archive evidence text blob hash is invalid")
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProjectArchiveError(
                "Archive evidence text blob is not valid UTF-8"
            ) from exc
        if text.encode("utf-8") != blob:
            raise ProjectArchiveError(
                "Archive evidence text blob is not canonical UTF-8"
            )
        texts[digest] = text

    imports_by_id = {record.import_id: record for record in imports}
    prepared_sets = tuple(
        _prepared_evidence_set_from_archive(
            record,
            texts,
            study_id=study_id,
            imports_by_id=imports_by_id,
        )
        for record in records
    )
    return prepared_sets, texts


def _prepared_evidence_set_from_archive(
    record: dict[str, object],
    texts: dict[str, str],
    *,
    study_id: str,
    imports_by_id: dict[str, EvidenceImportRecord],
) -> PreparedEvidenceSet:
    import_id = record["import_id"]
    if not isinstance(import_id, str) or import_id not in imports_by_id:
        raise ProjectArchiveError(
            "Archive evidence target references an unavailable import"
        )
    archived_import = imports_by_id[import_id]
    created_at = record["created_at"]
    _validate_timestamp(created_at, "evidence target timestamp")
    if created_at != archived_import.imported_at:
        raise ProjectArchiveError(
            "Archive evidence target creation time conflicts with its import"
        )
    if record["workspace_id"] != study_id:
        raise ProjectArchiveError(
            "Archive evidence target belongs to another workspace"
        )

    passages: list[EvidencePassageInput] = []
    for passage in record["passages"]:
        passage_text = texts[_archive_digest(passage["text_sha256"])]
        if len(passage_text) != passage["text_length"]:
            raise ProjectArchiveError(
                "Archive evidence passage length is invalid"
            )
        cunits: list[EvidenceCUnitInput] = []
        for cunit in passage["cunits"]:
            cunit_text = texts[_archive_digest(cunit["text_sha256"])]
            if len(cunit_text) != cunit["text_length"]:
                raise ProjectArchiveError(
                    "Archive evidence C-unit length is invalid"
                )
            cunits.append(
                EvidenceCUnitInput(
                    cunit_id=cunit["cunit_id"],
                    cunit_ordinal=cunit["cunit_ordinal"],
                    text=cunit_text,
                )
            )
        passages.append(
            EvidencePassageInput(
                passage_id=passage["passage_id"],
                passage_ordinal=passage["passage_ordinal"],
                role=passage["role"],
                text=passage_text,
                cunits=tuple(cunits),
            )
        )
    try:
        prepared = prepare_complete_evidence_set(
            import_id=import_id,
            workspace_id=record["workspace_id"],
            project_source_id=record["project_source_id"],
            transcript_revision_id=record["transcript_revision_id"],
            transcript_text=texts[
                _archive_digest(record["transcript_text_sha256"])
            ],
            producer_kind=record["producer_kind"],
            producer_version=record["producer_version"],
            producer_status=record["producer_status"],
            review_status=record["review_status"],
            passages=tuple(passages),
        )
    except EvidenceTargetValidationError as exc:
        if "current producer" in str(exc):
            raise ProjectArchiveError(
                "Archive C-unit evidence does not match the current producer"
            ) from exc
        raise ProjectArchiveError(
            "Archive evidence target records are invalid"
        ) from exc
    except (KeyError, TypeError) as exc:
        raise ProjectArchiveError(
            "Archive evidence target records are invalid"
        ) from exc
    if (
        record["format"] != _TARGET_SET_FORMAT
        or record["format_version"] != _TARGET_SET_FORMAT_VERSION
        or type(record["format_version"]) is not int
        or type(record["passage_count"]) is not int
        or type(record["cunit_count"]) is not int
        or record["passage_count"] != prepared.passage_count
        or record["cunit_count"] != prepared.cunit_count
        or record["snapshot_sha256"] != prepared.snapshot_sha256
        or record["evidence_set_id"] != prepared.evidence_set_id
        or prepared.to_manifest()
        != {
            key: value
            for key, value in record.items()
            if key not in {"evidence_set_id", "snapshot_sha256", "created_at"}
        }
    ):
        raise ProjectArchiveError(
            "Archive evidence target identity is invalid"
        )
    _validate_archived_producer_interpretation(prepared)
    return prepared


def _validate_archived_producer_interpretation(
    prepared: PreparedEvidenceSet,
) -> None:
    if prepared.producer_kind != "cunit_segmentation":
        return
    events = [
        RawTranscriptEvent(
            timestamp_seconds=passage.passage_ordinal,
            speaker=passage.role,
            text=passage.text,
            passage_id=passage.passage_id,
        )
        for passage in prepared.passages
    ]
    canonical = adjudicate_cunit_boundaries(events)
    if len(canonical.decisions) != len(prepared.passages):
        raise ProjectArchiveError(
            "Archive C-unit evidence does not match the current producer"
        )
    for passage, decision in zip(
        prepared.passages,
        canonical.decisions,
        strict=True,
    ):
        if (
            tuple(decision.cunit_ids)
            != tuple(cunit.cunit_id for cunit in passage.cunits)
            or tuple(decision.cunit_texts)
            != tuple(cunit.text for cunit in passage.cunits)
        ):
            raise ProjectArchiveError(
                "Archive C-unit evidence does not match the current producer"
            )


def _stage_and_validate_archive(
    stage_root: Path,
    study_id: str,
    members: dict[str, bytes],
    payload: _ParsedArchivePayload,
    *,
    format_version: int,
) -> Path:
    stage_dir = stage_root / "studies" / study_id
    stage_dir.mkdir(parents=True)
    for name, member_content in members.items():
        if name.startswith("study/"):
            relative = PurePosixPath(name).relative_to("study")
            atomic_write_bytes(
                _safe_extraction_target(stage_dir, relative),
                member_content,
            )
    study_payload = _strict_json_loads(
        (stage_dir / "study.json").read_bytes(),
        "Archive study record is malformed",
    )
    _validate_study_workspace_payload(study_payload, study_id)
    try:
        validate_study_batch_operation_database(
            stage_dir / "batch_operations.sqlite3",
            study_id,
        )
    except SchemaCompatibilityError as exc:
        raise ProjectArchiveConflict(str(exc)) from exc
    except (sqlite3.Error, ValueError) as exc:
        raise ProjectArchiveError(
            "Archive batch operation journal is invalid"
        ) from exc

    try:
        validation_blobs = SourceBlobStore(stage_root)
        for blob_name in sorted(payload.source_blob_names):
            digest = PurePosixPath(blob_name).stem
            validation_blobs.store(members[blob_name], digest)
        _restore_imports(EvidenceCatalog(stage_root), list(payload.imports))
        validation_texts = EvidenceTextBlobStore(stage_root)
        for digest, text in payload.evidence_texts:
            validation_texts.store(text, digest)
        validation_registry = EvidenceTargetRegistry(stage_root)
        for evidence_set in payload.evidence_sets:
            validation_registry.register_complete_set(evidence_set)
        stored_set_ids = tuple(
            snapshot.evidence_set_id
            for snapshot in validation_registry.workspace_snapshot(study_id)
        )
        expected_set_ids = tuple(
            evidence_set.evidence_set_id for evidence_set in payload.evidence_sets
        )
        if stored_set_ids != expected_set_ids:
            raise ProjectArchiveError(
                "Archive evidence target closure is inconsistent"
            )
        AuditLogStore(stage_root).import_events(list(payload.audit_events))
        if format_version == 1:
            _reject_v1_target_references(
                stage_dir,
                payload.audit_events,
            )
        validation_store = StudyWorkspaceStore(stage_root)
        compatibility = validation_store.validate_completed_batch_snapshots(
            study_id
        )
        for digest in payload.unretained_blob_digests:
            if any(
                record.import_id not in compatibility.legacy_import_ids
                for record in payload.imports
                if record.source_blob_sha256 == digest
            ):
                raise StudyBatchSnapshotConflict(
                    "Archive unretained blob is not eligible legacy evidence"
                )
        validation_store.validate_skill_pack_versions(
            study_id,
            legacy_unaudited_versions=compatibility.legacy_unaudited_versions,
        )
    except ProjectArchiveError:
        raise
    except (
        EvidenceTargetBlobConflict,
        EvidenceTargetConflictError,
        EvidenceTargetNotFoundError,
        EvidenceTargetValidationError,
        EvidenceTextBlobIntegrityError,
        FileNotFoundError,
        SourceBlobIntegrityError,
        StudyBatchOperationConflict,
        StudyBatchSnapshotConflict,
        StudySkillPackVersionConflict,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as exc:
        raise ProjectArchiveError(
            "Archive completed batch artifacts are invalid"
        ) from exc

    _validate_staged_qualitative_project(stage_root, study_id)
    return stage_dir


def _reject_v1_target_references(
    stage_dir: Path,
    audit_events: tuple[dict[str, object], ...],
) -> None:
    for artifact_path in sorted(stage_dir.rglob("*.json")):
        payload = _strict_json_loads(
            artifact_path.read_bytes(),
            "Archive study JSON artifact is malformed",
        )
        if _contains_nonempty_evidence_set_id(payload):
            raise ProjectArchiveError(
                "Format version 1 cannot reference evidence targets"
            )
    if any(
        _contains_nonempty_evidence_set_id(event)
        for event in audit_events
    ):
        raise ProjectArchiveError(
            "Format version 1 cannot reference evidence targets"
        )

    qualitative_path = stage_dir / "qualitative.sqlite3"
    if not qualitative_path.exists() and not qualitative_path.is_symlink():
        return
    database_uri = f"{qualitative_path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as connection:
        table_names = {
            str(row[0])
            for row in connection.execute(
                "select name from sqlite_master where type = 'table'"
            )
        }
        if "coding_references" in table_names:
            coding_reference = connection.execute(
                """
                select 1 from coding_references
                where evidence_set_id is not null and evidence_set_id != ''
                limit 1
                """
            ).fetchone()
            if coding_reference is not None:
                raise ProjectArchiveError(
                    "Format version 1 cannot reference evidence targets"
                )
        if "qualitative_notes" in table_names:
            note_reference = connection.execute(
                """
                select 1 from qualitative_notes
                where evidence_set_id is not null and evidence_set_id != ''
                limit 1
                """
            ).fetchone()
            if note_reference is not None:
                raise ProjectArchiveError(
                    "Format version 1 cannot reference evidence targets"
                )
        if "qualitative_audit_events" in table_names:
            for (metadata_json,) in connection.execute(
                "select metadata_json from qualitative_audit_events"
            ):
                metadata = _strict_json_loads(
                    str(metadata_json).encode("utf-8"),
                    "Archive qualitative audit metadata is malformed",
                )
                if _contains_nonempty_evidence_set_id(metadata):
                    raise ProjectArchiveError(
                        "Format version 1 cannot reference evidence targets"
                    )


def _contains_nonempty_evidence_set_id(value: object) -> bool:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            if (
                "evidence_set_id" in current
                and current["evidence_set_id"] not in (None, "")
            ):
                return True
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return False


def _archive_digest(value: object) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise ProjectArchiveError(
            "Archive evidence target SHA-256 is invalid"
        )
    return value


def _parse_evidence_imports(
    content: bytes,
    study_id: str,
) -> list[EvidenceImportRecord]:
    payloads = _strict_json_loads(
        content,
        "Archive evidence records are malformed",
    )
    if not isinstance(payloads, list):
        raise ProjectArchiveError("Archive evidence records are malformed")
    imports: list[EvidenceImportRecord] = []
    import_ids: set[str] = set()
    for payload in payloads:
        if not isinstance(payload, dict) or set(payload) != _EVIDENCE_REQUIRED_FIELDS:
            raise ProjectArchiveError("Archive evidence records are malformed")
        _validate_evidence_string(
            payload,
            "import_id",
            MAX_EVIDENCE_IDENTIFIER_LENGTH,
        )
        _validate_evidence_string(payload, "run_id", MAX_EVIDENCE_IDENTIFIER_LENGTH)
        _validate_evidence_string(payload, "pipeline", MAX_EVIDENCE_LABEL_LENGTH)
        _validate_evidence_string(
            payload,
            "source_id",
            MAX_EVIDENCE_IDENTIFIER_LENGTH,
        )
        _validate_evidence_string(
            payload,
            "source_filename",
            MAX_EVIDENCE_FILENAME_LENGTH,
        )
        _validate_evidence_string(
            payload,
            "source_media_type",
            MAX_EVIDENCE_LABEL_LENGTH,
        )
        _validate_evidence_string(
            payload,
            "transcript_revision_id",
            MAX_EVIDENCE_IDENTIFIER_LENGTH,
        )
        _validate_evidence_string(
            payload,
            "project_source_id",
            MAX_EVIDENCE_IDENTIFIER_LENGTH,
        )
        _validate_evidence_string(
            payload,
            "parent_transcript_revision_id",
            MAX_EVIDENCE_IDENTIFIER_LENGTH,
            allow_empty=True,
        )
        _validate_evidence_string(
            payload,
            "workspace_id",
            MAX_STUDY_ID_LENGTH,
        )
        for key in ("source_blob_sha256", "transcript_sha256"):
            digest = payload[key]
            if not isinstance(digest, str) or not _SHA256_PATTERN.fullmatch(digest):
                raise ProjectArchiveError("Archive evidence SHA-256 is invalid")
        _validate_timestamp(payload["imported_at"], "evidence import timestamp")
        if payload["workspace_id"] != study_id:
            raise ProjectArchiveError("Evidence import belongs to another workspace")
        if payload["import_id"] in import_ids:
            raise ProjectArchiveError("Archive evidence imports contain duplicate ids")
        import_ids.add(payload["import_id"])
        imports.append(EvidenceImportRecord(**payload))
    return imports


def _parse_unretained_blob_digests(content: bytes) -> set[str]:
    payload = _strict_json_loads(
        content,
        "Archive unretained blob records are malformed",
    )
    if (
        not isinstance(payload, list)
        or any(
            not isinstance(digest, str)
            or not _SHA256_PATTERN.fullmatch(digest)
            for digest in payload
        )
        or len(payload) != len(set(payload))
    ):
        raise ProjectArchiveError(
            "Archive unretained blob records are malformed"
        )
    return set(payload)


def _validate_evidence_string(
    payload: dict[str, object],
    key: str,
    maximum: int,
    *,
    allow_empty: bool = False,
) -> None:
    value = payload[key]
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or (not allow_empty and not value)
    ):
        raise ProjectArchiveError("Archive evidence records are malformed")


def _parse_audit_events(
    content: bytes,
    study_id: str,
) -> list[dict[str, object]]:
    events = _strict_json_loads(
        content,
        "Archive audit records are malformed",
    )
    if not isinstance(events, list):
        raise ProjectArchiveError("Archive audit records are malformed")
    event_ids: set[str] = set()
    for event in events:
        if not isinstance(event, dict) or set(event) != _AUDIT_REQUIRED_FIELDS:
            raise ProjectArchiveError("Archive audit records are malformed")
        event_id = event["id"]
        if (
            not isinstance(event_id, str)
            or not _AUDIT_EVENT_ID_PATTERN.fullmatch(event_id)
            or event_id in event_ids
        ):
            raise ProjectArchiveError("Archive audit record id is invalid")
        event_ids.add(event_id)
        for key in ("event_type", "subject_type", "subject_id", "actor"):
            value = event[key]
            if (
                not isinstance(value, str)
                or not value
                or len(value) > MAX_EVIDENCE_IDENTIFIER_LENGTH
            ):
                raise ProjectArchiveError("Archive audit records are malformed")
        if not isinstance(event["metadata"], dict):
            raise ProjectArchiveError("Archive audit records are malformed")
        _validate_timestamp(event["created_at"], "audit event timestamp")
        if event["subject_type"] != "study" or event["subject_id"] != study_id:
            raise ProjectArchiveError("Audit event belongs to another study")
    return events


def _validate_study_workspace_payload(payload: object, study_id: str) -> None:
    if not isinstance(payload, dict) or set(payload) != {
        "id",
        "name",
        "description",
        "created_at",
    }:
        raise ProjectArchiveError("Archive study record is malformed")
    if (
        not isinstance(payload["id"], str)
        or not isinstance(payload["name"], str)
        or not payload["name"].strip()
        or len(payload["name"]) > MAX_EVIDENCE_FILENAME_LENGTH
        or not isinstance(payload["description"], str)
        or len(payload["description"]) > MAX_ARCHIVE_FILE_BYTES
    ):
        raise ProjectArchiveError("Archive study record is malformed")
    _validate_timestamp(payload["created_at"], "study timestamp")
    if payload["id"] != study_id:
        raise ProjectArchiveError("Study identity does not match archive manifest")
    StudyWorkspace(**payload)


def _validate_timestamp(value: object, label: str) -> None:
    if not isinstance(value, str) or not value or len(value) > MAX_TIMESTAMP_LENGTH:
        raise ProjectArchiveError(f"Archive {label} is invalid")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ProjectArchiveError(f"Archive {label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProjectArchiveError(f"Archive {label} is invalid")


def _strict_json_loads(content: bytes, message: str) -> object:
    def object_from_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise ValueError(f"unsupported JSON constant: {value}")

    try:
        return json.loads(
            content.decode("utf-8"),
            object_pairs_hook=object_from_pairs,
            parse_constant=reject_constant,
        )
    except (
        json.JSONDecodeError,
        RecursionError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        raise ProjectArchiveError(message) from exc


def _prepare_non_symlink_directory(path: Path) -> None:
    if path.exists() or path.is_symlink():
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise ProjectArchiveError(
                "Archive destination directory is unavailable"
            ) from exc
        if not stat.S_ISDIR(mode):
            raise ProjectArchiveError(
                "Archive destination directory must be a non-symlink directory"
            )
        return
    try:
        path.mkdir()
    except OSError as exc:
        raise ProjectArchiveError(
            "Archive destination directory is unavailable"
        ) from exc


def _validate_optional_destination_directory(
    path: Path,
    *,
    parent_must_exist: bool = True,
) -> bool:
    if not path.exists() and not path.is_symlink():
        if parent_must_exist and not path.parent.exists():
            raise ProjectArchiveError(
                "Archive destination directory is unavailable"
            )
        return False
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise ProjectArchiveError(
            "Archive destination directory is unavailable"
        ) from exc
    if not stat.S_ISDIR(mode):
        raise ProjectArchiveError(
            "Archive destination directory must be a non-symlink directory"
        )
    return True


def _validate_optional_regular_file(path: Path, label: str) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise ProjectArchiveError(f"{label} is unavailable") from exc
    if not stat.S_ISREG(mode):
        raise ProjectArchiveError(
            f"{label} must be a non-symlink regular file"
        )
    return True


def _validate_audit_paths(audit: AuditLogStore) -> None:
    _validate_optional_destination_directory(audit.audit_dir)
    _validate_optional_regular_file(
        audit.events_path,
        "Archive audit event log",
    )


def _cleanup_restore_ancestors(
    root: Path,
    *,
    root_existed: bool,
    studies_existed: bool,
    lock_path: Path,
    lock_existed: bool,
) -> None:
    studies_dir = root / "studies"
    if not studies_existed and (
        studies_dir.exists() or studies_dir.is_symlink()
    ):
        studies_dir.rmdir()
    if not lock_existed and (lock_path.exists() or lock_path.is_symlink()):
        if not stat.S_ISREG(lock_path.lstat().st_mode):
            raise OSError("Restore-created workspace lock is not a regular file")
        lock_path.unlink()
    if not root_existed and (root.exists() or root.is_symlink()):
        root.rmdir()


def _validate_destination_blob_path(root: Path, path: Path) -> None:
    root_path = root.absolute()
    target_path = path.absolute()
    if not target_path.is_relative_to(root_path):
        raise ProjectArchiveError("Destination blob path escapes archive root")
    current = root_path
    for component in target_path.parent.relative_to(root_path).parts:
        current = current / component
        if not current.exists() and not current.is_symlink():
            continue
        try:
            mode = current.lstat().st_mode
        except OSError as exc:
            raise ProjectArchiveError(
                "Destination blob directory is unavailable"
            ) from exc
        if not stat.S_ISDIR(mode):
            raise ProjectArchiveError(
                "Destination blob directory must be a non-symlink directory"
            )
    if target_path.exists() or target_path.is_symlink():
        try:
            mode = target_path.lstat().st_mode
        except OSError as exc:
            raise ProjectArchiveError("Destination blob is unavailable") from exc
        if not stat.S_ISREG(mode):
            raise ProjectArchiveError(
                "Destination blob must be a non-symlink regular file"
            )


def _missing_parent_directories(path: Path, root: Path) -> set[Path]:
    root_path = root.absolute()
    current = path.absolute().parent
    missing: set[Path] = set()
    while current != root_path:
        if not current.is_relative_to(root_path):
            raise ProjectArchiveError("Destination blob path escapes archive root")
        if not current.exists() and not current.is_symlink():
            missing.add(current)
        current = current.parent
    return missing


def _remove_published_study(study_dir: Path) -> None:
    if not study_dir.exists() and not study_dir.is_symlink():
        return
    if study_dir.is_symlink() or study_dir.is_file():
        study_dir.unlink()
        return
    shutil.rmtree(study_dir)


def _copy_sqlite_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"{source.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source_connection:
        with sqlite3.connect(destination) as destination_connection:
            source_connection.backup(destination_connection)


def _restore_file_snapshot(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
        return
    atomic_write_bytes(path, content)


def _restore_imports(
    catalog: EvidenceCatalog,
    imports: list[EvidenceImportRecord],
) -> None:
    pending = list(imports)
    while pending:
        deferred: list[EvidenceImportRecord] = []
        progress = False
        for record in pending:
            try:
                catalog.record_import(record)
                progress = True
            except ValueError as exc:
                if "Parent revision does not belong" not in str(exc):
                    raise
                deferred.append(record)
        if not progress:
            raise ProjectArchiveError("Archive revision lineage cannot be restored")
        pending = deferred


def _validate_staged_qualitative_project(
    stage_root: Path,
    study_id: str,
) -> None:
    database_path = stage_root / "studies" / study_id / "qualitative.sqlite3"
    if not database_path.exists() and not database_path.is_symlink():
        return
    try:
        CaseService(stage_root, study_id).validate_project_state()
        CodingReferenceService(stage_root, study_id).validate_project_state()
        NoteService(stage_root, study_id).validate_project_state()
    except SchemaCompatibilityError as exc:
        raise ProjectArchiveConflict(str(exc)) from exc
    except (
        CaseConflictError,
        CaseNotFoundError,
        CaseValidationError,
        CodingReferenceConflictError,
        CodingReferenceNotFoundError,
        CodingReferenceValidationError,
        NoteConflictError,
        NoteNotFoundError,
        NoteValidationError,
        QualitativeDatabaseConflict,
        StudyBatchOperationConflict,
        sqlite3.Error,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        schema_error = _schema_compatibility_cause(exc)
        if schema_error is not None:
            raise ProjectArchiveConflict(str(schema_error)) from exc
        raise ProjectArchiveError("Archive qualitative project is invalid") from exc


def _schema_compatibility_cause(
    exc: BaseException,
) -> SchemaCompatibilityError | None:
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        if isinstance(current, SchemaCompatibilityError):
            return current
        visited.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def _enforce_archive_budget(members: dict[str, bytes]) -> None:
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise ProjectArchiveError("Archive contains too many members")
    if any(len(content) > MAX_ARCHIVE_FILE_BYTES for content in members.values()):
        raise ProjectArchiveError("Archive member exceeds file size limit")
    if sum(len(content) for content in members.values()) > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
        raise ProjectArchiveError("Archive exceeds uncompressed size limit")


def _validate_study_id(study_id: str) -> None:
    if (
        not _STUDY_ID_PATTERN.fullmatch(study_id)
        or len(study_id) > MAX_STUDY_ID_LENGTH
        or _WINDOWS_DEVICE_NAME.fullmatch(study_id)
    ):
        raise ProjectArchiveError("Invalid study id")
