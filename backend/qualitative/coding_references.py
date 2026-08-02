from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from backend.qualitative.database import (
    QualitativeDatabaseConflict,
    QualitativeProjectDatabase,
    new_qualitative_id,
)
from backend.storage.evidence_target_registry import (
    EvidenceTargetConflictError,
    EvidenceTargetNotFoundError,
    EvidenceTargetRegistry,
)
from backend.storage.sqlite_migrations import SchemaCompatibilityError
from backend.storage.study_batch_operation_store import StudyBatchOperationConflict
from backend.storage.workspace_lock import workspace_mutation_lock


_ENTITY_ID = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_CODING_REFERENCE_ID = re.compile(r"^cdr_[0-9a-f]{32}$")
_AUDIT_EVENT_ID = re.compile(r"^qae_[0-9a-f]{32}$")
_EVIDENCE_IDS = {
    "transcript_revision_id": re.compile(r"^trv_[0-9a-f]{32}$"),
    "evidence_set_id": re.compile(r"^evs_[0-9a-f]{32}$"),
    "passage_id": re.compile(r"^psg_[0-9a-f]{32}$"),
    "cunit_id": re.compile(r"^cun_[0-9a-f]{32}$"),
}
_TARGET_KINDS = {"passage", "cunit"}
_MAX_EXTERNAL_ID_LENGTH = 256
_MAX_TIMESTAMP_LENGTH = 64


class CodingReferenceNotFoundError(LookupError):
    pass


class CodingReferenceValidationError(ValueError):
    pass


class CodingReferenceConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodingReferenceRecord:
    coding_reference_id: str
    project_id: str
    project_source_id: str
    transcript_revision_id: str
    evidence_set_id: str
    target_kind: str
    passage_id: str
    cunit_id: str
    start_offset: int
    end_offset: int
    codebook_version_id: str
    code_id: str
    created_by: str
    created_at: str
    removed_by: str | None
    removed_at: str | None


class CodingReferenceService:
    def __init__(self, root: Path | str, project_id: str) -> None:
        try:
            self.database = QualitativeProjectDatabase(root, project_id)
        except (TypeError, ValueError) as exc:
            raise CodingReferenceValidationError(
                "Invalid qualitative project id"
            ) from exc
        self.root = Path(root)
        self.project_id = self.database.project_id

    def create_reference(
        self,
        *,
        researcher_id: str,
        project_source_id: str,
        transcript_revision_id: str,
        evidence_set_id: str,
        target_kind: str,
        passage_id: str,
        cunit_id: str = "",
        start_offset: int,
        end_offset: int,
        codebook_version_id: str,
        code_id: str,
    ) -> CodingReferenceRecord:
        actor_id = _input_entity_id(researcher_id, "researcher_id")
        source_id = _input_external_id(project_source_id)
        revision_id = _input_evidence_id(
            transcript_revision_id,
            "transcript_revision_id",
        )
        set_id = _input_evidence_id(evidence_set_id, "evidence_set_id")
        normalized_kind = _input_target_kind(target_kind)
        normalized_passage_id = _input_evidence_id(passage_id, "passage_id")
        normalized_cunit_id = _input_cunit_id(cunit_id, normalized_kind)
        normalized_start, normalized_end = _input_offsets(start_offset, end_offset)
        version_id = _input_entity_id(
            codebook_version_id,
            "codebook_version_id",
        )
        normalized_code_id = _input_entity_id(code_id, "code_id")

        self._resolve_target(
            project_source_id=source_id,
            transcript_revision_id=revision_id,
            evidence_set_id=set_id,
            target_kind=normalized_kind,
            passage_id=normalized_passage_id,
            cunit_id=normalized_cunit_id,
            start_offset=normalized_start,
            end_offset=normalized_end,
            missing_is_not_found=True,
        )

        now = _utc_now()
        coding_reference_id = new_qualitative_id("coding_reference")
        with self._write() as connection:
            self._require_active_researcher(connection, actor_id)
            self._require_frozen_code(
                connection,
                codebook_version_id=version_id,
                code_id=normalized_code_id,
            )
            existing_row = connection.execute(
                """
                select * from coding_references
                where project_id = ?
                  and project_source_id = ?
                  and transcript_revision_id = ?
                  and evidence_set_id = ?
                  and target_kind = ?
                  and passage_id = ?
                  and cunit_id = ?
                  and start_offset = ?
                  and end_offset = ?
                  and codebook_version_id = ?
                  and code_id = ?
                  and created_by = ?
                  and removed_at is null
                """,
                (
                    self.project_id,
                    source_id,
                    revision_id,
                    set_id,
                    normalized_kind,
                    normalized_passage_id,
                    normalized_cunit_id,
                    normalized_start,
                    normalized_end,
                    version_id,
                    normalized_code_id,
                    actor_id,
                ),
            ).fetchone()
            if existing_row is not None:
                existing = self._reference_record(existing_row)
                self._validate_local_relations(connection, (existing,))
                self._validate_audit_pairs(connection, (existing,))
                return existing

            connection.execute(
                """
                insert into coding_references (
                  coding_reference_id, project_id, project_source_id,
                  transcript_revision_id, evidence_set_id, target_kind,
                  passage_id, cunit_id, start_offset, end_offset,
                  codebook_version_id, code_id, created_by, created_at,
                  removed_by, removed_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, null, null)
                """,
                (
                    coding_reference_id,
                    self.project_id,
                    source_id,
                    revision_id,
                    set_id,
                    normalized_kind,
                    normalized_passage_id,
                    normalized_cunit_id,
                    normalized_start,
                    normalized_end,
                    version_id,
                    normalized_code_id,
                    actor_id,
                    now,
                ),
            )
            self._append_audit(
                connection,
                actor_id=actor_id,
                event_type="coding_reference.created",
                coding_reference_id=coding_reference_id,
                metadata={
                    "code_id": normalized_code_id,
                    "codebook_version_id": version_id,
                    "evidence_set_id": set_id,
                    "target_kind": normalized_kind,
                },
                created_at=now,
            )
            stored = self._require_reference(connection, coding_reference_id)
            self._validate_local_relations(connection, (stored,))
            self._validate_audit_pairs(connection, (stored,))
            return stored

    def read_reference(self, coding_reference_id: str) -> CodingReferenceRecord:
        normalized_id = _input_coding_reference_id(coding_reference_id)
        with self._read() as connection:
            self._require_project(connection)
            record = self._require_reference(connection, normalized_id)
            self._validate_local_relations(connection, (record,))
            self._validate_audit_pairs(connection, (record,))
        self._validate_external_records((record,))
        return record

    def list_references(
        self,
        *,
        include_removed: bool = False,
        project_source_id: str | None = None,
        codebook_version_id: str | None = None,
        code_id: str | None = None,
        created_by: str | None = None,
    ) -> tuple[CodingReferenceRecord, ...]:
        if type(include_removed) is not bool:
            raise CodingReferenceValidationError(
                "include_removed must be a boolean"
            )
        filters: list[str] = ["project_id = ?"]
        parameters: list[object] = [self.project_id]
        if not include_removed:
            filters.append("removed_at is null")
        if project_source_id is not None:
            filters.append("project_source_id = ?")
            parameters.append(_input_external_id(project_source_id))
        if codebook_version_id is not None:
            filters.append("codebook_version_id = ?")
            parameters.append(
                _input_entity_id(codebook_version_id, "codebook_version_id")
            )
        if code_id is not None:
            filters.append("code_id = ?")
            parameters.append(_input_entity_id(code_id, "code_id"))
        if created_by is not None:
            filters.append("created_by = ?")
            parameters.append(_input_entity_id(created_by, "created_by"))
        query = (
            "select * from coding_references where "
            + " and ".join(filters)
            + " order by created_at, coding_reference_id"
        )
        with self._read() as connection:
            self._require_project(connection)
            records = tuple(
                self._reference_record(row)
                for row in connection.execute(query, parameters).fetchall()
            )
            self._validate_local_relations(connection, records)
            self._validate_audit_pairs(connection, records)
        self._validate_external_records(records)
        return records

    def remove_reference(
        self,
        *,
        researcher_id: str,
        coding_reference_id: str,
    ) -> CodingReferenceRecord:
        actor_id = _input_entity_id(researcher_id, "researcher_id")
        normalized_id = _input_coding_reference_id(coding_reference_id)
        now = _utc_now()
        with self._write() as connection:
            self._require_active_researcher(connection, actor_id)
            existing = self._require_reference(connection, normalized_id)
            self._validate_local_relations(connection, (existing,))
            self._validate_audit_pairs(connection, (existing,))
            if existing.removed_at is not None:
                if existing.removed_by == actor_id:
                    return existing
                raise CodingReferenceConflictError(
                    "Coding reference was removed by a different researcher"
                )

            updated = connection.execute(
                """
                update coding_references
                set removed_by = ?, removed_at = ?
                where project_id = ? and coding_reference_id = ?
                  and removed_by is null and removed_at is null
                """,
                (actor_id, now, self.project_id, normalized_id),
            ).rowcount
            if updated != 1:
                raise CodingReferenceConflictError(
                    "Coding reference removal conflicted with stored state"
                )
            self._append_audit(
                connection,
                actor_id=actor_id,
                event_type="coding_reference.removed",
                coding_reference_id=normalized_id,
                metadata={},
                created_at=now,
            )
            removed = self._require_reference(connection, normalized_id)
            self._validate_local_relations(connection, (removed,))
            self._validate_audit_pairs(connection, (removed,))
            return removed

    def validate_project_state(self) -> None:
        with self._read() as connection:
            rows = connection.execute(
                """
                select * from coding_references
                where project_id = ?
                order by created_at, coding_reference_id
                """,
                (self.project_id,),
            ).fetchall()
            records = tuple(self._reference_record(row) for row in rows)
            self._validate_local_relations(connection, records)
            self._validate_audit_pairs(
                connection,
                records,
                reject_unmatched=True,
            )
        self._validate_external_records(records)

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        try:
            with self.database.read() as connection:
                connection.row_factory = sqlite3.Row
                yield connection
        except FileNotFoundError as exc:
            raise CodingReferenceNotFoundError(
                "Qualitative project not found"
            ) from exc
        except (
            QualitativeDatabaseConflict,
            SchemaCompatibilityError,
            StudyBatchOperationConflict,
        ) as exc:
            raise CodingReferenceConflictError(
                "Qualitative coding storage is unavailable or corrupt"
            ) from exc
        except sqlite3.DatabaseError as exc:
            raise CodingReferenceConflictError(
                "Qualitative coding storage is unavailable or corrupt"
            ) from exc

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        try:
            with self.database.transaction() as connection:
                connection.row_factory = sqlite3.Row
                yield connection
        except FileNotFoundError as exc:
            raise CodingReferenceNotFoundError(
                "Qualitative project not found"
            ) from exc
        except sqlite3.IntegrityError as exc:
            raise CodingReferenceConflictError(
                "Coding reference storage constraint conflict"
            ) from exc
        except (
            QualitativeDatabaseConflict,
            SchemaCompatibilityError,
            StudyBatchOperationConflict,
        ) as exc:
            raise CodingReferenceConflictError(
                "Qualitative coding storage is unavailable or corrupt"
            ) from exc
        except sqlite3.DatabaseError as exc:
            raise CodingReferenceConflictError(
                "Qualitative coding storage is unavailable or corrupt"
            ) from exc

    def _require_project(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "select 1 from qualitative_projects where project_id = ?",
            (self.project_id,),
        ).fetchone()
        if row is None:
            raise CodingReferenceNotFoundError(
                "Qualitative project is not initialized"
            )

    def _require_active_researcher(
        self,
        connection: sqlite3.Connection,
        researcher_id: str,
    ) -> None:
        row = connection.execute(
            """
            select project_id, researcher_id, active from researchers
            where project_id = ? and researcher_id = ?
            """,
            (self.project_id, researcher_id),
        ).fetchone()
        if row is None:
            raise CodingReferenceNotFoundError("Researcher not found")
        if (
            _stored_text(row["project_id"], "researcher project") != self.project_id
            or _stored_entity_id(row["researcher_id"], "researcher_id")
            != researcher_id
        ):
            raise CodingReferenceConflictError("Stored researcher record is invalid")
        active = row["active"]
        if type(active) is not int or active not in (0, 1):
            raise CodingReferenceConflictError("Stored researcher record is invalid")
        if active != 1:
            raise CodingReferenceConflictError("Researcher is inactive")

    def _require_frozen_code(
        self,
        connection: sqlite3.Connection,
        *,
        codebook_version_id: str,
        code_id: str,
    ) -> None:
        version = connection.execute(
            """
            select project_id, codebook_version_id, status
            from codebook_versions
            where project_id = ? and codebook_version_id = ?
            """,
            (self.project_id, codebook_version_id),
        ).fetchone()
        if version is None:
            raise CodingReferenceNotFoundError("Codebook version not found")
        if (
            _stored_text(version["project_id"], "codebook version project")
            != self.project_id
            or _stored_entity_id(
                version["codebook_version_id"],
                "codebook_version_id",
            )
            != codebook_version_id
        ):
            raise CodingReferenceConflictError(
                "Stored codebook version is invalid"
            )
        status = _stored_text(version["status"], "codebook version status")
        if status not in {"draft", "frozen"}:
            raise CodingReferenceConflictError(
                "Stored codebook version is invalid"
            )
        if status != "frozen":
            raise CodingReferenceConflictError(
                "Coding requires a frozen codebook version"
            )
        code = connection.execute(
            """
            select project_id, codebook_version_id, code_id from codes
            where project_id = ? and codebook_version_id = ? and code_id = ?
            """,
            (self.project_id, codebook_version_id, code_id),
        ).fetchone()
        if code is None:
            conflicting_code = connection.execute(
                """
                select project_id, codebook_version_id from codes
                where code_id = ?
                """,
                (code_id,),
            ).fetchone()
            if conflicting_code is not None:
                raise CodingReferenceConflictError(
                    "Code belongs to a different codebook version"
                )
            raise CodingReferenceNotFoundError("Code not found")
        if (
            _stored_text(code["project_id"], "code project") != self.project_id
            or _stored_entity_id(
                code["codebook_version_id"],
                "codebook_version_id",
            )
            != codebook_version_id
            or _stored_entity_id(code["code_id"], "code_id") != code_id
        ):
            raise CodingReferenceConflictError("Stored code record is invalid")

    def _require_reference(
        self,
        connection: sqlite3.Connection,
        coding_reference_id: str,
    ) -> CodingReferenceRecord:
        row = connection.execute(
            """
            select * from coding_references
            where project_id = ? and coding_reference_id = ?
            """,
            (self.project_id, coding_reference_id),
        ).fetchone()
        if row is None:
            raise CodingReferenceNotFoundError("Coding reference not found")
        return self._reference_record(row)

    def _reference_record(self, row: sqlite3.Row) -> CodingReferenceRecord:
        target_kind = _stored_target_kind(row["target_kind"])
        cunit_id = _stored_text(row["cunit_id"], "cunit_id")
        if target_kind == "passage":
            if cunit_id != "":
                raise CodingReferenceConflictError(
                    "Stored coding reference target is invalid"
                )
        elif not _EVIDENCE_IDS["cunit_id"].fullmatch(cunit_id):
            raise CodingReferenceConflictError(
                "Stored coding reference target is invalid"
            )
        start_offset = _stored_integer(row["start_offset"], "start_offset")
        end_offset = _stored_integer(row["end_offset"], "end_offset")
        if start_offset < 0 or end_offset <= start_offset:
            raise CodingReferenceConflictError(
                "Stored coding reference offsets are invalid"
            )
        removed_by_value = row["removed_by"]
        removed_at_value = row["removed_at"]
        removed_by = (
            None
            if removed_by_value is None
            else _stored_entity_id(removed_by_value, "removed_by")
        )
        removed_at = (
            None
            if removed_at_value is None
            else _stored_timestamp(removed_at_value, "removed_at")
        )
        if (removed_by is None) != (removed_at is None):
            raise CodingReferenceConflictError(
                "Stored coding reference removal state is invalid"
            )
        record = CodingReferenceRecord(
            coding_reference_id=_stored_coding_reference_id(
                row["coding_reference_id"]
            ),
            project_id=_stored_text(row["project_id"], "project_id"),
            project_source_id=_stored_external_id(row["project_source_id"]),
            transcript_revision_id=_stored_evidence_id(
                row["transcript_revision_id"],
                "transcript_revision_id",
            ),
            evidence_set_id=_stored_evidence_id(
                row["evidence_set_id"],
                "evidence_set_id",
            ),
            target_kind=target_kind,
            passage_id=_stored_evidence_id(row["passage_id"], "passage_id"),
            cunit_id=cunit_id,
            start_offset=start_offset,
            end_offset=end_offset,
            codebook_version_id=_stored_entity_id(
                row["codebook_version_id"],
                "codebook_version_id",
            ),
            code_id=_stored_entity_id(row["code_id"], "code_id"),
            created_by=_stored_entity_id(row["created_by"], "created_by"),
            created_at=_stored_timestamp(row["created_at"], "created_at"),
            removed_by=removed_by,
            removed_at=removed_at,
        )
        if record.project_id != self.project_id:
            raise CodingReferenceConflictError(
                "Stored coding reference belongs to another project"
            )
        return record

    def _validate_local_relations(
        self,
        connection: sqlite3.Connection,
        records: Sequence[CodingReferenceRecord],
    ) -> None:
        for record in records:
            self._require_frozen_code(
                connection,
                codebook_version_id=record.codebook_version_id,
                code_id=record.code_id,
            )
            actor_ids = [record.created_by]
            if record.removed_by is not None:
                actor_ids.append(record.removed_by)
            for actor_id in actor_ids:
                actor = connection.execute(
                    """
                    select project_id, researcher_id, active from researchers
                    where project_id = ? and researcher_id = ?
                    """,
                    (self.project_id, actor_id),
                ).fetchone()
                if actor is None:
                    raise CodingReferenceConflictError(
                        "Stored coding reference actor is unavailable"
                    )
                if (
                    _stored_text(actor["project_id"], "actor project")
                    != self.project_id
                    or _stored_entity_id(actor["researcher_id"], "researcher_id")
                    != actor_id
                    or type(actor["active"]) is not int
                    or actor["active"] not in (0, 1)
                ):
                    raise CodingReferenceConflictError(
                        "Stored coding reference actor is invalid"
                    )

    def _validate_audit_pairs(
        self,
        connection: sqlite3.Connection,
        records: Sequence[CodingReferenceRecord],
        *,
        reject_unmatched: bool = False,
    ) -> None:
        record_by_id = {record.coding_reference_id: record for record in records}
        if len(record_by_id) != len(records):
            raise CodingReferenceConflictError(
                "Stored coding references contain duplicate identities"
            )
        if reject_unmatched:
            candidate_rows = connection.execute(
                """
                select * from qualitative_audit_events
                where project_id = ?
                  and (
                    subject_type = 'coding_reference'
                    or event_type like 'coding_reference.%'
                  )
                order by event_id
                """,
                (self.project_id,),
            ).fetchall()
            for row in candidate_rows:
                subject_id = _stored_text(row["subject_id"], "audit subject_id")
                if subject_id not in record_by_id:
                    raise CodingReferenceConflictError(
                        "Coding reference audit history is invalid"
                    )
        for record in records:
            rows = connection.execute(
                """
                select * from qualitative_audit_events
                where project_id = ? and subject_id = ?
                  and (
                    subject_type = 'coding_reference'
                    or event_type like 'coding_reference.%'
                  )
                order by created_at, event_id
                """,
                (self.project_id, record.coding_reference_id),
            ).fetchall()
            expected_created_metadata = json.dumps(
                {
                    "code_id": record.code_id,
                    "codebook_version_id": record.codebook_version_id,
                    "evidence_set_id": record.evidence_set_id,
                    "target_kind": record.target_kind,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            expected = {
                "coding_reference.created": (
                    record.created_by,
                    record.created_at,
                    expected_created_metadata,
                )
            }
            if record.removed_by is not None and record.removed_at is not None:
                expected["coding_reference.removed"] = (
                    record.removed_by,
                    record.removed_at,
                    "{}",
                )
            actual: dict[str, tuple[str, str, str]] = {}
            for row in rows:
                event_id = _stored_text(row["event_id"], "audit event_id")
                if not _AUDIT_EVENT_ID.fullmatch(event_id):
                    raise CodingReferenceConflictError(
                        "Coding reference audit history is invalid"
                    )
                if (
                    _stored_text(row["project_id"], "audit project_id")
                    != self.project_id
                    or _stored_text(row["subject_type"], "audit subject_type")
                    != "coding_reference"
                    or _stored_text(row["subject_id"], "audit subject_id")
                    != record.coding_reference_id
                ):
                    raise CodingReferenceConflictError(
                        "Coding reference audit history is invalid"
                    )
                event_type = _stored_text(row["event_type"], "audit event_type")
                if event_type in actual:
                    raise CodingReferenceConflictError(
                        "Coding reference audit history is invalid"
                    )
                actual[event_type] = (
                    _stored_entity_id(row["actor_id"], "audit actor_id"),
                    _stored_timestamp(row["created_at"], "audit created_at"),
                    _stored_text(row["metadata_json"], "audit metadata_json"),
                )
            if actual != expected:
                raise CodingReferenceConflictError(
                    "Coding reference audit history is invalid"
                )

    def _append_audit(
        self,
        connection: sqlite3.Connection,
        *,
        actor_id: str,
        event_type: str,
        coding_reference_id: str,
        metadata: dict[str, str],
        created_at: str,
    ) -> None:
        connection.execute(
            """
            insert into qualitative_audit_events (
              event_id, project_id, actor_id, event_type,
              subject_type, subject_id, metadata_json, created_at
            ) values (?, ?, ?, ?, 'coding_reference', ?, ?, ?)
            """,
            (
                new_qualitative_id("audit_event"),
                self.project_id,
                actor_id,
                event_type,
                coding_reference_id,
                json.dumps(metadata, sort_keys=True, separators=(",", ":")),
                created_at,
            ),
        )

    def _validate_external_records(
        self,
        records: Sequence[CodingReferenceRecord],
    ) -> None:
        validated: set[tuple[object, ...]] = set()
        try:
            with workspace_mutation_lock(self.root):
                for record in records:
                    identity = (
                        record.project_source_id,
                        record.transcript_revision_id,
                        record.evidence_set_id,
                        record.target_kind,
                        record.passage_id,
                        record.cunit_id,
                        record.start_offset,
                        record.end_offset,
                    )
                    if identity in validated:
                        continue
                    self._resolve_target(
                        project_source_id=record.project_source_id,
                        transcript_revision_id=record.transcript_revision_id,
                        evidence_set_id=record.evidence_set_id,
                        target_kind=record.target_kind,
                        passage_id=record.passage_id,
                        cunit_id=record.cunit_id,
                        start_offset=record.start_offset,
                        end_offset=record.end_offset,
                        missing_is_not_found=False,
                    )
                    validated.add(identity)
        except CodingReferenceConflictError:
            raise
        except (OSError, sqlite3.Error, TypeError, ValueError, KeyError) as exc:
            raise CodingReferenceConflictError(
                "Evidence target storage is unavailable or invalid"
            ) from exc

    def _resolve_target(
        self,
        *,
        project_source_id: str,
        transcript_revision_id: str,
        evidence_set_id: str,
        target_kind: str,
        passage_id: str,
        cunit_id: str,
        start_offset: int,
        end_offset: int,
        missing_is_not_found: bool,
    ) -> None:
        try:
            with workspace_mutation_lock(self.root):
                resolved = EvidenceTargetRegistry(self.root).resolve(
                    workspace_id=self.project_id,
                    project_source_id=project_source_id,
                    transcript_revision_id=transcript_revision_id,
                    evidence_set_id=evidence_set_id,
                    passage_id=passage_id,
                    cunit_id=cunit_id,
                )
        except EvidenceTargetNotFoundError as exc:
            if missing_is_not_found:
                raise CodingReferenceNotFoundError(
                    "Evidence target not found"
                ) from exc
            raise CodingReferenceConflictError(
                "Stored coding reference evidence is unavailable"
            ) from exc
        except EvidenceTargetConflictError as exc:
            raise CodingReferenceConflictError(
                "Evidence target storage is unavailable or invalid"
            ) from exc
        except SchemaCompatibilityError as exc:
            raise CodingReferenceConflictError(
                "Evidence target schema is unsupported"
            ) from exc
        except (OSError, sqlite3.Error, TypeError, ValueError, KeyError) as exc:
            raise CodingReferenceConflictError(
                "Evidence target storage is unavailable or invalid"
            ) from exc

        expected = {
            "workspace_id": self.project_id,
            "project_source_id": project_source_id,
            "transcript_revision_id": transcript_revision_id,
            "evidence_set_id": evidence_set_id,
            "target_kind": target_kind,
            "passage_id": passage_id,
            "cunit_id": cunit_id,
        }
        for field_name, expected_value in expected.items():
            actual_value = getattr(resolved, field_name, None)
            if not isinstance(actual_value, str) or actual_value != expected_value:
                raise CodingReferenceConflictError(
                    "Resolved evidence target identity is invalid"
                )
        text = getattr(resolved, "text", None)
        if not isinstance(text, str):
            raise CodingReferenceConflictError(
                "Resolved evidence target content is invalid"
            )
        if not (0 <= start_offset < end_offset <= len(text)):
            if missing_is_not_found:
                raise CodingReferenceValidationError(
                    "Coding reference offsets are outside the evidence target"
                )
            raise CodingReferenceConflictError(
                "Stored coding reference offsets are outside the evidence target"
            )
        if not text[start_offset:end_offset]:
            if missing_is_not_found:
                raise CodingReferenceValidationError(
                    "Coding reference selection must be non-empty"
                )
            raise CodingReferenceConflictError(
                "Stored coding reference selection is empty"
            )


def _input_entity_id(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not _ENTITY_ID.fullmatch(value)
        or value != value.strip()
    ):
        raise CodingReferenceValidationError(
            f"{field_name} must be a stable lowercase identifier"
        )
    return value


def _input_coding_reference_id(value: object) -> str:
    if not isinstance(value, str) or not _CODING_REFERENCE_ID.fullmatch(value):
        raise CodingReferenceValidationError(
            "coding_reference_id must be a stable coding reference identifier"
        )
    return value


def _input_external_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_EXTERNAL_ID_LENGTH
        or value != value.strip()
    ):
        raise CodingReferenceValidationError(
            "project_source_id must be a non-empty exact identifier"
        )
    return value


def _input_evidence_id(value: object, field_name: str) -> str:
    pattern = _EVIDENCE_IDS[field_name]
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise CodingReferenceValidationError(f"{field_name} is invalid")
    return value


def _input_target_kind(value: object) -> str:
    if not isinstance(value, str) or value not in _TARGET_KINDS:
        raise CodingReferenceValidationError("target_kind is invalid")
    return value


def _input_cunit_id(value: object, target_kind: str) -> str:
    if not isinstance(value, str):
        raise CodingReferenceValidationError("cunit_id must be a string")
    if target_kind == "passage":
        if value != "":
            raise CodingReferenceValidationError(
                "Passage coding must not include cunit_id"
            )
        return value
    return _input_evidence_id(value, "cunit_id")


def _input_offsets(start_offset: object, end_offset: object) -> tuple[int, int]:
    if (
        type(start_offset) is not int
        or type(end_offset) is not int
        or start_offset < 0
        or end_offset <= start_offset
    ):
        raise CodingReferenceValidationError(
            "Coding reference offsets must define a positive integer span"
        )
    return start_offset, end_offset


def _stored_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise CodingReferenceConflictError(
            f"Stored coding reference {field_name} is invalid"
        )
    return value


def _stored_entity_id(value: object, field_name: str) -> str:
    stored = _stored_text(value, field_name)
    if not _ENTITY_ID.fullmatch(stored) or stored != stored.strip():
        raise CodingReferenceConflictError(
            f"Stored coding reference {field_name} is invalid"
        )
    return stored


def _stored_coding_reference_id(value: object) -> str:
    stored = _stored_text(value, "coding_reference_id")
    if not _CODING_REFERENCE_ID.fullmatch(stored):
        raise CodingReferenceConflictError(
            "Stored coding reference identity is invalid"
        )
    return stored


def _stored_external_id(value: object) -> str:
    stored = _stored_text(value, "project_source_id")
    if (
        not stored
        or len(stored) > _MAX_EXTERNAL_ID_LENGTH
        or stored != stored.strip()
    ):
        raise CodingReferenceConflictError(
            "Stored coding reference project_source_id is invalid"
        )
    return stored


def _stored_evidence_id(value: object, field_name: str) -> str:
    stored = _stored_text(value, field_name)
    if not _EVIDENCE_IDS[field_name].fullmatch(stored):
        raise CodingReferenceConflictError(
            f"Stored coding reference {field_name} is invalid"
        )
    return stored


def _stored_target_kind(value: object) -> str:
    stored = _stored_text(value, "target_kind")
    if stored not in _TARGET_KINDS:
        raise CodingReferenceConflictError(
            "Stored coding reference target_kind is invalid"
        )
    return stored


def _stored_integer(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise CodingReferenceConflictError(
            f"Stored coding reference {field_name} is invalid"
        )
    return value


def _stored_timestamp(value: object, field_name: str) -> str:
    stored = _stored_text(value, field_name)
    if (
        not stored
        or len(stored) > _MAX_TIMESTAMP_LENGTH
        or stored != stored.strip()
    ):
        raise CodingReferenceConflictError(
            f"Stored coding reference {field_name} is invalid"
        )
    normalized = f"{stored[:-1]}+00:00" if stored.endswith("Z") else stored
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise CodingReferenceConflictError(
            f"Stored coding reference {field_name} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CodingReferenceConflictError(
            f"Stored coding reference {field_name} is invalid"
        )
    return stored


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
