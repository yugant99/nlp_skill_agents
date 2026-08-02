from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.qualitative.database import (
    QualitativeDatabaseConflict,
    QualitativeProjectDatabase,
    new_qualitative_id,
)
from backend.storage.evidence_catalog import EvidenceCatalog
from backend.storage.evidence_target_registry import (
    EvidenceTargetConflictError,
    EvidenceTargetNotFoundError,
    EvidenceTargetRegistry,
)
from backend.storage.sqlite_migrations import SchemaCompatibilityError
from backend.storage.study_batch_operation_store import StudyBatchOperationConflict
from backend.storage.workspace_lock import workspace_mutation_lock


_ENTITY_ID = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_NOTE_IDS = {
    "memo": re.compile(r"^mem_[0-9a-f]{32}$"),
    "annotation": re.compile(r"^ann_[0-9a-f]{32}$"),
}
_NOTE_REVISION_ID = re.compile(r"^nrv_[0-9a-f]{32}$")
_AUDIT_EVENT_ID = re.compile(r"^qae_[0-9a-f]{32}$")
_EVIDENCE_IDS = {
    "transcript_revision_id": re.compile(r"^trv_[0-9a-f]{32}$"),
    "evidence_set_id": re.compile(r"^evs_[0-9a-f]{32}$"),
    "passage_id": re.compile(r"^psg_[0-9a-f]{32}$"),
    "cunit_id": re.compile(r"^cun_[0-9a-f]{32}$"),
}
_NOTE_KINDS = {"memo", "annotation"}
_TARGET_KINDS = {"study", "source", "case", "code", "excerpt"}
_EXCERPT_TARGET_KINDS = {"passage", "cunit"}
_MAX_EXTERNAL_ID_LENGTH = 256
_MAX_TIMESTAMP_LENGTH = 64
_MAX_TITLE_LENGTH = 512
_MAX_BODY_LENGTH = 262_144
_MAX_PROJECT_CONTENT_BYTES = 256 * 1024 * 1024
_MAX_PAGE_SIZE = 50
_MAX_AUDIT_METADATA_LENGTH = 1024


class NoteValidationError(ValueError):
    pass


class NoteNotFoundError(LookupError):
    pass


class NoteConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class NoteTarget:
    kind: str
    project_source_id: str | None = None
    case_id: str | None = None
    codebook_version_id: str | None = None
    code_id: str | None = None
    transcript_revision_id: str | None = None
    evidence_set_id: str | None = None
    excerpt_target_kind: str | None = None
    passage_id: str | None = None
    cunit_id: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None


@dataclass(frozen=True)
class NoteRecord:
    note_id: str
    note_kind: str
    project_id: str
    target: NoteTarget
    created_by: str
    created_at: str
    removed_by: str | None
    removed_at: str | None


@dataclass(frozen=True)
class NoteRevisionRecord:
    note_revision_id: str
    note_id: str
    revision_number: int
    title: str
    body: str
    created_by: str
    created_at: str


@dataclass(frozen=True)
class NoteSnapshot:
    note: NoteRecord
    current_revision: NoteRevisionRecord


class NoteService:
    def __init__(self, root: Path | str, project_id: str) -> None:
        try:
            self.database = QualitativeProjectDatabase(root, project_id)
        except (TypeError, ValueError) as exc:
            raise NoteValidationError("Invalid qualitative project id") from exc
        self.root = Path(root)
        self.project_id = self.database.project_id

    def create_note(
        self,
        *,
        note_kind: str,
        researcher_id: str,
        title: str,
        body: str,
        target: Mapping[str, object],
    ) -> NoteSnapshot:
        normalized_kind = _input_note_kind(note_kind)
        actor_id = _input_entity_id(researcher_id, "researcher_id")
        normalized_title = _input_title(normalized_kind, title)
        normalized_body = _input_body(body)
        normalized_target = _input_target(target)

        self._validate_external_targets(
            (normalized_target,),
            missing_is_not_found=True,
        )

        note_id = new_qualitative_id(normalized_kind)
        revision_id = new_qualitative_id("note_revision")
        now = _utc_now()
        with self._write() as connection:
            self._require_project(connection)
            self._validate_project_content_budget(connection)
            self._require_active_researcher(connection, actor_id)
            self._validate_target_local(
                connection,
                normalized_target,
                missing_is_not_found=True,
            )
            self._require_content_capacity(
                connection,
                normalized_title,
                normalized_body,
            )
            connection.execute(
                """
                insert into qualitative_notes (
                  note_id, project_id, note_kind, target_kind,
                  project_source_id, case_id, codebook_version_id, code_id,
                  transcript_revision_id, evidence_set_id, excerpt_target_kind,
                  passage_id, cunit_id, start_offset, end_offset,
                  created_by, created_at, removed_by, removed_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, null, null)
                """,
                (
                    note_id,
                    self.project_id,
                    normalized_kind,
                    normalized_target.kind,
                    normalized_target.project_source_id,
                    normalized_target.case_id,
                    normalized_target.codebook_version_id,
                    normalized_target.code_id,
                    normalized_target.transcript_revision_id,
                    normalized_target.evidence_set_id,
                    normalized_target.excerpt_target_kind,
                    normalized_target.passage_id,
                    normalized_target.cunit_id,
                    normalized_target.start_offset,
                    normalized_target.end_offset,
                    actor_id,
                    now,
                ),
            )
            connection.execute(
                """
                insert into qualitative_note_revisions (
                  note_revision_id, project_id, note_id, revision_number,
                  title, body, created_by, created_at
                ) values (?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    self.project_id,
                    note_id,
                    normalized_title,
                    normalized_body,
                    actor_id,
                    now,
                ),
            )
            self._append_audit(
                connection,
                actor_id=actor_id,
                event_type=f"{normalized_kind}.created",
                note_kind=normalized_kind,
                note_id=note_id,
                metadata={
                    "note_revision_id": revision_id,
                    "revision_number": 1,
                    "target_kind": normalized_target.kind,
                },
                created_at=now,
            )
            self._validate_project_content_budget(connection)
            return self._require_note(connection, normalized_kind, note_id)

    def read_note(self, note_kind: str, note_id: str) -> NoteSnapshot:
        normalized_kind = _input_note_kind(note_kind)
        normalized_id = _input_note_id(normalized_kind, note_id)
        with self._read() as connection:
            self._require_project(connection)
            self._validate_project_content_budget(connection)
            snapshot = self._require_note(
                connection,
                normalized_kind,
                normalized_id,
            )
        self._validate_external_targets(
            (snapshot.note.target,),
            missing_is_not_found=False,
        )
        return snapshot

    def list_notes(
        self,
        note_kind: str,
        *,
        target_kind: str | None = None,
        created_by: str | None = None,
        include_removed: bool = False,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[tuple[NoteSnapshot, ...], str | None]:
        normalized_kind = _input_note_kind(note_kind)
        normalized_target_kind = (
            None if target_kind is None else _input_target_kind_filter(target_kind)
        )
        normalized_creator = (
            None
            if created_by is None
            else _input_entity_id(created_by, "created_by")
        )
        if type(include_removed) is not bool:
            raise NoteValidationError("include_removed must be a boolean")
        normalized_limit = _input_limit(limit)
        normalized_cursor = (
            None
            if cursor is None
            else _input_note_cursor(cursor)
        )

        filters = ["project_id = ?", "note_kind = ?"]
        parameters: list[object] = [self.project_id, normalized_kind]
        if not include_removed:
            filters.append("removed_at is null")
        if normalized_target_kind is not None:
            filters.append("target_kind = ?")
            parameters.append(normalized_target_kind)
        if normalized_creator is not None:
            filters.append("created_by = ?")
            parameters.append(normalized_creator)

        with self._read() as connection:
            self._require_project(connection)
            self._validate_project_content_budget(connection)
            if normalized_cursor is not None:
                cursor_snapshot = self._require_note(
                    connection,
                    normalized_kind,
                    normalized_cursor,
                )
                filters.append("(created_at > ? or (created_at = ? and note_id > ?))")
                parameters.extend(
                    (
                        cursor_snapshot.note.created_at,
                        cursor_snapshot.note.created_at,
                        normalized_cursor,
                    )
                )
            rows = connection.execute(
                "select * from qualitative_notes where "
                + " and ".join(filters)
                + " order by created_at, note_id limit ?",
                (*parameters, normalized_limit + 1),
            ).fetchall()
            all_snapshots = tuple(
                self._snapshot_from_note_row(connection, row) for row in rows
            )
            has_more = len(all_snapshots) > normalized_limit
            snapshots = all_snapshots[:normalized_limit]
        self._validate_external_targets(
            tuple(snapshot.note.target for snapshot in snapshots),
            missing_is_not_found=False,
        )
        next_cursor = snapshots[-1].note.note_id if has_more and snapshots else None
        return snapshots, next_cursor

    def revise_note(
        self,
        *,
        note_kind: str,
        note_id: str,
        researcher_id: str,
        expected_revision_number: int,
        title: str,
        body: str,
    ) -> NoteSnapshot:
        normalized_kind = _input_note_kind(note_kind)
        normalized_id = _input_note_id(normalized_kind, note_id)
        actor_id = _input_entity_id(researcher_id, "researcher_id")
        expected = _input_expected_revision(expected_revision_number)
        normalized_title = _input_title(normalized_kind, title)
        normalized_body = _input_body(body)

        with self._read() as connection:
            self._require_project(connection)
            self._validate_project_content_budget(connection)
            initial = self._require_note(
                connection,
                normalized_kind,
                normalized_id,
            )
        self._validate_external_targets(
            (initial.note.target,),
            missing_is_not_found=False,
        )

        with self._write() as connection:
            self._require_project(connection)
            self._validate_project_content_budget(connection)
            current = self._require_note(
                connection,
                normalized_kind,
                normalized_id,
            )
            if current.note.target != initial.note.target:
                raise NoteConflictError("Stored note target changed during revision")
            self._validate_target_local(
                connection,
                current.note.target,
                missing_is_not_found=False,
            )
            if current.note.removed_at is not None:
                raise NoteConflictError("Removed note cannot be revised")

            newest = current.current_revision
            same_content = (
                newest.title == normalized_title and newest.body == normalized_body
            )
            if (
                newest.revision_number == expected + 1
                and newest.created_by == actor_id
                and same_content
            ):
                return current
            self._require_active_researcher(connection, actor_id)
            if newest.revision_number == expected:
                if same_content:
                    raise NoteValidationError("Note revision content is unchanged")
            elif newest.revision_number == expected + 1:
                raise NoteConflictError("Note revision retry conflicts with stored state")
            else:
                raise NoteConflictError("Note revision is stale")

            next_number = expected + 1
            revision_id = new_qualitative_id("note_revision")
            now = _utc_now()
            _, newest_instant = _stored_timestamp_with_instant(
                newest.created_at,
                "revision created_at",
            )
            now_instant = datetime.fromisoformat(now)
            if now_instant < newest_instant:
                raise NoteConflictError("Note revision timestamp is not monotonic")
            self._require_content_capacity(
                connection,
                normalized_title,
                normalized_body,
            )
            connection.execute(
                """
                insert into qualitative_note_revisions (
                  note_revision_id, project_id, note_id, revision_number,
                  title, body, created_by, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    self.project_id,
                    normalized_id,
                    next_number,
                    normalized_title,
                    normalized_body,
                    actor_id,
                    now,
                ),
            )
            self._append_audit(
                connection,
                actor_id=actor_id,
                event_type=f"{normalized_kind}.revised",
                note_kind=normalized_kind,
                note_id=normalized_id,
                metadata={
                    "note_revision_id": revision_id,
                    "revision_number": next_number,
                },
                created_at=now,
            )
            self._validate_project_content_budget(connection)
            return self._require_note(
                connection,
                normalized_kind,
                normalized_id,
            )

    def list_revisions(
        self,
        note_kind: str,
        note_id: str,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[tuple[NoteRevisionRecord, ...], str | None]:
        normalized_kind = _input_note_kind(note_kind)
        normalized_id = _input_note_id(normalized_kind, note_id)
        normalized_limit = _input_limit(limit)
        normalized_cursor = (
            None if cursor is None else _input_revision_id(cursor, "cursor")
        )
        with self._read() as connection:
            self._require_project(connection)
            self._validate_project_content_budget(connection)
            snapshot = self._require_note(
                connection,
                normalized_kind,
                normalized_id,
            )
            cursor_parameters: tuple[object, ...] = ()
            cursor_filter = ""
            if normalized_cursor is not None:
                cursor_row = connection.execute(
                    """
                    select * from qualitative_note_revisions
                    where project_id = ? and note_id = ?
                      and note_revision_id = ?
                    """,
                    (self.project_id, normalized_id, normalized_cursor),
                ).fetchone()
                if cursor_row is None:
                    raise NoteNotFoundError("Note revision cursor not found")
                cursor_revision = self._revision_record(
                    cursor_row,
                    normalized_kind,
                )
                if (
                    cursor_revision.note_id != normalized_id
                    or cursor_revision.note_revision_id != normalized_cursor
                ):
                    raise NoteConflictError("Stored note revision cursor is invalid")
                cursor_filter = """
                  and (
                    revision_number > ?
                    or (
                      revision_number = ? and note_revision_id > ?
                    )
                  )
                """
                cursor_parameters = (
                    cursor_revision.revision_number,
                    cursor_revision.revision_number,
                    normalized_cursor,
                )
            page_rows = connection.execute(
                """
                select * from qualitative_note_revisions
                where project_id = ? and note_id = ?
                """
                + cursor_filter
                + " order by revision_number, note_revision_id limit ?",
                (
                    self.project_id,
                    normalized_id,
                    *cursor_parameters,
                    normalized_limit + 1,
                ),
            ).fetchall()
            candidates = tuple(
                self._revision_record(row, normalized_kind) for row in page_rows
            )
            has_more = len(candidates) > normalized_limit
            page = tuple(candidates[:normalized_limit])
        self._validate_external_targets(
            (snapshot.note.target,),
            missing_is_not_found=False,
        )
        next_cursor = page[-1].note_revision_id if has_more and page else None
        return page, next_cursor

    def remove_note(
        self,
        *,
        note_kind: str,
        note_id: str,
        researcher_id: str,
    ) -> NoteSnapshot:
        normalized_kind = _input_note_kind(note_kind)
        normalized_id = _input_note_id(normalized_kind, note_id)
        actor_id = _input_entity_id(researcher_id, "researcher_id")
        with self._write() as connection:
            self._require_project(connection)
            self._validate_project_content_budget(connection)
            current = self._require_note(
                connection,
                normalized_kind,
                normalized_id,
            )
            if current.note.removed_at is not None:
                if current.note.removed_by == actor_id:
                    return current
                self._require_active_researcher(connection, actor_id)
                raise NoteConflictError("Note was removed by another researcher")
            self._require_active_researcher(connection, actor_id)
            self._validate_target_local(
                connection,
                current.note.target,
                missing_is_not_found=False,
            )
            now = _utc_now()
            _, newest_instant = _stored_timestamp_with_instant(
                current.current_revision.created_at,
                "revision created_at",
            )
            now_instant = datetime.fromisoformat(now)
            if now_instant < newest_instant:
                raise NoteConflictError("Note removal timestamp is not monotonic")
            updated = connection.execute(
                """
                update qualitative_notes
                set removed_by = ?, removed_at = ?
                where project_id = ? and note_id = ?
                  and removed_by is null and removed_at is null
                """,
                (actor_id, now, self.project_id, normalized_id),
            ).rowcount
            if updated != 1:
                raise NoteConflictError("Note removal conflicted with stored state")
            self._append_audit(
                connection,
                actor_id=actor_id,
                event_type=f"{normalized_kind}.removed",
                note_kind=normalized_kind,
                note_id=normalized_id,
                metadata={},
                created_at=now,
            )
            return self._require_note(
                connection,
                normalized_kind,
                normalized_id,
            )

    def validate_project_state(self) -> None:
        with self._read() as connection:
            self._require_project(connection)
            self._validate_project_content_budget(connection)
            rows = connection.execute(
                """
                select * from qualitative_notes
                where project_id = ? order by created_at, note_id
                """,
                (self.project_id,),
            ).fetchall()
            snapshots = tuple(
                self._snapshot_from_note_row(connection, row) for row in rows
            )
            self._reject_unmatched_note_audits(connection, snapshots)
        self._validate_external_targets(
            tuple(snapshot.note.target for snapshot in snapshots),
            missing_is_not_found=False,
        )

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        try:
            with self.database.read() as connection:
                connection.row_factory = sqlite3.Row
                yield connection
        except FileNotFoundError as exc:
            raise NoteNotFoundError("Qualitative project not found") from exc
        except (
            QualitativeDatabaseConflict,
            SchemaCompatibilityError,
            StudyBatchOperationConflict,
        ) as exc:
            raise NoteConflictError(
                "Qualitative note storage is unavailable or corrupt"
            ) from exc
        except sqlite3.DatabaseError as exc:
            raise NoteConflictError(
                "Qualitative note storage is unavailable or corrupt"
            ) from exc

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        try:
            with self.database.transaction() as connection:
                connection.row_factory = sqlite3.Row
                yield connection
        except FileNotFoundError as exc:
            raise NoteNotFoundError("Qualitative project not found") from exc
        except sqlite3.IntegrityError as exc:
            raise NoteConflictError("Qualitative note storage constraint conflict") from exc
        except (
            QualitativeDatabaseConflict,
            SchemaCompatibilityError,
            StudyBatchOperationConflict,
        ) as exc:
            raise NoteConflictError(
                "Qualitative note storage is unavailable or corrupt"
            ) from exc
        except sqlite3.DatabaseError as exc:
            raise NoteConflictError(
                "Qualitative note storage is unavailable or corrupt"
            ) from exc

    def _require_project(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "select project_id from qualitative_projects where project_id = ?",
            (self.project_id,),
        ).fetchone()
        if row is None:
            raise NoteNotFoundError("Qualitative project is not initialized")
        if _stored_text(row["project_id"], "project_id") != self.project_id:
            raise NoteConflictError("Stored qualitative project is invalid")

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
            raise NoteNotFoundError("Researcher not found")
        self._validate_actor_row(row, researcher_id)
        if row["active"] != 1:
            raise NoteConflictError("Researcher is inactive")

    def _validate_actor_row(
        self,
        row: sqlite3.Row,
        actor_id: str,
    ) -> None:
        if (
            _stored_text(row["project_id"], "actor project_id") != self.project_id
            or _stored_entity_id(row["researcher_id"], "researcher_id") != actor_id
            or type(row["active"]) is not int
            or row["active"] not in (0, 1)
        ):
            raise NoteConflictError("Stored note actor is invalid")

    def _validate_actor(self, connection: sqlite3.Connection, actor_id: str) -> None:
        row = connection.execute(
            """
            select project_id, researcher_id, active from researchers
            where project_id = ? and researcher_id = ?
            """,
            (self.project_id, actor_id),
        ).fetchone()
        if row is None:
            raise NoteConflictError("Stored note actor is unavailable")
        self._validate_actor_row(row, actor_id)

    def _validate_target_local(
        self,
        connection: sqlite3.Connection,
        target: NoteTarget,
        *,
        missing_is_not_found: bool,
    ) -> None:
        if target.kind in {"study", "source", "excerpt"}:
            return
        if target.kind == "case":
            row = connection.execute(
                """
                select project_id, case_id from cases
                where project_id = ? and case_id = ?
                """,
                (self.project_id, target.case_id),
            ).fetchone()
            if row is None:
                if missing_is_not_found:
                    raise NoteNotFoundError("Case not found")
                raise NoteConflictError("Stored note case is unavailable")
            if (
                _stored_text(row["project_id"], "case project_id")
                != self.project_id
                or _stored_entity_id(row["case_id"], "case_id") != target.case_id
            ):
                raise NoteConflictError("Stored note case is invalid")
            return
        if target.kind != "code":
            raise NoteConflictError("Stored note target is invalid")
        self._require_frozen_code(
            connection,
            codebook_version_id=target.codebook_version_id,
            code_id=target.code_id,
            missing_is_not_found=missing_is_not_found,
        )

    def _require_frozen_code(
        self,
        connection: sqlite3.Connection,
        *,
        codebook_version_id: str | None,
        code_id: str | None,
        missing_is_not_found: bool,
    ) -> None:
        assert codebook_version_id is not None
        assert code_id is not None
        version = connection.execute(
            """
            select project_id, codebook_version_id, status
            from codebook_versions
            where project_id = ? and codebook_version_id = ?
            """,
            (self.project_id, codebook_version_id),
        ).fetchone()
        if version is None:
            if missing_is_not_found:
                raise NoteNotFoundError("Codebook version not found")
            raise NoteConflictError("Stored note codebook version is unavailable")
        if (
            _stored_text(version["project_id"], "codebook version project_id")
            != self.project_id
            or _stored_entity_id(
                version["codebook_version_id"],
                "codebook_version_id",
            )
            != codebook_version_id
        ):
            raise NoteConflictError("Stored note codebook version is invalid")
        status = _stored_text(version["status"], "codebook version status")
        if status not in {"draft", "frozen"}:
            raise NoteConflictError("Stored note codebook version is invalid")
        if status != "frozen":
            raise NoteConflictError("Note code target requires a frozen version")
        code = connection.execute(
            """
            select project_id, codebook_version_id, code_id from codes
            where project_id = ? and codebook_version_id = ? and code_id = ?
            """,
            (self.project_id, codebook_version_id, code_id),
        ).fetchone()
        if code is None:
            conflicting = connection.execute(
                "select project_id, codebook_version_id from codes where code_id = ?",
                (code_id,),
            ).fetchone()
            if conflicting is not None:
                raise NoteConflictError("Code belongs to another version")
            if missing_is_not_found:
                raise NoteNotFoundError("Code not found")
            raise NoteConflictError("Stored note code is unavailable")
        if (
            _stored_text(code["project_id"], "code project_id") != self.project_id
            or _stored_entity_id(
                code["codebook_version_id"],
                "codebook_version_id",
            )
            != codebook_version_id
            or _stored_entity_id(code["code_id"], "code_id") != code_id
        ):
            raise NoteConflictError("Stored note code is invalid")

    def _require_note(
        self,
        connection: sqlite3.Connection,
        note_kind: str,
        note_id: str,
    ) -> NoteSnapshot:
        row = connection.execute(
            """
            select * from qualitative_notes
            where project_id = ? and note_id = ?
            """,
            (self.project_id, note_id),
        ).fetchone()
        if row is None:
            raise NoteNotFoundError("Note not found")
        snapshot = self._snapshot_from_note_row(connection, row)
        if snapshot.note.note_kind != note_kind:
            raise NoteNotFoundError("Note not found for requested kind")
        return snapshot

    def _snapshot_from_note_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> NoteSnapshot:
        note = self._note_record(row)
        revision_cursor = connection.execute(
            """
            select * from qualitative_note_revisions
            where project_id = ? and note_id = ?
            order by revision_number, note_revision_id
            """,
            (self.project_id, note.note_id),
        )
        audit_cursor = connection.execute(
            """
            select * from qualitative_audit_events
            where project_id = ?
              and (
                subject_id = ?
                or (
                  typeof(subject_id) != 'text'
                  and cast(subject_id as text) = ?
                )
              )
            order by
              case event_type
                when ? then 0
                when ? then 1
                when ? then 2
                else 3
              end,
              case
                when typeof(metadata_json) = 'text'
                  and json_valid(metadata_json)
                then coalesce(
                  cast(json_extract(metadata_json, '$.revision_number') as integer),
                  -1
                )
                else -1
              end,
              created_at,
              event_id
            """,
            (
                self.project_id,
                note.note_id,
                note.note_id,
                f"{note.note_kind}.created",
                f"{note.note_kind}.revised",
                f"{note.note_kind}.removed",
            ),
        )
        first: NoteRevisionRecord | None = None
        latest: NoteRevisionRecord | None = None
        previous_instant: datetime | None = None
        validated_actor_ids: set[str] = set()
        for expected_number, revision_row in enumerate(revision_cursor, start=1):
            revision = self._revision_record(revision_row, note.note_kind)
            if (
                revision.note_id != note.note_id
                or revision.revision_number != expected_number
            ):
                raise NoteConflictError("Stored note revision history is invalid")
            if revision.created_by not in validated_actor_ids:
                self._validate_actor(connection, revision.created_by)
                validated_actor_ids.add(revision.created_by)
            _, instant = _stored_timestamp_with_instant(
                revision.created_at,
                "revision created_at",
            )
            if previous_instant is not None and instant < previous_instant:
                raise NoteConflictError("Stored note revision chronology is invalid")
            previous_instant = instant
            if first is None:
                first = revision
            latest = revision
            event_type = (
                f"{note.note_kind}.created"
                if revision.revision_number == 1
                else f"{note.note_kind}.revised"
            )
            metadata: dict[str, object] = {
                "note_revision_id": revision.note_revision_id,
                "revision_number": revision.revision_number,
            }
            if revision.revision_number == 1:
                metadata["target_kind"] = note.target.kind
            self._require_matching_note_audit(
                audit_cursor.fetchone(),
                note,
                (
                    event_type,
                    revision.created_by,
                    revision.created_at,
                    _canonical_json(metadata),
                ),
            )
        if first is None or latest is None:
            raise NoteConflictError("Stored note revision history is missing")
        if first.created_by != note.created_by or first.created_at != note.created_at:
            raise NoteConflictError("Stored initial note revision is invalid")
        if note.created_by not in validated_actor_ids:
            self._validate_actor(connection, note.created_by)
            validated_actor_ids.add(note.created_by)
        if note.removed_at is not None:
            _, removed_instant = _stored_timestamp_with_instant(
                note.removed_at,
                "removed_at",
            )
            if previous_instant is not None and removed_instant < previous_instant:
                raise NoteConflictError("Stored note removal chronology is invalid")
            assert note.removed_by is not None
            if note.removed_by not in validated_actor_ids:
                self._validate_actor(connection, note.removed_by)
            self._require_matching_note_audit(
                audit_cursor.fetchone(),
                note,
                (
                    f"{note.note_kind}.removed",
                    note.removed_by,
                    note.removed_at,
                    "{}",
                ),
            )
        if audit_cursor.fetchone() is not None:
            raise NoteConflictError("Stored note audit history is invalid")

        self._validate_target_local(
            connection,
            note.target,
            missing_is_not_found=False,
        )
        return NoteSnapshot(note=note, current_revision=latest)

    def _note_record(self, row: sqlite3.Row) -> NoteRecord:
        note_kind = _stored_note_kind(row["note_kind"])
        note_id = _stored_note_id(row["note_id"], note_kind)
        project_id = _stored_text(row["project_id"], "project_id")
        if project_id != self.project_id:
            raise NoteConflictError("Stored note belongs to another project")
        target = _stored_target(row)
        created_by = _stored_entity_id(row["created_by"], "created_by")
        created_at, created_instant = _stored_timestamp_with_instant(
            row["created_at"],
            "created_at",
        )
        removed_by = _stored_optional_entity_id(row["removed_by"], "removed_by")
        removed_at: str | None = None
        removed_instant: datetime | None = None
        if row["removed_at"] is not None:
            removed_at, removed_instant = _stored_timestamp_with_instant(
                row["removed_at"],
                "removed_at",
            )
        if (removed_by is None) != (removed_at is None):
            raise NoteConflictError("Stored note tombstone is invalid")
        if removed_instant is not None and removed_instant < created_instant:
            raise NoteConflictError("Stored note removal timestamp is invalid")
        return NoteRecord(
            note_id=note_id,
            note_kind=note_kind,
            project_id=project_id,
            target=target,
            created_by=created_by,
            created_at=created_at,
            removed_by=removed_by,
            removed_at=removed_at,
        )

    def _revision_record(
        self,
        row: sqlite3.Row,
        note_kind: str,
    ) -> NoteRevisionRecord:
        project_id = _stored_text(row["project_id"], "revision project_id")
        if project_id != self.project_id:
            raise NoteConflictError("Stored note revision belongs to another project")
        note_id = _stored_note_id(row["note_id"], note_kind)
        revision_number = _stored_positive_integer(
            row["revision_number"],
            "revision_number",
        )
        title = _stored_title(note_kind, row["title"])
        body = _stored_body(row["body"])
        return NoteRevisionRecord(
            note_revision_id=_stored_revision_id(row["note_revision_id"]),
            note_id=note_id,
            revision_number=revision_number,
            title=title,
            body=body,
            created_by=_stored_entity_id(row["created_by"], "revision created_by"),
            created_at=_stored_timestamp(row["created_at"], "revision created_at"),
        )

    def _require_matching_note_audit(
        self,
        row: sqlite3.Row | None,
        note: NoteRecord,
        expected: tuple[str, str, str, str],
    ) -> None:
        if row is None:
            raise NoteConflictError("Stored note audit history is invalid")
        if not _AUDIT_EVENT_ID.fullmatch(
            _stored_text(row["event_id"], "audit event_id")
        ):
            raise NoteConflictError("Stored note audit history is invalid")
        if (
            _stored_text(row["project_id"], "audit project_id") != self.project_id
            or _stored_text(row["subject_type"], "audit subject_type")
            != note.note_kind
            or _stored_text(row["subject_id"], "audit subject_id") != note.note_id
        ):
            raise NoteConflictError("Stored note audit history is invalid")
        event_type = _stored_text(row["event_type"], "audit event_type")
        if event_type not in {
            f"{note.note_kind}.created",
            f"{note.note_kind}.revised",
            f"{note.note_kind}.removed",
        }:
            raise NoteConflictError("Stored note audit history is invalid")
        actual = (
            event_type,
            _stored_entity_id(row["actor_id"], "audit actor_id"),
            _stored_timestamp(row["created_at"], "audit created_at"),
            _stored_canonical_json(row["metadata_json"]),
        )
        if actual != expected:
            raise NoteConflictError("Stored note audit history is invalid")

    def _reject_unmatched_note_audits(
        self,
        connection: sqlite3.Connection,
        snapshots: Sequence[NoteSnapshot],
    ) -> None:
        known = {snapshot.note.note_id for snapshot in snapshots}
        if len(known) != len(snapshots):
            raise NoteConflictError("Stored notes contain duplicate identities")
        rows = connection.execute(
            """
            select subject_id, subject_type, event_type
            from qualitative_audit_events
            where project_id = ? order by event_id
            """,
            (self.project_id,),
        ).fetchall()
        for row in rows:
            subject_value = row["subject_id"]
            subject_type = row["subject_type"]
            event_type = row["event_type"]
            is_candidate = (
                _has_note_subject_prefix(subject_value)
                or _has_note_subject_type(subject_type)
                or _has_note_event_prefix(event_type)
            )
            if not is_candidate:
                continue
            subject_id = _stored_text(subject_value, "audit subject_id")
            if subject_id not in known:
                raise NoteConflictError("Stored note audit history is unmatched")

    def _append_audit(
        self,
        connection: sqlite3.Connection,
        *,
        actor_id: str,
        event_type: str,
        note_kind: str,
        note_id: str,
        metadata: Mapping[str, object],
        created_at: str,
    ) -> None:
        connection.execute(
            """
            insert into qualitative_audit_events (
              event_id, project_id, actor_id, event_type,
              subject_type, subject_id, metadata_json, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_qualitative_id("audit_event"),
                self.project_id,
                actor_id,
                event_type,
                note_kind,
                note_id,
                _canonical_json(metadata),
                created_at,
            ),
        )

    def _validate_project_content_budget(
        self,
        connection: sqlite3.Connection,
    ) -> int:
        rows = connection.execute(
            """
            select n.note_kind, r.title, r.body
            from qualitative_note_revisions r
            join qualitative_notes n
              on n.project_id = r.project_id and n.note_id = r.note_id
            where r.project_id = ?
            order by r.note_id, r.revision_number, r.note_revision_id
            """,
            (self.project_id,),
        )
        total = 0
        for row in rows:
            note_kind = _stored_note_kind(row["note_kind"])
            title = _stored_title(note_kind, row["title"])
            body = _stored_body(row["body"])
            try:
                total += len(title.encode("utf-8")) + len(body.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise NoteConflictError("Stored note content is invalid") from exc
            if total > _MAX_PROJECT_CONTENT_BYTES:
                raise NoteConflictError("Stored project note content exceeds capacity")
        return total

    def _require_content_capacity(
        self,
        connection: sqlite3.Connection,
        title: str,
        body: str,
    ) -> None:
        total = self._validate_project_content_budget(connection)
        required = len(title.encode("utf-8")) + len(body.encode("utf-8"))
        if total + required > _MAX_PROJECT_CONTENT_BYTES:
            raise NoteValidationError("Project note content capacity would be exceeded")

    def _validate_external_targets(
        self,
        targets: Sequence[NoteTarget],
        *,
        missing_is_not_found: bool,
    ) -> None:
        external = tuple(
            target for target in targets if target.kind in {"source", "excerpt"}
        )
        if not external:
            return
        seen: set[tuple[object, ...]] = set()
        try:
            with workspace_mutation_lock(self.root):
                catalog = EvidenceCatalog(self.root)
                registry = EvidenceTargetRegistry(self.root)
                for target in external:
                    identity = (
                        target.kind,
                        target.project_source_id,
                        target.transcript_revision_id,
                        target.evidence_set_id,
                        target.excerpt_target_kind,
                        target.passage_id,
                        target.cunit_id,
                        target.start_offset,
                        target.end_offset,
                    )
                    if identity in seen:
                        continue
                    if target.kind == "source":
                        self._resolve_source(
                            catalog,
                            target,
                            missing_is_not_found=missing_is_not_found,
                        )
                    else:
                        self._resolve_excerpt(
                            registry,
                            target,
                            missing_is_not_found=missing_is_not_found,
                        )
                    seen.add(identity)
        except (NoteValidationError, NoteNotFoundError, NoteConflictError):
            raise
        except SchemaCompatibilityError as exc:
            raise NoteConflictError("Evidence storage schema is unsupported") from exc
        except (OSError, sqlite3.Error, TypeError, ValueError, KeyError) as exc:
            raise NoteConflictError(
                "Evidence storage is unavailable or invalid"
            ) from exc

    def _resolve_source(
        self,
        catalog: EvidenceCatalog,
        target: NoteTarget,
        *,
        missing_is_not_found: bool,
    ) -> None:
        assert target.project_source_id is not None
        try:
            history = catalog.source_history(target.project_source_id)
        except FileNotFoundError as exc:
            if missing_is_not_found:
                raise NoteNotFoundError("Evidence source not found") from exc
            raise NoteConflictError("Stored note source is unavailable") from exc
        source = history.get("source") if isinstance(history, Mapping) else None
        if not isinstance(source, Mapping):
            raise NoteConflictError("Evidence source record is invalid")
        if (
            source.get("project_source_id") != target.project_source_id
            or not isinstance(source.get("project_source_id"), str)
            or not isinstance(source.get("workspace_id"), str)
        ):
            raise NoteConflictError("Evidence source record is invalid")
        if source["workspace_id"] != self.project_id:
            raise NoteConflictError("Evidence source belongs to another project")

    def _resolve_excerpt(
        self,
        registry: EvidenceTargetRegistry,
        target: NoteTarget,
        *,
        missing_is_not_found: bool,
    ) -> None:
        assert target.project_source_id is not None
        assert target.transcript_revision_id is not None
        assert target.evidence_set_id is not None
        assert target.excerpt_target_kind is not None
        assert target.passage_id is not None
        assert target.start_offset is not None
        assert target.end_offset is not None
        try:
            resolved = registry.resolve(
                workspace_id=self.project_id,
                project_source_id=target.project_source_id,
                transcript_revision_id=target.transcript_revision_id,
                evidence_set_id=target.evidence_set_id,
                passage_id=target.passage_id,
                cunit_id=target.cunit_id or "",
            )
        except EvidenceTargetNotFoundError as exc:
            if missing_is_not_found:
                raise NoteNotFoundError("Evidence target not found") from exc
            raise NoteConflictError("Stored note evidence target is unavailable") from exc
        except EvidenceTargetConflictError as exc:
            raise NoteConflictError(
                "Evidence target storage is unavailable or invalid"
            ) from exc
        expected = {
            "workspace_id": self.project_id,
            "project_source_id": target.project_source_id,
            "transcript_revision_id": target.transcript_revision_id,
            "evidence_set_id": target.evidence_set_id,
            "target_kind": target.excerpt_target_kind,
            "passage_id": target.passage_id,
            "cunit_id": target.cunit_id or "",
        }
        for field_name, expected_value in expected.items():
            actual = getattr(resolved, field_name, None)
            if not isinstance(actual, str) or actual != expected_value:
                raise NoteConflictError("Resolved evidence target identity is invalid")
        text = getattr(resolved, "text", None)
        if not isinstance(text, str):
            raise NoteConflictError("Resolved evidence target content is invalid")
        if not (0 <= target.start_offset < target.end_offset <= len(text)):
            if missing_is_not_found:
                raise NoteValidationError("Excerpt offsets are outside the target")
            raise NoteConflictError("Stored excerpt offsets are outside the target")


def _input_note_kind(value: object) -> str:
    if not isinstance(value, str) or value not in _NOTE_KINDS:
        raise NoteValidationError("note_kind must be memo or annotation")
    return value


def _input_target_kind_filter(value: object) -> str:
    if not isinstance(value, str) or value not in _TARGET_KINDS:
        raise NoteValidationError("target_kind is invalid")
    return value


def _input_entity_id(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not _ENTITY_ID.fullmatch(value)
        or value != value.strip()
    ):
        raise NoteValidationError(
            f"{field_name} must be a stable lowercase identifier"
        )
    return value


def _input_note_id(
    note_kind: str,
    value: object,
    *,
    field_name: str = "note_id",
) -> str:
    if not isinstance(value, str) or not _NOTE_IDS[note_kind].fullmatch(value):
        raise NoteValidationError(f"{field_name} is invalid")
    return value


def _input_note_cursor(value: object) -> str:
    if not isinstance(value, str) or not any(
        pattern.fullmatch(value) for pattern in _NOTE_IDS.values()
    ):
        raise NoteValidationError("cursor is invalid")
    return value


def _input_revision_id(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _NOTE_REVISION_ID.fullmatch(value):
        raise NoteValidationError(f"{field_name} is invalid")
    return value


def _input_expected_revision(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise NoteValidationError("expected_revision_number must be positive")
    return value


def _input_limit(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_PAGE_SIZE:
        raise NoteValidationError("limit must be an integer from 1 through 50")
    return value


def _input_title(note_kind: str, value: object) -> str:
    if not isinstance(value, str):
        raise NoteValidationError("title must be a string")
    _require_valid_unicode(value, "title")
    if note_kind == "annotation":
        if value != "":
            raise NoteValidationError("Annotation title must be empty")
        return value
    normalized = value.strip()
    if not normalized:
        raise NoteValidationError("Memo title must be non-blank")
    if len(normalized) > _MAX_TITLE_LENGTH:
        raise NoteValidationError("Memo title is too long")
    return normalized


def _input_body(value: object) -> str:
    if not isinstance(value, str):
        raise NoteValidationError("body must be a string")
    _require_valid_unicode(value, "body")
    if not value.strip():
        raise NoteValidationError("body must be non-blank")
    if len(value) > _MAX_BODY_LENGTH:
        raise NoteValidationError("body is too long")
    return value


def _require_valid_unicode(value: str, field_name: str) -> None:
    if "\x00" in value or any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise NoteValidationError(f"{field_name} contains invalid Unicode")


def _input_target(value: object) -> NoteTarget:
    if not isinstance(value, Mapping):
        raise NoteValidationError("target must be an object")
    try:
        keys = set(value.keys())
    except (TypeError, ValueError) as exc:
        raise NoteValidationError("target is invalid") from exc
    if not all(isinstance(key, str) for key in keys):
        raise NoteValidationError("target keys must be strings")
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in _TARGET_KINDS:
        raise NoteValidationError("target kind is invalid")
    expected_keys = {
        "study": {"kind"},
        "source": {"kind", "project_source_id"},
        "case": {"kind", "case_id"},
        "code": {"kind", "codebook_version_id", "code_id"},
        "excerpt": {
            "kind",
            "project_source_id",
            "transcript_revision_id",
            "evidence_set_id",
            "excerpt_target_kind",
            "passage_id",
            "start_offset",
            "end_offset",
        },
    }[kind]
    if kind == "excerpt" and value.get("excerpt_target_kind") == "cunit":
        expected_keys = expected_keys | {"cunit_id"}
    if keys != expected_keys:
        raise NoteValidationError("target fields do not match target kind")
    if kind == "study":
        return NoteTarget(kind="study")
    if kind == "source":
        return NoteTarget(
            kind="source",
            project_source_id=_input_external_id(value["project_source_id"]),
        )
    if kind == "case":
        return NoteTarget(
            kind="case",
            case_id=_input_entity_id(value["case_id"], "case_id"),
        )
    if kind == "code":
        return NoteTarget(
            kind="code",
            codebook_version_id=_input_entity_id(
                value["codebook_version_id"],
                "codebook_version_id",
            ),
            code_id=_input_entity_id(value["code_id"], "code_id"),
        )
    excerpt_target_kind = value["excerpt_target_kind"]
    if (
        not isinstance(excerpt_target_kind, str)
        or excerpt_target_kind not in _EXCERPT_TARGET_KINDS
    ):
        raise NoteValidationError("excerpt_target_kind is invalid")
    start_offset, end_offset = _input_offsets(
        value["start_offset"],
        value["end_offset"],
    )
    return NoteTarget(
        kind="excerpt",
        project_source_id=_input_external_id(value["project_source_id"]),
        transcript_revision_id=_input_evidence_id(
            value["transcript_revision_id"],
            "transcript_revision_id",
        ),
        evidence_set_id=_input_evidence_id(
            value["evidence_set_id"],
            "evidence_set_id",
        ),
        excerpt_target_kind=excerpt_target_kind,
        passage_id=_input_evidence_id(value["passage_id"], "passage_id"),
        cunit_id=(
            None
            if excerpt_target_kind == "passage"
            else _input_evidence_id(value["cunit_id"], "cunit_id")
        ),
        start_offset=start_offset,
        end_offset=end_offset,
    )


def _input_external_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_EXTERNAL_ID_LENGTH
        or value != value.strip()
        or "\x00" in value
    ):
        raise NoteValidationError(
            "project_source_id must be a non-empty exact identifier"
        )
    _require_valid_unicode(value, "project_source_id")
    return value


def _input_evidence_id(value: object, field_name: str) -> str:
    pattern = _EVIDENCE_IDS[field_name]
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise NoteValidationError(f"{field_name} is invalid")
    return value


def _input_offsets(start_offset: object, end_offset: object) -> tuple[int, int]:
    if (
        type(start_offset) is not int
        or type(end_offset) is not int
        or start_offset < 0
        or end_offset <= start_offset
    ):
        raise NoteValidationError("Excerpt offsets must define a positive span")
    return start_offset, end_offset


def _stored_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise NoteConflictError(f"Stored note {field_name} is invalid")
    return value


def _has_note_subject_prefix(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().casefold().startswith(("mem_", "ann_"))
    if isinstance(value, bytes):
        return value.strip().lower().startswith((b"mem_", b"ann_"))
    return False


def _has_note_subject_type(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in _NOTE_KINDS
    if isinstance(value, bytes):
        return value.strip().lower() in {b"memo", b"annotation"}
    return False


def _has_note_event_prefix(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().casefold().startswith(("memo.", "annotation."))
    if isinstance(value, bytes):
        return value.strip().lower().startswith((b"memo.", b"annotation."))
    return False


def _stored_note_kind(value: object) -> str:
    stored = _stored_text(value, "note_kind")
    if stored not in _NOTE_KINDS:
        raise NoteConflictError("Stored note kind is invalid")
    return stored


def _stored_note_id(value: object, note_kind: str) -> str:
    stored = _stored_text(value, "note_id")
    if not _NOTE_IDS[note_kind].fullmatch(stored):
        raise NoteConflictError("Stored note identity is invalid")
    return stored


def _stored_revision_id(value: object) -> str:
    stored = _stored_text(value, "note_revision_id")
    if not _NOTE_REVISION_ID.fullmatch(stored):
        raise NoteConflictError("Stored note revision identity is invalid")
    return stored


def _stored_entity_id(value: object, field_name: str) -> str:
    stored = _stored_text(value, field_name)
    if not _ENTITY_ID.fullmatch(stored) or stored != stored.strip():
        raise NoteConflictError(f"Stored note {field_name} is invalid")
    return stored


def _stored_optional_entity_id(value: object, field_name: str) -> str | None:
    return None if value is None else _stored_entity_id(value, field_name)


def _stored_external_id(value: object) -> str:
    stored = _stored_text(value, "project_source_id")
    if (
        not stored
        or len(stored) > _MAX_EXTERNAL_ID_LENGTH
        or stored != stored.strip()
        or "\x00" in stored
        or any(0xD800 <= ord(character) <= 0xDFFF for character in stored)
    ):
        raise NoteConflictError("Stored note project_source_id is invalid")
    return stored


def _stored_evidence_id(value: object, field_name: str) -> str:
    stored = _stored_text(value, field_name)
    if not _EVIDENCE_IDS[field_name].fullmatch(stored):
        raise NoteConflictError(f"Stored note {field_name} is invalid")
    return stored


def _stored_positive_integer(value: object, field_name: str) -> int:
    if type(value) is not int or value <= 0:
        raise NoteConflictError(f"Stored note {field_name} is invalid")
    return value


def _stored_offset(value: object, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise NoteConflictError(f"Stored note {field_name} is invalid")
    return value


def _stored_target(row: sqlite3.Row) -> NoteTarget:
    kind = _stored_text(row["target_kind"], "target_kind")
    if kind not in _TARGET_KINDS:
        raise NoteConflictError("Stored note target kind is invalid")
    fields = {
        "project_source_id": row["project_source_id"],
        "case_id": row["case_id"],
        "codebook_version_id": row["codebook_version_id"],
        "code_id": row["code_id"],
        "transcript_revision_id": row["transcript_revision_id"],
        "evidence_set_id": row["evidence_set_id"],
        "excerpt_target_kind": row["excerpt_target_kind"],
        "passage_id": row["passage_id"],
        "cunit_id": row["cunit_id"],
        "start_offset": row["start_offset"],
        "end_offset": row["end_offset"],
    }
    if kind == "study":
        if any(value is not None for value in fields.values()):
            raise NoteConflictError("Stored study target is invalid")
        return NoteTarget(kind="study")
    if kind == "source":
        if any(
            value is not None
            for field_name, value in fields.items()
            if field_name != "project_source_id"
        ):
            raise NoteConflictError("Stored source target is invalid")
        return NoteTarget(
            kind="source",
            project_source_id=_stored_external_id(fields["project_source_id"]),
        )
    if kind == "case":
        if any(
            value is not None
            for field_name, value in fields.items()
            if field_name != "case_id"
        ):
            raise NoteConflictError("Stored case target is invalid")
        return NoteTarget(
            kind="case",
            case_id=_stored_entity_id(fields["case_id"], "case_id"),
        )
    if kind == "code":
        if any(
            value is not None
            for field_name, value in fields.items()
            if field_name not in {"codebook_version_id", "code_id"}
        ):
            raise NoteConflictError("Stored code target is invalid")
        return NoteTarget(
            kind="code",
            codebook_version_id=_stored_entity_id(
                fields["codebook_version_id"],
                "codebook_version_id",
            ),
            code_id=_stored_entity_id(fields["code_id"], "code_id"),
        )
    if fields["case_id"] is not None or fields["codebook_version_id"] is not None or fields["code_id"] is not None:
        raise NoteConflictError("Stored excerpt target is invalid")
    excerpt_kind = _stored_text(
        fields["excerpt_target_kind"],
        "excerpt_target_kind",
    )
    if excerpt_kind not in _EXCERPT_TARGET_KINDS:
        raise NoteConflictError("Stored excerpt target kind is invalid")
    cunit_id: str | None
    if excerpt_kind == "passage":
        if fields["cunit_id"] is not None:
            raise NoteConflictError("Stored excerpt target is invalid")
        cunit_id = None
    else:
        cunit_id = _stored_evidence_id(fields["cunit_id"], "cunit_id")
    start_offset = _stored_offset(fields["start_offset"], "start_offset")
    end_offset = _stored_offset(fields["end_offset"], "end_offset")
    if end_offset <= start_offset:
        raise NoteConflictError("Stored excerpt offsets are invalid")
    return NoteTarget(
        kind="excerpt",
        project_source_id=_stored_external_id(fields["project_source_id"]),
        transcript_revision_id=_stored_evidence_id(
            fields["transcript_revision_id"],
            "transcript_revision_id",
        ),
        evidence_set_id=_stored_evidence_id(
            fields["evidence_set_id"],
            "evidence_set_id",
        ),
        excerpt_target_kind=excerpt_kind,
        passage_id=_stored_evidence_id(fields["passage_id"], "passage_id"),
        cunit_id=cunit_id,
        start_offset=start_offset,
        end_offset=end_offset,
    )


def _stored_title(note_kind: str, value: object) -> str:
    title = _stored_text(value, "title")
    try:
        normalized = _input_title(note_kind, title)
    except NoteValidationError as exc:
        raise NoteConflictError("Stored note title is invalid") from exc
    if normalized != title:
        raise NoteConflictError("Stored memo title normalization is invalid")
    return title


def _stored_body(value: object) -> str:
    body = _stored_text(value, "body")
    try:
        return _input_body(body)
    except NoteValidationError as exc:
        raise NoteConflictError("Stored note body is invalid") from exc


def _stored_timestamp(value: object, field_name: str) -> str:
    stored, _ = _stored_timestamp_with_instant(value, field_name)
    return stored


def _stored_timestamp_with_instant(
    value: object,
    field_name: str,
) -> tuple[str, datetime]:
    stored = _stored_text(value, field_name)
    if (
        not stored
        or len(stored) > _MAX_TIMESTAMP_LENGTH
        or stored != stored.strip()
    ):
        raise NoteConflictError(f"Stored note {field_name} is invalid")
    normalized = f"{stored[:-1]}+00:00" if stored.endswith("Z") else stored
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise NoteConflictError(f"Stored note {field_name} is invalid") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset() != timedelta(0)
    ):
        raise NoteConflictError(f"Stored note {field_name} must be UTC")
    return stored, parsed


def _canonical_json(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _stored_canonical_json(value: object) -> str:
    stored = _stored_text(value, "audit metadata_json")
    if len(stored) > _MAX_AUDIT_METADATA_LENGTH:
        raise NoteConflictError("Stored note audit metadata is invalid")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = item
        return result

    try:
        parsed = json.loads(
            stored,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        canonical = _canonical_json(parsed) if isinstance(parsed, dict) else None
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise NoteConflictError("Stored note audit metadata is invalid") from exc
    if canonical != stored:
        raise NoteConflictError("Stored note audit metadata is invalid")
    return stored


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
