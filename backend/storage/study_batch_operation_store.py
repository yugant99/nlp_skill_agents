from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from backend.storage.sqlite_migrations import (
    Migration,
    apply_migrations,
    schema_status,
)


STUDY_BATCH_OPERATION_STAGES = (
    "prepared",
    "items_processed",
    "aggregate_json_written",
    "csv_exports_written",
    "batch_manifest_written",
    "audit_recorded",
)
STUDY_BATCH_ITEM_STAGES = (
    "prepared",
    "analysis_completed",
    "source_blob_stored",
    "evidence_cataloged",
    "snapshot_written",
    "completed",
)
_NEXT_OPERATION_STAGE = dict(
    zip(
        STUDY_BATCH_OPERATION_STAGES,
        STUDY_BATCH_OPERATION_STAGES[1:],
        strict=False,
    )
)
_STUDY_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
STUDY_BATCH_ID_PATTERN = r"^batch_[0-9]{14}_[0-9a-f]{8}$"
_BATCH_ID = re.compile(STUDY_BATCH_ID_PATTERN)
_SKILL_PACK_VERSION_ID = re.compile(r"^[a-z0-9_]+-[a-z0-9_]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ERROR_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")


class StudyBatchOperationConflict(RuntimeError):
    pass


class StudyBatchOperationStore:
    def __init__(
        self,
        root: Path | str,
        study_id: str,
    ) -> None:
        if not _STUDY_ID.fullmatch(study_id):
            raise ValueError("study_id must be a normalized study identifier")
        self.root = Path(root)
        self.study_id = study_id
        self.study_dir = self.root / "studies" / study_id
        self.db_path = self.study_dir / "batch_operations.sqlite3"

    def begin(
        self,
        *,
        batch_id: str,
        skill_pack_version_id: str,
        skill_pack_sha256: str,
        request_sha256: str,
        item_count: int,
        created_at: str,
    ) -> str:
        _validate_operation_identity(
            batch_id=batch_id,
            skill_pack_version_id=skill_pack_version_id,
            skill_pack_sha256=skill_pack_sha256,
            request_sha256=request_sha256,
            item_count=item_count,
            created_at=created_at,
        )
        self._ensure_schema()
        now = _utc_now()
        with self._immediate_connection(timeout=1) as connection:
            stored = connection.execute(
                """
                select study_id, skill_pack_version_id, skill_pack_sha256,
                       request_sha256, item_count, created_at, status
                from study_batch_operations where batch_id = ?
                """,
                (batch_id,),
            ).fetchone()
            expected = (
                self.study_id,
                skill_pack_version_id,
                skill_pack_sha256,
                request_sha256,
                item_count,
                created_at,
            )
            if stored is not None:
                if stored[:6] != expected:
                    raise StudyBatchOperationConflict(
                        "Study batch operation identity conflicts with journal"
                    )
                if stored[6] == "running":
                    raise StudyBatchOperationConflict(
                        "Study batch operation is already running"
                    )
                if stored[6] == "completed":
                    return batch_id
                connection.execute(
                    """
                    update study_batch_operations
                    set status = 'running', stage = 'prepared',
                        attempt_count = attempt_count + 1, last_error_type = '',
                        updated_at = ?, completed_at = ''
                    where batch_id = ?
                    """,
                    (now, batch_id),
                )
                return batch_id

            connection.execute(
                """
                insert into study_batch_operations (
                  batch_id, study_id, skill_pack_version_id, skill_pack_sha256,
                  request_sha256, item_count, audit_event_id,
                  status, stage, attempt_count, last_error_type,
                  created_at, updated_at, completed_at
                ) values (?, ?, ?, ?, ?, ?, ?, 'running', 'prepared', 1, '', ?, ?, '')
                """,
                (
                    batch_id,
                    self.study_id,
                    skill_pack_version_id,
                    skill_pack_sha256,
                    request_sha256,
                    item_count,
                    _audit_event_id(self.study_id, batch_id),
                    created_at,
                    now,
                ),
            )
        return batch_id

    def reserve_item(
        self,
        batch_id: str,
        *,
        item_index: int,
        item_request_sha256: str,
        run_id: str,
        import_id: str,
        project_source_id: str,
        source_blob_sha256: str,
        transcript_sha256: str,
        transcript_revision_id: str,
        created_at: str,
    ) -> dict[str, Any]:
        _validate_item_identity(
            item_index=item_index,
            item_request_sha256=item_request_sha256,
            run_id=run_id,
            import_id=import_id,
            project_source_id=project_source_id,
            source_blob_sha256=source_blob_sha256,
            transcript_sha256=transcript_sha256,
            transcript_revision_id=transcript_revision_id,
            created_at=created_at,
        )
        self._ensure_schema()
        with self._connect() as connection:
            connection.execute("begin immediate")
            status, stage, item_count = self._operation_state(connection, batch_id)
            if status != "running" or stage != "prepared":
                raise RuntimeError("Study batch operation is not preparing items")
            if item_index >= item_count:
                raise ValueError("item_index is outside the batch item count")
            stored = connection.execute(
                """
                select item_request_sha256, run_id, import_id, project_source_id,
                       source_blob_sha256, transcript_sha256,
                       transcript_revision_id, created_at
                from study_batch_operation_items
                where batch_id = ? and item_index = ?
                """,
                (batch_id, item_index),
            ).fetchone()
            expected = (
                item_request_sha256,
                run_id,
                import_id,
                project_source_id,
                source_blob_sha256,
                transcript_sha256,
                transcript_revision_id,
                created_at,
            )
            if stored is not None:
                if stored != expected:
                    raise StudyBatchOperationConflict(
                        "Study batch item identity conflicts with journal"
                    )
            else:
                try:
                    connection.execute(
                        """
                        insert into study_batch_operation_items (
                          batch_id, item_index, item_request_sha256,
                          run_id, import_id, project_source_id,
                          source_blob_sha256, transcript_sha256,
                          transcript_revision_id, run_payload_sha256,
                          stage, last_error_type, created_at, updated_at
                        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, '',
                                  'prepared', '', ?, ?)
                        """,
                        (
                            batch_id,
                            item_index,
                            item_request_sha256,
                            run_id,
                            import_id,
                            project_source_id,
                            source_blob_sha256,
                            transcript_sha256,
                            transcript_revision_id,
                            created_at,
                            _utc_now(),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise StudyBatchOperationConflict(
                        "Study batch item identity conflicts with journal"
                    ) from exc
        item = self.get_item(batch_id, item_index)
        if item is None:
            raise RuntimeError("Study batch item was not persisted")
        return item

    def record_analysis_completed(
        self,
        batch_id: str,
        item_index: int,
        *,
        run_payload_sha256: str,
    ) -> None:
        _validate_sha256(run_payload_sha256, "run_payload_sha256")
        self._ensure_schema()
        with self._connect() as connection:
            connection.execute("begin immediate")
            status, operation_stage, _ = self._operation_state(connection, batch_id)
            if status != "running" or operation_stage != "prepared":
                raise RuntimeError("Study batch operation is not preparing items")
            row = connection.execute(
                """
                select stage, run_payload_sha256
                from study_batch_operation_items
                where batch_id = ? and item_index = ?
                """,
                (batch_id, item_index),
            ).fetchone()
            if row is None:
                raise FileNotFoundError(f"{batch_id}:{item_index}")
            current_stage = str(row[0])
            current_payload_sha256 = str(row[1])
            if current_stage == "rejected":
                raise StudyBatchOperationConflict(
                    "Rejected study batch item cannot record analysis"
                )
            if current_stage != "prepared":
                if current_payload_sha256 != run_payload_sha256:
                    raise StudyBatchOperationConflict(
                        "Study batch analysis payload conflicts with journal"
                    )
                return
            connection.execute(
                """
                update study_batch_operation_items
                set run_payload_sha256 = ?, stage = 'analysis_completed',
                    updated_at = ?
                where batch_id = ? and item_index = ? and stage = 'prepared'
                """,
                (run_payload_sha256, _utc_now(), batch_id, item_index),
            )

    def reject_item(
        self,
        batch_id: str,
        *,
        item_index: int,
        item_request_sha256: str,
        error_type: str,
    ) -> dict[str, Any]:
        if item_index < 0:
            raise ValueError("item_index must be non-negative")
        _validate_sha256(item_request_sha256, "item_request_sha256")
        if not _ERROR_TYPE.fullmatch(error_type):
            raise ValueError("error_type must be an exception class name")
        self._ensure_schema()
        with self._connect() as connection:
            connection.execute("begin immediate")
            status, stage, item_count = self._operation_state(connection, batch_id)
            if status != "running" or stage != "prepared":
                raise RuntimeError("Study batch operation is not preparing items")
            if item_index >= item_count:
                raise ValueError("item_index is outside the batch item count")
            stored = connection.execute(
                """
                select item_request_sha256, stage, last_error_type
                from study_batch_operation_items
                where batch_id = ? and item_index = ?
                """,
                (batch_id, item_index),
            ).fetchone()
            expected = (item_request_sha256, "rejected", error_type)
            if stored is None:
                raise FileNotFoundError(f"{batch_id}:{item_index}")
            if stored == expected:
                pass
            elif stored[0] == item_request_sha256 and stored[1] == "prepared":
                connection.execute(
                    """
                    update study_batch_operation_items
                    set stage = 'rejected', last_error_type = ?, updated_at = ?
                    where batch_id = ? and item_index = ? and stage = 'prepared'
                    """,
                    (
                        error_type,
                        _utc_now(),
                        batch_id,
                        item_index,
                    ),
                )
            else:
                raise StudyBatchOperationConflict(
                    "Study batch item identity conflicts with journal"
                )
        item = self.get_item(batch_id, item_index)
        if item is None:
            raise RuntimeError("Study batch rejected item was not persisted")
        return item

    def get_item(
        self,
        batch_id: str,
        item_index: int,
    ) -> dict[str, Any] | None:
        self._ensure_schema()
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                select batch_id, item_index, item_request_sha256,
                       run_id, import_id, project_source_id,
                       source_blob_sha256, transcript_sha256,
                       transcript_revision_id, run_payload_sha256,
                       stage, last_error_type, created_at, updated_at
                from study_batch_operation_items
                where batch_id = ? and item_index = ?
                """,
                (batch_id, item_index),
            ).fetchone()
        return dict(row) if row is not None else None

    def advance_item(
        self,
        batch_id: str,
        item_index: int,
        stage: str,
    ) -> None:
        if stage not in STUDY_BATCH_ITEM_STAGES[2:]:
            raise ValueError(f"Unsupported study batch item stage: {stage}")
        self._ensure_schema()
        with self._connect() as connection:
            connection.execute("begin immediate")
            status, operation_stage, _ = self._operation_state(connection, batch_id)
            if status != "running" or operation_stage != "prepared":
                raise RuntimeError("Study batch operation is not preparing items")
            row = connection.execute(
                """
                select stage from study_batch_operation_items
                where batch_id = ? and item_index = ?
                """,
                (batch_id, item_index),
            ).fetchone()
            if row is None:
                raise FileNotFoundError(f"{batch_id}:{item_index}")
            current_stage = str(row[0])
            if current_stage == "rejected":
                raise RuntimeError("Rejected study batch item cannot advance")
            current_index = STUDY_BATCH_ITEM_STAGES.index(current_stage)
            target_index = STUDY_BATCH_ITEM_STAGES.index(stage)
            if target_index <= current_index:
                return
            if target_index != current_index + 1:
                raise ValueError(
                    f"Study batch item cannot advance from {current_stage} to {stage}"
                )
            connection.execute(
                """
                update study_batch_operation_items set stage = ?, updated_at = ?
                where batch_id = ? and item_index = ? and stage = ?
                """,
                (stage, _utc_now(), batch_id, item_index, current_stage),
            )

    def advance(self, batch_id: str, stage: str) -> None:
        if stage not in STUDY_BATCH_OPERATION_STAGES[1:]:
            raise ValueError(f"Unsupported study batch operation stage: {stage}")
        self._ensure_schema()
        with self._connect() as connection:
            connection.execute("begin immediate")
            status, current_stage, _ = self._operation_state(connection, batch_id)
            if status != "running":
                raise RuntimeError("Study batch operation is not running")
            if _NEXT_OPERATION_STAGE.get(current_stage) != stage:
                raise ValueError(
                    f"Study batch operation cannot advance from {current_stage} to {stage}"
                )
            if stage == "items_processed":
                terminal_items = int(
                    connection.execute(
                        """
                        select count(*) from study_batch_operation_items
                        where batch_id = ? and stage in ('completed', 'rejected')
                        """,
                        (batch_id,),
                    ).fetchone()[0]
                )
                if terminal_items != self._operation_state(
                    connection,
                    batch_id,
                )[2]:
                    raise ValueError(
                        "Study batch operation has unprocessed items"
                    )
            connection.execute(
                """
                update study_batch_operations set stage = ?, updated_at = ?
                where batch_id = ? and status = 'running' and stage = ?
                """,
                (stage, _utc_now(), batch_id, current_stage),
            )

    def fail(self, batch_id: str, *, error_type: str) -> None:
        if not _ERROR_TYPE.fullmatch(error_type):
            raise ValueError("error_type must be an exception class name")
        self._ensure_schema()
        with self._connect() as connection:
            connection.execute("begin immediate")
            status, _, _ = self._operation_state(connection, batch_id)
            if status != "running":
                raise RuntimeError("Study batch operation is not running")
            connection.execute(
                """
                update study_batch_operations
                set status = 'failed', last_error_type = ?,
                    updated_at = ?, completed_at = ''
                where batch_id = ? and status = 'running'
                """,
                (error_type, _utc_now(), batch_id),
            )

    def complete(self, batch_id: str) -> None:
        self._ensure_schema()
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("begin immediate")
            status, stage, _ = self._operation_state(connection, batch_id)
            if status != "running":
                raise RuntimeError("Study batch operation is not running")
            if stage != "audit_recorded":
                raise ValueError(
                    "Study batch operation cannot complete before audit_recorded"
                )
            connection.execute(
                """
                update study_batch_operations
                set status = 'completed', stage = 'completed',
                    last_error_type = '', updated_at = ?, completed_at = ?
                where batch_id = ? and status = 'running'
                  and stage = 'audit_recorded'
                """,
                (now, now, batch_id),
            )

    def get_operation(self, batch_id: str) -> dict[str, Any]:
        self._ensure_schema()
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                select batch_id, study_id, skill_pack_version_id,
                       skill_pack_sha256, request_sha256, item_count,
                       audit_event_id, status, stage,
                       attempt_count, last_error_type,
                       created_at, updated_at, completed_at
                from study_batch_operations where batch_id = ?
                """,
                (batch_id,),
            ).fetchone()
        if row is None:
            raise FileNotFoundError(batch_id)
        return dict(row)

    def list_operations(
        self,
        *,
        limit: int = 100,
        incomplete_only: bool = False,
    ) -> list[dict[str, Any]]:
        self._ensure_schema()
        bounded_limit = max(1, min(limit, 500))
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                select batch_id, study_id, skill_pack_version_id,
                       skill_pack_sha256, request_sha256, item_count,
                       audit_event_id, status, stage,
                       attempt_count, last_error_type,
                       created_at, updated_at, completed_at
                from study_batch_operations
                where ? = 0 or status != 'completed'
                order by updated_at desc, batch_id
                limit ?
                """,
                (int(incomplete_only), bounded_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_items(self, batch_id: str) -> list[dict[str, Any]]:
        self._ensure_schema()
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                select batch_id, item_index, item_request_sha256,
                       run_id, import_id, project_source_id,
                       source_blob_sha256, transcript_sha256,
                       transcript_revision_id, run_payload_sha256,
                       stage, last_error_type, created_at, updated_at
                from study_batch_operation_items where batch_id = ?
                order by item_index
                """,
                (batch_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def migration_status(self) -> list[dict[str, object]]:
        self._ensure_schema()
        with self._connect() as connection:
            return schema_status(connection)

    @contextmanager
    def archive_snapshot_guard(self) -> Iterator[None]:
        self._ensure_schema()
        with self._immediate_connection() as connection:
            running = connection.execute(
                """
                select batch_id from study_batch_operations
                where status = 'running'
                order by batch_id limit 1
                """
            ).fetchone()
            if running is not None:
                raise StudyBatchOperationConflict(
                    "Study has a running batch operation"
                )
            yield

    @contextmanager
    def study_mutation_guard(self) -> Iterator[None]:
        self._ensure_schema()
        with self._immediate_connection(timeout=1):
            yield

    def _ensure_schema(self) -> None:
        if not (self.study_dir / "study.json").is_file():
            raise FileNotFoundError(self.study_id)
        with self._connect() as connection:
            apply_migrations(
                connection,
                database_name="study batch operations",
                migrations=STUDY_BATCH_OPERATION_MIGRATIONS,
            )

    def _connect(self, *, timeout: float = 30) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=timeout)
        connection.execute("pragma foreign_keys = on")
        return connection

    @contextmanager
    def _immediate_connection(
        self,
        *,
        timeout: float = 30,
    ) -> Iterator[sqlite3.Connection]:
        try:
            with self._connect(timeout=timeout) as connection:
                connection.execute("begin immediate")
                yield connection
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            raise StudyBatchOperationConflict(
                "Study batch journal is busy with another operation"
            ) from exc

    @staticmethod
    def _operation_state(
        connection: sqlite3.Connection,
        batch_id: str,
    ) -> tuple[str, str, int]:
        row = connection.execute(
            """
            select status, stage, item_count from study_batch_operations
            where batch_id = ?
            """,
            (batch_id,),
        ).fetchone()
        if row is None:
            raise FileNotFoundError(batch_id)
        return str(row[0]), str(row[1]), int(row[2])


def _create_study_batch_operations(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        create table study_batch_operations (
          batch_id text primary key,
          study_id text not null,
          skill_pack_version_id text not null,
          skill_pack_sha256 text not null check (length(skill_pack_sha256) = 64),
          request_sha256 text not null check (length(request_sha256) = 64),
          item_count integer not null check (item_count >= 0),
          audit_event_id text not null unique,
          status text not null check (status in ('running', 'failed', 'completed')),
          stage text not null check (
            stage in (
              'prepared', 'items_processed', 'aggregate_json_written',
              'csv_exports_written', 'batch_manifest_written',
              'audit_recorded', 'completed'
            )
          ),
          attempt_count integer not null check (attempt_count > 0),
          last_error_type text not null default '',
          created_at text not null,
          updated_at text not null,
          completed_at text not null default '',
          check (
            (status = 'completed' and stage = 'completed' and completed_at != '')
            or (status != 'completed' and stage != 'completed' and completed_at = '')
          ),
          check (
            (status = 'failed' and last_error_type != '')
            or (status != 'failed' and last_error_type = '')
          )
        )
        """
    )
    connection.execute(
        """
        create table study_batch_operation_items (
          batch_id text not null references study_batch_operations(batch_id)
            on delete cascade,
          item_index integer not null check (item_index >= 0),
          item_request_sha256 text not null check (length(item_request_sha256) = 64),
          run_id text not null,
          import_id text not null,
          project_source_id text not null,
          source_blob_sha256 text not null,
          transcript_sha256 text not null,
          transcript_revision_id text not null,
          run_payload_sha256 text not null,
          stage text not null check (
            stage in (
              'prepared', 'analysis_completed', 'source_blob_stored',
              'evidence_cataloged', 'snapshot_written', 'completed', 'rejected'
            )
          ),
          last_error_type text not null default '',
          created_at text not null,
          updated_at text not null,
          primary key (batch_id, item_index),
          check (
            run_id != '' and import_id != '' and project_source_id != ''
            and length(source_blob_sha256) = 64
            and length(transcript_sha256) = 64
            and transcript_revision_id != '' and created_at != ''
          ),
          check (
            (stage in ('prepared', 'rejected') and run_payload_sha256 = '')
            or (stage not in ('prepared', 'rejected')
                and length(run_payload_sha256) = 64)
          ),
          check (
            (stage = 'rejected' and last_error_type != '')
            or (stage != 'rejected' and last_error_type = '')
          )
        )
        """
    )
    connection.execute(
        """
        create index study_batch_operations_status_updated_idx
        on study_batch_operations(status, updated_at desc)
        """
    )
    connection.execute(
        """
        create unique index study_batch_operation_items_run_idx
        on study_batch_operation_items(run_id) where run_id != ''
        """
    )
    connection.execute(
        """
        create unique index study_batch_operation_items_import_idx
        on study_batch_operation_items(import_id) where import_id != ''
        """
    )
    connection.execute(
        """
        create trigger study_batch_operation_items_index_guard
        before insert on study_batch_operation_items
        when new.item_index >= (
          select item_count from study_batch_operations
          where batch_id = new.batch_id
        )
        begin
          select raise(abort, 'study batch item index exceeds item count');
        end
        """
    )
    connection.execute(
        """
        create trigger study_batch_operation_items_index_update_guard
        before update of batch_id, item_index on study_batch_operation_items
        when new.item_index >= (
          select item_count from study_batch_operations
          where batch_id = new.batch_id
        )
        begin
          select raise(abort, 'study batch item index exceeds item count');
        end
        """
    )
    connection.execute(
        """
        create trigger study_batch_operations_item_count_guard
        before update of item_count on study_batch_operations
        when exists (
          select 1 from study_batch_operation_items
          where batch_id = new.batch_id and item_index >= new.item_count
        )
        begin
          select raise(abort, 'study batch item count excludes reserved item');
        end
        """
    )


STUDY_BATCH_OPERATION_MIGRATIONS = (
    Migration(
        1,
        "create-study-batch-operations",
        _create_study_batch_operations,
    ),
)


def validate_study_batch_operation_database(
    db_path: Path | str,
    study_id: str,
) -> None:
    if not _STUDY_ID.fullmatch(study_id):
        raise ValueError("study_id must be a normalized study identifier")
    with sqlite3.connect(Path(db_path), timeout=30) as connection:
        connection.execute("pragma foreign_keys = on")
        apply_migrations(
            connection,
            database_name="study batch operations",
            migrations=STUDY_BATCH_OPERATION_MIGRATIONS,
        )
        integrity_rows = connection.execute("pragma integrity_check").fetchall()
        if integrity_rows != [("ok",)]:
            raise ValueError("Study batch operation journal failed integrity check")
        if connection.execute("pragma foreign_key_check").fetchone() is not None:
            raise ValueError(
                "Study batch operation journal failed foreign-key validation"
            )
        mismatched_study = connection.execute(
            """
            select 1 from study_batch_operations
            where study_id != ? limit 1
            """,
            (study_id,),
        ).fetchone()
        if mismatched_study is not None:
            raise ValueError(
                "Study batch operation journal belongs to another study"
            )


def _validate_operation_identity(
    *,
    batch_id: str,
    skill_pack_version_id: str,
    skill_pack_sha256: str,
    request_sha256: str,
    item_count: int,
    created_at: str,
) -> None:
    validate_study_batch_id(batch_id)
    if not _SKILL_PACK_VERSION_ID.fullmatch(skill_pack_version_id):
        raise ValueError("skill_pack_version_id must be a normalized version identifier")
    _validate_sha256(skill_pack_sha256, "skill_pack_sha256")
    _validate_sha256(request_sha256, "request_sha256")
    if item_count < 0:
        raise ValueError("item_count must be non-negative")
    if not created_at.strip():
        raise ValueError("created_at must be non-empty")


def _validate_item_identity(
    *,
    item_index: int,
    item_request_sha256: str,
    run_id: str,
    import_id: str,
    project_source_id: str,
    source_blob_sha256: str,
    transcript_sha256: str,
    transcript_revision_id: str,
    created_at: str,
) -> None:
    if item_index < 0:
        raise ValueError("item_index must be non-negative")
    for field_name, value in (
        ("run_id", run_id),
        ("import_id", import_id),
        ("project_source_id", project_source_id),
        ("transcript_revision_id", transcript_revision_id),
        ("created_at", created_at),
    ):
        if not value.strip():
            raise ValueError(f"{field_name} must be non-empty")
    _validate_sha256(item_request_sha256, "item_request_sha256")
    _validate_sha256(source_blob_sha256, "source_blob_sha256")
    _validate_sha256(transcript_sha256, "transcript_sha256")


def validate_study_batch_id(batch_id: str) -> None:
    if not _BATCH_ID.fullmatch(batch_id):
        raise ValueError("batch_id must be a generated study batch identifier")


def _validate_sha256(value: str, field_name: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _audit_event_id(study_id: str, batch_id: str) -> str:
    return sha256(f"batch.completed\0{study_id}\0{batch_id}".encode("utf-8")).hexdigest()
