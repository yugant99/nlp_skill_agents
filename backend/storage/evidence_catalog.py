from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvidenceImportRecord:
    import_id: str
    run_id: str
    pipeline: str
    source_id: str
    source_filename: str
    source_media_type: str
    source_blob_sha256: str
    transcript_revision_id: str
    transcript_sha256: str
    imported_at: str


class EvidenceCatalog:
    def __init__(self, root: Path | str = "local_data") -> None:
        self.root = Path(root)
        self.db_path = self.root / "evidence.sqlite3"

    def record_import(self, record: EvidenceImportRecord) -> None:
        self._ensure_schema()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                insert or ignore into transcript_revisions (
                  transcript_revision_id, source_id, transcript_sha256, created_at
                ) values (?, ?, ?, ?)
                """,
                (
                    record.transcript_revision_id,
                    record.source_id,
                    record.transcript_sha256,
                    record.imported_at,
                ),
            )
            stored_revision = connection.execute(
                """
                select source_id, transcript_sha256
                from transcript_revisions
                where transcript_revision_id = ?
                """,
                (record.transcript_revision_id,),
            ).fetchone()
            if stored_revision != (record.source_id, record.transcript_sha256):
                raise ValueError("Transcript revision identity conflicts with catalog")
            connection.execute(
                """
                insert or ignore into source_imports (
                  import_id, run_id, pipeline, source_id, source_filename,
                  source_media_type, source_blob_sha256,
                  transcript_revision_id, imported_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.import_id,
                    record.run_id,
                    record.pipeline,
                    record.source_id,
                    record.source_filename,
                    record.source_media_type,
                    record.source_blob_sha256,
                    record.transcript_revision_id,
                    record.imported_at,
                ),
            )
            stored_import = connection.execute(
                """
                select run_id, pipeline, source_id, source_filename,
                       source_media_type, source_blob_sha256,
                       transcript_revision_id, imported_at
                from source_imports
                where import_id = ?
                """,
                (record.import_id,),
            ).fetchone()
            expected_import = (
                record.run_id,
                record.pipeline,
                record.source_id,
                record.source_filename,
                record.source_media_type,
                record.source_blob_sha256,
                record.transcript_revision_id,
                record.imported_at,
            )
            if stored_import != expected_import:
                raise ValueError("Source import identity conflicts with catalog")

    def list_imports(self, limit: int = 100) -> list[dict[str, Any]]:
        self._ensure_schema()
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                select import_id, run_id, pipeline, source_id, source_filename,
                       source_media_type, source_blob_sha256,
                       transcript_revision_id, imported_at
                from source_imports
                order by imported_at desc, import_id desc
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _ensure_schema(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("pragma foreign_keys = on")
            connection.execute(
                """
                create table if not exists transcript_revisions (
                  transcript_revision_id text primary key,
                  source_id text not null,
                  transcript_sha256 text not null,
                  created_at text not null
                )
                """
            )
            connection.execute(
                """
                create table if not exists source_imports (
                  import_id text primary key,
                  run_id text not null,
                  pipeline text not null,
                  source_id text not null,
                  source_filename text not null,
                  source_media_type text not null,
                  source_blob_sha256 text not null,
                  transcript_revision_id text not null references transcript_revisions,
                  imported_at text not null
                )
                """
            )
            connection.execute(
                """
                create index if not exists source_imports_revision_idx
                on source_imports (transcript_revision_id)
                """
            )
