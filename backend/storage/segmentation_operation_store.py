from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from backend.storage.sqlite_migrations import (
    Migration,
    apply_migrations,
    schema_status,
)


SEGMENTATION_OPERATION_KINDS = {"create", "patch", "verify", "rewrite"}
SEGMENTATION_OPERATION_STAGES = (
    "prepared",
    "source_blob_stored",
    "evidence_cataloged",
    "specialist_artifacts_written",
    "snapshot_written",
)
_NEXT_STAGE = dict(
    zip(
        SEGMENTATION_OPERATION_STAGES,
        SEGMENTATION_OPERATION_STAGES[1:],
        strict=False,
    )
)
_ERROR_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")


class SegmentationOperationStore:
    def __init__(self, root: Path | str = "local_data") -> None:
        self.root = Path(root)
        self.db_path = self.root / "segmentation.sqlite3"

    def begin(
        self,
        *,
        run_id: str,
        import_id: str,
        operation_kind: str,
        previous_payload_sha256: str,
        payload_sha256: str,
    ) -> str:
        _validate_identity(
            run_id=run_id,
            import_id=import_id,
            operation_kind=operation_kind,
            previous_payload_sha256=previous_payload_sha256,
            payload_sha256=payload_sha256,
        )
        self._ensure_schema()
        operation_id = _operation_id(
            run_id,
            operation_kind,
            previous_payload_sha256,
            payload_sha256,
        )
        now = _utc_now()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("begin immediate")
            conflicting_import = connection.execute(
                """
                select import_id from segmentation_operations
                where run_id = ? and import_id != ?
                limit 1
                """,
                (run_id, import_id),
            ).fetchone()
            if conflicting_import is not None:
                raise ValueError("Segmentation run import identity conflicts with journal")
            conflicting_run = connection.execute(
                """
                select run_id from segmentation_operations
                where import_id = ? and run_id != ?
                limit 1
                """,
                (import_id, run_id),
            ).fetchone()
            if conflicting_run is not None:
                raise ValueError("Segmentation import run identity conflicts with journal")

            stored = connection.execute(
                """
                select run_id, import_id, operation_kind,
                       previous_payload_sha256, payload_sha256
                from segmentation_operations where operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            expected = (
                run_id,
                import_id,
                operation_kind,
                previous_payload_sha256,
                payload_sha256,
            )
            if stored is not None:
                if stored != expected:
                    raise ValueError(
                        "Segmentation operation identity conflicts with journal"
                    )
                active_other = connection.execute(
                    """
                    select operation_id from segmentation_operations
                    where run_id = ? and status = 'running' and operation_id != ?
                    limit 1
                    """,
                    (run_id, operation_id),
                ).fetchone()
                if active_other is not None:
                    raise RuntimeError(
                        "Another segmentation operation is already running"
                    )
                connection.execute(
                    """
                    update segmentation_operations
                    set status = 'running', stage = 'prepared',
                        attempt_count = attempt_count + 1, last_error_type = '',
                        updated_at = ?, completed_at = ''
                    where operation_id = ?
                    """,
                    (now, operation_id),
                )
                return operation_id

            active = connection.execute(
                """
                select operation_id from segmentation_operations
                where run_id = ? and status = 'running'
                limit 1
                """,
                (run_id,),
            ).fetchone()
            if active is not None:
                raise RuntimeError("Another segmentation operation is already running")
            connection.execute(
                """
                insert into segmentation_operations (
                  operation_id, run_id, import_id, operation_kind,
                  previous_payload_sha256, payload_sha256,
                  status, stage, attempt_count, last_error_type,
                  started_at, updated_at, completed_at
                ) values (?, ?, ?, ?, ?, ?, 'running', 'prepared', 1, '', ?, ?, '')
                """,
                (
                    operation_id,
                    run_id,
                    import_id,
                    operation_kind,
                    previous_payload_sha256,
                    payload_sha256,
                    now,
                    now,
                ),
            )
        return operation_id

    def advance(self, operation_id: str, stage: str) -> None:
        if stage not in SEGMENTATION_OPERATION_STAGES[1:]:
            raise ValueError(f"Unsupported segmentation operation stage: {stage}")
        self._ensure_schema()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("begin immediate")
            status, current_stage = self._state(connection, operation_id)
            if status != "running":
                raise RuntimeError("Segmentation operation is not running")
            if _NEXT_STAGE.get(current_stage) != stage:
                raise ValueError(
                    f"Segmentation operation cannot advance from {current_stage} to {stage}"
                )
            connection.execute(
                """
                update segmentation_operations set stage = ?, updated_at = ?
                where operation_id = ? and status = 'running' and stage = ?
                """,
                (stage, _utc_now(), operation_id, current_stage),
            )

    def fail(self, operation_id: str, *, error_type: str) -> None:
        if not _ERROR_TYPE.fullmatch(error_type):
            raise ValueError("error_type must be an exception class name")
        self._ensure_schema()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("begin immediate")
            status, _ = self._state(connection, operation_id)
            if status != "running":
                raise RuntimeError("Segmentation operation is not running")
            connection.execute(
                """
                update segmentation_operations
                set status = 'failed', last_error_type = ?,
                    updated_at = ?, completed_at = ''
                where operation_id = ? and status = 'running'
                """,
                (error_type, _utc_now(), operation_id),
            )

    def complete(self, operation_id: str) -> None:
        self._ensure_schema()
        now = _utc_now()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("begin immediate")
            status, stage = self._state(connection, operation_id)
            if status != "running":
                raise RuntimeError("Segmentation operation is not running")
            if stage != "snapshot_written":
                raise ValueError(
                    "Segmentation operation cannot complete before snapshot_written"
                )
            connection.execute(
                """
                update segmentation_operations
                set status = 'completed', stage = 'completed',
                    last_error_type = '', updated_at = ?, completed_at = ?
                where operation_id = ? and status = 'running'
                  and stage = 'snapshot_written'
                """,
                (now, now, operation_id),
            )

    def list_operations(
        self,
        *,
        limit: int = 100,
        incomplete_only: bool = False,
    ) -> list[dict[str, Any]]:
        self._ensure_schema()
        bounded_limit = max(1, min(limit, 500))
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                select operation_id, run_id, import_id, operation_kind,
                       previous_payload_sha256, payload_sha256,
                       status, stage, attempt_count, last_error_type,
                       started_at, updated_at, completed_at
                from segmentation_operations
                where ? = 0 or status != 'completed'
                order by updated_at desc, operation_id
                limit ?
                """,
                (int(incomplete_only), bounded_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def migration_status(self) -> list[dict[str, object]]:
        self._ensure_schema()
        with sqlite3.connect(self.db_path) as connection:
            return schema_status(connection)

    def _ensure_schema(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            apply_migrations(
                connection,
                database_name="segmentation operations",
                migrations=SEGMENTATION_OPERATION_MIGRATIONS,
            )

    @staticmethod
    def _state(
        connection: sqlite3.Connection,
        operation_id: str,
    ) -> tuple[str, str]:
        row = connection.execute(
            """
            select status, stage from segmentation_operations
            where operation_id = ?
            """,
            (operation_id,),
        ).fetchone()
        if row is None:
            raise FileNotFoundError(operation_id)
        return str(row[0]), str(row[1])


def _create_segmentation_operations(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        create table if not exists segmentation_operations (
          operation_id text primary key,
          run_id text not null,
          import_id text not null,
          operation_kind text not null check (
            operation_kind in ('create', 'patch', 'verify', 'rewrite')
          ),
          previous_payload_sha256 text not null,
          payload_sha256 text not null check (length(payload_sha256) = 64),
          status text not null check (status in ('running', 'failed', 'completed')),
          stage text not null check (
            stage in (
              'prepared', 'source_blob_stored', 'evidence_cataloged',
              'specialist_artifacts_written', 'snapshot_written', 'completed'
            )
          ),
          attempt_count integer not null check (attempt_count > 0),
          last_error_type text not null default '',
          started_at text not null,
          updated_at text not null,
          completed_at text not null default '',
          check (
            (operation_kind = 'create' and previous_payload_sha256 = '')
            or
            (operation_kind != 'create' and length(previous_payload_sha256) = 64)
          )
        )
        """
    )
    connection.execute(
        """
        create index if not exists segmentation_operations_run_idx
        on segmentation_operations (run_id, updated_at desc)
        """
    )
    connection.execute(
        """
        create index if not exists segmentation_operations_status_idx
        on segmentation_operations (status, updated_at desc)
        """
    )
    connection.execute(
        """
        create unique index if not exists segmentation_operations_active_run_idx
        on segmentation_operations (run_id) where status = 'running'
        """
    )


SEGMENTATION_OPERATION_MIGRATIONS = [
    Migration(1, "create-segmentation-operations", _create_segmentation_operations),
]


def _validate_identity(
    *,
    run_id: str,
    import_id: str,
    operation_kind: str,
    previous_payload_sha256: str,
    payload_sha256: str,
) -> None:
    if not run_id.strip() or not import_id.strip():
        raise ValueError("Segmentation operation identities must be non-empty")
    if operation_kind not in SEGMENTATION_OPERATION_KINDS:
        raise ValueError(f"Unsupported segmentation operation kind: {operation_kind}")
    _validate_sha256(payload_sha256, field="payload_sha256")
    if operation_kind == "create":
        if previous_payload_sha256:
            raise ValueError("Create operations cannot have a previous payload")
    else:
        _validate_sha256(
            previous_payload_sha256,
            field="previous_payload_sha256",
        )


def _validate_sha256(value: str, *, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def _operation_id(
    run_id: str,
    operation_kind: str,
    previous_payload_sha256: str,
    payload_sha256: str,
) -> str:
    identity = ":".join(
        (run_id, operation_kind, previous_payload_sha256, payload_sha256)
    )
    return f"sop_{sha256(identity.encode('utf-8')).hexdigest()[:32]}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
