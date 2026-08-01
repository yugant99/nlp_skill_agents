from __future__ import annotations

import json
import math
import sqlite3
import stat
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TypeAlias

from backend.qualitative.database import (
    QualitativeProjectDatabase,
    new_qualitative_id,
)
from backend.storage.evidence_catalog import EvidenceCatalog
from backend.storage.sqlite_migrations import SchemaCompatibilityError
from backend.storage.workspace_lock import workspace_mutation_lock


_CASE_KINDS = {"participant", "session", "dyad", "condition", "timepoint"}
_VALUE_TYPES = {"text", "number", "boolean", "date", "categorical"}

CaseAttributeScalar: TypeAlias = str | int | float | bool


class CaseNotFoundError(LookupError):
    pass


class CaseValidationError(ValueError):
    pass


class CaseConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class CaseRecord:
    case_id: str
    project_id: str
    case_kind: str
    label: str
    description: str
    created_by: str
    updated_by: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AttributeDefinitionRecord:
    attribute_definition_id: str
    project_id: str
    attribute_key: str
    label: str
    value_type: str
    allowed_values: tuple[str, ...]
    required: bool
    created_by: str
    updated_by: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CaseAttributeValueRecord:
    project_id: str
    case_id: str
    attribute_definition_id: str
    attribute_key: str
    value_type: str
    value: CaseAttributeScalar
    updated_by: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SourceCaseLinkRecord:
    project_id: str
    project_source_id: str
    case_id: str
    linked_by: str
    created_at: str


@dataclass(frozen=True)
class CaseSnapshot:
    case: CaseRecord
    attribute_values: tuple[CaseAttributeValueRecord, ...]
    project_source_ids: tuple[str, ...]


class CaseService:
    def __init__(self, root: Path | str, project_id: str) -> None:
        try:
            self.database = QualitativeProjectDatabase(root, project_id)
        except (TypeError, ValueError) as exc:
            raise CaseValidationError("Invalid qualitative project id") from exc
        self.root = Path(root)
        self.project_id = self.database.project_id

    def create_case(
        self,
        *,
        researcher_id: str,
        case_kind: str,
        label: str,
        description: str = "",
    ) -> CaseRecord:
        normalized_kind = _case_kind(case_kind)
        normalized_label = _required_trimmed_text(label, "label")
        normalized_description = _text(description, "description")
        case_id = new_qualitative_id("case")
        now = _utc_now()
        with self._write() as connection:
            actor_id = self._require_active_researcher(connection, researcher_id)
            connection.execute(
                """
                insert into cases (
                  case_id, project_id, case_kind, label, description,
                  created_by, updated_by, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    self.project_id,
                    normalized_kind,
                    normalized_label,
                    normalized_description,
                    actor_id,
                    actor_id,
                    now,
                    now,
                ),
            )
            self._append_audit(
                connection,
                actor_id=actor_id,
                event_type="case.created",
                subject_type="case",
                subject_id=case_id,
                metadata={"case_kind": normalized_kind},
                created_at=now,
            )
            return self._require_case(connection, case_id)

    def update_case(
        self,
        *,
        researcher_id: str,
        case_id: str,
        case_kind: str,
        label: str,
        description: str,
    ) -> CaseRecord:
        normalized_case_id = _identifier(case_id, "case_id")
        normalized_kind = _case_kind(case_kind)
        normalized_label = _required_trimmed_text(label, "label")
        normalized_description = _text(description, "description")
        now = _utc_now()
        with self._write() as connection:
            actor_id = self._require_active_researcher(connection, researcher_id)
            self._require_case(connection, normalized_case_id)
            connection.execute(
                """
                update cases
                set case_kind = ?, label = ?, description = ?,
                    updated_by = ?, updated_at = ?
                where project_id = ? and case_id = ?
                """,
                (
                    normalized_kind,
                    normalized_label,
                    normalized_description,
                    actor_id,
                    now,
                    self.project_id,
                    normalized_case_id,
                ),
            )
            self._append_audit(
                connection,
                actor_id=actor_id,
                event_type="case.updated",
                subject_type="case",
                subject_id=normalized_case_id,
                metadata={},
                created_at=now,
            )
            return self._require_case(connection, normalized_case_id)

    def read_case(self, case_id: str) -> CaseSnapshot:
        normalized_case_id = _identifier(case_id, "case_id")
        with self._read() as connection:
            self._require_project(connection)
            return self._snapshot(connection, normalized_case_id)

    def list_cases(self) -> tuple[CaseRecord, ...]:
        with self._read() as connection:
            self._require_project(connection)
            rows = connection.execute(
                "select * from cases where project_id = ?",
                (self.project_id,),
            ).fetchall()
            records = tuple(self._case_record(row) for row in rows)
        return tuple(sorted(records, key=_case_sort_key))

    def create_attribute_definition(
        self,
        *,
        researcher_id: str,
        attribute_key: str,
        label: str,
        value_type: str,
        allowed_values: Sequence[str] = (),
        required: bool = False,
    ) -> AttributeDefinitionRecord:
        normalized_key = _required_trimmed_text(attribute_key, "attribute_key")
        normalized_label = _required_trimmed_text(label, "label")
        normalized_type = _value_type(value_type)
        normalized_allowed = _allowed_values(allowed_values, normalized_type)
        if type(required) is not bool:
            raise CaseValidationError("required must be a boolean")
        attribute_definition_id = new_qualitative_id("attribute_definition")
        now = _utc_now()
        with self._write() as connection:
            actor_id = self._require_active_researcher(connection, researcher_id)
            duplicate = connection.execute(
                """
                select 1 from attribute_definitions
                where project_id = ? and attribute_key = ?
                """,
                (self.project_id, normalized_key),
            ).fetchone()
            if duplicate is not None:
                raise CaseConflictError("Attribute key already exists")
            connection.execute(
                """
                insert into attribute_definitions (
                  attribute_definition_id, project_id, attribute_key, label,
                  value_type, allowed_values_json, required, created_by,
                  updated_by, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attribute_definition_id,
                    self.project_id,
                    normalized_key,
                    normalized_label,
                    normalized_type,
                    _allowed_values_json(normalized_allowed),
                    1 if required else 0,
                    actor_id,
                    actor_id,
                    now,
                    now,
                ),
            )
            self._append_audit(
                connection,
                actor_id=actor_id,
                event_type="case.attribute_definition.created",
                subject_type="attribute_definition",
                subject_id=attribute_definition_id,
                metadata={"value_type": normalized_type},
                created_at=now,
            )
            return self._require_definition(connection, attribute_definition_id)

    def list_attribute_definitions(self) -> tuple[AttributeDefinitionRecord, ...]:
        with self._read() as connection:
            self._require_project(connection)
            rows = connection.execute(
                "select * from attribute_definitions where project_id = ?",
                (self.project_id,),
            ).fetchall()
            records = tuple(self._definition_record(row) for row in rows)
        return tuple(sorted(records, key=_definition_sort_key))

    def set_attribute_value(
        self,
        *,
        researcher_id: str,
        case_id: str,
        attribute_definition_id: str,
        value: object,
    ) -> CaseAttributeValueRecord:
        normalized_case_id = _identifier(case_id, "case_id")
        normalized_definition_id = _identifier(
            attribute_definition_id,
            "attribute_definition_id",
        )
        now = _utc_now()
        with self._write() as connection:
            actor_id = self._require_active_researcher(connection, researcher_id)
            self._require_case(connection, normalized_case_id)
            definition = self._require_definition(
                connection,
                normalized_definition_id,
            )
            normalized_value = _attribute_value(value, definition)
            existing_row = self._value_row(
                connection,
                normalized_case_id,
                normalized_definition_id,
            )
            if existing_row is None:
                event_type = "case.attribute_value.set"
                connection.execute(
                    """
                    insert into case_attribute_values (
                      project_id, case_id, attribute_definition_id, value_json,
                      updated_by, created_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.project_id,
                        normalized_case_id,
                        normalized_definition_id,
                        _value_json(normalized_value),
                        actor_id,
                        now,
                        now,
                    ),
                )
            else:
                self._value_record(existing_row, definition)
                event_type = "case.attribute_value.replaced"
                connection.execute(
                    """
                    update case_attribute_values
                    set value_json = ?, updated_by = ?, updated_at = ?
                    where project_id = ? and case_id = ?
                      and attribute_definition_id = ?
                    """,
                    (
                        _value_json(normalized_value),
                        actor_id,
                        now,
                        self.project_id,
                        normalized_case_id,
                        normalized_definition_id,
                    ),
                )
            self._append_audit(
                connection,
                actor_id=actor_id,
                event_type=event_type,
                subject_type="case",
                subject_id=normalized_case_id,
                metadata={
                    "attribute_definition_id": normalized_definition_id,
                    "value_type": definition.value_type,
                },
                created_at=now,
            )
            return self._require_value(
                connection,
                normalized_case_id,
                definition,
            )

    def read_attribute_value(
        self,
        *,
        case_id: str,
        attribute_definition_id: str,
    ) -> CaseAttributeValueRecord:
        normalized_case_id = _identifier(case_id, "case_id")
        normalized_definition_id = _identifier(
            attribute_definition_id,
            "attribute_definition_id",
        )
        with self._read() as connection:
            self._require_project(connection)
            self._require_case(connection, normalized_case_id)
            definition = self._require_definition(
                connection,
                normalized_definition_id,
            )
            return self._require_value(connection, normalized_case_id, definition)

    def clear_attribute_value(
        self,
        *,
        researcher_id: str,
        case_id: str,
        attribute_definition_id: str,
    ) -> None:
        normalized_case_id = _identifier(case_id, "case_id")
        normalized_definition_id = _identifier(
            attribute_definition_id,
            "attribute_definition_id",
        )
        now = _utc_now()
        with self._write() as connection:
            actor_id = self._require_active_researcher(connection, researcher_id)
            self._require_case(connection, normalized_case_id)
            definition = self._require_definition(
                connection,
                normalized_definition_id,
            )
            self._require_value(connection, normalized_case_id, definition)
            connection.execute(
                """
                delete from case_attribute_values
                where project_id = ? and case_id = ?
                  and attribute_definition_id = ?
                """,
                (
                    self.project_id,
                    normalized_case_id,
                    normalized_definition_id,
                ),
            )
            self._append_audit(
                connection,
                actor_id=actor_id,
                event_type="case.attribute_value.cleared",
                subject_type="case",
                subject_id=normalized_case_id,
                metadata={"attribute_definition_id": normalized_definition_id},
                created_at=now,
            )

    def link_source(
        self,
        *,
        researcher_id: str,
        case_id: str,
        project_source_id: str,
    ) -> SourceCaseLinkRecord:
        normalized_case_id = _identifier(case_id, "case_id")
        exact_source_id = _external_source_id(project_source_id)
        self._validate_source_ownership(
            (exact_source_id,),
            missing_is_not_found=True,
            preserve_schema_error=False,
        )
        now = _utc_now()
        with self._write() as connection:
            actor_id = self._require_active_researcher(connection, researcher_id)
            self._require_case(connection, normalized_case_id)
            existing = self._link_row(
                connection,
                normalized_case_id,
                exact_source_id,
            )
            if existing is not None:
                link = self._link_record(existing)
                if link.linked_by == actor_id:
                    return link
                raise CaseConflictError(
                    "Source link already exists with a different actor"
                )
            connection.execute(
                """
                insert into source_case_links (
                  project_id, project_source_id, case_id, linked_by, created_at
                ) values (?, ?, ?, ?, ?)
                """,
                (
                    self.project_id,
                    exact_source_id,
                    normalized_case_id,
                    actor_id,
                    now,
                ),
            )
            self._append_audit(
                connection,
                actor_id=actor_id,
                event_type="case.source.linked",
                subject_type="case",
                subject_id=normalized_case_id,
                metadata={"project_source_id": exact_source_id},
                created_at=now,
            )
            return self._require_link(
                connection,
                normalized_case_id,
                exact_source_id,
            )

    def unlink_source(
        self,
        *,
        researcher_id: str,
        case_id: str,
        project_source_id: str,
    ) -> None:
        normalized_case_id = _identifier(case_id, "case_id")
        exact_source_id = _external_source_id(project_source_id)
        now = _utc_now()
        with self._write() as connection:
            actor_id = self._require_active_researcher(connection, researcher_id)
            self._require_case(connection, normalized_case_id)
            self._require_link(connection, normalized_case_id, exact_source_id)
            connection.execute(
                """
                delete from source_case_links
                where project_id = ? and project_source_id = ? and case_id = ?
                """,
                (self.project_id, exact_source_id, normalized_case_id),
            )
            self._append_audit(
                connection,
                actor_id=actor_id,
                event_type="case.source.unlinked",
                subject_type="case",
                subject_id=normalized_case_id,
                metadata={"project_source_id": exact_source_id},
                created_at=now,
            )

    def validate_project_state(self) -> None:
        source_ids: set[str] = set()
        with self._read() as connection:
            for row in connection.execute("select * from cases").fetchall():
                self._case_record(row)
            for row in connection.execute(
                "select * from attribute_definitions"
            ).fetchall():
                self._definition_record(row)
            for row in connection.execute(
                """
                select values_row.*,
                       definitions.attribute_key as definition_attribute_key,
                       definitions.label as definition_label,
                       definitions.value_type as definition_value_type,
                       definitions.allowed_values_json
                         as definition_allowed_values_json,
                       definitions.required as definition_required,
                       definitions.created_by as definition_created_by,
                       definitions.updated_by as definition_updated_by,
                       definitions.created_at as definition_created_at,
                       definitions.updated_at as definition_updated_at
                from case_attribute_values values_row
                join attribute_definitions definitions
                  on definitions.project_id = values_row.project_id
                 and definitions.attribute_definition_id =
                     values_row.attribute_definition_id
                """
            ).fetchall():
                definition = self._definition_from_value_row(row)
                self._value_record(row, definition)
            for row in connection.execute("select * from source_case_links").fetchall():
                link = self._link_record(row)
                source_ids.add(link.project_source_id)
        if source_ids:
            self._validate_source_ownership(
                tuple(sorted(source_ids)),
                missing_is_not_found=False,
                preserve_schema_error=True,
            )

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        try:
            with self.database.read() as connection:
                connection.row_factory = sqlite3.Row
                yield connection
        except FileNotFoundError as exc:
            raise CaseNotFoundError("Qualitative project not found") from exc
        except sqlite3.DatabaseError as exc:
            raise CaseConflictError(
                "Qualitative case storage is unavailable or corrupt"
            ) from exc

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        try:
            with self.database.transaction() as connection:
                connection.row_factory = sqlite3.Row
                yield connection
        except FileNotFoundError as exc:
            raise CaseNotFoundError("Qualitative project not found") from exc
        except sqlite3.IntegrityError as exc:
            raise _translated_integrity_error(exc) from exc
        except sqlite3.DatabaseError as exc:
            raise CaseConflictError(
                "Qualitative case storage is unavailable or corrupt"
            ) from exc

    def _require_active_researcher(
        self,
        connection: sqlite3.Connection,
        researcher_id: str,
    ) -> str:
        normalized_researcher_id = _identifier(researcher_id, "researcher_id")
        row = connection.execute(
            """
            select active from researchers
            where project_id = ? and researcher_id = ?
            """,
            (self.project_id, normalized_researcher_id),
        ).fetchone()
        if row is None:
            raise CaseNotFoundError("Researcher not found")
        active = row["active"]
        if type(active) is not int or active not in (0, 1):
            raise CaseConflictError("Stored researcher record is invalid")
        if active != 1:
            raise CaseConflictError("Researcher is inactive")
        return normalized_researcher_id

    def _require_project(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "select 1 from qualitative_projects where project_id = ?",
            (self.project_id,),
        ).fetchone()
        if row is None:
            raise CaseNotFoundError("Qualitative project is not initialized")

    def _require_case(
        self,
        connection: sqlite3.Connection,
        case_id: str,
    ) -> CaseRecord:
        row = connection.execute(
            "select * from cases where project_id = ? and case_id = ?",
            (self.project_id, case_id),
        ).fetchone()
        if row is None:
            raise CaseNotFoundError("Case not found")
        return self._case_record(row)

    def _require_definition(
        self,
        connection: sqlite3.Connection,
        attribute_definition_id: str,
    ) -> AttributeDefinitionRecord:
        row = connection.execute(
            """
            select * from attribute_definitions
            where project_id = ? and attribute_definition_id = ?
            """,
            (self.project_id, attribute_definition_id),
        ).fetchone()
        if row is None:
            raise CaseNotFoundError("Attribute definition not found")
        return self._definition_record(row)

    def _value_row(
        self,
        connection: sqlite3.Connection,
        case_id: str,
        attribute_definition_id: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            select * from case_attribute_values
            where project_id = ? and case_id = ?
              and attribute_definition_id = ?
            """,
            (self.project_id, case_id, attribute_definition_id),
        ).fetchone()

    def _require_value(
        self,
        connection: sqlite3.Connection,
        case_id: str,
        definition: AttributeDefinitionRecord,
    ) -> CaseAttributeValueRecord:
        row = self._value_row(
            connection,
            case_id,
            definition.attribute_definition_id,
        )
        if row is None:
            raise CaseNotFoundError("Case attribute value not found")
        return self._value_record(row, definition)

    def _link_row(
        self,
        connection: sqlite3.Connection,
        case_id: str,
        project_source_id: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            select * from source_case_links
            where project_id = ? and project_source_id = ? and case_id = ?
            """,
            (self.project_id, project_source_id, case_id),
        ).fetchone()

    def _require_link(
        self,
        connection: sqlite3.Connection,
        case_id: str,
        project_source_id: str,
    ) -> SourceCaseLinkRecord:
        row = self._link_row(connection, case_id, project_source_id)
        if row is None:
            raise CaseNotFoundError("Source link not found")
        return self._link_record(row)

    def _snapshot(
        self,
        connection: sqlite3.Connection,
        case_id: str,
    ) -> CaseSnapshot:
        case = self._require_case(connection, case_id)
        rows = connection.execute(
            """
            select values_row.*,
                   definitions.attribute_key as definition_attribute_key,
                   definitions.label as definition_label,
                   definitions.value_type as definition_value_type,
                   definitions.allowed_values_json
                     as definition_allowed_values_json,
                   definitions.required as definition_required,
                   definitions.created_by as definition_created_by,
                   definitions.updated_by as definition_updated_by,
                   definitions.created_at as definition_created_at,
                   definitions.updated_at as definition_updated_at
            from case_attribute_values values_row
            join attribute_definitions definitions
              on definitions.project_id = values_row.project_id
             and definitions.attribute_definition_id =
                 values_row.attribute_definition_id
            where values_row.project_id = ? and values_row.case_id = ?
            """,
            (self.project_id, case_id),
        ).fetchall()
        values = []
        for row in rows:
            definition = self._definition_from_value_row(row)
            values.append(self._value_record(row, definition))
        link_rows = connection.execute(
            """
            select * from source_case_links
            where project_id = ? and case_id = ?
            """,
            (self.project_id, case_id),
        ).fetchall()
        source_ids = tuple(
            sorted(self._link_record(row).project_source_id for row in link_rows)
        )
        return CaseSnapshot(
            case=case,
            attribute_values=tuple(sorted(values, key=_value_sort_key)),
            project_source_ids=source_ids,
        )

    def _case_record(self, row: sqlite3.Row) -> CaseRecord:
        record = CaseRecord(
            case_id=_stored_trimmed_text(row["case_id"], "case_id"),
            project_id=_stored_trimmed_text(row["project_id"], "project_id"),
            case_kind=_stored_text(row["case_kind"], "case_kind"),
            label=_stored_trimmed_text(row["label"], "label"),
            description=_stored_text(row["description"], "description"),
            created_by=_stored_trimmed_text(row["created_by"], "created_by"),
            updated_by=_stored_trimmed_text(row["updated_by"], "updated_by"),
            created_at=_stored_trimmed_text(row["created_at"], "created_at"),
            updated_at=_stored_trimmed_text(row["updated_at"], "updated_at"),
        )
        if record.project_id != self.project_id or record.case_kind not in _CASE_KINDS:
            raise CaseConflictError("Stored case record is invalid")
        return record

    def _definition_record(self, row: sqlite3.Row) -> AttributeDefinitionRecord:
        value_type = _stored_text(row["value_type"], "value_type")
        if value_type not in _VALUE_TYPES:
            raise CaseConflictError("Stored attribute definition is invalid")
        allowed_values = _stored_allowed_values(
            row["allowed_values_json"],
            value_type,
        )
        raw_required = row["required"]
        if type(raw_required) is not int or raw_required not in (0, 1):
            raise CaseConflictError("Stored attribute definition is invalid")
        record = AttributeDefinitionRecord(
            attribute_definition_id=_stored_trimmed_text(
                row["attribute_definition_id"],
                "attribute_definition_id",
            ),
            project_id=_stored_trimmed_text(row["project_id"], "project_id"),
            attribute_key=_stored_trimmed_text(
                row["attribute_key"],
                "attribute_key",
            ),
            label=_stored_trimmed_text(row["label"], "label"),
            value_type=value_type,
            allowed_values=allowed_values,
            required=raw_required == 1,
            created_by=_stored_trimmed_text(row["created_by"], "created_by"),
            updated_by=_stored_trimmed_text(row["updated_by"], "updated_by"),
            created_at=_stored_trimmed_text(row["created_at"], "created_at"),
            updated_at=_stored_trimmed_text(row["updated_at"], "updated_at"),
        )
        if record.project_id != self.project_id:
            raise CaseConflictError("Stored attribute definition is invalid")
        return record

    def _definition_from_value_row(
        self,
        row: sqlite3.Row,
    ) -> AttributeDefinitionRecord:
        definition_row = {
            "attribute_definition_id": row["attribute_definition_id"],
            "project_id": row["project_id"],
            "attribute_key": row["definition_attribute_key"],
            "label": row["definition_label"],
            "value_type": row["definition_value_type"],
            "allowed_values_json": row["definition_allowed_values_json"],
            "required": row["definition_required"],
            "created_by": row["definition_created_by"],
            "updated_by": row["definition_updated_by"],
            "created_at": row["definition_created_at"],
            "updated_at": row["definition_updated_at"],
        }
        return self._definition_record(definition_row)  # type: ignore[arg-type]

    def _value_record(
        self,
        row: sqlite3.Row,
        definition: AttributeDefinitionRecord,
    ) -> CaseAttributeValueRecord:
        value_json = _stored_text(row["value_json"], "value_json")
        try:
            raw_value = json.loads(value_json)
        except (TypeError, UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise CaseConflictError("Stored case attribute value is invalid") from exc
        try:
            value = _attribute_value(raw_value, definition)
            if _value_json(value) != value_json:
                raise CaseConflictError("Stored case attribute value is invalid")
        except CaseValidationError as exc:
            raise CaseConflictError("Stored case attribute value is invalid") from exc
        record = CaseAttributeValueRecord(
            project_id=_stored_trimmed_text(row["project_id"], "project_id"),
            case_id=_stored_trimmed_text(row["case_id"], "case_id"),
            attribute_definition_id=_stored_trimmed_text(
                row["attribute_definition_id"],
                "attribute_definition_id",
            ),
            attribute_key=definition.attribute_key,
            value_type=definition.value_type,
            value=value,
            updated_by=_stored_trimmed_text(row["updated_by"], "updated_by"),
            created_at=_stored_trimmed_text(row["created_at"], "created_at"),
            updated_at=_stored_trimmed_text(row["updated_at"], "updated_at"),
        )
        if (
            record.project_id != self.project_id
            or record.attribute_definition_id
            != definition.attribute_definition_id
        ):
            raise CaseConflictError("Stored case attribute value is invalid")
        return record

    def _link_record(self, row: sqlite3.Row) -> SourceCaseLinkRecord:
        record = SourceCaseLinkRecord(
            project_id=_stored_trimmed_text(row["project_id"], "project_id"),
            project_source_id=_stored_external_source_id(row["project_source_id"]),
            case_id=_stored_trimmed_text(row["case_id"], "case_id"),
            linked_by=_stored_trimmed_text(row["linked_by"], "linked_by"),
            created_at=_stored_trimmed_text(row["created_at"], "created_at"),
        )
        if record.project_id != self.project_id:
            raise CaseConflictError("Stored source link is invalid")
        return record

    def _validate_source_ownership(
        self,
        project_source_ids: Sequence[str],
        *,
        missing_is_not_found: bool,
        preserve_schema_error: bool,
    ) -> None:
        try:
            with workspace_mutation_lock(self.root):
                catalog = EvidenceCatalog(self.root)
                if catalog.db_path.exists() or catalog.db_path.is_symlink():
                    mode = catalog.db_path.lstat().st_mode
                    if not stat.S_ISREG(mode):
                        raise OSError(
                            "Evidence catalog must be a non-symlink regular file"
                        )
                for project_source_id in project_source_ids:
                    try:
                        history = catalog.source_history(project_source_id)
                    except FileNotFoundError as exc:
                        if missing_is_not_found:
                            raise CaseNotFoundError("Evidence source not found") from exc
                        raise CaseConflictError(
                            "Stored source link references unavailable evidence"
                        ) from exc
                    source = history.get("source") if isinstance(history, Mapping) else None
                    if not isinstance(source, Mapping):
                        raise CaseConflictError("Evidence source record is invalid")
                    stored_source_id = source.get("project_source_id")
                    workspace_id = source.get("workspace_id")
                    if (
                        not isinstance(stored_source_id, str)
                        or stored_source_id != project_source_id
                        or not isinstance(workspace_id, str)
                    ):
                        raise CaseConflictError("Evidence source record is invalid")
                    if workspace_id != self.project_id:
                        raise CaseConflictError(
                            "Evidence source belongs to a different project"
                        )
        except SchemaCompatibilityError:
            if preserve_schema_error:
                raise
            raise CaseConflictError("Evidence catalog schema is unsupported")
        except (CaseNotFoundError, CaseConflictError):
            raise
        except (OSError, sqlite3.Error, TypeError, ValueError, KeyError) as exc:
            raise CaseConflictError(
                "Evidence source storage is unavailable or invalid"
            ) from exc

    def _append_audit(
        self,
        connection: sqlite3.Connection,
        *,
        actor_id: str,
        event_type: str,
        subject_type: str,
        subject_id: str,
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
                subject_type,
                subject_id,
                json.dumps(metadata, sort_keys=True, separators=(",", ":")),
                created_at,
            ),
        )


def _case_kind(value: object) -> str:
    if not isinstance(value, str) or value not in _CASE_KINDS:
        raise CaseValidationError("case_kind is invalid")
    return value


def _value_type(value: object) -> str:
    if not isinstance(value, str) or value not in _VALUE_TYPES:
        raise CaseValidationError("value_type is invalid")
    return value


def _required_trimmed_text(value: object, field_name: str) -> str:
    normalized = _text(value, field_name).strip()
    if not normalized:
        raise CaseValidationError(f"{field_name} must be non-empty")
    return normalized


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise CaseValidationError(f"{field_name} must be a string")
    return value


def _identifier(value: object, field_name: str) -> str:
    return _required_trimmed_text(value, field_name)


def _external_source_id(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CaseValidationError(
            "project_source_id must be a non-empty exact string"
        )
    return value


def _allowed_values(
    value: object,
    value_type: str,
) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise CaseValidationError("allowed_values must be a sequence of strings")
    if not all(isinstance(choice, str) for choice in value):
        raise CaseValidationError("allowed_values must contain only strings")
    normalized = tuple(choice.strip() for choice in value)
    if any(not choice for choice in normalized):
        raise CaseValidationError("allowed_values must not contain empty choices")
    if len(set(normalized)) != len(normalized):
        raise CaseValidationError("allowed_values must contain unique choices")
    if value_type == "categorical":
        if not normalized:
            raise CaseValidationError(
                "categorical attributes require allowed_values"
            )
    elif normalized:
        raise CaseValidationError(
            "allowed_values are supported only for categorical attributes"
        )
    return normalized


def _attribute_value(
    value: object,
    definition: AttributeDefinitionRecord,
) -> CaseAttributeScalar:
    if definition.value_type == "text":
        if not isinstance(value, str):
            raise CaseValidationError("text attribute value must be a string")
        return value
    if definition.value_type == "number":
        if type(value) not in (int, float):
            raise CaseValidationError("number attribute value must be numeric")
        if type(value) is float and not math.isfinite(value):
            raise CaseValidationError("number attribute value must be finite")
        return value
    if definition.value_type == "boolean":
        if type(value) is not bool:
            raise CaseValidationError("boolean attribute value must be a boolean")
        return value
    if definition.value_type == "date":
        if not isinstance(value, str):
            raise CaseValidationError("date attribute value must be a string")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise CaseValidationError("date attribute value is invalid") from exc
        if parsed.isoformat() != value:
            raise CaseValidationError("date attribute value must use YYYY-MM-DD")
        return value
    if definition.value_type == "categorical":
        if not isinstance(value, str) or value not in definition.allowed_values:
            raise CaseValidationError("categorical attribute value is invalid")
        return value
    raise CaseConflictError("Stored attribute definition is invalid")


def _allowed_values_json(values: Sequence[str]) -> str:
    return json.dumps(
        list(values),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _value_json(value: CaseAttributeScalar) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _stored_allowed_values(value: object, value_type: str) -> tuple[str, ...]:
    raw = _stored_text(value, "allowed_values_json")
    try:
        parsed = json.loads(raw)
    except (TypeError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise CaseConflictError("Stored attribute definition is invalid") from exc
    if not isinstance(parsed, list):
        raise CaseConflictError("Stored attribute definition is invalid")
    try:
        normalized = _allowed_values(parsed, value_type)
    except CaseValidationError as exc:
        raise CaseConflictError("Stored attribute definition is invalid") from exc
    if _allowed_values_json(normalized) != raw:
        raise CaseConflictError("Stored attribute definition is invalid")
    return normalized


def _stored_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise CaseConflictError(f"Stored case {field_name} is invalid")
    return value


def _stored_trimmed_text(value: object, field_name: str) -> str:
    stored = _stored_text(value, field_name)
    if not stored or stored != stored.strip():
        raise CaseConflictError(f"Stored case {field_name} is invalid")
    return stored


def _stored_external_source_id(value: object) -> str:
    stored = _stored_text(value, "project_source_id")
    if not stored or stored != stored.strip():
        raise CaseConflictError("Stored source link is invalid")
    return stored


def _case_sort_key(record: CaseRecord) -> tuple[str, str, str, str]:
    return (
        record.case_kind,
        record.label.casefold(),
        record.label,
        record.case_id,
    )


def _definition_sort_key(
    record: AttributeDefinitionRecord,
) -> tuple[str, str, str]:
    return (
        record.attribute_key.casefold(),
        record.attribute_key,
        record.attribute_definition_id,
    )


def _value_sort_key(record: CaseAttributeValueRecord) -> tuple[str, str, str]:
    return (
        record.attribute_key.casefold(),
        record.attribute_key,
        record.attribute_definition_id,
    )


def _translated_integrity_error(
    exc: sqlite3.IntegrityError,
) -> CaseConflictError | CaseValidationError:
    message = str(exc).casefold()
    if "unique" in message:
        return CaseConflictError("Case identity conflicts with existing state")
    if "foreign key" in message:
        return CaseValidationError("Case data references invalid project state")
    if "check constraint" in message or "not null" in message:
        return CaseValidationError("Case data violates the storage contract")
    return CaseConflictError("Case storage constraint conflict")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
