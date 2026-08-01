from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import unicodedata
import zlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from uuid import uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from backend.storage.atomic import atomic_binary_writer, atomic_write_bytes
from backend.storage.audit_log import AuditLogStore
from backend.storage.evidence_catalog import EvidenceCatalog, EvidenceImportRecord
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
from backend.storage.workspace_lock import workspace_mutation_lock


ARCHIVE_FORMAT_VERSION = 1
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
                    legacy_unaudited_versions = (
                        validation_store.validate_completed_batch_snapshots(
                            study_id
                        )
                    )
                    validation_store.validate_skill_pack_versions(
                        study_id,
                        legacy_unaudited_versions=legacy_unaudited_versions,
                    )
                    return self._create_archive_snapshot(study_id, study_dir)
        except (
            FileNotFoundError,
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
    ) -> ProjectArchiveExport:
        if self.catalog.db_path.is_symlink() or self.audit.events_path.is_symlink():
            raise ProjectArchiveError(
                "Study archive dependencies cannot contain symbolic links"
            )
        created_at = datetime.now(UTC).isoformat()
        members: dict[str, bytes] = {}
        for path in sorted(study_dir.rglob("*")):
            if path.is_symlink():
                raise ProjectArchiveError("Study archive cannot contain symbolic links")
            if path.is_file():
                relative = path.relative_to(study_dir).as_posix()
                members[f"study/{relative}"] = path.read_bytes()

        imports = self.catalog.workspace_import_records(study_id)
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
        for digest in sorted({record.source_blob_sha256 for record in imports}):
            members[f"blobs/{digest}.blob"] = self.blobs.read_verified(digest)
        _validate_member_names(["manifest.json", *members])
        _enforce_archive_budget(members)

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
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        archive_path = (
            self.backups_dir
            / f"{study_id}-{timestamp}-{uuid4().hex[:8]}.nlpstudy.zip"
        )
        with atomic_binary_writer(archive_path) as archive_file:
            with ZipFile(archive_file, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
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

        imports = _parse_evidence_imports(
            members["evidence/imports.json"],
            study_id,
        )
        audit_events = _parse_audit_events(
            members["evidence/audit.json"],
            study_id,
        )
        expected_blob_names = {
            f"blobs/{record.source_blob_sha256}.blob" for record in imports
        }
        actual_blob_names = {name for name in members if name.startswith("blobs/")}
        if actual_blob_names != expected_blob_names:
            raise ProjectArchiveError("Archive blob set does not match evidence imports")

        self.studies_dir.mkdir(parents=True, exist_ok=True)
        stage_root = Path(
            tempfile.mkdtemp(prefix=f".{study_id}.restore.", dir=self.studies_dir)
        )
        stage_dir = stage_root / "studies" / study_id
        stage_dir.mkdir(parents=True)
        try:
            for name, content in members.items():
                if name.startswith("study/"):
                    relative = PurePosixPath(name).relative_to("study")
                    atomic_write_bytes(
                        _safe_extraction_target(stage_dir, relative),
                        content,
                    )
            try:
                study_payload = json.loads(
                    (stage_dir / "study.json").read_text("utf-8")
                )
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ProjectArchiveError("Archive study record is malformed") from exc
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

            validation_blobs = SourceBlobStore(stage_root)
            try:
                for blob_name in sorted(actual_blob_names):
                    digest = PurePosixPath(blob_name).stem
                    validation_blobs.store(members[blob_name], digest)
                _restore_imports(EvidenceCatalog(stage_root), imports)
                AuditLogStore(stage_root).import_events(audit_events)
                validation_store = StudyWorkspaceStore(stage_root)
                legacy_unaudited_versions = (
                    validation_store.validate_completed_batch_snapshots(
                        study_id
                    )
                )
                validation_store.validate_skill_pack_versions(
                    study_id,
                    legacy_unaudited_versions=legacy_unaudited_versions,
                )
            except (
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

            with workspace_mutation_lock(self.root):
                if study_dir.exists() or study_dir.is_symlink():
                    raise FileExistsError(study_id)
                imported_audit_count = self._preflight_destination_state(
                    stage_root,
                    imports,
                    audit_events,
                    actual_blob_names,
                    members,
                )
                self._commit_restore(
                    stage_root,
                    stage_dir,
                    study_dir,
                    actual_blob_names,
                    members,
                )
        finally:
            shutil.rmtree(stage_root, ignore_errors=True)
        return ProjectRestoreResult(
            study_id=study_id,
            study_dir=study_dir,
            import_count=len(imports),
            blob_count=len(actual_blob_names),
            audit_event_count=imported_audit_count,
        )

    def _preflight_destination_state(
        self,
        stage_root: Path,
        imports: list[EvidenceImportRecord],
        audit_events: list[dict[str, object]],
        blob_names: set[str],
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
            if self.audit.events_path.is_symlink():
                raise ValueError("Destination audit log is a symbolic link")
            if self.audit.events_path.exists():
                atomic_write_bytes(
                    preflight_root / "audit" / "events.jsonl",
                    self.audit.events_path.read_bytes(),
                )
            for blob_name in sorted(blob_names):
                digest = PurePosixPath(blob_name).stem
                destination = self.blobs.blob_path(digest)
                if destination.is_symlink():
                    raise ValueError("Destination source blob is a symbolic link")
                if destination.exists():
                    stored_content = self.blobs.read_verified(digest)
                    if stored_content != members[blob_name]:
                        raise ValueError("Destination source blob conflicts")
            _restore_imports(EvidenceCatalog(preflight_root), imports)
            return AuditLogStore(preflight_root).import_events(audit_events)
        except (
            OSError,
            SourceBlobIntegrityError,
            sqlite3.Error,
            TypeError,
            ValueError,
        ) as exc:
            raise ProjectArchiveError(
                "Archive evidence conflicts with destination"
            ) from exc

    def _commit_restore(
        self,
        stage_root: Path,
        stage_dir: Path,
        study_dir: Path,
        blob_names: set[str],
        members: dict[str, bytes],
    ) -> None:
        preflight_root = stage_root / "destination-preflight"
        preflight_catalog = preflight_root / "evidence.sqlite3"
        preflight_audit = preflight_root / "audit" / "events.jsonl"
        catalog_existed = self.catalog.db_path.exists()
        audit_existed = self.audit.events_path.exists()
        catalog_before = self.catalog.db_path.read_bytes() if catalog_existed else None
        audit_before = self.audit.events_path.read_bytes() if audit_existed else None
        created_blobs: list[Path] = []
        try:
            for blob_name in sorted(blob_names):
                digest = PurePosixPath(blob_name).stem
                blob_path = self.blobs.blob_path(digest)
                existed = blob_path.exists()
                self.blobs.store(members[blob_name], digest)
                if not existed:
                    created_blobs.append(blob_path)
            if preflight_catalog.exists():
                atomic_write_bytes(
                    self.catalog.db_path,
                    preflight_catalog.read_bytes(),
                )
            if preflight_audit.exists():
                atomic_write_bytes(
                    self.audit.events_path,
                    preflight_audit.read_bytes(),
                )
            self._publish_study(stage_dir, study_dir)
        except BaseException as exc:
            try:
                _restore_file_snapshot(self.catalog.db_path, catalog_before)
                _restore_file_snapshot(self.audit.events_path, audit_before)
                for blob_path in created_blobs:
                    blob_path.unlink(missing_ok=True)
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
    try:
        manifest = json.loads(members["manifest.json"].decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProjectArchiveError("Archive manifest is malformed") from exc
    if not isinstance(manifest, dict):
        raise ProjectArchiveError("Archive manifest is malformed")
    if set(manifest) != {"format_version", "study_id", "created_at", "members"}:
        raise ProjectArchiveError("Archive manifest is malformed")
    if (
        isinstance(manifest["format_version"], bool)
        or not isinstance(manifest["format_version"], int)
        or manifest["format_version"] != ARCHIVE_FORMAT_VERSION
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
            or size_bytes > MAX_ARCHIVE_UNCOMPRESSED_BYTES
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
    for name, record in records.items():
        content = members[name]
        if record["size_bytes"] != len(content):
            raise ProjectArchiveError(f"Archive member size mismatch: {name}")
        if record["sha256"] != sha256(content).hexdigest():
            raise ProjectArchiveError(f"Archive member hash mismatch: {name}")
    return members, manifest


def _validate_member(info: ZipInfo) -> None:
    _validate_member_name(info.filename)
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


def _parse_evidence_imports(
    content: bytes,
    study_id: str,
) -> list[EvidenceImportRecord]:
    try:
        payloads = json.loads(content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProjectArchiveError("Archive evidence records are malformed") from exc
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
    try:
        events = json.loads(content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProjectArchiveError("Archive audit records are malformed") from exc
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


def _enforce_archive_budget(members: dict[str, bytes]) -> None:
    if len(members) + 1 > MAX_ARCHIVE_MEMBERS:
        raise ProjectArchiveError("Archive contains too many members")
    if sum(len(content) for content in members.values()) > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
        raise ProjectArchiveError("Archive exceeds uncompressed size limit")


def _validate_study_id(study_id: str) -> None:
    if (
        not _STUDY_ID_PATTERN.fullmatch(study_id)
        or len(study_id) > MAX_STUDY_ID_LENGTH
        or _WINDOWS_DEVICE_NAME.fullmatch(study_id)
    ):
        raise ProjectArchiveError("Invalid study id")
