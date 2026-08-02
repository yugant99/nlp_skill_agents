from __future__ import annotations

import re
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from backend.storage.sqlite_migrations import (
    Migration,
    SchemaCompatibilityError,
    apply_migrations,
    schema_status,
)
from backend.storage.study_batch_operation_store import StudyBatchOperationStore


_PROJECT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_ENTITY_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_ID_PREFIXES = {
    "researcher": "res",
    "codebook": "cbk",
    "codebook_version": "cbv",
    "code": "cod",
    "case": "cas",
    "attribute_definition": "atr",
    "coding_reference": "cdr",
    "audit_event": "qae",
}


class QualitativeDatabaseConflict(RuntimeError):
    pass


class QualitativeProjectDatabase:
    """Owns the per-study SQLite contract for accepted qualitative metadata."""

    def __init__(
        self,
        root: Path | str,
        project_id: str,
    ) -> None:
        if not _PROJECT_ID_PATTERN.fullmatch(project_id):
            raise ValueError("Invalid qualitative project id")
        self.root = Path(root)
        self.project_id = project_id
        self.project_dir = self.root / "studies" / project_id
        self.db_path = self.project_dir / "qualitative.sqlite3"

    def initialize(
        self,
        *,
        researcher_id: str,
        researcher_name: str,
    ) -> None:
        """Create the project binding and its first attributable researcher."""

        _validate_entity_id(researcher_id, "researcher_id")
        display_name = researcher_name.strip()
        if not display_name:
            raise ValueError("researcher_name must be non-empty")
        now = _utc_now()
        bootstrap_event_id = _bootstrap_event_id(self.project_id, researcher_id)
        try:
            with self.transaction() as connection:
                connection.execute(
                    """
                    insert or ignore into qualitative_projects (project_id, created_at)
                    values (?, ?)
                    """,
                    (self.project_id, now),
                )
                bootstrap_actors = connection.execute(
                    """
                    select actor_id from qualitative_audit_events
                    where project_id = ?
                      and event_type = 'qualitative.project.initialized'
                      and subject_type = 'project'
                      and subject_id = ?
                    order by event_id
                    """,
                    (self.project_id, self.project_id),
                ).fetchall()
                existing_researchers = connection.execute(
                    """
                    select researcher_id, display_name, role, active
                    from researchers where project_id = ?
                    order by researcher_id
                    """,
                    (self.project_id,),
                ).fetchall()
                if bootstrap_actors:
                    identity_matches = bootstrap_actors == [(researcher_id,)]
                else:
                    identity_matches = not existing_researchers or (
                        existing_researchers
                        == [(researcher_id, display_name, "researcher", 1)]
                    )
                if not identity_matches:
                    raise ValueError(
                        "Researcher identity conflicts with qualitative project"
                    )
                connection.execute(
                    """
                    insert or ignore into researchers (
                      researcher_id, project_id, display_name, role,
                      active, created_at, updated_at
                    ) values (?, ?, ?, 'researcher', 1, ?, ?)
                    """,
                    (researcher_id, self.project_id, display_name, now, now),
                )
                stored = connection.execute(
                    """
                    select project_id, display_name, role, active
                    from researchers where researcher_id = ?
                    """,
                    (researcher_id,),
                ).fetchone()
                if stored != (self.project_id, display_name, "researcher", 1):
                    raise ValueError(
                        "Researcher identity conflicts with qualitative project"
                    )
                connection.execute(
                    """
                    insert or ignore into qualitative_audit_events (
                      event_id, project_id, actor_id, event_type,
                      subject_type, subject_id, metadata_json, created_at
                    ) values (?, ?, ?, 'qualitative.project.initialized',
                              'project', ?, '{}', ?)
                    """,
                    (
                        bootstrap_event_id,
                        self.project_id,
                        researcher_id,
                        self.project_id,
                        now,
                    ),
                )
                stored_event = connection.execute(
                    """
                    select project_id, actor_id, event_type, subject_type,
                           subject_id, metadata_json
                    from qualitative_audit_events where event_id = ?
                    """,
                    (bootstrap_event_id,),
                ).fetchone()
                if stored_event != (
                    self.project_id,
                    researcher_id,
                    "qualitative.project.initialized",
                    "project",
                    self.project_id,
                    "{}",
                ):
                    raise ValueError("Initialization audit identity conflicts")
        except FileNotFoundError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise QualitativeDatabaseConflict(
                "Qualitative project initialization failed"
            ) from exc

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """Yield one guarded, query-only qualitative database connection."""

        self._require_study()
        with StudyBatchOperationStore(
            self.root,
            self.project_id,
        ).study_mutation_guard():
            with self._prepared_connection() as connection:
                try:
                    connection.row_factory = sqlite3.Row
                    connection.execute("pragma query_only = on")
                except (OSError, sqlite3.Error) as exc:
                    raise QualitativeDatabaseConflict(
                        "Qualitative database is invalid"
                    ) from exc
                yield connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield one immediate transaction with foreign-key enforcement enabled."""

        self._require_study()
        with StudyBatchOperationStore(
            self.root,
            self.project_id,
        ).study_mutation_guard():
            with self._prepared_connection() as connection:
                try:
                    connection.execute("begin immediate")
                except (OSError, sqlite3.Error) as exc:
                    raise QualitativeDatabaseConflict(
                        "Qualitative database transaction could not start"
                    ) from exc
                try:
                    yield connection
                except BaseException:
                    connection.rollback()
                    raise
                else:
                    try:
                        connection.commit()
                    except (OSError, sqlite3.Error) as exc:
                        connection.rollback()
                        raise QualitativeDatabaseConflict(
                            "Qualitative database transaction could not commit"
                        ) from exc

    def migration_status(self) -> list[dict[str, object]]:
        with self.read() as connection:
            return schema_status(connection)

    @contextmanager
    def _prepared_connection(self) -> Iterator[sqlite3.Connection]:
        self._require_study()
        connection: sqlite3.Connection | None = None
        try:
            if self.db_path.exists() or self.db_path.is_symlink():
                mode = self.db_path.lstat().st_mode
                if not stat.S_ISREG(mode):
                    raise OSError(
                        "Qualitative database must be a non-symlink regular file"
                    )
            connection = sqlite3.connect(self.db_path, timeout=30)
            connection.execute("pragma foreign_keys = on")
            connection.execute("pragma trusted_schema = off")
            current_version = int(
                connection.execute("pragma user_version").fetchone()[0]
            )
            if current_version < 0:
                raise ValueError("Qualitative database schema version is invalid")
            if current_version > len(QUALITATIVE_MIGRATIONS):
                apply_migrations(
                    connection,
                    database_name=f"qualitative project {self.project_id}",
                    migrations=QUALITATIVE_MIGRATIONS,
                )
            _validate_schema_definition(connection, current_version)
            _validate_database_integrity(connection)
            _validate_project_ownership(
                connection,
                self.project_id,
                schema_version=current_version,
            )
            apply_migrations(
                connection,
                database_name=f"qualitative project {self.project_id}",
                migrations=QUALITATIVE_MIGRATIONS,
            )
            supported_version = len(QUALITATIVE_MIGRATIONS)
            _validate_schema_definition(connection, supported_version)
            _validate_database_integrity(connection)
            _validate_project_ownership(
                connection,
                self.project_id,
                schema_version=supported_version,
            )
        except SchemaCompatibilityError:
            if connection is not None:
                connection.close()
            raise
        except (OSError, sqlite3.Error, ValueError) as exc:
            if connection is not None:
                connection.close()
            raise QualitativeDatabaseConflict(
                "Qualitative database is invalid"
            ) from exc

        if connection is None:
            raise QualitativeDatabaseConflict("Qualitative database is invalid")
        try:
            yield connection
        finally:
            connection.close()

    def _require_study(self) -> None:
        if self.project_dir.is_symlink():
            raise QualitativeDatabaseConflict(
                "Qualitative study directory is invalid"
            )
        study_path = self.project_dir / "study.json"
        if study_path.is_symlink():
            raise QualitativeDatabaseConflict("Qualitative study record is invalid")
        if not study_path.is_file():
            raise FileNotFoundError(self.project_id)


def new_qualitative_id(entity: str) -> str:
    try:
        prefix = _ID_PREFIXES[entity]
    except KeyError as exc:
        raise ValueError(f"Unknown qualitative entity type: {entity}") from exc
    return f"{prefix}_{uuid4().hex}"


def _create_qualitative_core(connection: sqlite3.Connection) -> None:
    _execute_schema_script(
        connection,
        """
        create table qualitative_projects (
          project_id text primary key,
          created_at text not null
        );

        create table researchers (
          researcher_id text primary key,
          project_id text not null,
          display_name text not null,
          role text not null check (role in ('researcher', 'reviewer', 'administrator')),
          active integer not null check (active in (0, 1)),
          created_at text not null,
          updated_at text not null,
          unique (project_id, researcher_id),
          foreign key (project_id) references qualitative_projects(project_id)
            on delete restrict
        );

        create table codebooks (
          codebook_id text primary key,
          project_id text not null,
          title text not null,
          description text not null default '',
          created_by text not null,
          updated_by text not null,
          created_at text not null,
          updated_at text not null,
          unique (project_id, codebook_id),
          foreign key (project_id) references qualitative_projects(project_id)
            on delete restrict,
          foreign key (project_id, created_by)
            references researchers(project_id, researcher_id) on delete restrict,
          foreign key (project_id, updated_by)
            references researchers(project_id, researcher_id) on delete restrict
        );

        create table codebook_versions (
          codebook_version_id text primary key,
          project_id text not null,
          codebook_id text not null,
          version_number integer not null check (version_number > 0),
          status text not null check (status in ('draft', 'frozen')),
          based_on_version_id text,
          created_by text not null,
          created_at text not null,
          frozen_at text,
          unique (project_id, codebook_version_id),
          unique (project_id, codebook_id, codebook_version_id),
          unique (project_id, codebook_id, version_number),
          check (
            (status = 'draft' and frozen_at is null)
            or (status = 'frozen' and frozen_at is not null)
          ),
          foreign key (project_id, codebook_id)
            references codebooks(project_id, codebook_id) on delete restrict,
          foreign key (project_id, codebook_id, based_on_version_id)
            references codebook_versions(
              project_id, codebook_id, codebook_version_id
            ) on delete restrict,
          foreign key (project_id, created_by)
            references researchers(project_id, researcher_id) on delete restrict
        );

        create table codes (
          code_id text primary key,
          project_id text not null,
          codebook_version_id text not null,
          stable_code_key text not null,
          parent_code_id text,
          label text not null,
          definition text not null default '',
          inclusion_criteria text not null default '',
          exclusion_criteria text not null default '',
          examples_json text not null default '[]',
          notes text not null default '',
          color text not null default '',
          sort_order integer not null default 0 check (sort_order >= 0),
          created_by text not null,
          created_at text not null,
          updated_at text not null,
          unique (project_id, codebook_version_id, code_id),
          unique (project_id, codebook_version_id, stable_code_key),
          foreign key (project_id, codebook_version_id)
            references codebook_versions(project_id, codebook_version_id)
            on delete restrict,
          foreign key (project_id, codebook_version_id, parent_code_id)
            references codes(project_id, codebook_version_id, code_id)
            on delete restrict,
          foreign key (project_id, created_by)
            references researchers(project_id, researcher_id) on delete restrict
        );

        create table cases (
          case_id text primary key,
          project_id text not null,
          case_kind text not null check (
            case_kind in ('participant', 'session', 'dyad', 'condition', 'timepoint')
          ),
          label text not null,
          description text not null default '',
          created_by text not null,
          updated_by text not null,
          created_at text not null,
          updated_at text not null,
          unique (project_id, case_id),
          foreign key (project_id) references qualitative_projects(project_id)
            on delete restrict,
          foreign key (project_id, created_by)
            references researchers(project_id, researcher_id) on delete restrict,
          foreign key (project_id, updated_by)
            references researchers(project_id, researcher_id) on delete restrict
        );

        create table attribute_definitions (
          attribute_definition_id text primary key,
          project_id text not null,
          attribute_key text not null,
          label text not null,
          value_type text not null check (
            value_type in ('text', 'number', 'boolean', 'date', 'categorical')
          ),
          allowed_values_json text not null default '[]',
          required integer not null default 0 check (required in (0, 1)),
          created_by text not null,
          updated_by text not null,
          created_at text not null,
          updated_at text not null,
          unique (project_id, attribute_definition_id),
          unique (project_id, attribute_key),
          foreign key (project_id) references qualitative_projects(project_id)
            on delete restrict,
          foreign key (project_id, created_by)
            references researchers(project_id, researcher_id) on delete restrict,
          foreign key (project_id, updated_by)
            references researchers(project_id, researcher_id) on delete restrict
        );

        create table case_attribute_values (
          project_id text not null,
          case_id text not null,
          attribute_definition_id text not null,
          value_json text not null,
          updated_by text not null,
          created_at text not null,
          updated_at text not null,
          primary key (project_id, case_id, attribute_definition_id),
          foreign key (project_id, case_id)
            references cases(project_id, case_id) on delete restrict,
          foreign key (project_id, attribute_definition_id)
            references attribute_definitions(project_id, attribute_definition_id)
            on delete restrict,
          foreign key (project_id, updated_by)
            references researchers(project_id, researcher_id) on delete restrict
        );

        create table source_case_links (
          project_id text not null,
          project_source_id text not null,
          case_id text not null,
          linked_by text not null,
          created_at text not null,
          primary key (project_id, project_source_id, case_id),
          foreign key (project_id, case_id)
            references cases(project_id, case_id) on delete restrict,
          foreign key (project_id, linked_by)
            references researchers(project_id, researcher_id) on delete restrict
        );

        create table qualitative_audit_events (
          event_id text primary key,
          project_id text not null,
          actor_id text not null,
          event_type text not null,
          subject_type text not null,
          subject_id text not null,
          metadata_json text not null default '{}',
          created_at text not null,
          foreign key (project_id) references qualitative_projects(project_id)
            on delete restrict,
          foreign key (project_id, actor_id)
            references researchers(project_id, researcher_id) on delete restrict
        );

        create index codebook_versions_by_codebook
          on codebook_versions(project_id, codebook_id, version_number desc);
        create index codes_by_parent
          on codes(project_id, codebook_version_id, parent_code_id, sort_order);
        create index cases_by_kind
          on cases(project_id, case_kind, label);
        create index source_case_links_by_case
          on source_case_links(project_id, case_id, project_source_id);
        create index qualitative_audit_by_subject
          on qualitative_audit_events(
            project_id, subject_type, subject_id, created_at
          );

        create trigger prevent_second_qualitative_project
        before insert on qualitative_projects
        when exists (
          select 1 from qualitative_projects
          where project_id != new.project_id
        )
        begin
          select raise(abort, 'qualitative database belongs to one project');
        end;

        create trigger prevent_frozen_code_insert
        before insert on codes
        when exists (
          select 1 from codebook_versions
          where project_id = new.project_id
            and codebook_version_id = new.codebook_version_id
            and status = 'frozen'
        )
        begin
          select raise(abort, 'frozen codebook version is immutable');
        end;

        create trigger prevent_frozen_code_update
        before update on codes
        when
          exists (
            select 1 from codebook_versions
            where project_id = old.project_id
              and codebook_version_id = old.codebook_version_id
              and status = 'frozen'
          )
          or exists (
            select 1 from codebook_versions
            where project_id = new.project_id
              and codebook_version_id = new.codebook_version_id
              and status = 'frozen'
          )
        begin
          select raise(abort, 'frozen codebook version is immutable');
        end;

        create trigger prevent_frozen_code_delete
        before delete on codes
        when exists (
          select 1 from codebook_versions
          where project_id = old.project_id
            and codebook_version_id = old.codebook_version_id
            and status = 'frozen'
        )
        begin
          select raise(abort, 'frozen codebook version is immutable');
        end;

        create trigger prevent_frozen_version_update
        before update on codebook_versions
        when old.status = 'frozen'
        begin
          select raise(abort, 'frozen codebook version is immutable');
        end;

        create trigger prevent_frozen_version_delete
        before delete on codebook_versions
        when old.status = 'frozen'
        begin
          select raise(abort, 'frozen codebook version is immutable');
        end;

        create trigger prevent_qualitative_audit_update
        before update on qualitative_audit_events
        begin
          select raise(abort, 'qualitative audit events are append-only');
        end;

        create trigger prevent_qualitative_audit_delete
        before delete on qualitative_audit_events
        begin
          select raise(abort, 'qualitative audit events are append-only');
        end;
        """
    )


def _add_coding_references(connection: sqlite3.Connection) -> None:
    _execute_schema_script(
        connection,
        """
        create table coding_references (
          coding_reference_id text primary key,
          project_id text not null,
          project_source_id text not null,
          transcript_revision_id text not null,
          evidence_set_id text not null,
          target_kind text not null check (target_kind in ('passage', 'cunit')),
          passage_id text not null,
          cunit_id text not null default '',
          start_offset integer not null check (start_offset >= 0),
          end_offset integer not null check (end_offset > start_offset),
          codebook_version_id text not null,
          code_id text not null,
          created_by text not null,
          created_at text not null,
          removed_by text,
          removed_at text,
          unique (project_id, coding_reference_id),
          check (
            (target_kind = 'passage' and cunit_id = '')
            or (target_kind = 'cunit' and cunit_id != '')
          ),
          check (
            (removed_by is null and removed_at is null)
            or (removed_by is not null and removed_at is not null)
          ),
          foreign key (project_id) references qualitative_projects(project_id)
            on delete restrict,
          foreign key (project_id, codebook_version_id, code_id)
            references codes(project_id, codebook_version_id, code_id)
            on delete restrict,
          foreign key (project_id, created_by)
            references researchers(project_id, researcher_id) on delete restrict,
          foreign key (project_id, removed_by)
            references researchers(project_id, researcher_id) on delete restrict
        );

        create unique index active_coding_reference_identity
          on coding_references (
            project_id, project_source_id, transcript_revision_id,
            evidence_set_id, target_kind, passage_id, cunit_id,
            start_offset, end_offset, codebook_version_id, code_id, created_by
          ) where removed_at is null;

        create index coding_references_by_created
          on coding_references (project_id, created_at, coding_reference_id);

        create index coding_references_by_code
          on coding_references (
            project_id, codebook_version_id, code_id, created_at
          );

        create trigger require_frozen_coding_reference_version
        before insert on coding_references
        when not exists (
          select 1 from codebook_versions
          where project_id = new.project_id
            and codebook_version_id = new.codebook_version_id
            and status = 'frozen'
        )
        begin
          select raise(abort, 'coding reference requires a frozen codebook version');
        end;

        create trigger reject_pre_removed_coding_reference
        before insert on coding_references
        when new.removed_by is not null or new.removed_at is not null
        begin
          select raise(abort, 'coding reference must be active when inserted');
        end;

        create trigger prevent_coding_reference_delete
        before delete on coding_references
        begin
          select raise(abort, 'coding references cannot be physically deleted');
        end;

        create trigger restrict_coding_reference_update
        before update on coding_references
        when
          new.coding_reference_id is not old.coding_reference_id
          or new.project_id is not old.project_id
          or new.project_source_id is not old.project_source_id
          or new.transcript_revision_id is not old.transcript_revision_id
          or new.evidence_set_id is not old.evidence_set_id
          or new.target_kind is not old.target_kind
          or new.passage_id is not old.passage_id
          or new.cunit_id is not old.cunit_id
          or new.start_offset is not old.start_offset
          or new.end_offset is not old.end_offset
          or new.codebook_version_id is not old.codebook_version_id
          or new.code_id is not old.code_id
          or new.created_by is not old.created_by
          or new.created_at is not old.created_at
          or old.removed_by is not null
          or old.removed_at is not null
          or new.removed_by is null
          or new.removed_at is null
          or julianday(new.removed_at) is null
          or julianday(old.created_at) is null
          or julianday(new.removed_at) < julianday(old.created_at)
        begin
          select raise(abort, 'coding reference update is not an initial removal');
        end;
        """,
    )


def _execute_schema_script(
    connection: sqlite3.Connection,
    script: str,
) -> None:
    """Execute a SQL script without sqlite3.executescript's implicit commit."""

    statement = ""
    for line in script.splitlines():
        statement += f"{line}\n"
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise ValueError("Qualitative schema contains an incomplete SQL statement")


def _validate_schema_definition(
    connection: sqlite3.Connection,
    version: int,
) -> None:
    if version > len(QUALITATIVE_MIGRATIONS):
        raise SchemaCompatibilityError(
            f"qualitative project schema version {version} is newer than "
            f"supported version {len(QUALITATIVE_MIGRATIONS)}"
        )
    with sqlite3.connect(":memory:") as expected:
        expected.execute("pragma trusted_schema = off")
        apply_migrations(
            expected,
            database_name="expected qualitative project",
            migrations=QUALITATIVE_MIGRATIONS[:version],
        )
        expected_signature = _schema_signature(expected)
    if _schema_signature(connection) != expected_signature:
        raise ValueError("Qualitative database schema definition is invalid")


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


def _validate_database_integrity(connection: sqlite3.Connection) -> None:
    integrity_rows = connection.execute("pragma integrity_check").fetchall()
    if integrity_rows != [("ok",)]:
        raise ValueError("Qualitative database failed integrity check")
    if connection.execute("pragma foreign_key_check").fetchone() is not None:
        raise ValueError("Qualitative database failed foreign-key validation")


def _validate_project_ownership(
    connection: sqlite3.Connection,
    project_id: str,
    *,
    schema_version: int,
) -> None:
    if schema_version == 0:
        return
    mismatched_project = connection.execute(
        """
        select 1 from qualitative_projects
        where project_id != ? limit 1
        """,
        (project_id,),
    ).fetchone()
    if mismatched_project is not None:
        raise ValueError("Qualitative database belongs to another project")


QUALITATIVE_MIGRATIONS = (
    Migration(1, "create-qualitative-core-contract", _create_qualitative_core),
    Migration(2, "add-coding-reference-contract", _add_coding_references),
)


def _validate_entity_id(value: str, field_name: str) -> None:
    if not _ENTITY_ID_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must be a stable lowercase identifier")


def _bootstrap_event_id(project_id: str, researcher_id: str) -> str:
    digest = sha256(f"{project_id}\0{researcher_id}".encode("utf-8")).hexdigest()
    return f"qae_init_{digest[:32]}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
