from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
import sqlite3
import stat
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.analysis.pipeline import (
    execute_analysis,
    prepare_analysis_evidence_target_set,
)
from backend.analysis.skill_packs import parse_skill_pack
from backend.analysis.transcripts import StudyConfig
from backend.evidence.identifiers import (
    source_import_identity,
    transcript_evidence_identity,
)
from backend.storage.audit_log import AuditLogStore
from backend.storage.atomic import atomic_write_bytes, atomic_write_text
from backend.storage.evidence_catalog import EvidenceCatalog, EvidenceImportRecord
from backend.storage.evidence_target_registry import (
    EvidenceSetSnapshot,
    EvidenceTargetBlobConflict,
    EvidenceTargetConflictError,
    EvidenceTargetNotFoundError,
    EvidenceTargetRegistry,
    EvidenceTargetValidationError,
)
from backend.storage.evidence_text_blob_store import evidence_text_sha256
from backend.storage.source_blob_store import SourceBlobIntegrityError, SourceBlobStore
from backend.storage.study_batch_operation_store import (
    StudyBatchOperationConflict,
    StudyBatchOperationStore,
    validate_study_batch_id,
    validate_study_skill_pack_version_id,
)
from backend.storage.workspace_lock import workspace_mutation_lock


MAX_STUDY_PARTICIPANTS = 10_000
MAX_STUDY_ID_LENGTH = 96
_STUDY_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LEGACY_BASE_RUN_FIELDS = {
    "run_id",
    "source_filename",
    "created_at",
    "turn_count",
    "results",
}
_EARLY_EVIDENCE_FIELDS = {
    "source_id",
    "source_sha256",
    "transcript_revision_id",
}
_IMPORT_V1_EVIDENCE_FIELDS = {
    "import_id",
    "source_blob_sha256",
    "source_media_type",
    "source_id",
    "transcript_sha256",
    "transcript_revision_id",
}
_CURRENT_EVIDENCE_FIELDS = {
    "import_id",
    "project_source_id",
    "parent_transcript_revision_id",
    "workspace_id",
    "source_blob_sha256",
    "source_media_type",
    "source_id",
    "transcript_sha256",
    "transcript_revision_id",
}
_TARGET_V1_EVIDENCE_FIELDS = _CURRENT_EVIDENCE_FIELDS | {"evidence_set_id"}
_ALL_LEGACY_EVIDENCE_FIELDS = (
    _EARLY_EVIDENCE_FIELDS
    | _IMPORT_V1_EVIDENCE_FIELDS
    | _TARGET_V1_EVIDENCE_FIELDS
)
_EVIDENCE_SET_ID = re.compile(r"^evs_[0-9a-f]{32}$")
_WINDOWS_DEVICE_NAME = re.compile(
    r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])$",
    re.IGNORECASE,
)


class StudyBatchSnapshotConflict(RuntimeError):
    pass


class StudySkillPackVersionConflict(RuntimeError):
    pass


class StudyWorkspaceConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class StudyWorkspace:
    id: str
    name: str
    description: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True)
class StudySkillPackVersion:
    study_id: str
    version_id: str
    payload: dict[str, Any]
    artifact_path: Path
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True)
class StudySchema:
    study_id: str
    participant_count: int
    participants: list[str]
    conditions: list[str]
    week_count: int
    weeks: list[str]
    custom_fields: list[str] = field(default_factory=list)
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True)
class StudyBatchRun:
    study_id: str
    batch_id: str
    skill_pack_version_id: str
    run_count: int
    failure_count: int
    aggregate_dir: Path
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True)
class StudyBundleExport:
    study_id: str
    bundle_id: str
    bundle_dir: Path
    manifest_path: Path
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True)
class StudyBatchArchiveCompatibility:
    legacy_unaudited_versions: frozenset[str]
    legacy_import_ids: frozenset[str]


class StudyWorkspaceStore:
    def __init__(self, root: Path | str = "local_data") -> None:
        self.root = Path(root)
        self.studies_dir = self.root / "studies"
        self.audit_log = AuditLogStore(self.root)

    def create_study(self, payload: dict[str, Any]) -> StudyWorkspace:
        with workspace_mutation_lock(self.root):
            return self._create_study(payload)

    def _create_study(self, payload: dict[str, Any]) -> StudyWorkspace:
        name = _required_string(payload, "name")
        study = StudyWorkspace(
            id=_slugify(str(payload.get("id") or name)),
            name=name,
            description=str(payload.get("description") or ""),
        )
        study_dir = self._study_dir(study.id)
        existing = self._matching_existing_study(study_dir, study)
        if existing is not None:
            self._ensure_study_created_audit(existing)
            return existing

        self.studies_dir.mkdir(parents=True, exist_ok=True)
        if study_dir.exists():
            try:
                study_dir.rmdir()
            except OSError as exc:
                existing = self._matching_existing_study(study_dir, study)
                if existing is not None:
                    self._ensure_study_created_audit(existing)
                    return existing
                raise FileExistsError(study.id) from exc
        stage_root = self.studies_dir / ".staging"
        stage_root.mkdir(exist_ok=True)
        stage_dir = stage_root / f"{study.id}.{uuid4().hex}"
        stage_dir.mkdir()
        try:
            atomic_write_text(
                stage_dir / "study.json",
                json.dumps(asdict(study), indent=2),
            )
            try:
                stage_dir.rename(study_dir)
            except OSError as exc:
                existing = self._matching_existing_study(study_dir, study)
                if existing is None:
                    raise FileExistsError(study.id) from exc
                study = existing
        finally:
            if stage_dir.exists():
                shutil.rmtree(stage_dir)
        self._ensure_study_created_audit(study)
        return study

    def _matching_existing_study(
        self,
        study_dir: Path,
        requested: StudyWorkspace,
    ) -> StudyWorkspace | None:
        if study_dir.is_symlink():
            raise FileExistsError(requested.id)
        if not study_dir.exists():
            return None
        if not study_dir.is_dir():
            raise FileExistsError(requested.id)
        study_path = study_dir / "study.json"
        if not study_path.is_file():
            if any(study_dir.iterdir()):
                raise FileExistsError(requested.id)
            return None
        try:
            existing = StudyWorkspace(
                **json.loads(study_path.read_text(encoding="utf-8"))
            )
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
            raise FileExistsError(requested.id) from exc
        if (
            existing.id,
            existing.name,
            existing.description,
        ) != (
            requested.id,
            requested.name,
            requested.description,
        ):
            raise FileExistsError(requested.id)
        return existing

    def _ensure_study_created_audit(self, study: StudyWorkspace) -> None:
        for event in self.audit_log.events_for_subject("study", study.id):
            if event.get("event_type") != "study.created":
                continue
            metadata = event.get("metadata")
            if not isinstance(metadata, dict) or metadata.get("name") != study.name:
                raise FileExistsError(study.id)
            return
        self.audit_log.import_events(
            [
                {
                    "id": hashlib.sha256(
                        f"study.created\0{study.id}".encode("utf-8")
                    ).hexdigest(),
                    "event_type": "study.created",
                    "subject_type": "study",
                    "subject_id": study.id,
                    "actor": "local-system",
                    "metadata": {"name": study.name},
                    "created_at": study.created_at,
                }
            ]
        )

    def list_studies(self) -> list[StudyWorkspace]:
        if not self.studies_dir.exists():
            return []
        studies = [
            StudyWorkspace(**json.loads(path.read_text(encoding="utf-8")))
            for path in self.studies_dir.glob("*/study.json")
        ]
        return sorted(studies, key=lambda study: study.created_at, reverse=True)

    def load_study(self, study_id: str) -> StudyWorkspace:
        if (
            not isinstance(study_id, str)
            or not _STUDY_ID.fullmatch(study_id)
            or len(study_id) > MAX_STUDY_ID_LENGTH
            or _WINDOWS_DEVICE_NAME.fullmatch(study_id)
        ):
            raise ValueError("study_id must be a normalized study identifier")
        study_dir = self._study_dir(study_id)
        study_path = study_dir / "study.json"
        if not study_path.exists() and not study_path.is_symlink():
            raise FileNotFoundError(study_id)
        try:
            if study_dir.is_symlink() or not study_dir.is_dir():
                raise OSError("study directory is not a regular directory")
            _validate_non_symlink_regular_path(study_path)
            payload = json.loads(study_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) != {
                "id",
                "name",
                "description",
                "created_at",
            }:
                raise ValueError("study record has invalid fields")
            if (
                type(payload["id"]) is not str
                or payload["id"] != study_id
                or type(payload["name"]) is not str
                or not payload["name"].strip()
                or type(payload["description"]) is not str
            ):
                raise ValueError("study record identity is invalid")
            _validate_timezone_aware_timestamp(
                payload["created_at"],
                "study created_at",
            )
            return StudyWorkspace(**payload)
        except FileNotFoundError:
            raise
        except (
            json.JSONDecodeError,
            KeyError,
            OSError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
        ) as exc:
            raise StudyWorkspaceConflict(
                "Study workspace record is unavailable or invalid"
            ) from exc

    def save_study_schema(self, study_id: str, payload: dict[str, Any]) -> StudySchema:
        self._require_study(study_id)
        schema = _study_schema_from_payload(study_id, payload)
        with StudyBatchOperationStore(
            self.root,
            study_id,
        ).study_mutation_guard():
            schema_path = self._study_dir(study_id) / "study_schema.json"
            if schema_path.exists():
                existing_schema = StudySchema(
                    **json.loads(schema_path.read_text(encoding="utf-8"))
                )
                if _study_schema_semantic_payload(
                    existing_schema
                ) == _study_schema_semantic_payload(schema):
                    self._ensure_study_schema_audit(existing_schema)
                    return existing_schema
            atomic_write_text(schema_path, json.dumps(asdict(schema), indent=2))
            self._ensure_study_schema_audit(schema)
        return schema

    def _ensure_study_schema_audit(self, schema: StudySchema) -> None:
        metadata = {
            "participant_count": schema.participant_count,
            "conditions": schema.conditions,
            "week_count": schema.week_count,
            "custom_fields": schema.custom_fields,
        }
        latest_schema_event = next(
            (
                event
                for event in reversed(
                    self.audit_log.events_for_subject("study", schema.study_id)
                )
                if event.get("event_type") == "study.schema.updated"
            ),
            None,
        )
        if (
            latest_schema_event is not None
            and latest_schema_event.get("metadata") == metadata
        ):
            return
        self.audit_log.import_events(
            [
                {
                    "id": hashlib.sha256(
                        (
                            "study.schema.updated\0"
                            f"{schema.study_id}\0{schema.updated_at}"
                        ).encode("utf-8")
                    ).hexdigest(),
                    "event_type": "study.schema.updated",
                    "subject_type": "study",
                    "subject_id": schema.study_id,
                    "actor": "local-system",
                    "metadata": metadata,
                    "created_at": schema.updated_at,
                }
            ]
        )

    def load_study_schema(self, study_id: str) -> StudySchema:
        self._require_study(study_id)
        path = self._study_dir(study_id) / "study_schema.json"
        if not path.exists():
            raise FileNotFoundError("study_schema.json")
        return StudySchema(**json.loads(path.read_text(encoding="utf-8")))

    def add_skill_pack_version(
        self,
        study_id: str,
        payload: dict[str, Any],
        *,
        validate: bool = True,
    ) -> StudySkillPackVersion:
        self._require_study(study_id)
        if validate:
            parse_skill_pack(payload)
        version_id = _skill_pack_version_id(payload)
        try:
            validate_study_skill_pack_version_id(version_id)
        except ValueError as exc:
            raise ValueError(
                "skill-pack id and version must contain normalized identifier text"
            ) from exc
        with StudyBatchOperationStore(
            self.root,
            study_id,
        ).study_mutation_guard():
            version_dir = self._study_dir(study_id) / "skill_packs"
            version_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = version_dir / f"{version_id}.json"
            metadata_path = version_dir / f"{version_id}.metadata.json"
            artifact_exists = artifact_path.exists() or artifact_path.is_symlink()
            metadata_exists = metadata_path.exists() or metadata_path.is_symlink()
            if (
                artifact_exists
                and (
                    artifact_path.is_symlink()
                    or not artifact_path.is_file()
                )
            ) or (
                metadata_exists
                and (
                    metadata_path.is_symlink()
                    or not metadata_path.is_file()
                )
            ):
                raise StudySkillPackVersionConflict(
                    "Study skill-pack version artifacts are invalid"
                )
            if metadata_exists and not artifact_exists:
                raise StudySkillPackVersionConflict(
                    "Study skill-pack version artifacts are incomplete"
                )

            existing_payload: dict[str, Any] | None = None
            if artifact_exists:
                try:
                    loaded_payload = json.loads(
                        _read_non_symlink_regular_file(artifact_path).decode("utf-8")
                    )
                except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
                    raise StudySkillPackVersionConflict(
                        "Study skill-pack version artifacts are invalid"
                    ) from exc
                if not isinstance(loaded_payload, dict):
                    raise StudySkillPackVersionConflict(
                        "Study skill-pack version artifacts are invalid"
                    )
                if (
                    _canonical_json_sha256(loaded_payload)
                    != _canonical_json_sha256(payload)
                ):
                    raise StudySkillPackVersionConflict(
                        "Study skill-pack version already exists with different content"
                    )
                existing_payload = loaded_payload

            existing_created_at = ""
            if metadata_exists:
                try:
                    existing_metadata = json.loads(
                        _read_non_symlink_regular_file(metadata_path).decode("utf-8")
                    )
                    if not isinstance(existing_metadata, dict):
                        raise TypeError("metadata must be an object")
                    existing_created_at = str(existing_metadata["created_at"])
                except (
                    json.JSONDecodeError,
                    KeyError,
                    OSError,
                    TypeError,
                    UnicodeDecodeError,
                ) as exc:
                    raise StudySkillPackVersionConflict(
                        "Study skill-pack version artifacts are invalid"
                    ) from exc
                if (
                    str(existing_metadata.get("study_id") or "") != study_id
                    or str(existing_metadata.get("version_id") or "") != version_id
                    or not existing_created_at.strip()
                ):
                    raise StudySkillPackVersionConflict(
                        "Study skill-pack version metadata conflicts with its identity"
                    )

            if existing_payload is None:
                atomic_write_text(artifact_path, json.dumps(payload, indent=2))
                existing_payload = payload
            metadata = StudySkillPackVersion(
                study_id=study_id,
                version_id=version_id,
                payload=existing_payload,
                artifact_path=artifact_path,
                **(
                    {"created_at": existing_created_at}
                    if existing_created_at
                    else {}
                ),
            )
            if not metadata_exists:
                atomic_write_text(
                    metadata_path,
                    json.dumps(
                        {
                            "study_id": metadata.study_id,
                            "version_id": metadata.version_id,
                            "artifact_path": str(metadata.artifact_path),
                            "created_at": metadata.created_at,
                        },
                        indent=2,
                    ),
                )
            self._ensure_skill_pack_version_audit(
                study_id,
                version_id,
                payload,
            )
        return metadata

    def _ensure_skill_pack_version_audit(
        self,
        study_id: str,
        version_id: str,
        payload: dict[str, Any],
    ) -> None:
        expected_metadata = {
            "version_id": version_id,
            "skill_pack_id": str(payload["id"]),
            "skill_pack_version": str(payload["version"]),
        }
        if self._has_skill_pack_version_audit(
            study_id,
            version_id,
            expected_metadata,
        ):
            return
        self.audit_log.record(
            "skill_pack.versioned",
            "study",
            study_id,
            expected_metadata,
        )

    def _has_skill_pack_version_audit(
        self,
        study_id: str,
        version_id: str,
        expected_metadata: dict[str, str],
    ) -> bool:
        try:
            events = self.audit_log.list_events(limit=None)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise StudySkillPackVersionConflict(
                "Study skill-pack version audit log is invalid"
            ) from exc
        if any(not isinstance(event, dict) for event in events):
            raise StudySkillPackVersionConflict(
                "Study skill-pack version audit log is invalid"
            )
        found = False
        for event in events:
            event_metadata = event.get("metadata")
            if (
                event.get("subject_type") != "study"
                or event.get("subject_id") != study_id
                or event.get("event_type") != "skill_pack.versioned"
                or not isinstance(event_metadata, dict)
                or event_metadata.get("version_id") != version_id
            ):
                continue
            if any(
                event_metadata.get(key) != value
                for key, value in expected_metadata.items()
            ):
                raise StudySkillPackVersionConflict(
                    "Study skill-pack version audit conflicts with its identity"
                )
            found = True
        return found

    def run_text_batch(
        self,
        study_id: str,
        skill_pack_version_id: str,
        transcripts: list[dict[str, Any]],
        *,
        batch_id: str | None = None,
    ) -> StudyBatchRun:
        self._require_study(study_id)
        validate_study_skill_pack_version_id(skill_pack_version_id)
        resolved_batch_id = batch_id or (
            f"batch_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}_"
            f"{uuid4().hex[:8]}"
        )
        validate_study_batch_id(resolved_batch_id)
        aggregate_dir = self._study_dir(study_id) / "batches" / resolved_batch_id
        journal = StudyBatchOperationStore(self.root, study_id)
        existing_operation = None
        if journal.db_path.exists():
            try:
                existing_operation = journal.get_operation(resolved_batch_id)
            except FileNotFoundError:
                pass
        try:
            skill_pack_payload = self._load_skill_pack_version(
                study_id,
                skill_pack_version_id,
            )
        except StudySkillPackVersionConflict as exc:
            if (
                existing_operation is not None
                and existing_operation["status"] == "completed"
            ):
                raise StudyBatchSnapshotConflict(
                    f"Completed study batch cannot be replayed: {exc}"
                ) from exc
            raise
        study_schema = self._load_optional_study_schema(study_id)
        skill_pack_sha256 = _canonical_json_sha256(skill_pack_payload)
        item_request_sha256s = [
            _study_batch_item_request_sha256(item) for item in transcripts
        ]
        request_sha256 = _canonical_json_sha256(
            {
                "skill_pack_sha256": skill_pack_sha256,
                "study_schema": asdict(study_schema) if study_schema else None,
                "items": item_request_sha256s,
            }
        )
        if existing_operation is None:
            try:
                existing_operation = journal.get_operation(resolved_batch_id)
            except FileNotFoundError:
                pass
        if existing_operation is None and aggregate_dir.exists():
            raise StudyBatchSnapshotConflict(
                "Study batch artifacts exist without a journal operation"
            )
        created_at = (
            str(existing_operation["created_at"])
            if existing_operation is not None
            else datetime.now(UTC).isoformat()
        )
        journal.begin(
            batch_id=resolved_batch_id,
            skill_pack_version_id=skill_pack_version_id,
            skill_pack_sha256=skill_pack_sha256,
            request_sha256=request_sha256,
            item_count=len(transcripts),
            created_at=created_at,
        )
        operation = journal.get_operation(resolved_batch_id)
        if operation["status"] == "completed":
            return self._load_completed_batch(
                study_id,
                resolved_batch_id,
                journal,
                operation,
            )

        runs_dir = aggregate_dir / "runs"
        try:
            runs_dir.mkdir(parents=True, exist_ok=True)
            successes: list[dict[str, Any]] = []
            failures: list[dict[str, str]] = []
            evidence_catalog = EvidenceCatalog(self.root)
            evidence_target_registry = EvidenceTargetRegistry(self.root)
            source_blob_store = SourceBlobStore(self.root)
            for item_index, item in enumerate(transcripts):
                source_filename = _required_string(item, "source_filename")
                metadata = _normalized_metadata(item.get("metadata", {}))
                content = str(item.get("content") or "").strip()
                source_bytes = item.get("source_bytes")
                source_media_type = str(
                    item.get("source_media_type") or "text/plain"
                )
                requested_project_source_id = str(
                    item.get("project_source_id") or ""
                )
                parent_transcript_revision_id = str(
                    item.get("parent_transcript_revision_id") or ""
                )
                transcript_identity = transcript_evidence_identity(content)
                source_identity = source_import_identity(
                    content,
                    source_bytes=source_bytes,
                    source_media_type=source_media_type,
                    project_source_id=requested_project_source_id,
                )
                existing_item = journal.get_item(resolved_batch_id, item_index)
                reserved = journal.reserve_item(
                    resolved_batch_id,
                    item_index=item_index,
                    item_request_sha256=item_request_sha256s[item_index],
                    run_id=(
                        str(existing_item["run_id"])
                        if existing_item is not None
                        else uuid4().hex
                    ),
                    import_id=(
                        str(existing_item["import_id"])
                        if existing_item is not None
                        else source_identity.import_id
                    ),
                    project_source_id=(
                        str(existing_item["project_source_id"])
                        if existing_item is not None
                        else source_identity.project_source_id
                    ),
                    source_blob_sha256=source_identity.source_blob_sha256,
                    transcript_sha256=transcript_identity.transcript_sha256,
                    transcript_revision_id=(
                        transcript_identity.transcript_revision_id
                    ),
                    created_at=(
                        str(existing_item["created_at"])
                        if existing_item is not None
                        else datetime.now(UTC).isoformat()
                    ),
                )
                try:
                    run = execute_analysis(
                        _required_string(item, "content"),
                        _study_config_for_batch_item(
                            skill_pack_payload,
                            metadata,
                        ),
                        source_filename=source_filename,
                        source_bytes=source_bytes,
                        source_media_type=source_media_type,
                        project_source_id=requested_project_source_id,
                        parent_transcript_revision_id=(
                            parent_transcript_revision_id
                        ),
                        workspace_id=study_id,
                    )
                except (ValueError, KeyError) as exc:
                    journal.reject_item(
                        resolved_batch_id,
                        item_index=item_index,
                        item_request_sha256=item_request_sha256s[item_index],
                        error_type=type(exc).__name__,
                    )
                    failures.append(
                        {
                            "source_filename": source_filename,
                            "error": str(exc),
                        }
                    )
                    continue

                run = replace(
                    run,
                    run_id=str(reserved["run_id"]),
                    import_id=str(reserved["import_id"]),
                    project_source_id=str(reserved["project_source_id"]),
                    source_blob_sha256=str(reserved["source_blob_sha256"]),
                    source_id=transcript_identity.source_id,
                    transcript_sha256=str(reserved["transcript_sha256"]),
                    transcript_revision_id=str(
                        reserved["transcript_revision_id"]
                    ),
                    created_at=str(reserved["created_at"]),
                    evidence_set_id="",
                )
                prepared_evidence_set = prepare_analysis_evidence_target_set(
                    run,
                    registry=evidence_target_registry,
                )
                run = replace(
                    run,
                    evidence_set_id=prepared_evidence_set.evidence_set_id,
                )
                run_payload = _study_batch_run_payload(run, metadata)
                journal.record_analysis_completed(
                    resolved_batch_id,
                    item_index,
                    run_payload_sha256=_canonical_json_sha256(run_payload),
                )
                evidence_record = EvidenceImportRecord(
                    import_id=run.import_id,
                    run_id=run.run_id,
                    pipeline="study_batch",
                    project_source_id=run.project_source_id,
                    parent_transcript_revision_id=(
                        run.parent_transcript_revision_id
                    ),
                    workspace_id=run.workspace_id,
                    source_id=run.source_id,
                    source_filename=run.source_filename,
                    source_media_type=run.source_media_type,
                    source_blob_sha256=run.source_blob_sha256,
                    transcript_revision_id=run.transcript_revision_id,
                    transcript_sha256=run.transcript_sha256,
                    imported_at=run.created_at,
                )
                evidence_catalog.validate_lineage(
                    project_source_id=run.project_source_id,
                    parent_transcript_revision_id=(
                        run.parent_transcript_revision_id
                    ),
                    workspace_id=run.workspace_id,
                    transcript_revision_id=run.transcript_revision_id,
                )
                source_blob_store.store(
                    source_bytes
                    if source_bytes is not None
                    else run.source_content.encode("utf-8"),
                    run.source_blob_sha256,
                )
                journal.advance_item(
                    resolved_batch_id,
                    item_index,
                    "source_blob_stored",
                )
                evidence_catalog.record_import(evidence_record)
                stored_evidence_set = (
                    evidence_target_registry.register_complete_set(
                        prepared_evidence_set
                    )
                )
                if stored_evidence_set.evidence_set_id != run.evidence_set_id:
                    raise RuntimeError(
                        "Registered study evidence target set does not match the run"
                    )
                journal.advance_item(
                    resolved_batch_id,
                    item_index,
                    "evidence_cataloged",
                )
                _write_exact_json(
                    runs_dir / f"{run.run_id}.json",
                    run_payload,
                )
                journal.advance_item(
                    resolved_batch_id,
                    item_index,
                    "snapshot_written",
                )
                journal.advance_item(
                    resolved_batch_id,
                    item_index,
                    "completed",
                )
                successes.append(run_payload)

            journal.advance(resolved_batch_id, "items_processed")
            aggregate_payload = _aggregate_batch_payload(
                study_id,
                resolved_batch_id,
                skill_pack_version_id,
                study_schema,
                successes,
                failures,
                created_at=created_at,
            )
            _write_exact_json(
                aggregate_dir / "aggregate_results.json",
                aggregate_payload,
            )
            journal.record_aggregate_written(
                resolved_batch_id,
                aggregate_payload_sha256=_canonical_json_sha256(
                    aggregate_payload
                ),
            )
            for result in aggregate_payload["results"]:
                _write_exact_csv(
                    aggregate_dir / f"{result['metric_id']}.csv",
                    result["rows"],
                )
            journal.advance(resolved_batch_id, "csv_exports_written")

            batch = StudyBatchRun(
                study_id=study_id,
                batch_id=resolved_batch_id,
                skill_pack_version_id=skill_pack_version_id,
                run_count=len(successes),
                failure_count=len(failures),
                aggregate_dir=aggregate_dir,
                created_at=created_at,
            )
            _write_exact_json(
                aggregate_dir / "batch.json",
                {
                    **asdict(batch),
                    "aggregate_dir": batch.aggregate_dir.relative_to(
                        self.root
                    ).as_posix(),
                },
            )
            journal.advance(resolved_batch_id, "batch_manifest_written")
            operation = journal.get_operation(resolved_batch_id)
            self.audit_log.import_events(
                [
                    {
                        "id": str(operation["audit_event_id"]),
                        "event_type": "batch.completed",
                        "subject_type": "study",
                        "subject_id": study_id,
                        "actor": "local-system",
                        "metadata": {
                            "batch_id": batch.batch_id,
                            "skill_pack_version_id": skill_pack_version_id,
                            "run_count": batch.run_count,
                            "failure_count": batch.failure_count,
                        },
                        "created_at": created_at,
                    }
                ]
            )
            journal.advance(resolved_batch_id, "audit_recorded")
            journal.complete(resolved_batch_id)
            return batch
        except BaseException as exc:
            try:
                journal.fail(
                    resolved_batch_id,
                    error_type=type(exc).__name__,
                )
            except BaseException as journal_exc:
                raise RuntimeError(
                    "Study batch failed and its journal could not record the failure"
                ) from journal_exc
            raise

    def _load_completed_batch(
        self,
        study_id: str,
        batch_id: str,
        journal: StudyBatchOperationStore,
        operation: dict[str, Any],
    ) -> StudyBatchRun:
        try:
            skill_pack_payload = self._load_skill_pack_version(
                study_id,
                str(operation["skill_pack_version_id"]),
            )
            batch = self._load_batch_manifest(
                study_id,
                batch_id,
                operation=operation,
            )
            aggregate_payload = json.loads(
                _read_non_symlink_regular_file(
                    batch.aggregate_dir / "aggregate_results.json"
                ).decode("utf-8")
            )
            if not isinstance(aggregate_payload, dict):
                raise TypeError("aggregate snapshot must be an object")
            items = journal.list_items(batch_id)
        except FileNotFoundError as exc:
            raise StudyBatchSnapshotConflict(
                "Completed study batch is missing a persisted artifact"
            ) from exc
        except (
            json.JSONDecodeError,
            KeyError,
            OSError,
            sqlite3.Error,
            TypeError,
            UnicodeDecodeError,
            ValueError,
        ) as exc:
            raise StudyBatchSnapshotConflict(
                "Completed study batch contains an invalid persisted artifact"
            ) from exc
        except StudySkillPackVersionConflict as exc:
            raise StudyBatchSnapshotConflict(
                "Completed study batch skill-pack state is invalid"
            ) from exc

        if _canonical_json_sha256(skill_pack_payload) != str(
            operation["skill_pack_sha256"]
        ):
            raise StudyBatchSnapshotConflict(
                "Completed study batch skill pack conflicts with its journal"
            )

        completed_items = [item for item in items if item["stage"] == "completed"]
        rejected_items = [item for item in items if item["stage"] == "rejected"]
        import_records: dict[str, EvidenceImportRecord] = {}
        evidence_sets: dict[str, EvidenceSetSnapshot] | None = None
        if completed_items:
            try:
                evidence_catalog = EvidenceCatalog(self.root)
                _validate_non_symlink_regular_path(evidence_catalog.db_path)
                import_records = {
                    record.import_id: record
                    for record in evidence_catalog.workspace_import_records(study_id)
                }
            except (FileNotFoundError, OSError, sqlite3.Error) as exc:
                raise StudyBatchSnapshotConflict(
                    "Completed study batch evidence catalog is invalid"
                ) from exc
        if len(items) != int(operation["item_count"]) or len(items) != (
            len(completed_items) + len(rejected_items)
        ):
            raise StudyBatchSnapshotConflict(
                "Completed study batch journal contains non-terminal items"
            )
        if (
            batch.study_id != study_id
            or batch.batch_id != batch_id
            or batch.skill_pack_version_id != operation["skill_pack_version_id"]
            or batch.created_at != operation["created_at"]
            or batch.run_count != len(completed_items)
            or batch.failure_count != len(rejected_items)
        ):
            raise StudyBatchSnapshotConflict(
                "Completed study batch manifest conflicts with its journal"
            )
        if _canonical_json_sha256(aggregate_payload) != str(
            operation["aggregate_payload_sha256"]
        ):
            raise StudyBatchSnapshotConflict(
                "Completed study batch aggregate conflicts with its journal"
            )

        successes: list[dict[str, Any]] = []
        expected_run_paths: set[Path] = set()
        source_blob_store = SourceBlobStore(self.root)
        for item in completed_items:
            run_path = batch.aggregate_dir / "runs" / f"{item['run_id']}.json"
            expected_run_paths.add(run_path)
            try:
                run_payload = json.loads(
                    _read_non_symlink_regular_file(run_path).decode("utf-8")
                )
                if not isinstance(run_payload, dict):
                    raise TypeError("run snapshot must be an object")
            except FileNotFoundError as exc:
                raise StudyBatchSnapshotConflict(
                    "Completed study batch is missing a run snapshot"
                ) from exc
            except (
                json.JSONDecodeError,
                OSError,
                TypeError,
                UnicodeDecodeError,
            ) as exc:
                raise StudyBatchSnapshotConflict(
                    "Completed study batch contains an invalid run snapshot"
                ) from exc
            if _canonical_json_sha256(run_payload) != item["run_payload_sha256"]:
                raise StudyBatchSnapshotConflict(
                    "Completed study batch run snapshot conflicts with its journal"
                )
            for field_name in (
                "run_id",
                "import_id",
                "project_source_id",
                "source_blob_sha256",
                "transcript_sha256",
                "transcript_revision_id",
                "created_at",
            ):
                if str(run_payload.get(field_name) or "") != str(
                    item[field_name]
                ):
                    raise StudyBatchSnapshotConflict(
                        "Completed study batch run identity conflicts with its journal"
                    )
            try:
                source_blob_store.read_verified(str(item["source_blob_sha256"]))
            except FileNotFoundError as exc:
                raise StudyBatchSnapshotConflict(
                    "Completed study batch is missing a source blob"
                ) from exc
            except (OSError, SourceBlobIntegrityError) as exc:
                raise StudyBatchSnapshotConflict(
                    "Completed study batch source blob conflicts with its journal"
                ) from exc
            import_record = import_records.get(str(item["import_id"]))
            try:
                evidence_generation = _legacy_evidence_generation(run_payload)
                if evidence_generation not in {"current", "target-v1"}:
                    raise ValueError(
                        "Current run evidence identity is incomplete"
                    )
                expected_import = {
                    "import_id": run_payload["import_id"],
                    "run_id": run_payload["run_id"],
                    "pipeline": "study_batch",
                    "project_source_id": run_payload["project_source_id"],
                    "parent_transcript_revision_id": run_payload[
                        "parent_transcript_revision_id"
                    ],
                    "workspace_id": run_payload["workspace_id"],
                    "source_id": run_payload["source_id"],
                    "source_filename": run_payload["source_filename"],
                    "source_media_type": run_payload["source_media_type"],
                    "source_blob_sha256": run_payload["source_blob_sha256"],
                    "transcript_revision_id": run_payload[
                        "transcript_revision_id"
                    ],
                    "transcript_sha256": run_payload["transcript_sha256"],
                    "imported_at": run_payload["created_at"],
                }
            except (KeyError, TypeError, ValueError) as exc:
                raise StudyBatchSnapshotConflict(
                    "Completed study batch run evidence is invalid"
                ) from exc
            if import_record is None or asdict(import_record) != expected_import:
                raise StudyBatchSnapshotConflict(
                    "Completed study batch evidence conflicts with its journal"
                )
            if evidence_generation == "target-v1":
                try:
                    if evidence_sets is None:
                        evidence_sets = {
                            snapshot.evidence_set_id: snapshot
                            for snapshot in EvidenceTargetRegistry(
                                self.root
                            ).workspace_snapshot(study_id)
                        }
                    _validate_analysis_evidence_set_payload(
                        run_payload,
                        evidence_sets,
                    )
                except (
                    EvidenceTargetBlobConflict,
                    EvidenceTargetConflictError,
                    EvidenceTargetNotFoundError,
                    EvidenceTargetValidationError,
                    KeyError,
                    TypeError,
                    ValueError,
                ) as exc:
                    raise StudyBatchSnapshotConflict(
                        "Completed study batch evidence target is invalid"
                    ) from exc
            successes.append(run_payload)

        actual_run_paths = set((batch.aggregate_dir / "runs").glob("*.json"))
        if actual_run_paths != expected_run_paths:
            raise StudyBatchSnapshotConflict(
                "Completed study batch run snapshots conflict with its journal"
            )
        failures = aggregate_payload.get("failures")
        if not isinstance(failures, list) or len(failures) != len(rejected_items):
            raise StudyBatchSnapshotConflict(
                "Completed study batch failures conflict with its journal"
            )
        schema_payload = aggregate_payload.get("study_schema")
        try:
            archived_schema = (
                StudySchema(**schema_payload)
                if isinstance(schema_payload, dict)
                else None
            )
            expected_aggregate = _aggregate_batch_payload(
                study_id,
                batch_id,
                str(operation["skill_pack_version_id"]),
                archived_schema,
                successes,
                failures,
                created_at=str(operation["created_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StudyBatchSnapshotConflict(
                "Completed study batch aggregate is invalid"
            ) from exc
        if _canonical_json_sha256(aggregate_payload) != _canonical_json_sha256(
            expected_aggregate
        ):
            raise StudyBatchSnapshotConflict(
                "Completed study batch aggregate conflicts with its run snapshots"
            )
        expected_csv_paths = {
            batch.aggregate_dir / f"{result['metric_id']}.csv"
            for result in expected_aggregate["results"]
        }
        if set(batch.aggregate_dir.glob("*.csv")) != expected_csv_paths:
            raise StudyBatchSnapshotConflict(
                "Completed study batch CSV exports conflict with its aggregate"
            )
        for result in expected_aggregate["results"]:
            csv_path = batch.aggregate_dir / f"{result['metric_id']}.csv"
            try:
                csv_bytes = _read_non_symlink_regular_file(csv_path)
            except FileNotFoundError as exc:
                raise StudyBatchSnapshotConflict(
                    "Completed study batch is missing a CSV export"
                ) from exc
            except OSError as exc:
                raise StudyBatchSnapshotConflict(
                    "Completed study batch contains an invalid CSV export"
                ) from exc
            if csv_bytes != _csv_text(result["rows"]).encode("utf-8"):
                raise StudyBatchSnapshotConflict(
                    "Completed study batch CSV export conflicts with its aggregate"
                )

        expected_audit_event = {
            "id": str(operation["audit_event_id"]),
            "event_type": "batch.completed",
            "subject_type": "study",
            "subject_id": study_id,
            "actor": "local-system",
            "metadata": {
                "batch_id": batch_id,
                "skill_pack_version_id": str(
                    operation["skill_pack_version_id"]
                ),
                "run_count": batch.run_count,
                "failure_count": batch.failure_count,
            },
            "created_at": str(operation["created_at"]),
        }
        try:
            audit_events = self.audit_log.list_events(limit=None)
        except (
            json.JSONDecodeError,
            OSError,
            UnicodeDecodeError,
            ValueError,
        ) as exc:
            raise StudyBatchSnapshotConflict(
                "Completed study batch audit log is invalid"
            ) from exc
        if any(not isinstance(event, dict) for event in audit_events):
            raise StudyBatchSnapshotConflict(
                "Completed study batch audit log is invalid"
            )
        matching_audit_events = [
            event
            for event in audit_events
            if event.get("id") == operation["audit_event_id"]
        ]
        if matching_audit_events != [expected_audit_event]:
            raise StudyBatchSnapshotConflict(
                "Completed study batch audit event conflicts with its journal"
            )
        return batch

    def validate_completed_batch_snapshots(
        self,
        study_id: str,
    ) -> StudyBatchArchiveCompatibility:
        self._require_study(study_id)
        journal = StudyBatchOperationStore(self.root, study_id)
        legacy_unaudited_versions: set[str] = set()
        legacy_import_ids: set[str] = set()
        batches_dir = self._study_dir(study_id) / "batches"
        manifest_batch_ids = {
            path.parent.name for path in batches_dir.glob("*/batch.json")
        }
        completed_batch_ids = journal.completed_batch_ids()
        if completed_batch_ids - manifest_batch_ids:
            raise StudyBatchSnapshotConflict(
                "Completed study batch is missing its manifest"
            )
        for batch_id in sorted(manifest_batch_ids):
            try:
                operation = journal.get_operation(batch_id)
            except FileNotFoundError:
                operation = None
            if operation is None:
                self._load_legacy_completed_batch(
                    study_id,
                    batch_id,
                    legacy_unaudited_versions=legacy_unaudited_versions,
                    legacy_import_ids=legacy_import_ids,
                )
            elif operation["status"] == "completed":
                self._load_completed_batch(
                    study_id,
                    batch_id,
                    journal,
                    operation,
                )
        return StudyBatchArchiveCompatibility(
            legacy_unaudited_versions=frozenset(legacy_unaudited_versions),
            legacy_import_ids=frozenset(legacy_import_ids),
        )

    def _load_legacy_completed_batch(
        self,
        study_id: str,
        batch_id: str,
        *,
        legacy_unaudited_versions: set[str] | None = None,
        legacy_import_ids: set[str] | None = None,
    ) -> StudyBatchRun:
        try:
            batch = self._load_batch_manifest(study_id, batch_id)
            aggregate_payload = json.loads(
                _read_non_symlink_regular_file(
                    batch.aggregate_dir / "aggregate_results.json"
                ).decode("utf-8")
            )
            if not isinstance(aggregate_payload, dict):
                raise TypeError("aggregate snapshot must be an object")
            aggregate_created_at = aggregate_payload.get("created_at")
            _validate_timezone_aware_timestamp(
                aggregate_created_at,
                "legacy aggregate created_at",
            )
            _validate_timezone_aware_timestamp(
                batch.created_at,
                "legacy batch created_at",
            )
            runs_dir = batch.aggregate_dir / "runs"
            if runs_dir.is_symlink() or not runs_dir.is_dir():
                raise OSError("Expected a non-symlink run directory")
            run_payloads: dict[str, dict[str, Any]] = {}
            for run_path in runs_dir.glob("*.json"):
                _validate_study_run_id(run_path.stem)
                run_payload = json.loads(
                    _read_non_symlink_regular_file(run_path).decode("utf-8")
                )
                if (
                    not isinstance(run_payload, dict)
                    or run_payload.get("run_id") != run_path.stem
                ):
                    raise ValueError("run identity mismatch")
                _batch_run_summary(run_payload)
                _validate_timezone_aware_timestamp(
                    run_payload.get("created_at"),
                    "legacy run created_at",
                )
                run_payloads[run_path.stem] = run_payload
            pre_audit_shape = (
                "study_schema" not in aggregate_payload
                and all(
                    set(run_payload) == _LEGACY_BASE_RUN_FIELDS
                    for run_payload in run_payloads.values()
                )
            )
            self._load_skill_pack_version(
                study_id,
                batch.skill_pack_version_id,
                allow_missing_audit=pre_audit_shape,
                missing_audit_versions=legacy_unaudited_versions,
            )
        except FileNotFoundError as exc:
            raise StudyBatchSnapshotConflict(
                "Legacy completed study batch is missing a persisted artifact"
            ) from exc
        except (
            json.JSONDecodeError,
            KeyError,
            OSError,
            sqlite3.Error,
            TypeError,
            UnicodeDecodeError,
            ValueError,
            StudySkillPackVersionConflict,
        ) as exc:
            raise StudyBatchSnapshotConflict(
                "Legacy completed study batch contains an invalid persisted artifact"
            ) from exc

        if len(run_payloads) != batch.run_count:
            raise StudyBatchSnapshotConflict(
                "Legacy completed study batch run snapshots conflict with its manifest"
            )
        import_records: dict[str, EvidenceImportRecord] = {}
        try:
            evidence_generations = {
                _legacy_evidence_generation(payload)
                for payload in run_payloads.values()
            }
        except ValueError as exc:
            raise StudyBatchSnapshotConflict(
                "Legacy completed study batch run evidence is invalid"
            ) from exc
        catalog_required = bool(
            {"current", "target-v1"}.intersection(evidence_generations)
        )
        catalog_optional = "import-v1" in evidence_generations
        evidence_catalog = EvidenceCatalog(self.root)
        if catalog_required or (
            catalog_optional
            and (
                evidence_catalog.db_path.exists()
                or evidence_catalog.db_path.is_symlink()
            )
        ):
            try:
                _validate_non_symlink_regular_path(evidence_catalog.db_path)
                workspace_ids = {study_id}
                if catalog_optional:
                    workspace_ids.update({"legacy", "local-default"})
                for workspace_id in workspace_ids:
                    import_records.update(
                        {
                            record.import_id: record
                            for record in evidence_catalog.workspace_import_records(
                                workspace_id
                            )
                        }
                    )
            except (FileNotFoundError, OSError, sqlite3.Error) as exc:
                raise StudyBatchSnapshotConflict(
                    "Legacy completed study batch evidence catalog is invalid"
                ) from exc
        failures = aggregate_payload.get("failures")
        results = aggregate_payload.get("results")
        if (
            not isinstance(failures, list)
            or len(failures) != batch.failure_count
            or not isinstance(results, list)
        ):
            raise StudyBatchSnapshotConflict(
                "Legacy completed study batch aggregate conflicts with its manifest"
            )
        try:
            for result in results:
                if (
                    not isinstance(result, dict)
                    or set(result) != {"metric_id", "label", "rows"}
                    or not isinstance(result["metric_id"], str)
                    or not result["metric_id"]
                    or not isinstance(result["label"], str)
                    or not result["label"]
                    or not isinstance(result["rows"], list)
                ):
                    raise TypeError("legacy aggregate metric is invalid")
            run_order = _legacy_aggregate_run_order(run_payloads, results)
            aggregate_metric_labels = {
                result["metric_id"]: result["label"]
                for result in results
            }
            if len(aggregate_metric_labels) != len(results):
                raise ValueError("legacy aggregate metric ids are duplicated")
            for run_payload in run_payloads.values():
                run_results = run_payload.get("results")
                if not isinstance(run_results, list):
                    raise TypeError("legacy run results must be a list")
                run_metric_labels: dict[str, str] = {}
                for result in run_results:
                    if (
                        not isinstance(result, dict)
                        or set(result) != {"metric_id", "label", "rows"}
                        or not isinstance(result["metric_id"], str)
                        or not isinstance(result["label"], str)
                        or not isinstance(result["rows"], list)
                        or result["metric_id"] in run_metric_labels
                    ):
                        raise TypeError("legacy run metric is invalid")
                    run_metric_labels[result["metric_id"]] = result["label"]
                if run_metric_labels != aggregate_metric_labels:
                    raise ValueError("legacy run metrics conflict with aggregate")
                self._validate_legacy_run_evidence(
                    study_id,
                    run_payload,
                    import_records,
                )
                if (
                    legacy_import_ids is not None
                    and _legacy_evidence_generation(run_payload)
                    in {"import-v1", "current"}
                ):
                    legacy_import_ids.add(str(run_payload["import_id"]))
        except (
            KeyError,
            OSError,
            SourceBlobIntegrityError,
            TypeError,
            ValueError,
        ) as exc:
            raise StudyBatchSnapshotConflict(
                "Legacy completed study batch run evidence is invalid"
            ) from exc
        schema_payload = aggregate_payload.get("study_schema")
        try:
            archived_schema = (
                StudySchema(**schema_payload)
                if isinstance(schema_payload, dict)
                else None
            )
            expected_aggregate = _aggregate_batch_payload(
                study_id,
                batch_id,
                batch.skill_pack_version_id,
                archived_schema,
                [run_payloads[run_id] for run_id in run_order],
                failures,
                created_at=str(aggregate_created_at),
            )
            if "study_schema" not in aggregate_payload:
                expected_aggregate.pop("study_schema")
        except (KeyError, TypeError, ValueError) as exc:
            raise StudyBatchSnapshotConflict(
                "Legacy completed study batch aggregate is invalid"
            ) from exc
        if _canonical_json_sha256(aggregate_payload) != _canonical_json_sha256(
            expected_aggregate
        ):
            raise StudyBatchSnapshotConflict(
                "Legacy completed study batch aggregate conflicts with its run snapshots"
            )
        expected_csv_paths = {
            batch.aggregate_dir / f"{result['metric_id']}.csv"
            for result in expected_aggregate["results"]
        }
        if set(batch.aggregate_dir.glob("*.csv")) != expected_csv_paths:
            raise StudyBatchSnapshotConflict(
                "Legacy completed study batch CSV exports conflict with its aggregate"
            )
        for result in expected_aggregate["results"]:
            csv_path = batch.aggregate_dir / f"{result['metric_id']}.csv"
            try:
                csv_bytes = _read_non_symlink_regular_file(csv_path)
            except (FileNotFoundError, OSError) as exc:
                raise StudyBatchSnapshotConflict(
                    "Legacy completed study batch contains an invalid CSV export"
                ) from exc
            if csv_bytes != _csv_text(result["rows"]).encode("utf-8"):
                raise StudyBatchSnapshotConflict(
                    "Legacy completed study batch CSV export conflicts with its aggregate"
                )

        expected_audit_fields = {
            "event_type": "batch.completed",
            "subject_type": "study",
            "subject_id": study_id,
            "actor": "local-system",
            "metadata": {
                "batch_id": batch_id,
                "skill_pack_version_id": batch.skill_pack_version_id,
                "run_count": batch.run_count,
                "failure_count": batch.failure_count,
            },
        }
        try:
            audit_events = self.audit_log.list_events(limit=None)
            if any(not isinstance(event, dict) for event in audit_events):
                raise ValueError("audit event must be an object")
            matching_events = [
                event
                for event in audit_events
                if all(
                    event.get(key) == value
                    for key, value in expected_audit_fields.items()
                )
            ]
        except (
            json.JSONDecodeError,
            OSError,
            UnicodeDecodeError,
            ValueError,
        ) as exc:
            raise StudyBatchSnapshotConflict(
                "Legacy completed study batch audit log is invalid"
            ) from exc
        if not matching_events and pre_audit_shape:
            return batch
        if len(matching_events) != 1:
            raise StudyBatchSnapshotConflict(
                "Legacy completed study batch audit event is missing or ambiguous"
            )
        try:
            _validate_timezone_aware_timestamp(
                matching_events[0].get("created_at"),
                "legacy audit created_at",
            )
        except ValueError as exc:
            raise StudyBatchSnapshotConflict(
                "Legacy completed study batch audit event is invalid"
            ) from exc
        return batch

    def _validate_legacy_run_evidence(
        self,
        study_id: str,
        run_payload: dict[str, Any],
        import_records: dict[str, EvidenceImportRecord],
    ) -> None:
        generation = _legacy_evidence_generation(run_payload)
        if generation == "none":
            return
        if generation == "early":
            transcript_sha256 = run_payload["source_sha256"]
            _validate_legacy_transcript_identity(run_payload, transcript_sha256)
            return

        identity_fields = (
            _CURRENT_EVIDENCE_FIELDS
            if generation in {"current", "target-v1"}
            else _IMPORT_V1_EVIDENCE_FIELDS
        )
        optional_empty_fields = (
            {"parent_transcript_revision_id"}
            if generation in {"current", "target-v1"}
            else set()
        )
        for field_name in identity_fields - optional_empty_fields:
            if not isinstance(run_payload[field_name], str) or not run_payload[
                field_name
            ]:
                raise ValueError("legacy run evidence identity is invalid")
        if generation in {"current", "target-v1"} and not isinstance(
            run_payload["parent_transcript_revision_id"], str
        ):
            raise ValueError("legacy run evidence identity is invalid")
        if not _SHA256.fullmatch(run_payload["source_blob_sha256"]):
            raise ValueError("legacy source blob identity is invalid")
        _validate_legacy_transcript_identity(
            run_payload,
            run_payload["transcript_sha256"],
        )
        if (
            generation in {"current", "target-v1"}
            and run_payload["workspace_id"] != study_id
        ):
            raise ValueError("legacy run evidence belongs to another study")
        expected_import = {
            "import_id": run_payload["import_id"],
            "run_id": run_payload["run_id"],
            "pipeline": "study_batch",
            "source_id": run_payload["source_id"],
            "source_filename": run_payload["source_filename"],
            "source_media_type": run_payload["source_media_type"],
            "source_blob_sha256": run_payload["source_blob_sha256"],
            "transcript_revision_id": run_payload["transcript_revision_id"],
            "transcript_sha256": run_payload["transcript_sha256"],
            "imported_at": run_payload["created_at"],
        }
        if generation in {"current", "target-v1"}:
            expected_import.update(
                {
                    "project_source_id": run_payload["project_source_id"],
                    "parent_transcript_revision_id": run_payload[
                        "parent_transcript_revision_id"
                    ],
                    "workspace_id": run_payload["workspace_id"],
                }
            )
        import_record = import_records.get(run_payload["import_id"])
        if generation in {"current", "target-v1"} and import_record is None:
            raise ValueError("legacy run evidence is missing from catalog")
        if import_record is not None and any(
            getattr(import_record, field_name) != value
            for field_name, value in expected_import.items()
        ):
            raise ValueError("legacy run evidence conflicts with catalog")
        if generation in {"current", "target-v1"}:
            try:
                SourceBlobStore(self.root).read_verified(
                    run_payload["source_blob_sha256"]
                )
            except FileNotFoundError:
                pass
        if generation == "target-v1":
            try:
                snapshots = {
                    snapshot.evidence_set_id: snapshot
                    for snapshot in EvidenceTargetRegistry(
                        self.root
                    ).workspace_snapshot(study_id)
                }
                _validate_analysis_evidence_set_payload(run_payload, snapshots)
            except (
                EvidenceTargetBlobConflict,
                EvidenceTargetConflictError,
                EvidenceTargetNotFoundError,
                EvidenceTargetValidationError,
            ) as exc:
                raise ValueError("legacy evidence target is invalid") from exc

    def validate_skill_pack_versions(
        self,
        study_id: str,
        *,
        legacy_unaudited_versions: set[str] | frozenset[str] | None = None,
    ) -> None:
        self._require_study(study_id)
        version_dir = self._study_dir(study_id) / "skill_packs"
        if not version_dir.exists():
            return
        artifact_ids = {
            path.stem
            for path in version_dir.glob("*.json")
            if not path.name.endswith(".metadata.json")
        }
        metadata_ids = {
            path.name.removesuffix(".metadata.json")
            for path in version_dir.glob("*.metadata.json")
        }
        if artifact_ids != metadata_ids:
            raise StudySkillPackVersionConflict(
                "Study skill-pack version artifacts are incomplete"
            )
        for version_id in sorted(artifact_ids):
            payload = self._load_skill_pack_version(
                study_id,
                version_id,
                allow_missing_audit=(
                    legacy_unaudited_versions is not None
                    and version_id in legacy_unaudited_versions
                ),
            )
            try:
                parse_skill_pack(payload)
            except ValueError as exc:
                raise StudySkillPackVersionConflict(
                    "Study skill-pack version is semantically invalid"
                ) from exc

    def list_batches(self, study_id: str) -> list[StudyBatchRun]:
        self._require_study(study_id)
        batches_dir = self._study_dir(study_id) / "batches"
        journal = StudyBatchOperationStore(self.root, study_id)
        completed_batch_ids = journal.completed_batch_ids()
        manifest_paths = {
            path.parent.name: path
            for path in batches_dir.glob("*/batch.json")
        }
        if completed_batch_ids - set(manifest_paths):
            raise StudyBatchSnapshotConflict(
                "Completed study batch is missing its manifest"
            )
        batches = []
        for batch_id in sorted(manifest_paths):
            try:
                operation = journal.get_operation(batch_id)
            except FileNotFoundError:
                operation = None
            if operation is not None and operation["status"] != "completed":
                continue
            if operation is None:
                batch = self._load_legacy_completed_batch(study_id, batch_id)
            else:
                batch = self._load_batch_manifest(
                    study_id,
                    batch_id,
                    operation=operation,
                )
                items = journal.list_items(batch_id)
                completed_count = sum(
                    item["stage"] == "completed" for item in items
                )
                rejected_count = sum(
                    item["stage"] == "rejected" for item in items
                )
                if (
                    len(items) != operation["item_count"]
                    or len(items) != completed_count + rejected_count
                    or batch.run_count != completed_count
                    or batch.failure_count != rejected_count
                ):
                    raise StudyBatchSnapshotConflict(
                        "Completed study batch manifest conflicts with its journal"
                    )
            batches.append(batch)
        return sorted(batches, key=lambda batch: batch.created_at, reverse=True)

    def load_batch(self, study_id: str, batch_id: str) -> StudyBatchRun:
        self._require_study(study_id)
        validate_study_batch_id(batch_id)
        journal = StudyBatchOperationStore(self.root, study_id)
        try:
            operation = journal.get_operation(batch_id)
        except FileNotFoundError:
            operation = None
        if operation is None:
            return self._load_legacy_completed_batch(study_id, batch_id)
        if operation["status"] != "completed":
            raise StudyBatchOperationConflict(
                "Study batch has not reached the completed boundary"
            )
        return self._load_completed_batch(
            study_id,
            batch_id,
            journal,
            operation,
        )

    def _load_batch_manifest(
        self,
        study_id: str,
        batch_id: str,
        *,
        operation: dict[str, Any] | None = None,
    ) -> StudyBatchRun:
        batch_path = self._study_dir(study_id) / "batches" / batch_id / "batch.json"
        if not batch_path.exists():
            if operation is None:
                raise FileNotFoundError(batch_id)
            raise StudyBatchSnapshotConflict("Completed study batch is missing its manifest")
        try:
            validate_study_batch_id(batch_id)
            batch = _batch_run_from_payload(
                json.loads(
                    _read_non_symlink_regular_file(batch_path).decode("utf-8")
                ),
                aggregate_dir=batch_path.parent,
            )
            validate_study_skill_pack_version_id(batch.skill_pack_version_id)
        except FileNotFoundError as exc:
            if operation is None:
                raise FileNotFoundError(batch_id) from exc
            raise StudyBatchSnapshotConflict(
                "Completed study batch is missing its manifest"
            ) from exc
        except (
            json.JSONDecodeError,
            KeyError,
            OSError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
        ) as exc:
            raise StudyBatchSnapshotConflict(
                "Completed study batch contains an invalid manifest"
            ) from exc
        if (
            batch.study_id != study_id
            or batch.batch_id != batch_id
            or batch.run_count < 0
            or batch.failure_count < 0
            or not batch.created_at.strip()
        ):
            raise StudyBatchSnapshotConflict(
                "Completed study batch manifest conflicts with its identity"
            )
        if operation is not None and (
            batch.skill_pack_version_id != operation["skill_pack_version_id"]
            or batch.created_at != operation["created_at"]
        ):
            raise StudyBatchSnapshotConflict(
                "Completed study batch manifest conflicts with its journal"
            )
        return batch

    def list_batch_runs(self, study_id: str, batch_id: str) -> list[dict[str, Any]]:
        batch = self.load_batch(study_id, batch_id)
        runs_dir = batch.aggregate_dir / "runs"
        paths = list(runs_dir.glob("*.json")) if runs_dir.exists() else []
        if len(paths) != batch.run_count:
            raise StudyBatchSnapshotConflict(
                "Completed study batch run snapshots conflict with its manifest"
            )
        runs = []
        try:
            for path in paths:
                _validate_study_run_id(path.stem)
                payload = json.loads(
                    _read_non_symlink_regular_file(path).decode("utf-8")
                )
                if not isinstance(payload, dict) or payload.get("run_id") != path.stem:
                    raise ValueError("run identity mismatch")
                runs.append(_batch_run_summary(payload))
        except (
            json.JSONDecodeError,
            KeyError,
            OSError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
        ) as exc:
            raise StudyBatchSnapshotConflict(
                "Completed study batch contains an invalid run snapshot"
            ) from exc
        return sorted(runs, key=lambda run: str(run["source_filename"]))

    def load_batch_run(self, study_id: str, batch_id: str, run_id: str) -> dict[str, Any]:
        batch = self.load_batch(study_id, batch_id)
        _validate_study_run_id(run_id)
        run_path = batch.aggregate_dir / "runs" / f"{run_id}.json"
        try:
            payload = json.loads(
                _read_non_symlink_regular_file(run_path).decode("utf-8")
            )
        except FileNotFoundError as exc:
            raise FileNotFoundError(run_id) from exc
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            raise StudyBatchSnapshotConflict(
                "Completed study batch contains an invalid run snapshot"
            ) from exc
        if not isinstance(payload, dict) or payload.get("run_id") != run_id:
            raise StudyBatchSnapshotConflict(
                "Completed study batch run snapshot conflicts with its identity"
            )
        return payload

    def export_study_bundle(self, study_id: str) -> StudyBundleExport:
        self._require_study(study_id)
        bundle_id = f"{study_id}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        bundle_dir = self.root / "bundles" / bundle_id
        bundle_dir.mkdir(parents=True, exist_ok=True)
        study_dir = self._study_dir(study_id)
        study_payload = json.loads((study_dir / "study.json").read_text(encoding="utf-8"))
        manifest = {
            "bundle_id": bundle_id,
            "study": study_payload,
            "created_at": datetime.now(UTC).isoformat(),
            "files": [
                _file_record(path, self.root)
                for path in sorted(study_dir.rglob("*"))
                if path.is_file()
            ],
        }
        manifest_path = bundle_dir / "manifest.json"
        atomic_write_text(manifest_path, json.dumps(manifest, indent=2))
        bundle = StudyBundleExport(
            study_id=study_id,
            bundle_id=bundle_id,
            bundle_dir=bundle_dir,
            manifest_path=manifest_path,
        )
        self.audit_log.record(
            "bundle.exported",
            "study",
            study_id,
            {
                "bundle_id": bundle.bundle_id,
                "manifest_path": str(bundle.manifest_path),
            },
        )
        return bundle

    def _study_dir(self, study_id: str) -> Path:
        return self.studies_dir / study_id

    def _require_study(self, study_id: str) -> None:
        if not (self._study_dir(study_id) / "study.json").exists():
            raise FileNotFoundError(study_id)

    def _load_skill_pack_version(
        self,
        study_id: str,
        skill_pack_version_id: str,
        *,
        allow_missing_audit: bool = False,
        missing_audit_versions: set[str] | None = None,
    ) -> dict[str, Any]:
        try:
            validate_study_skill_pack_version_id(skill_pack_version_id)
        except ValueError as exc:
            raise StudySkillPackVersionConflict(
                "Study skill-pack version identity is invalid"
            ) from exc
        version_dir = self._study_dir(study_id) / "skill_packs"
        path = version_dir / f"{skill_pack_version_id}.json"
        metadata_path = version_dir / f"{skill_pack_version_id}.metadata.json"
        try:
            payload = json.loads(
                _read_non_symlink_regular_file(path).decode("utf-8")
            )
            metadata = json.loads(
                _read_non_symlink_regular_file(metadata_path).decode("utf-8")
            )
        except FileNotFoundError as exc:
            raise StudySkillPackVersionConflict(
                "Study skill-pack version has not been fully published"
            ) from exc
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            raise StudySkillPackVersionConflict(
                "Study skill-pack version artifacts are invalid"
            ) from exc
        if not isinstance(payload, dict) or not isinstance(metadata, dict):
            raise StudySkillPackVersionConflict(
                "Study skill-pack version artifacts are invalid"
            )
        try:
            identity_matches = (
                metadata.get("study_id") == study_id
                and metadata.get("version_id") == skill_pack_version_id
                and bool(str(metadata.get("created_at") or "").strip())
                and _skill_pack_version_id(payload) == skill_pack_version_id
            )
            expected_audit_metadata = {
                "version_id": skill_pack_version_id,
                "skill_pack_id": str(payload["id"]),
                "skill_pack_version": str(payload["version"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise StudySkillPackVersionConflict(
                "Study skill-pack version artifacts are invalid"
            ) from exc
        if not identity_matches:
            raise StudySkillPackVersionConflict(
                "Study skill-pack version metadata conflicts with its identity"
            )
        has_audit = self._has_skill_pack_version_audit(
            study_id,
            skill_pack_version_id,
            expected_audit_metadata,
        )
        if not has_audit and not allow_missing_audit:
            raise StudySkillPackVersionConflict(
                "Study skill-pack version audit is missing"
            )
        if not has_audit and missing_audit_versions is not None:
            missing_audit_versions.add(skill_pack_version_id)
        return payload

    def _load_optional_study_schema(self, study_id: str) -> StudySchema | None:
        path = self._study_dir(study_id) / "study_schema.json"
        if not path.exists():
            return None
        return StudySchema(**json.loads(path.read_text(encoding="utf-8")))


def _aggregate_batch_payload(
    study_id: str,
    batch_id: str,
    skill_pack_version_id: str,
    study_schema: StudySchema | None,
    runs: list[dict[str, Any]],
    failures: list[dict[str, str]],
    *,
    created_at: str,
) -> dict[str, Any]:
    results_by_metric: dict[str, dict[str, Any]] = {}
    for run in runs:
        for result in run["results"]:
            aggregate = results_by_metric.setdefault(
                result["metric_id"],
                {
                    "metric_id": result["metric_id"],
                    "label": result["label"],
                    "rows": [],
                },
            )
            aggregate["rows"].extend(
                {
                    **_ordered_metadata(run.get("metadata", {})),
                    "source_filename": run["source_filename"],
                    "run_id": run["run_id"],
                    **row,
                }
                for row in result["rows"]
            )
    return {
        "study_id": study_id,
        "batch_id": batch_id,
        "skill_pack_version_id": skill_pack_version_id,
        "study_schema": asdict(study_schema) if study_schema else None,
        "created_at": created_at,
        "run_count": len(runs),
        "failure_count": len(failures),
        "failures": failures,
        "results": list(results_by_metric.values()),
    }


def _study_batch_run_payload(
    run: Any,
    metadata: dict[str, str],
) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "import_id": run.import_id,
        "project_source_id": run.project_source_id,
        "parent_transcript_revision_id": run.parent_transcript_revision_id,
        "workspace_id": run.workspace_id,
        "source_blob_sha256": run.source_blob_sha256,
        "source_media_type": run.source_media_type,
        "source_id": run.source_id,
        "transcript_sha256": run.transcript_sha256,
        "transcript_revision_id": run.transcript_revision_id,
        "evidence_set_id": run.evidence_set_id,
        "source_filename": run.source_filename,
        "metadata": metadata,
        "created_at": run.created_at,
        "turn_count": len(run.transcript.turns),
        "turns": [asdict(turn) for turn in run.transcript.turns],
        "results": [asdict(result) for result in run.results],
    }


def _validate_analysis_evidence_set_payload(
    run_payload: dict[str, Any],
    evidence_sets: dict[str, EvidenceSetSnapshot],
) -> None:
    evidence_set_id = run_payload.get("evidence_set_id")
    if (
        type(evidence_set_id) is not str
        or not _EVIDENCE_SET_ID.fullmatch(evidence_set_id)
    ):
        raise ValueError("Study analysis evidence_set_id is invalid")
    snapshot = evidence_sets.get(evidence_set_id)
    if snapshot is None:
        raise ValueError("Study analysis evidence set is missing")
    turns = run_payload.get("turns")
    if not isinstance(turns, list):
        raise TypeError("Study analysis turns must be a list")
    if (
        snapshot.import_id != run_payload.get("import_id")
        or snapshot.workspace_id != run_payload.get("workspace_id")
        or snapshot.project_source_id != run_payload.get("project_source_id")
        or snapshot.transcript_revision_id
        != run_payload.get("transcript_revision_id")
        or snapshot.transcript_text_sha256
        != run_payload.get("transcript_sha256")
        or snapshot.producer_kind != "analysis_turns"
        or snapshot.producer_version != 1
        or snapshot.producer_status != "verified"
        or snapshot.review_status != "not_applicable"
        or snapshot.passage_count != len(turns)
        or snapshot.cunit_count != 0
        or len(snapshot.passages) != len(turns)
    ):
        raise ValueError("Study analysis evidence set conflicts with the run")
    for turn_index, (turn, passage) in enumerate(
        zip(turns, snapshot.passages, strict=True)
    ):
        if not isinstance(turn, dict):
            raise TypeError("Study analysis turn must be an object")
        turn_text = turn.get("text")
        if (
            type(turn.get("turn_index")) is not int
            or turn["turn_index"] != turn_index
            or type(turn.get("passage_id")) is not str
            or turn["passage_id"] != passage.passage_id
            or type(turn.get("role")) is not str
            or turn["role"] != passage.role
            or type(turn_text) is not str
            or not turn_text
            or passage.passage_ordinal != turn_index
            or passage.text_sha256 != evidence_text_sha256(turn_text)
            or passage.text_length != len(turn_text)
            or passage.cunits
        ):
            raise ValueError("Study analysis turn conflicts with evidence targets")


def _batch_run_from_payload(
    payload: Any,
    *,
    aggregate_dir: Path,
) -> StudyBatchRun:
    expected_fields = {
        "study_id",
        "batch_id",
        "skill_pack_version_id",
        "run_count",
        "failure_count",
        "aggregate_dir",
        "created_at",
    }
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise ValueError("Study batch manifest must contain the exact expected fields")
    for field_name in (
        "study_id",
        "batch_id",
        "skill_pack_version_id",
        "aggregate_dir",
        "created_at",
    ):
        if type(payload[field_name]) is not str:
            raise ValueError(f"Study batch manifest {field_name} must be a string")
    for field_name in ("run_count", "failure_count"):
        if type(payload[field_name]) is not int:
            raise ValueError(f"Study batch manifest {field_name} must be an integer")
    return StudyBatchRun(
        study_id=payload["study_id"],
        batch_id=payload["batch_id"],
        skill_pack_version_id=payload["skill_pack_version_id"],
        run_count=payload["run_count"],
        failure_count=payload["failure_count"],
        aggregate_dir=aggregate_dir,
        created_at=payload["created_at"],
    )


def _batch_run_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": payload["run_id"],
        "import_id": str(payload.get("import_id") or ""),
        "project_source_id": str(payload.get("project_source_id") or ""),
        "parent_transcript_revision_id": str(
            payload.get("parent_transcript_revision_id") or ""
        ),
        "workspace_id": str(payload.get("workspace_id") or ""),
        "source_blob_sha256": str(payload.get("source_blob_sha256") or ""),
        "source_media_type": str(payload.get("source_media_type") or ""),
        "source_id": str(payload.get("source_id") or ""),
        "transcript_sha256": str(
            payload.get("transcript_sha256") or payload.get("source_sha256") or ""
        ),
        "transcript_revision_id": str(payload.get("transcript_revision_id") or ""),
        "evidence_set_id": str(payload.get("evidence_set_id") or ""),
        "source_filename": payload["source_filename"],
        "metadata": payload.get("metadata", {}),
        "created_at": payload["created_at"],
        "turn_count": payload["turn_count"],
        "metric_ids": [result["metric_id"] for result in payload.get("results", [])],
    }


def _study_config_from_skill_pack_payload(payload: dict[str, Any]) -> StudyConfig:
    pack = parse_skill_pack(payload)
    return StudyConfig(
        participant_id="",
        speaker_prefixes=pack.speaker_prefixes,
        speaker_labels=pack.speaker_roles,
        selected_metrics=[metric.id for metric in pack.metrics],
        disfluency_tokens=pack.disfluency_tokens,
        concept_lexicons=pack.concept_lexicons,
        nonverbal_cues=pack.nonverbal_cues,
        skill_pack_id=pack.id,
        skill_pack_name=pack.name,
        skill_pack_version=pack.version,
    )


def _study_config_for_batch_item(
    skill_pack_payload: dict[str, Any],
    metadata: dict[str, str],
) -> StudyConfig:
    config = _study_config_from_skill_pack_payload(skill_pack_payload)
    participant_id = metadata.get("participant_id", "").strip()
    if not participant_id:
        return config
    return replace(config, participant_id=participant_id)


def _skill_pack_version_id(payload: dict[str, Any]) -> str:
    return f"{_identifier(str(payload['id']))}-{_identifier(str(payload['version']))}"


def _identifier(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()


def _validate_study_run_id(run_id: str) -> None:
    if not _RUN_ID.fullmatch(run_id) or _WINDOWS_DEVICE_NAME.fullmatch(run_id):
        raise ValueError("run_id must be a portable path-safe identifier")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    normalized = slug or f"study-{uuid4().hex[:8]}"
    portable = (
        f"{normalized}-study"
        if _WINDOWS_DEVICE_NAME.fullmatch(normalized)
        else normalized
    )
    if len(portable) <= MAX_STUDY_ID_LENGTH:
        return portable
    suffix = hashlib.sha256(portable.encode("utf-8")).hexdigest()[:8]
    prefix = portable[: MAX_STUDY_ID_LENGTH - len(suffix) - 1].rstrip("-")
    return f"{prefix}-{suffix}"


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _normalized_metadata(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    metadata: dict[str, str] = {}
    for key, value in sorted(payload.items(), key=lambda item: str(item[0])):
        normalized_key = str(key).strip()
        normalized_value = str(value).strip()
        if normalized_key and normalized_value:
            metadata[normalized_key] = normalized_value
    return metadata


def _study_schema_from_payload(study_id: str, payload: dict[str, Any]) -> StudySchema:
    participant_count = _bounded_positive_int(
        payload.get("participant_count"),
        1,
        MAX_STUDY_PARTICIPANTS,
    )
    week_count = _bounded_positive_int(payload.get("week_count"), 1, 52)
    conditions = _normalized_string_list(payload.get("conditions")) or ["home", "lab"]
    custom_fields = _normalized_string_list(payload.get("custom_fields"))
    return StudySchema(
        study_id=study_id,
        participant_count=participant_count,
        participants=[f"P{index + 1}" for index in range(participant_count)],
        conditions=conditions,
        week_count=week_count,
        weeks=[f"week_{index + 1}" for index in range(week_count)],
        custom_fields=custom_fields,
    )


def _study_schema_semantic_payload(schema: StudySchema) -> dict[str, Any]:
    return {
        "study_id": schema.study_id,
        "participant_count": schema.participant_count,
        "participants": schema.participants,
        "conditions": schema.conditions,
        "week_count": schema.week_count,
        "weeks": schema.weeks,
        "custom_fields": schema.custom_fields,
    }


def _bounded_positive_int(value: Any, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, 1), maximum)


def _normalized_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    normalized: list[str] = []
    for item in raw_items:
        text = str(item).strip().lower()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _ordered_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    normalized = _normalized_metadata(metadata)
    ordered: dict[str, str] = {}
    for key in ["participant_id", "condition", "week"]:
        if key in normalized:
            ordered[key] = normalized[key]
    for key in sorted(normalized):
        if key not in ordered:
            ordered[key] = normalized[key]
    return ordered


def _write_exact_json(path: Path, payload: dict[str, Any]) -> None:
    _write_exact_text(path, json.dumps(payload, indent=2))


def _write_exact_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    _write_exact_text(path, _csv_text(rows))


def _csv_text(rows: list[dict[str, Any]]) -> str:
    fieldnames = _ordered_fieldnames(rows)
    csv_file = io.StringIO(newline="")
    writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return csv_file.getvalue()


def _write_exact_text(path: Path, content: str) -> None:
    expected_bytes = content.encode("utf-8")
    if path.exists():
        if path.read_bytes() != expected_bytes:
            raise StudyBatchSnapshotConflict(
                f"Study batch artifact conflicts with existing snapshot: {path.name}"
            )
        return
    atomic_write_bytes(path, expected_bytes)


def _read_non_symlink_regular_file(path: Path) -> bytes:
    _validate_non_symlink_regular_path(path)
    return path.read_bytes()


def _validate_non_symlink_regular_path(path: Path) -> None:
    file_stat = path.lstat()
    if not stat.S_ISREG(file_stat.st_mode):
        raise OSError("Expected a non-symlink regular file")


def _validate_timezone_aware_timestamp(value: object, label: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError(f"{label} must be a bounded timestamp")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")


def _legacy_evidence_generation(run_payload: dict[str, Any]) -> str:
    present_fields = _ALL_LEGACY_EVIDENCE_FIELDS.intersection(run_payload)
    if not present_fields:
        return "none"
    if present_fields == _EARLY_EVIDENCE_FIELDS:
        return "early"
    if present_fields == _IMPORT_V1_EVIDENCE_FIELDS:
        return "import-v1"
    if present_fields == _CURRENT_EVIDENCE_FIELDS:
        return "current"
    if present_fields == _TARGET_V1_EVIDENCE_FIELDS:
        return "target-v1"
    raise ValueError("legacy run evidence identity is incomplete")


def _validate_legacy_transcript_identity(
    run_payload: dict[str, Any],
    transcript_sha256: Any,
) -> None:
    if not isinstance(transcript_sha256, str) or not _SHA256.fullmatch(
        transcript_sha256
    ):
        raise ValueError("legacy transcript digest is invalid")
    if run_payload.get("source_id") != f"src_{transcript_sha256[:32]}":
        raise ValueError("legacy source identity conflicts with transcript digest")
    if run_payload.get("transcript_revision_id") != (
        f"trv_{transcript_sha256[:32]}"
    ):
        raise ValueError("legacy revision identity conflicts with transcript digest")


def _legacy_aggregate_run_order(
    run_payloads: dict[str, dict[str, Any]],
    results: list[Any],
) -> list[str]:
    edges: dict[str, set[str]] = {run_id: set() for run_id in run_payloads}
    indegrees = {run_id: 0 for run_id in run_payloads}
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("rows"), list):
            raise TypeError("legacy aggregate metric rows must be a list")
        metric_order: list[str] = []
        for row in result["rows"]:
            if not isinstance(row, dict) or not isinstance(row.get("run_id"), str):
                raise TypeError("legacy aggregate row must contain a run id")
            run_id = row["run_id"]
            if run_id not in run_payloads:
                raise ValueError("legacy aggregate references an unknown run")
            if run_id not in metric_order:
                metric_order.append(run_id)
        for before, after in zip(metric_order, metric_order[1:], strict=False):
            if after not in edges[before]:
                edges[before].add(after)
                indegrees[after] += 1

    ready = sorted(run_id for run_id, degree in indegrees.items() if degree == 0)
    ordered: list[str] = []
    while ready:
        run_id = ready.pop(0)
        ordered.append(run_id)
        for successor in sorted(edges[run_id]):
            indegrees[successor] -= 1
            if indegrees[successor] == 0:
                ready.append(successor)
                ready.sort()
    if len(ordered) != len(run_payloads):
        raise ValueError("legacy aggregate run order is inconsistent")
    return ordered


def _study_batch_item_request_sha256(item: dict[str, Any]) -> str:
    return _canonical_json_sha256(_canonical_request_value(item))


def _canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_request_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {
            "type": "bytes",
            "sha256": hashlib.sha256(value).hexdigest(),
            "size_bytes": len(value),
        }
    if isinstance(value, dict):
        return {
            str(key): _canonical_request_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_request_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _ordered_fieldnames(rows: list[dict[str, Any]]) -> list[str]:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    return fieldnames


def _file_record(path: Path, root: Path) -> dict[str, str | int]:
    return {
        "relative_path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
    }
