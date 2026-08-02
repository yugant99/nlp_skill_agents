from __future__ import annotations

import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.storage.sqlite_migrations import (
    Migration,
    SchemaCompatibilityError,
    apply_migrations,
    schema_status,
)
from backend.storage.workspace_lock import workspace_mutation_lock


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


class EvidenceCatalogConflict(ValueError):
    pass


class EvidenceCatalog:
    def __init__(self, root: Path | str = "local_data") -> None:
        self.root = Path(root)
        self.db_path = self.root / "evidence.sqlite3"

    def record_import(self, record: EvidenceImportRecord) -> None:
        with self.transaction() as connection:
            self._record_import(connection, record)

    def _record_import(
        self,
        connection: sqlite3.Connection,
        record: EvidenceImportRecord,
    ) -> None:
        project_source_id = record.project_source_id or _legacy_source_id(
            record.import_id
        )
        parent_revision_id = record.parent_transcript_revision_id
        workspace_id = record.workspace_id or "local-default"
        if parent_revision_id == record.transcript_revision_id:
            raise ValueError("Transcript revision cannot be its own parent")

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
        with self.read() as connection:
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
        with self.read() as connection:
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
        with self.read() as connection:
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
        with self.read() as connection:
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

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        with workspace_mutation_lock(self.root):
            with self._prepared_connection() as connection:
                try:
                    connection.execute("pragma query_only = on")
                except sqlite3.Error as exc:
                    raise EvidenceCatalogConflict(
                        "Evidence catalog is unavailable or invalid"
                    ) from exc
                yield connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with workspace_mutation_lock(self.root):
            with self._prepared_connection() as connection:
                try:
                    connection.execute("begin immediate")
                except sqlite3.Error as exc:
                    raise EvidenceCatalogConflict(
                        "Evidence catalog transaction could not start"
                    ) from exc
                try:
                    yield connection
                except BaseException:
                    connection.rollback()
                    raise
                else:
                    try:
                        connection.commit()
                    except sqlite3.Error as exc:
                        connection.rollback()
                        raise EvidenceCatalogConflict(
                            "Evidence catalog transaction could not commit"
                        ) from exc

    @contextmanager
    def _prepared_connection(self) -> Iterator[sqlite3.Connection]:
        self.root.mkdir(parents=True, exist_ok=True)
        connection: sqlite3.Connection | None = None
        try:
            if self.db_path.exists() or self.db_path.is_symlink():
                if not stat.S_ISREG(self.db_path.lstat().st_mode):
                    raise OSError(
                        "Evidence catalog must be a non-symlink regular file"
                    )
            connection = sqlite3.connect(self.db_path, timeout=30)
            connection.execute("pragma foreign_keys = on")
            connection.execute("pragma trusted_schema = off")
            current_version = int(
                connection.execute("pragma user_version").fetchone()[0]
            )
            _validate_schema_definition(connection, current_version)
            _validate_database_integrity(connection)
            apply_migrations(
                connection,
                database_name="evidence catalog",
                migrations=EVIDENCE_CATALOG_MIGRATIONS,
            )
            _validate_schema_definition(
                connection,
                len(EVIDENCE_CATALOG_MIGRATIONS),
            )
            _validate_database_integrity(connection)
        except SchemaCompatibilityError:
            if connection is not None:
                connection.close()
            raise
        except (OSError, sqlite3.Error, ValueError) as exc:
            if connection is not None:
                connection.close()
            raise EvidenceCatalogConflict(
                "Evidence catalog is unavailable or invalid"
            ) from exc
        if connection is None:
            raise EvidenceCatalogConflict(
                "Evidence catalog is unavailable or invalid"
            )
        try:
            yield connection
        finally:
            connection.close()

    def migration_status(self) -> list[dict[str, object]]:
        with self.read() as connection:
            return schema_status(connection)


def _legacy_source_id(import_id: str) -> str:
    return f"psrc_legacy_{import_id}"


def _evidence_v1_import_catalog(connection: sqlite3.Connection) -> None:
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


def _evidence_v2_project_lineage(connection: sqlite3.Connection) -> None:
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
        create table if not exists source_revisions (
          project_source_id text not null references project_sources,
          transcript_revision_id text not null references transcript_revisions,
          parent_transcript_revision_id text not null default '',
          created_at text not null,
          primary key (project_source_id, transcript_revision_id)
        )
        """
    )
    columns = [
        str(row[1]) for row in connection.execute("pragma table_info(source_imports)")
    ]
    optional = [
        column
        for column in ("project_source_id", "parent_transcript_revision_id")
        if column in columns
    ]
    selected = [
        "import_id",
        "run_id",
        "pipeline",
        "source_id",
        "source_filename",
        "source_media_type",
        "source_blob_sha256",
        "transcript_revision_id",
        "imported_at",
        *optional,
    ]
    rows = [
        dict(zip(selected, row, strict=True))
        for row in connection.execute(f"select {', '.join(selected)} from source_imports")
    ]
    for row in rows:
        project_source_id = str(
            row.get("project_source_id") or _legacy_source_id(str(row["import_id"]))
        )
        existing_workspace = connection.execute(
            "select workspace_id from project_sources where project_source_id = ?",
            (project_source_id,),
        ).fetchone()
        workspace_id = str(existing_workspace[0]) if existing_workspace else "legacy"
        parent_revision_id = str(row.get("parent_transcript_revision_id") or "")
        connection.execute(
            """
            insert or ignore into project_sources (
              project_source_id, workspace_id, created_at
            ) values (?, ?, ?)
            """,
            (project_source_id, workspace_id, row["imported_at"]),
        )
        connection.execute(
            """
            insert or ignore into source_revisions (
              project_source_id, transcript_revision_id,
              parent_transcript_revision_id, created_at
            ) values (?, ?, ?, ?)
            """,
            (
                project_source_id,
                row["transcript_revision_id"],
                parent_revision_id,
                row["imported_at"],
            ),
        )
        row["project_source_id"] = project_source_id
        row["parent_transcript_revision_id"] = parent_revision_id

    connection.execute("alter table source_imports rename to source_imports_pre_v2")
    connection.execute(
        """
        create table source_imports (
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
    for row in rows:
        connection.execute(
            """
            insert into source_imports values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["import_id"],
                row["run_id"],
                row["pipeline"],
                row["project_source_id"],
                row["source_id"],
                row["source_filename"],
                row["source_media_type"],
                row["source_blob_sha256"],
                row["transcript_revision_id"],
                row["parent_transcript_revision_id"],
                row["imported_at"],
            ),
        )
    connection.execute("drop table source_imports_pre_v2")
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


def _evidence_v3_workspace_indexes(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        create index if not exists project_sources_workspace_idx
        on project_sources (workspace_id)
        """
    )
    connection.execute(
        """
        create index if not exists source_imports_imported_idx
        on source_imports (imported_at desc)
        """
    )


def _evidence_v4_canonical_targets(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        create unique index source_imports_target_identity_idx
        on source_imports (
          import_id, project_source_id, transcript_revision_id
        )
        """
    )
    connection.execute(
        """
        create unique index transcript_revisions_digest_identity_idx
        on transcript_revisions (transcript_revision_id, transcript_sha256)
        """
    )
    connection.execute(
        """
        create table evidence_sets (
          evidence_set_id text primary key
            check (
              length(evidence_set_id) = 36
              and substr(evidence_set_id, 1, 4) = 'evs_'
              and substr(evidence_set_id, 5) not glob '*[^0-9a-f]*'
            ),
          import_id text not null,
          project_source_id text not null,
          transcript_revision_id text not null,
          producer_kind text not null
            check (producer_kind in ('analysis_turns', 'cunit_segmentation')),
          producer_version integer not null
            check (typeof(producer_version) = 'integer' and producer_version > 0),
          producer_status text not null check (producer_status = 'verified'),
          review_status text not null
            check (review_status in ('not_applicable', 'not_domain_validated')),
          transcript_text_sha256 text not null
            check (
              length(transcript_text_sha256) = 64
              and transcript_text_sha256 not glob '*[^0-9a-f]*'
            ),
          snapshot_sha256 text not null
            check (
              length(snapshot_sha256) = 64
              and snapshot_sha256 not glob '*[^0-9a-f]*'
            ),
          passage_count integer not null
            check (typeof(passage_count) = 'integer' and passage_count >= 0),
          cunit_count integer not null
            check (typeof(cunit_count) = 'integer' and cunit_count >= 0),
          created_at text not null,
          foreign key (
            import_id, project_source_id, transcript_revision_id
          ) references source_imports (
            import_id, project_source_id, transcript_revision_id
          ) on delete restrict deferrable initially deferred,
          foreign key (
            project_source_id, transcript_revision_id
          ) references source_revisions (
            project_source_id, transcript_revision_id
          ) on delete restrict deferrable initially deferred,
          foreign key (
            transcript_revision_id, transcript_text_sha256
          ) references transcript_revisions (
            transcript_revision_id, transcript_sha256
          ) on delete restrict deferrable initially deferred
        )
        """
    )
    connection.execute(
        """
        create table evidence_passages (
          evidence_set_id text not null,
          passage_id text not null,
          passage_ordinal integer not null
            check (typeof(passage_ordinal) = 'integer' and passage_ordinal >= 0),
          role text not null,
          text_sha256 text not null
            check (
              length(text_sha256) = 64
              and text_sha256 not glob '*[^0-9a-f]*'
            ),
          text_length integer not null
            check (typeof(text_length) = 'integer' and text_length >= 0),
          primary key (evidence_set_id, passage_id),
          unique (evidence_set_id, passage_ordinal),
          foreign key (evidence_set_id)
            references evidence_sets(evidence_set_id)
            on delete restrict deferrable initially deferred
        )
        """
    )
    connection.execute(
        """
        create table evidence_cunits (
          evidence_set_id text not null,
          cunit_id text not null,
          passage_id text not null,
          cunit_ordinal integer not null
            check (typeof(cunit_ordinal) = 'integer' and cunit_ordinal >= 0),
          text_sha256 text not null
            check (
              length(text_sha256) = 64
              and text_sha256 not glob '*[^0-9a-f]*'
            ),
          text_length integer not null
            check (typeof(text_length) = 'integer' and text_length >= 0),
          primary key (evidence_set_id, cunit_id),
          unique (evidence_set_id, passage_id, cunit_ordinal),
          foreign key (evidence_set_id)
            references evidence_sets(evidence_set_id)
            on delete restrict deferrable initially deferred,
          foreign key (evidence_set_id, passage_id)
            references evidence_passages(evidence_set_id, passage_id)
            on delete restrict deferrable initially deferred
        )
        """
    )
    connection.execute(
        """
        create index evidence_sets_by_import
        on evidence_sets(import_id, evidence_set_id)
        """
    )
    connection.execute(
        """
        create trigger evidence_set_header_requires_complete_children
        before insert on evidence_sets
        when
          (select count(*) from evidence_passages
           where evidence_set_id = new.evidence_set_id) != new.passage_count
          or
          (select count(*) from evidence_cunits
           where evidence_set_id = new.evidence_set_id) != new.cunit_count
        begin
          select raise(abort, 'evidence set child counts are incomplete');
        end
        """
    )
    connection.execute(
        """
        create trigger prevent_evidence_passage_append
        before insert on evidence_passages
        when exists (
          select 1 from evidence_sets
          where evidence_set_id = new.evidence_set_id
        )
        begin
          select raise(abort, 'completed evidence set is immutable');
        end
        """
    )
    connection.execute(
        """
        create trigger prevent_evidence_cunit_append
        before insert on evidence_cunits
        when exists (
          select 1 from evidence_sets
          where evidence_set_id = new.evidence_set_id
        )
        begin
          select raise(abort, 'completed evidence set is immutable');
        end
        """
    )
    for table in ("evidence_sets", "evidence_passages", "evidence_cunits"):
        connection.execute(
            f"""
            create trigger prevent_{table}_update
            before update on {table}
            begin
              select raise(abort, 'completed evidence set is immutable');
            end
            """
        )
        connection.execute(
            f"""
            create trigger prevent_{table}_delete
            before delete on {table}
            begin
              select raise(abort, 'completed evidence set is immutable');
            end
            """
        )


EVIDENCE_CATALOG_MIGRATIONS = [
    Migration(1, "create-import-catalog", _evidence_v1_import_catalog),
    Migration(2, "add-project-source-lineage", _evidence_v2_project_lineage),
    Migration(3, "index-workspace-history", _evidence_v3_workspace_indexes),
    Migration(4, "add-canonical-evidence-targets", _evidence_v4_canonical_targets),
]


def _validate_schema_definition(
    connection: sqlite3.Connection,
    version: int,
) -> None:
    if version < 0 or version > len(EVIDENCE_CATALOG_MIGRATIONS):
        raise SchemaCompatibilityError(
            f"evidence catalog schema version {version} is newer than "
            f"supported version {len(EVIDENCE_CATALOG_MIGRATIONS)}"
        )
    actual_signature = _schema_signature(connection)
    if version == 0 and not actual_signature:
        return
    if version == 0 and _is_legacy_unversioned_v1(connection):
        return
    with sqlite3.connect(":memory:") as expected:
        expected.execute("pragma foreign_keys = on")
        expected.execute("pragma trusted_schema = off")
        apply_migrations(
            expected,
            database_name="expected evidence catalog",
            migrations=EVIDENCE_CATALOG_MIGRATIONS[:version],
        )
        expected_signature = _schema_signature(expected)
    if actual_signature != expected_signature:
        raise ValueError("Evidence catalog schema definition is invalid")


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


def _is_legacy_unversioned_v1(connection: sqlite3.Connection) -> bool:
    with sqlite3.connect(":memory:") as expected:
        expected.execute(
            """
            create table transcript_revisions (
              transcript_revision_id text primary key,
              source_id text not null,
              transcript_sha256 text not null,
              created_at text not null
            )
            """
        )
        expected.execute(
            """
            create table source_imports (
              import_id text primary key,
              run_id text not null,
              pipeline text not null,
              source_id text not null,
              source_filename text not null,
              source_media_type text not null,
              source_blob_sha256 text not null,
              transcript_revision_id text not null,
              imported_at text not null
            )
            """
        )
        return _schema_signature(connection) == _schema_signature(expected)


def _validate_database_integrity(connection: sqlite3.Connection) -> None:
    if connection.execute("pragma integrity_check").fetchall() != [("ok",)]:
        raise ValueError("Evidence catalog failed integrity check")
    if connection.execute("pragma foreign_key_check").fetchone() is not None:
        raise ValueError("Evidence catalog failed foreign-key validation")
