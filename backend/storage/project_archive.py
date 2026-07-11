from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo

from backend.storage.atomic import atomic_binary_writer, atomic_write_bytes
from backend.storage.audit_log import AuditLogStore
from backend.storage.evidence_catalog import EvidenceCatalog, EvidenceImportRecord
from backend.storage.source_blob_store import SourceBlobStore


ARCHIVE_FORMAT_VERSION = 1
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_FILE_BYTES = 512 * 1024 * 1024
_STUDY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


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
        if not (study_dir / "study.json").is_file():
            raise FileNotFoundError(study_id)
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
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        archive_path = self.backups_dir / f"{study_id}-{timestamp}.nlpstudy.zip"
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
                members = _verified_archive_members(archive)
            manifest = json.loads(members.pop("manifest.json").decode("utf-8"))
        except (BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProjectArchiveError("Archive is malformed") from exc
        study_id = str(manifest.get("study_id") or "")
        _validate_study_id(study_id)
        study_dir = self.studies_dir / study_id
        if study_dir.exists():
            raise FileExistsError(study_id)

        try:
            import_payloads = json.loads(
                members["evidence/imports.json"].decode("utf-8")
            )
            imports = [EvidenceImportRecord(**payload) for payload in import_payloads]
            audit_events = json.loads(members["evidence/audit.json"].decode("utf-8"))
            if not isinstance(audit_events, list):
                raise TypeError("audit events must be a list")
        except (KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProjectArchiveError("Archive evidence records are malformed") from exc
        if any(record.workspace_id != study_id for record in imports):
            raise ProjectArchiveError("Evidence import belongs to another workspace")
        if any(
            event.get("subject_type") != "study"
            or event.get("subject_id") != study_id
            for event in audit_events
        ):
            raise ProjectArchiveError("Audit event belongs to another study")
        expected_blob_names = {
            f"blobs/{record.source_blob_sha256}.blob" for record in imports
        }
        actual_blob_names = {name for name in members if name.startswith("blobs/")}
        if actual_blob_names != expected_blob_names:
            raise ProjectArchiveError("Archive blob set does not match evidence imports")

        self.studies_dir.mkdir(parents=True, exist_ok=True)
        stage_dir = Path(
            tempfile.mkdtemp(prefix=f".{study_id}.restore.", dir=self.studies_dir)
        )
        try:
            for name, content in members.items():
                if name.startswith("study/"):
                    relative = PurePosixPath(name).relative_to("study")
                    atomic_write_bytes(stage_dir.joinpath(*relative.parts), content)
            study_payload = json.loads((stage_dir / "study.json").read_text("utf-8"))
            if str(study_payload.get("id") or "") != study_id:
                raise ProjectArchiveError("Study identity does not match archive manifest")
            for blob_name in sorted(actual_blob_names):
                digest = PurePosixPath(blob_name).stem
                self.blobs.store(members[blob_name], digest)
            try:
                _restore_imports(self.catalog, imports)
                imported_audit_count = self.audit.import_events(audit_events)
            except ValueError as exc:
                raise ProjectArchiveError("Archive evidence conflicts with destination") from exc
            os.replace(stage_dir, study_dir)
        except BaseException:
            shutil.rmtree(stage_dir, ignore_errors=True)
            raise
        return ProjectRestoreResult(
            study_id=study_id,
            study_dir=study_dir,
            import_count=len(imports),
            blob_count=len(actual_blob_names),
            audit_event_count=imported_audit_count,
        )


def _verified_archive_members(archive: ZipFile) -> dict[str, bytes]:
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise ProjectArchiveError("Archive contains too many members")
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise ProjectArchiveError("Archive contains duplicate members")
    for info in infos:
        _validate_member(info)
    if sum(info.file_size for info in infos) > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
        raise ProjectArchiveError("Archive exceeds uncompressed size limit")
    if "manifest.json" not in names:
        raise ProjectArchiveError("Archive manifest is missing")
    members = {info.filename: archive.read(info) for info in infos}
    manifest = json.loads(members["manifest.json"].decode("utf-8"))
    if int(manifest.get("format_version", 0)) != ARCHIVE_FORMAT_VERSION:
        raise ProjectArchiveError("Unsupported archive format version")
    declared = manifest.get("members")
    if not isinstance(declared, list):
        raise ProjectArchiveError("Archive manifest members are invalid")
    records = {str(record.get("path") or ""): record for record in declared}
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
        if int(record.get("size_bytes", -1)) != len(content):
            raise ProjectArchiveError(f"Archive member size mismatch: {name}")
        if str(record.get("sha256") or "") != sha256(content).hexdigest():
            raise ProjectArchiveError(f"Archive member hash mismatch: {name}")
    return members


def _validate_member(info: ZipInfo) -> None:
    path = PurePosixPath(info.filename)
    if info.is_dir() or path.is_absolute() or ".." in path.parts or "\\" in info.filename:
        raise ProjectArchiveError("Archive contains an unsafe member path")
    unix_mode = (info.external_attr >> 16) & 0o170000
    if unix_mode == 0o120000:
        raise ProjectArchiveError("Archive contains a symbolic link")


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
    if not _STUDY_ID_PATTERN.fullmatch(study_id):
        raise ProjectArchiveError("Invalid study id")
