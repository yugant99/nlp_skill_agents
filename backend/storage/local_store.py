from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from backend.analysis.pipeline import AnalysisRun
from backend.storage.atomic import atomic_text_writer, atomic_write_text
from backend.storage.evidence_catalog import EvidenceCatalog, EvidenceImportRecord
from backend.storage.source_blob_store import SourceBlobStore
from backend.storage.sqlite_migrations import (
    Migration,
    add_text_column_if_missing,
    apply_migrations,
    schema_status,
)


@dataclass(frozen=True)
class StoredRun:
    run_id: str
    run_dir: Path
    export_dir: Path
    results_json: Path


class LocalRunStore:
    def __init__(self, root: Path | str = "local_data") -> None:
        self.root = Path(root)
        self.runs_dir = self.root / "runs"
        self.exports_dir = self.root / "exports"
        self.db_path = self.root / "runs.sqlite3"

    def persist_run(
        self,
        run: AnalysisRun,
        *,
        source_bytes: bytes | None = None,
    ) -> StoredRun:
        self._ensure_schema()
        evidence_catalog = EvidenceCatalog(self.root)
        evidence_catalog.validate_lineage(
            project_source_id=run.project_source_id,
            parent_transcript_revision_id=run.parent_transcript_revision_id,
            workspace_id=run.workspace_id,
            transcript_revision_id=run.transcript_revision_id,
        )
        run_dir = self.runs_dir / run.run_id
        export_dir = self.exports_dir / run.run_id
        results_json = run_dir / "results.json"
        run_payload_sha256 = _run_payload_sha256(run)
        self._begin_operation(run, run_payload_sha256)

        completed_stage = "validated"
        try:
            SourceBlobStore(self.root).store(
                source_bytes
                if source_bytes is not None
                else run.source_content.encode("utf-8"),
                run.source_blob_sha256,
            )
            completed_stage = "source_blob_stored"
            self._advance_operation(run.run_id, completed_stage)

            evidence_catalog.record_import(_evidence_import_record(run))
            completed_stage = "evidence_cataloged"
            self._advance_operation(run.run_id, completed_stage)

            run_dir.mkdir(parents=True, exist_ok=True)
            export_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                results_json,
                json.dumps(_run_to_payload(run), indent=2),
            )
            for result in run.results:
                _write_metric_csv(export_dir / f"{result.metric_id}.csv", result.rows)
            completed_stage = "artifacts_written"
            self._advance_operation(run.run_id, completed_stage)

            self._record_run_and_complete_operation(run, run_payload_sha256)
        except BaseException as exc:
            try:
                self._fail_operation(
                    run.run_id,
                    completed_stage=completed_stage,
                    error_type=type(exc).__name__,
                )
            except Exception as journal_exc:
                raise RuntimeError(
                    "Analysis persistence failed and its journal could not be updated"
                ) from journal_exc
            raise
        return StoredRun(
            run_id=run.run_id,
            run_dir=run_dir,
            export_dir=export_dir,
            results_json=results_json,
        )

    def export_path(self, run_id: str, filename: str) -> Path:
        return self.exports_dir / run_id / filename

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        self._ensure_schema()
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                select run_id, import_id, project_source_id,
                       parent_transcript_revision_id, workspace_id,
                       source_blob_sha256, source_media_type,
                       source_id, transcript_sha256, transcript_revision_id,
                       source_filename, created_at, metric_count
                from analysis_runs
                order by created_at desc
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "import_id": row["import_id"],
                "project_source_id": row["project_source_id"],
                "parent_transcript_revision_id": row[
                    "parent_transcript_revision_id"
                ],
                "workspace_id": row["workspace_id"],
                "source_blob_sha256": row["source_blob_sha256"],
                "source_media_type": row["source_media_type"],
                "source_id": row["source_id"],
                "transcript_sha256": row["transcript_sha256"],
                "transcript_revision_id": row["transcript_revision_id"],
                "source_filename": row["source_filename"],
                "created_at": row["created_at"],
                "metric_count": row["metric_count"],
                "results_json": str(self.runs_dir / row["run_id"] / "results.json"),
                "export_dir": str(self.exports_dir / row["run_id"]),
            }
            for row in rows
        ]

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
                select run_id, import_id, run_payload_sha256, status, stage,
                       attempt_count, last_error_type, started_at, updated_at,
                       completed_at
                from analysis_operations
                where ? = 0 or status != 'completed'
                order by updated_at desc, run_id
                limit ?
                """,
                (int(incomplete_only), bounded_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def _ensure_schema(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            apply_migrations(
                connection,
                database_name="analysis runs",
                migrations=ANALYSIS_RUN_MIGRATIONS,
            )

    def migration_status(self) -> list[dict[str, object]]:
        self._ensure_schema()
        with sqlite3.connect(self.db_path) as connection:
            return schema_status(connection)

    def _begin_operation(self, run: AnalysisRun, run_payload_sha256: str) -> None:
        now = _utc_now()
        with sqlite3.connect(self.db_path) as connection:
            inserted = connection.execute(
                """
                insert or ignore into analysis_operations (
                  run_id, import_id, run_payload_sha256, status, stage,
                  attempt_count, last_error_type, started_at, updated_at,
                  completed_at
                ) values (?, ?, ?, 'running', 'validated', 1, '', ?, ?, '')
                """,
                (run.run_id, run.import_id, run_payload_sha256, now, now),
            ).rowcount
            if inserted:
                return
            stored = connection.execute(
                """
                select import_id, run_payload_sha256
                from analysis_operations where run_id = ?
                """,
                (run.run_id,),
            ).fetchone()
            if stored is None:
                raise RuntimeError("Analysis operation disappeared during retry")
            if stored != (run.import_id, run_payload_sha256):
                raise ValueError("Analysis operation identity conflicts with journal")
            connection.execute(
                """
                update analysis_operations
                set status = 'running', stage = 'validated',
                    attempt_count = attempt_count + 1, last_error_type = '',
                    updated_at = ?, completed_at = ''
                where run_id = ?
                """,
                (now, run.run_id),
            )

    def _advance_operation(self, run_id: str, completed_stage: str) -> None:
        with sqlite3.connect(self.db_path) as connection:
            updated = connection.execute(
                """
                update analysis_operations set stage = ?, updated_at = ?
                where run_id = ? and status = 'running'
                """,
                (completed_stage, _utc_now(), run_id),
            ).rowcount
            if updated != 1:
                raise RuntimeError("Analysis operation is not running")

    def _fail_operation(
        self,
        run_id: str,
        *,
        completed_stage: str,
        error_type: str,
    ) -> None:
        with sqlite3.connect(self.db_path) as connection:
            updated = connection.execute(
                """
                update analysis_operations
                set status = 'failed', stage = ?, last_error_type = ?,
                    updated_at = ?, completed_at = ''
                where run_id = ? and status = 'running'
                """,
                (completed_stage, error_type, _utc_now(), run_id),
            ).rowcount
            if updated != 1:
                raise RuntimeError("Analysis operation failure could not be recorded")

    def _record_run_and_complete_operation(
        self,
        run: AnalysisRun,
        run_payload_sha256: str,
    ) -> None:
        expected_run = (
            run.import_id,
            run.project_source_id,
            run.parent_transcript_revision_id,
            run.workspace_id,
            run.source_blob_sha256,
            run.source_media_type,
            run.source_id,
            run.transcript_sha256,
            run.transcript_revision_id,
            run.source_filename,
            run.created_at,
            len(run.results),
        )
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                insert or ignore into analysis_runs (
                  run_id,
                  import_id,
                  project_source_id,
                  parent_transcript_revision_id,
                  workspace_id,
                  source_blob_sha256,
                  source_media_type,
                  source_id,
                  transcript_sha256,
                  transcript_revision_id,
                  source_filename,
                  created_at,
                  metric_count
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.import_id,
                    run.project_source_id,
                    run.parent_transcript_revision_id,
                    run.workspace_id,
                    run.source_blob_sha256,
                    run.source_media_type,
                    run.source_id,
                    run.transcript_sha256,
                    run.transcript_revision_id,
                    run.source_filename,
                    run.created_at,
                    len(run.results),
                ),
            )
            stored_run = connection.execute(
                """
                select import_id, project_source_id,
                       parent_transcript_revision_id, workspace_id,
                       source_blob_sha256, source_media_type, source_id,
                       transcript_sha256, transcript_revision_id,
                       source_filename, created_at, metric_count
                from analysis_runs where run_id = ?
                """,
                (run.run_id,),
            ).fetchone()
            if stored_run != expected_run:
                raise ValueError("Analysis run identity conflicts with stored run")
            now = _utc_now()
            completed = connection.execute(
                """
                update analysis_operations
                set status = 'completed', stage = 'completed',
                    last_error_type = '', updated_at = ?, completed_at = ?
                where run_id = ? and import_id = ? and run_payload_sha256 = ?
                  and status = 'running'
                """,
                (now, now, run.run_id, run.import_id, run_payload_sha256),
            ).rowcount
            if completed != 1:
                raise RuntimeError("Analysis operation could not be completed")


def _run_to_payload(run: AnalysisRun) -> dict[str, Any]:
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
        "created_at": run.created_at,
        "participant_id": run.transcript.config.participant_id,
        "skill_pack": _skill_pack_payload(run),
        "turn_count": len(run.transcript.turns),
        "results": [asdict(result) for result in run.results],
    }


def _run_payload_sha256(run: AnalysisRun) -> str:
    canonical_payload = json.dumps(
        _run_to_payload(run),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(canonical_payload).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _evidence_import_record(run: AnalysisRun) -> EvidenceImportRecord:
    return EvidenceImportRecord(
        import_id=run.import_id,
        run_id=run.run_id,
        pipeline="analysis",
        project_source_id=run.project_source_id,
        parent_transcript_revision_id=run.parent_transcript_revision_id,
        workspace_id=run.workspace_id,
        source_id=run.source_id,
        source_filename=run.source_filename,
        source_media_type=run.source_media_type,
        source_blob_sha256=run.source_blob_sha256,
        transcript_revision_id=run.transcript_revision_id,
        transcript_sha256=run.transcript_sha256,
        imported_at=run.created_at,
    )


def _skill_pack_payload(run: AnalysisRun) -> dict[str, str] | None:
    config = run.transcript.config
    if not config.skill_pack_id:
        return None
    return {
        "id": config.skill_pack_id,
        "name": config.skill_pack_name,
        "version": config.skill_pack_version,
    }


def _write_metric_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = _ordered_fieldnames(rows)
    with atomic_text_writer(path, newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _ordered_fieldnames(rows: list[dict[str, Any]]) -> list[str]:
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    return fieldnames


def _analysis_v1_base_runs(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        create table if not exists analysis_runs (
          run_id text primary key,
          source_filename text not null,
          created_at text not null,
          metric_count integer not null
        )
        """
    )


def _analysis_v2_evidence_identity(connection: sqlite3.Connection) -> None:
    _add_analysis_columns(
        connection,
        "source_id",
        "transcript_sha256",
        "transcript_revision_id",
    )


def _analysis_v3_import_identity(connection: sqlite3.Connection) -> None:
    _add_analysis_columns(
        connection,
        "import_id",
        "source_blob_sha256",
        "source_media_type",
    )


def _analysis_v4_source_lineage(connection: sqlite3.Connection) -> None:
    _add_analysis_columns(
        connection,
        "project_source_id",
        "parent_transcript_revision_id",
        "workspace_id",
    )


def _analysis_v5_created_index(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        create index if not exists analysis_runs_created_idx
        on analysis_runs (created_at desc)
        """
    )


def _analysis_v6_operation_journal(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        create table if not exists analysis_operations (
          run_id text primary key,
          import_id text not null,
          run_payload_sha256 text not null,
          status text not null check (status in ('running', 'failed', 'completed')),
          stage text not null check (
            stage in (
              'validated', 'source_blob_stored', 'evidence_cataloged',
              'artifacts_written', 'completed'
            )
          ),
          attempt_count integer not null check (attempt_count > 0),
          last_error_type text not null default '',
          started_at text not null,
          updated_at text not null,
          completed_at text not null default ''
        )
        """
    )
    connection.execute(
        """
        create index if not exists analysis_operations_status_idx
        on analysis_operations (status, updated_at desc)
        """
    )


def _add_analysis_columns(
    connection: sqlite3.Connection,
    *columns: str,
) -> None:
    for column in columns:
        add_text_column_if_missing(
            connection,
            table="analysis_runs",
            column=column,
        )


ANALYSIS_RUN_MIGRATIONS = [
    Migration(1, "create-base-analysis-runs", _analysis_v1_base_runs),
    Migration(2, "add-evidence-identity", _analysis_v2_evidence_identity),
    Migration(3, "add-source-import-identity", _analysis_v3_import_identity),
    Migration(4, "add-project-source-lineage", _analysis_v4_source_lineage),
    Migration(5, "index-analysis-run-history", _analysis_v5_created_index),
    Migration(6, "create-analysis-operation-journal", _analysis_v6_operation_journal),
]
