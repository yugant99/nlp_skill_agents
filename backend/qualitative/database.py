from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from backend.storage.sqlite_migrations import (
    Migration,
    apply_migrations,
    schema_status,
)


_PROJECT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_ENTITY_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_ID_PREFIXES = {
    "researcher": "res",
    "codebook": "cbk",
    "codebook_version": "cbv",
    "code": "cod",
    "case": "cas",
    "attribute_definition": "atr",
    "audit_event": "qae",
}


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
        with self.transaction() as connection:
            connection.execute(
                """
                insert or ignore into qualitative_projects (project_id, created_at)
                values (?, ?)
                """,
                (self.project_id, now),
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
                raise ValueError("Researcher identity conflicts with qualitative project")
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

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield one immediate transaction with foreign-key enforcement enabled."""

        self._ensure_schema()
        with sqlite3.connect(self.db_path, timeout=30) as connection:
            connection.execute("pragma foreign_keys = on")
            connection.execute("begin immediate")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    def migration_status(self) -> list[dict[str, object]]:
        self._ensure_schema()
        with sqlite3.connect(self.db_path) as connection:
            return schema_status(connection)

    def _ensure_schema(self) -> None:
        self._require_study()
        with sqlite3.connect(self.db_path, timeout=30) as connection:
            connection.execute("pragma foreign_keys = on")
            apply_migrations(
                connection,
                database_name=f"qualitative project {self.project_id}",
                migrations=QUALITATIVE_MIGRATIONS,
            )

    def _require_study(self) -> None:
        if not (self.project_dir / "study.json").is_file():
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
        when exists (
          select 1 from codebook_versions
          where project_id = old.project_id
            and codebook_version_id = old.codebook_version_id
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


QUALITATIVE_MIGRATIONS = (
    Migration(1, "create-qualitative-core-contract", _create_qualitative_core),
)


def _validate_entity_id(value: str, field_name: str) -> None:
    if not _ENTITY_ID_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must be a stable lowercase identifier")


def _bootstrap_event_id(project_id: str, researcher_id: str) -> str:
    digest = sha256(f"{project_id}\0{researcher_id}".encode("utf-8")).hexdigest()
    return f"qae_init_{digest[:32]}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
