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
    project_source_id: str = ""
    parent_transcript_revision_id: str = ""
    workspace_id: str = "local-default"


class EvidenceCatalog:
    def __init__(self, root: Path | str = "local_data") -> None:
        self.root = Path(root)
        self.db_path = self.root / "evidence.sqlite3"

    def record_import(self, record: EvidenceImportRecord) -> None:
        self._ensure_schema()
        project_source_id = record.project_source_id or _legacy_source_id(
            record.import_id
        )
        parent_revision_id = record.parent_transcript_revision_id
        workspace_id = record.workspace_id or "local-default"
        if parent_revision_id == record.transcript_revision_id:
            raise ValueError("Transcript revision cannot be its own parent")

        with sqlite3.connect(self.db_path) as connection:
            connection.execute("pragma foreign_keys = on")
            existing_source = connection.execute(
                """
                select workspace_id from project_sources where project_source_id = ?
                """,
                (project_source_id,),
            ).fetchone()
            connection.execute(
                """
                insert or ignore into project_sources (
                  project_source_id, workspace_id, created_at
                ) values (?, ?, ?)
                """,
                (project_source_id, workspace_id, record.imported_at),
            )
            stored_source = connection.execute(
                """
                select workspace_id from project_sources where project_source_id = ?
                """,
                (project_source_id,),
            ).fetchone()
            if stored_source != (workspace_id,):
                raise ValueError("Project source belongs to a different workspace")

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

            existing_source_revision = connection.execute(
                """
                select parent_transcript_revision_id
                from source_revisions
                where project_source_id = ? and transcript_revision_id = ?
                """,
                (project_source_id, record.transcript_revision_id),
            ).fetchone()
            if (
                existing_source is not None
                and existing_source_revision is None
                and not parent_revision_id
            ):
                raise ValueError("A new revision for an existing source requires a parent")

            if parent_revision_id:
                parent = connection.execute(
                    """
                    select 1 from source_revisions
                    where project_source_id = ? and transcript_revision_id = ?
                    """,
                    (project_source_id, parent_revision_id),
                ).fetchone()
                if parent is None:
                    raise ValueError("Parent revision does not belong to project source")

            connection.execute(
                """
                insert or ignore into source_revisions (
                  project_source_id, transcript_revision_id,
                  parent_transcript_revision_id, created_at
                ) values (?, ?, ?, ?)
                """,
                (
                    project_source_id,
                    record.transcript_revision_id,
                    parent_revision_id,
                    record.imported_at,
                ),
            )
            stored_source_revision = connection.execute(
                """
                select parent_transcript_revision_id
                from source_revisions
                where project_source_id = ? and transcript_revision_id = ?
                """,
                (project_source_id, record.transcript_revision_id),
            ).fetchone()
            if stored_source_revision != (parent_revision_id,):
                raise ValueError("Source revision lineage conflicts with catalog")

            connection.execute(
                """
                insert or ignore into source_imports (
                  import_id, run_id, pipeline, project_source_id, source_id,
                  source_filename, source_media_type, source_blob_sha256,
                  transcript_revision_id, parent_transcript_revision_id, imported_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.import_id,
                    record.run_id,
                    record.pipeline,
                    project_source_id,
                    record.source_id,
                    record.source_filename,
                    record.source_media_type,
                    record.source_blob_sha256,
                    record.transcript_revision_id,
                    parent_revision_id,
                    record.imported_at,
                ),
            )
            stored_import = connection.execute(
                """
                select run_id, pipeline, project_source_id, source_id,
                       source_filename, source_media_type, source_blob_sha256,
                       transcript_revision_id, parent_transcript_revision_id,
                       imported_at
                from source_imports where import_id = ?
                """,
                (record.import_id,),
            ).fetchone()
            expected_import = (
                record.run_id,
                record.pipeline,
                project_source_id,
                record.source_id,
                record.source_filename,
                record.source_media_type,
                record.source_blob_sha256,
                record.transcript_revision_id,
                parent_revision_id,
                record.imported_at,
            )
            if stored_import != expected_import:
                raise ValueError("Source import identity conflicts with catalog")

    def validate_lineage(
        self,
        *,
        project_source_id: str,
        parent_transcript_revision_id: str,
        workspace_id: str,
        transcript_revision_id: str,
    ) -> None:
        if not project_source_id and not parent_transcript_revision_id:
            return
        if not project_source_id:
            raise ValueError("A parent revision requires project_source_id")
        self._ensure_schema()
        with sqlite3.connect(self.db_path) as connection:
            source = connection.execute(
                """
                select workspace_id from project_sources where project_source_id = ?
                """,
                (project_source_id,),
            ).fetchone()
            if source is None:
                if parent_transcript_revision_id:
                    raise ValueError("Project source does not exist")
                return
            if source != (workspace_id or "local-default",):
                raise ValueError("Project source belongs to a different workspace")
            current_revision = connection.execute(
                """
                select parent_transcript_revision_id
                from source_revisions
                where project_source_id = ? and transcript_revision_id = ?
                """,
                (project_source_id, transcript_revision_id),
            ).fetchone()
            if current_revision is not None:
                if current_revision != (parent_transcript_revision_id,):
                    raise ValueError("Source revision lineage conflicts with catalog")
                return
            if not parent_transcript_revision_id:
                raise ValueError("A new revision for an existing source requires a parent")
            parent = connection.execute(
                """
                select 1 from source_revisions
                where project_source_id = ? and transcript_revision_id = ?
                """,
                (project_source_id, parent_transcript_revision_id),
            ).fetchone()
            if parent is None:
                raise ValueError("Parent revision does not belong to project source")

    def list_imports(self, limit: int = 100) -> list[dict[str, Any]]:
        self._ensure_schema()
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                select import_id, run_id, pipeline, project_source_id, source_id,
                       source_filename, source_media_type, source_blob_sha256,
                       transcript_revision_id, parent_transcript_revision_id,
                       imported_at
                from source_imports
                order by imported_at desc, import_id desc
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def source_history(self, project_source_id: str) -> dict[str, Any]:
        self._ensure_schema()
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            source = connection.execute(
                """
                select project_source_id, workspace_id, created_at
                from project_sources where project_source_id = ?
                """,
                (project_source_id,),
            ).fetchone()
            if source is None:
                raise FileNotFoundError(project_source_id)
            revisions = connection.execute(
                """
                select sr.transcript_revision_id,
                       sr.parent_transcript_revision_id,
                       tr.transcript_sha256,
                       sr.created_at
                from source_revisions sr
                join transcript_revisions tr using (transcript_revision_id)
                where sr.project_source_id = ?
                order by sr.created_at, sr.transcript_revision_id
                """,
                (project_source_id,),
            ).fetchall()
        return {
            "source": dict(source),
            "revisions": [dict(revision) for revision in revisions],
        }

    def workspace_import_records(self, workspace_id: str) -> list[EvidenceImportRecord]:
        self._ensure_schema()
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                select si.import_id, si.run_id, si.pipeline, si.source_id,
                       si.source_filename, si.source_media_type,
                       si.source_blob_sha256, si.transcript_revision_id,
                       tr.transcript_sha256, si.imported_at,
                       si.project_source_id,
                       si.parent_transcript_revision_id,
                       ps.workspace_id
                from source_imports si
                join project_sources ps using (project_source_id)
                join transcript_revisions tr using (transcript_revision_id)
                where ps.workspace_id = ?
                order by si.imported_at, si.import_id
                """,
                (workspace_id,),
            ).fetchall()
        return [EvidenceImportRecord(**dict(row)) for row in rows]

    def _ensure_schema(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("pragma foreign_keys = on")
            connection.execute(
                """
                create table if not exists project_sources (
                  project_source_id text primary key,
                  workspace_id text not null,
                  created_at text not null
                )
                """
            )
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
                create table if not exists source_revisions (
                  project_source_id text not null references project_sources,
                  transcript_revision_id text not null references transcript_revisions,
                  parent_transcript_revision_id text not null default '',
                  created_at text not null,
                  primary key (project_source_id, transcript_revision_id)
                )
                """
            )
            connection.execute(
                """
                create table if not exists source_imports (
                  import_id text primary key,
                  run_id text not null,
                  pipeline text not null,
                  project_source_id text not null references project_sources,
                  source_id text not null,
                  source_filename text not null,
                  source_media_type text not null,
                  source_blob_sha256 text not null,
                  transcript_revision_id text not null references transcript_revisions,
                  parent_transcript_revision_id text not null default '',
                  imported_at text not null
                )
                """
            )
            existing_columns = {
                row[1] for row in connection.execute("pragma table_info(source_imports)")
            }
            for column in (
                "project_source_id",
                "parent_transcript_revision_id",
            ):
                if column not in existing_columns:
                    connection.execute(
                        f"alter table source_imports add column {column} text not null default ''"
                    )
            self._backfill_legacy_sources(connection)
            connection.execute(
                """
                create index if not exists source_imports_revision_idx
                on source_imports (transcript_revision_id)
                """
            )
            connection.execute(
                """
                create index if not exists source_revisions_parent_idx
                on source_revisions (project_source_id, parent_transcript_revision_id)
                """
            )

    @staticmethod
    def _backfill_legacy_sources(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            select import_id, transcript_revision_id, imported_at, project_source_id
            from source_imports
            """
        ).fetchall()
        for import_id, revision_id, imported_at, stored_source_id in rows:
            project_source_id = stored_source_id or _legacy_source_id(import_id)
            connection.execute(
                """
                insert or ignore into project_sources (
                  project_source_id, workspace_id, created_at
                ) values (?, 'legacy', ?)
                """,
                (project_source_id, imported_at),
            )
            connection.execute(
                """
                insert or ignore into source_revisions (
                  project_source_id, transcript_revision_id,
                  parent_transcript_revision_id, created_at
                ) values (?, ?, '', ?)
                """,
                (project_source_id, revision_id, imported_at),
            )
            if not stored_source_id:
                connection.execute(
                    """
                    update source_imports set project_source_id = ? where import_id = ?
                    """,
                    (project_source_id, import_id),
                )


def _legacy_source_id(import_id: str) -> str:
    return f"psrc_legacy_{import_id}"
