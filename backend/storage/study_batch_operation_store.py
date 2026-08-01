from __future__ import annotations

import json
import re
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from backend.storage.sqlite_migrations import (
    Migration,
    SchemaCompatibilityError,
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
_PATH_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MAX_SKILL_PACK_VERSION_ID_LENGTH = 128
_WINDOWS_DEVICE_NAME = re.compile(
    r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])$",
    re.IGNORECASE,
)


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
        if stage == "aggregate_json_written":
            raise ValueError(
                "Use record_aggregate_written to bind the aggregate payload"
            )
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

    def record_aggregate_written(
        self,
        batch_id: str,
        *,
        aggregate_payload_sha256: str,
    ) -> None:
        _validate_sha256(
            aggregate_payload_sha256,
            "aggregate_payload_sha256",
        )
        self._ensure_schema()
        with self._connect() as connection:
            connection.execute("begin immediate")
            status, current_stage, _ = self._operation_state(connection, batch_id)
            if status != "running":
                raise RuntimeError("Study batch operation is not running")
            stored_sha256 = str(
                connection.execute(
                    """
                    select aggregate_payload_sha256
                    from study_batch_operations where batch_id = ?
                    """,
                    (batch_id,),
                ).fetchone()[0]
            )
            if stored_sha256 and stored_sha256 != aggregate_payload_sha256:
                raise StudyBatchOperationConflict(
                    "Study batch aggregate payload conflicts with journal"
                )
            if current_stage != "items_processed":
                raise ValueError(
                    "Study batch operation cannot record aggregate from "
                    f"{current_stage}"
                )
            connection.execute(
                """
                update study_batch_operations
                set aggregate_payload_sha256 = ?,
                    stage = 'aggregate_json_written', updated_at = ?
                where batch_id = ? and status = 'running'
                  and stage = 'items_processed'
                """,
                (
                    aggregate_payload_sha256,
                    _utc_now(),
                    batch_id,
                ),
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
            aggregate_payload_sha256 = str(
                connection.execute(
                    """
                    select aggregate_payload_sha256
                    from study_batch_operations where batch_id = ?
                    """,
                    (batch_id,),
                ).fetchone()[0]
            )
            if not _SHA256.fullmatch(aggregate_payload_sha256):
                raise ValueError(
                    "Study batch operation cannot complete without aggregate hash"
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
                       aggregate_payload_sha256, audit_event_id, status, stage,
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
                       aggregate_payload_sha256, audit_event_id, status, stage,
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

    def completed_batch_ids(self) -> set[str]:
        self._ensure_schema()
        with self._connect() as connection:
            return {
                str(row[0])
                for row in connection.execute(
                    """
                    select batch_id from study_batch_operations
                    where status = 'completed'
                    """
                )
            }

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
        try:
            with self._connect() as connection:
                apply_migrations(
                    connection,
                    database_name="study batch operations",
                    migrations=STUDY_BATCH_OPERATION_MIGRATIONS,
                )
        except (OSError, sqlite3.Error) as exc:
            raise StudyBatchOperationConflict(
                "Study batch operation journal is invalid"
            ) from exc

    def _connect(self, *, timeout: float = 30) -> sqlite3.Connection:
        try:
            if self.db_path.exists() or self.db_path.is_symlink():
                mode = self.db_path.lstat().st_mode
                if not stat.S_ISREG(mode):
                    raise OSError(
                        "Study batch operation journal must be a regular file"
                    )
            connection = sqlite3.connect(self.db_path, timeout=timeout)
            try:
                connection.execute("pragma foreign_keys = on")
            except BaseException:
                connection.close()
                raise
            return connection
        except (OSError, sqlite3.Error) as exc:
            raise StudyBatchOperationConflict(
                "Study batch operation journal is invalid"
            ) from exc

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


def _add_study_batch_aggregate_hash(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        alter table study_batch_operations
        add column aggregate_payload_sha256 text not null default ''
          check (
            aggregate_payload_sha256 = ''
            or length(aggregate_payload_sha256) = 64
          )
        """
    )
    _backfill_legacy_aggregate_hashes(connection)


def _backfill_legacy_aggregate_hashes(connection: sqlite3.Connection) -> None:
    completed_operations = [
        operation
        for operation in _completed_operation_summaries(connection)
        if not operation[6]
    ]
    if not completed_operations:
        return
    database_path = next(
        (
            str(row[2])
            for row in connection.execute("pragma database_list")
            if str(row[1]) == "main"
        ),
        "",
    )
    if not database_path:
        raise ValueError(
            "Completed version-1 study batch journal has no database path"
        )
    study_dir = Path(database_path).parent
    for (
        batch_id,
        study_id,
        skill_pack_version_id,
        created_at,
        run_count,
        failure_count,
        _,
    ) in completed_operations:
        validate_study_batch_id(batch_id)
        if not _STUDY_ID.fullmatch(study_id):
            raise ValueError("Completed study batch has an invalid study id")
        aggregate_payload = _load_aggregate_payload(
            study_dir,
            batch_id,
            study_id=study_id,
            skill_pack_version_id=skill_pack_version_id,
            created_at=created_at,
            run_count=run_count,
            failure_count=failure_count,
        )
        connection.execute(
            """
            update study_batch_operations
            set aggregate_payload_sha256 = ?
            where batch_id = ? and status = 'completed'
            """,
            (_canonical_json_sha256(aggregate_payload), batch_id),
        )


def _repair_study_batch_aggregate_hashes(
    connection: sqlite3.Connection,
) -> None:
    _backfill_legacy_aggregate_hashes(connection)


STUDY_BATCH_OPERATION_MIGRATIONS = (
    Migration(
        1,
        "create-study-batch-operations",
        _create_study_batch_operations,
    ),
    Migration(
        2,
        "add-study-batch-aggregate-hash",
        _add_study_batch_aggregate_hash,
    ),
    Migration(
        3,
        "repair-study-batch-aggregate-hashes",
        _repair_study_batch_aggregate_hashes,
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
        connection.execute("pragma trusted_schema = off")
        current_version = int(
            connection.execute("pragma user_version").fetchone()[0]
        )
        if current_version > len(STUDY_BATCH_OPERATION_MIGRATIONS):
            raise SchemaCompatibilityError(
                "study batch operations schema version "
                f"{current_version} is newer than supported version "
                f"{len(STUDY_BATCH_OPERATION_MIGRATIONS)}"
            )
        _validate_schema_definition(connection, current_version)
        _validate_database_integrity(connection)
        if current_version:
            _validate_study_ownership(connection, study_id)
            _validate_persisted_operations(
                connection,
                study_id,
                schema_version=current_version,
            )
        apply_migrations(
            connection,
            database_name="study batch operations",
            migrations=STUDY_BATCH_OPERATION_MIGRATIONS,
        )
        _validate_schema_definition(
            connection,
            len(STUDY_BATCH_OPERATION_MIGRATIONS),
        )
        _validate_database_integrity(connection)
        _validate_study_ownership(connection, study_id)
        _validate_persisted_operations(
            connection,
            study_id,
            schema_version=len(STUDY_BATCH_OPERATION_MIGRATIONS),
        )
        _validate_completed_aggregate_files(connection, Path(db_path).parent)


def _validate_database_integrity(connection: sqlite3.Connection) -> None:
    integrity_rows = connection.execute("pragma integrity_check").fetchall()
    if integrity_rows != [("ok",)]:
        raise ValueError("Study batch operation journal failed integrity check")
    if connection.execute("pragma foreign_key_check").fetchone() is not None:
        raise ValueError(
            "Study batch operation journal failed foreign-key validation"
        )


def _validate_study_ownership(
    connection: sqlite3.Connection,
    study_id: str,
) -> None:
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


def _validate_schema_definition(
    connection: sqlite3.Connection,
    version: int,
) -> None:
    if version == 0:
        expected_signature: tuple[tuple[str, str, str, str], ...] = ()
    else:
        with sqlite3.connect(":memory:") as expected:
            expected.execute("pragma trusted_schema = off")
            apply_migrations(
                expected,
                database_name="expected study batch operations",
                migrations=STUDY_BATCH_OPERATION_MIGRATIONS[:version],
            )
            expected_signature = _schema_signature(expected)
    if _schema_signature(connection) != expected_signature:
        raise ValueError(
            "Study batch operation journal schema definition is invalid"
        )


def _schema_signature(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(
        (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            " ".join(str(row[3] or "").split()),
        )
        for row in connection.execute(
            """
            select type, name, tbl_name, sql from sqlite_master
            where name not like 'sqlite_%'
            order by type, name
            """
        )
    )


def _validate_persisted_operations(
    connection: sqlite3.Connection,
    study_id: str,
    *,
    schema_version: int,
) -> None:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    aggregate_hash_projection = (
        "aggregate_payload_sha256" if schema_version >= 2 else "''"
    )
    operations = cursor.execute(
        f"""
        select batch_id, study_id, skill_pack_version_id,
               skill_pack_sha256, request_sha256, item_count,
               typeof(item_count) as item_count_type,
               {aggregate_hash_projection} as aggregate_payload_sha256,
               audit_event_id, status, stage,
               attempt_count, typeof(attempt_count) as attempt_count_type,
               last_error_type,
               created_at, updated_at, completed_at
        from study_batch_operations order by batch_id
        """
    ).fetchall()
    for operation in operations:
        batch_id = str(operation["batch_id"])
        if str(operation["item_count_type"]) != "integer":
            raise ValueError("Study batch item count must be an integer")
        if (
            str(operation["attempt_count_type"]) != "integer"
            or int(operation["attempt_count"]) <= 0
        ):
            raise ValueError("Study batch attempt count must be a positive integer")
        _validate_operation_identity(
            batch_id=batch_id,
            skill_pack_version_id=str(operation["skill_pack_version_id"]),
            skill_pack_sha256=str(operation["skill_pack_sha256"]),
            request_sha256=str(operation["request_sha256"]),
            item_count=int(operation["item_count"]),
            created_at=str(operation["created_at"]),
        )
        if str(operation["study_id"]) != study_id:
            raise ValueError(
                "Study batch operation journal belongs to another study"
            )
        if str(operation["audit_event_id"]) != _audit_event_id(
            study_id,
            batch_id,
        ):
            raise ValueError("Study batch audit identity is invalid")
        _validate_timestamp(str(operation["updated_at"]), "updated_at")
        completed_at = str(operation["completed_at"])
        if completed_at:
            _validate_timestamp(completed_at, "completed_at")
        last_error_type = str(operation["last_error_type"])
        if last_error_type and not _ERROR_TYPE.fullmatch(last_error_type):
            raise ValueError("Study batch operation error type is invalid")
        aggregate_payload_sha256 = str(
            operation["aggregate_payload_sha256"]
        )
        if aggregate_payload_sha256:
            _validate_sha256(
                aggregate_payload_sha256,
                "aggregate_payload_sha256",
            )
        if str(operation["status"]) == "running":
            raise ValueError("Archived study batch operation is still running")
        if str(operation["status"]) == "completed":
            if schema_version >= 3 and not aggregate_payload_sha256:
                raise ValueError(
                    "Completed study batch operation has no aggregate hash"
                )
            item_counts = connection.execute(
                """
                select count(*),
                       sum(case when stage in ('completed', 'rejected')
                                then 1 else 0 end)
                from study_batch_operation_items where batch_id = ?
                """,
                (batch_id,),
            ).fetchone()
            if (int(item_counts[0]), int(item_counts[1] or 0)) != (
                int(operation["item_count"]),
                int(operation["item_count"]),
            ):
                raise ValueError(
                    "Completed study batch operation has invalid item state"
                )

    items = cursor.execute(
        """
        select batch_id, item_index, item_request_sha256,
               typeof(item_index) as item_index_type,
               run_id, import_id, project_source_id,
               source_blob_sha256, transcript_sha256,
               transcript_revision_id, run_payload_sha256,
               stage, last_error_type, created_at, updated_at
        from study_batch_operation_items order by batch_id, item_index
        """
    ).fetchall()
    casefolded_run_ids: set[tuple[str, str]] = set()
    for item in items:
        batch_id = str(item["batch_id"])
        validate_study_batch_id(batch_id)
        if str(item["item_index_type"]) != "integer":
            raise ValueError("Study batch item index must be an integer")
        run_id = str(item["run_id"])
        run_identity = (batch_id, run_id.casefold())
        if run_identity in casefolded_run_ids:
            raise ValueError(
                "Study batch run ids collide on a case-insensitive filesystem"
            )
        casefolded_run_ids.add(run_identity)
        _validate_item_identity(
            item_index=int(item["item_index"]),
            item_request_sha256=str(item["item_request_sha256"]),
            run_id=run_id,
            import_id=str(item["import_id"]),
            project_source_id=str(item["project_source_id"]),
            source_blob_sha256=str(item["source_blob_sha256"]),
            transcript_sha256=str(item["transcript_sha256"]),
            transcript_revision_id=str(item["transcript_revision_id"]),
            created_at=str(item["created_at"]),
        )
        run_payload_sha256 = str(item["run_payload_sha256"])
        if run_payload_sha256:
            _validate_sha256(run_payload_sha256, "run_payload_sha256")
        last_error_type = str(item["last_error_type"])
        if last_error_type and not _ERROR_TYPE.fullmatch(last_error_type):
            raise ValueError("Study batch item error type is invalid")
        _validate_timestamp(str(item["updated_at"]), "updated_at")


def _validate_completed_aggregate_files(
    connection: sqlite3.Connection,
    study_dir: Path,
) -> None:
    for (
        batch_id,
        study_id,
        skill_pack_version_id,
        created_at,
        run_count,
        failure_count,
        expected_sha256,
    ) in _completed_operation_summaries(connection):
        validate_study_batch_id(batch_id)
        aggregate_payload = _load_aggregate_payload(
            study_dir,
            batch_id,
            study_id=study_id,
            skill_pack_version_id=skill_pack_version_id,
            created_at=created_at,
            run_count=run_count,
            failure_count=failure_count,
        )
        if _canonical_json_sha256(aggregate_payload) != str(expected_sha256):
            raise ValueError(
                "Completed study batch aggregate conflicts with its journal"
            )


def _completed_operation_summaries(
    connection: sqlite3.Connection,
) -> list[tuple[str, str, str, str, int, int, str]]:
    return [
        (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            int(row[4]),
            int(row[5]),
            str(row[6]),
        )
        for row in connection.execute(
            """
            select operation.batch_id, operation.study_id,
                   operation.skill_pack_version_id, operation.created_at,
                   sum(case when item.stage = 'completed' then 1 else 0 end),
                   sum(case when item.stage = 'rejected' then 1 else 0 end),
                   operation.aggregate_payload_sha256
            from study_batch_operations as operation
            left join study_batch_operation_items as item
              on item.batch_id = operation.batch_id
            where operation.status = 'completed'
            group by operation.batch_id, operation.study_id,
                     operation.skill_pack_version_id, operation.created_at,
                     operation.aggregate_payload_sha256
            order by operation.batch_id
            """
        )
    ]


def _load_aggregate_payload(
    study_dir: Path,
    batch_id: str,
    *,
    study_id: str,
    skill_pack_version_id: str,
    created_at: str,
    run_count: int,
    failure_count: int,
) -> dict[str, Any]:
    aggregate_path = (
        study_dir / "batches" / batch_id / "aggregate_results.json"
    )
    if aggregate_path.is_symlink() or not aggregate_path.is_file():
        raise ValueError(
            "Completed study batch aggregate snapshot is missing"
        )
    try:
        payload = json.loads(aggregate_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(
            "Completed study batch aggregate snapshot is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(
            "Completed study batch aggregate snapshot is invalid"
        )
    failures = payload.get("failures")
    if (
        payload.get("study_id") != study_id
        or payload.get("batch_id") != batch_id
        or payload.get("skill_pack_version_id") != skill_pack_version_id
        or payload.get("created_at") != created_at
        or type(payload.get("run_count")) is not int
        or payload.get("run_count") != run_count
        or type(payload.get("failure_count")) is not int
        or payload.get("failure_count") != failure_count
        or not isinstance(failures, list)
        or len(failures) != failure_count
        or not isinstance(payload.get("results"), list)
    ):
        raise ValueError(
            "Completed study batch aggregate identity is invalid"
        )
    return payload


def _canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


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
    validate_study_skill_pack_version_id(skill_pack_version_id)
    _validate_sha256(skill_pack_sha256, "skill_pack_sha256")
    _validate_sha256(request_sha256, "request_sha256")
    if item_count < 0:
        raise ValueError("item_count must be non-negative")
    _validate_timestamp(created_at, "created_at")


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
        ("import_id", import_id),
        ("project_source_id", project_source_id),
        ("transcript_revision_id", transcript_revision_id),
    ):
        if not value.strip():
            raise ValueError(f"{field_name} must be non-empty")
    if not _PATH_SAFE_IDENTIFIER.fullmatch(run_id):
        raise ValueError("run_id must be a path-safe identifier")
    if _WINDOWS_DEVICE_NAME.fullmatch(run_id):
        raise ValueError("run_id must be portable across supported filesystems")
    _validate_timestamp(created_at, "created_at")
    _validate_sha256(item_request_sha256, "item_request_sha256")
    _validate_sha256(source_blob_sha256, "source_blob_sha256")
    _validate_sha256(transcript_sha256, "transcript_sha256")


def validate_study_batch_id(batch_id: str) -> None:
    if not _BATCH_ID.fullmatch(batch_id):
        raise ValueError("batch_id must be a generated study batch identifier")


def validate_study_skill_pack_version_id(skill_pack_version_id: str) -> None:
    if (
        not isinstance(skill_pack_version_id, str)
        or len(skill_pack_version_id) > MAX_SKILL_PACK_VERSION_ID_LENGTH
        or not _SKILL_PACK_VERSION_ID.fullmatch(skill_pack_version_id)
    ):
        raise ValueError(
            "skill_pack_version_id must be a bounded normalized version identifier"
        )


def _validate_sha256(value: str, field_name: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256")


def _validate_timestamp(value: str, field_name: str) -> None:
    if not value.strip() or len(value) > 64:
        raise ValueError(f"{field_name} must be a bounded ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} must be a bounded ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _audit_event_id(study_id: str, batch_id: str) -> str:
    return sha256(f"batch.completed\0{study_id}\0{batch_id}".encode("utf-8")).hexdigest()
