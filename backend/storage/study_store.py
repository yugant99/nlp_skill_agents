from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.analysis.pipeline import execute_analysis
from backend.analysis.skill_packs import parse_skill_pack
from backend.analysis.transcripts import StudyConfig
from backend.evidence.identifiers import (
    source_import_identity,
    transcript_evidence_identity,
)
from backend.storage.audit_log import AuditLogStore
from backend.storage.atomic import atomic_write_bytes, atomic_write_text
from backend.storage.evidence_catalog import EvidenceCatalog, EvidenceImportRecord
from backend.storage.source_blob_store import SourceBlobStore
from backend.storage.study_batch_operation_store import (
    StudyBatchOperationStore,
    validate_study_batch_id,
)


MAX_STUDY_PARTICIPANTS = 10_000
_SKILL_PACK_VERSION_ID = re.compile(r"^[a-z0-9_]+-[a-z0-9_]+$")


class StudyBatchSnapshotConflict(RuntimeError):
    pass


class StudySkillPackVersionConflict(RuntimeError):
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


class StudyWorkspaceStore:
    def __init__(self, root: Path | str = "local_data") -> None:
        self.root = Path(root)
        self.studies_dir = self.root / "studies"
        self.audit_log = AuditLogStore(self.root)

    def create_study(self, payload: dict[str, Any]) -> StudyWorkspace:
        name = _required_string(payload, "name")
        study = StudyWorkspace(
            id=_slugify(str(payload.get("id") or name)),
            name=name,
            description=str(payload.get("description") or ""),
        )
        study_dir = self._study_dir(study.id)
        study_dir.mkdir(parents=True)
        atomic_write_text(
            study_dir / "study.json",
            json.dumps(asdict(study), indent=2),
        )
        self.audit_log.record(
            "study.created",
            "study",
            study.id,
            {"name": study.name},
        )
        return study

    def list_studies(self) -> list[StudyWorkspace]:
        if not self.studies_dir.exists():
            return []
        studies = [
            StudyWorkspace(**json.loads(path.read_text(encoding="utf-8")))
            for path in self.studies_dir.glob("*/study.json")
        ]
        return sorted(studies, key=lambda study: study.created_at, reverse=True)

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
                    return existing_schema
            atomic_write_text(schema_path, json.dumps(asdict(schema), indent=2))
            self.audit_log.record(
                "study.schema.updated",
                "study",
                study_id,
                {
                    "participant_count": schema.participant_count,
                    "conditions": schema.conditions,
                    "week_count": schema.week_count,
                    "custom_fields": schema.custom_fields,
                },
            )
        return schema

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
        with StudyBatchOperationStore(
            self.root,
            study_id,
        ).study_mutation_guard():
            version_dir = self._study_dir(study_id) / "skill_packs"
            version_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = version_dir / f"{version_id}.json"
            metadata_path = version_dir / f"{version_id}.metadata.json"
            if artifact_path.exists() or metadata_path.exists():
                if not artifact_path.is_file() or not metadata_path.is_file():
                    raise StudySkillPackVersionConflict(
                        "Study skill-pack version artifacts are incomplete"
                    )
                try:
                    existing_payload = json.loads(
                        artifact_path.read_text(encoding="utf-8")
                    )
                    existing_metadata = json.loads(
                        metadata_path.read_text(encoding="utf-8")
                    )
                    existing_created_at = str(existing_metadata["created_at"])
                except (
                    json.JSONDecodeError,
                    KeyError,
                    TypeError,
                    UnicodeDecodeError,
                ) as exc:
                    raise StudySkillPackVersionConflict(
                        "Study skill-pack version artifacts are invalid"
                    ) from exc
                if (
                    _canonical_json_sha256(existing_payload)
                    != _canonical_json_sha256(payload)
                ):
                    raise StudySkillPackVersionConflict(
                        "Study skill-pack version already exists with different content"
                    )
                if (
                    str(existing_metadata.get("study_id") or "") != study_id
                    or str(existing_metadata.get("version_id") or "") != version_id
                    or not existing_created_at.strip()
                ):
                    raise StudySkillPackVersionConflict(
                        "Study skill-pack version metadata conflicts with its identity"
                    )
                return StudySkillPackVersion(
                    study_id=study_id,
                    version_id=version_id,
                    payload=existing_payload,
                    artifact_path=artifact_path,
                    created_at=existing_created_at,
                )
            atomic_write_text(artifact_path, json.dumps(payload, indent=2))
            metadata = StudySkillPackVersion(
                study_id=study_id,
                version_id=version_id,
                payload=payload,
                artifact_path=artifact_path,
            )
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
            self.audit_log.record(
                "skill_pack.versioned",
                "study",
                study_id,
                {
                    "version_id": version_id,
                    "skill_pack_id": str(payload["id"]),
                    "skill_pack_version": str(payload["version"]),
                },
            )
        return metadata

    def run_text_batch(
        self,
        study_id: str,
        skill_pack_version_id: str,
        transcripts: list[dict[str, Any]],
        *,
        batch_id: str | None = None,
    ) -> StudyBatchRun:
        self._require_study(study_id)
        if not _SKILL_PACK_VERSION_ID.fullmatch(skill_pack_version_id):
            raise ValueError(
                "skill_pack_version_id must be a normalized version identifier"
            )
        skill_pack_payload = self._load_skill_pack_version(
            study_id,
            skill_pack_version_id,
        )
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
        resolved_batch_id = batch_id or (
            f"batch_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}_"
            f"{uuid4().hex[:8]}"
        )
        validate_study_batch_id(resolved_batch_id)
        aggregate_dir = self._study_dir(study_id) / "batches" / resolved_batch_id
        journal = StudyBatchOperationStore(self.root, study_id)
        try:
            existing_operation = journal.get_operation(resolved_batch_id)
        except FileNotFoundError:
            existing_operation = None
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
            batch = self.load_batch(study_id, batch_id)
            aggregate_payload = json.loads(
                (batch.aggregate_dir / "aggregate_results.json").read_text(
                    encoding="utf-8"
                )
            )
            items = journal.list_items(batch_id)
            import_records = {
                record.import_id: record
                for record in EvidenceCatalog(self.root).workspace_import_records(
                    study_id
                )
            }
        except FileNotFoundError as exc:
            raise StudyBatchSnapshotConflict(
                "Completed study batch is missing a persisted artifact"
            ) from exc
        except (json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError) as exc:
            raise StudyBatchSnapshotConflict(
                "Completed study batch contains an invalid persisted artifact"
            ) from exc

        completed_items = [item for item in items if item["stage"] == "completed"]
        rejected_items = [item for item in items if item["stage"] == "rejected"]
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
                run_payload = json.loads(run_path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise StudyBatchSnapshotConflict(
                    "Completed study batch is missing a run snapshot"
                ) from exc
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
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
            import_record = import_records.get(str(item["import_id"]))
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
                "transcript_revision_id": run_payload["transcript_revision_id"],
                "transcript_sha256": run_payload["transcript_sha256"],
                "imported_at": run_payload["created_at"],
            }
            if import_record is None or asdict(import_record) != expected_import:
                raise StudyBatchSnapshotConflict(
                    "Completed study batch evidence conflicts with its journal"
                )
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
            if csv_path.read_bytes() != _csv_text(result["rows"]).encode("utf-8"):
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
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
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

    def list_batches(self, study_id: str) -> list[StudyBatchRun]:
        self._require_study(study_id)
        batches_dir = self._study_dir(study_id) / "batches"
        if not batches_dir.exists():
            return []
        batches = []
        for path in batches_dir.glob("*/batch.json"):
            batches.append(
                _batch_run_from_payload(
                    json.loads(path.read_text(encoding="utf-8")),
                    aggregate_dir=path.parent,
                )
            )
        return sorted(batches, key=lambda batch: batch.created_at, reverse=True)

    def load_batch(self, study_id: str, batch_id: str) -> StudyBatchRun:
        self._require_study(study_id)
        batch_path = self._study_dir(study_id) / "batches" / batch_id / "batch.json"
        if not batch_path.exists():
            raise FileNotFoundError(batch_id)
        return _batch_run_from_payload(
            json.loads(batch_path.read_text(encoding="utf-8")),
            aggregate_dir=batch_path.parent,
        )

    def list_batch_runs(self, study_id: str, batch_id: str) -> list[dict[str, Any]]:
        batch = self.load_batch(study_id, batch_id)
        runs_dir = batch.aggregate_dir / "runs"
        if not runs_dir.exists():
            return []
        runs = [
            _batch_run_summary(json.loads(path.read_text(encoding="utf-8")))
            for path in runs_dir.glob("*.json")
        ]
        return sorted(runs, key=lambda run: str(run["source_filename"]))

    def load_batch_run(self, study_id: str, batch_id: str, run_id: str) -> dict[str, Any]:
        batch = self.load_batch(study_id, batch_id)
        run_path = batch.aggregate_dir / "runs" / f"{run_id}.json"
        if not run_path.exists():
            raise FileNotFoundError(run_id)
        return json.loads(run_path.read_text(encoding="utf-8"))

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
    ) -> dict[str, Any]:
        path = self._study_dir(study_id) / "skill_packs" / f"{skill_pack_version_id}.json"
        if not path.exists():
            raise FileNotFoundError(skill_pack_version_id)
        return json.loads(path.read_text(encoding="utf-8"))

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
        "source_filename": run.source_filename,
        "metadata": metadata,
        "created_at": run.created_at,
        "turn_count": len(run.transcript.turns),
        "turns": [asdict(turn) for turn in run.transcript.turns],
        "results": [asdict(result) for result in run.results],
    }


def _batch_run_from_payload(
    payload: dict[str, Any],
    *,
    aggregate_dir: Path,
) -> StudyBatchRun:
    return StudyBatchRun(
        study_id=str(payload["study_id"]),
        batch_id=str(payload["batch_id"]),
        skill_pack_version_id=str(payload["skill_pack_version_id"]),
        run_count=int(payload["run_count"]),
        failure_count=int(payload["failure_count"]),
        aggregate_dir=aggregate_dir,
        created_at=str(payload["created_at"]),
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


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return slug or f"study-{uuid4().hex[:8]}"


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
