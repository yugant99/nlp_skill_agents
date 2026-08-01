from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.qualitative.database import (
    QualitativeProjectDatabase,
    new_qualitative_id,
)


class CodebookNotFoundError(LookupError):
    pass


class CodebookValidationError(ValueError):
    pass


class CodebookConflictError(RuntimeError):
    pass


class CodebookImmutableError(CodebookConflictError):
    pass


@dataclass(frozen=True)
class CodebookRecord:
    codebook_id: str
    project_id: str
    title: str
    description: str
    created_by: str
    updated_by: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CodebookVersionRecord:
    codebook_version_id: str
    project_id: str
    codebook_id: str
    version_number: int
    status: str
    based_on_version_id: str | None
    created_by: str
    created_at: str
    frozen_at: str | None


@dataclass(frozen=True)
class CodeRecord:
    code_id: str
    project_id: str
    codebook_version_id: str
    stable_code_key: str
    parent_code_id: str | None
    label: str
    definition: str
    inclusion_criteria: str
    exclusion_criteria: str
    examples: tuple[str, ...]
    notes: str
    color: str
    sort_order: int
    created_by: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CodebookVersionSnapshot:
    codebook: CodebookRecord
    version: CodebookVersionRecord
    codes: tuple[CodeRecord, ...]


@dataclass(frozen=True)
class _ImportedCode:
    stable_code_key: str
    parent_stable_code_key: str | None
    label: str
    definition: str
    inclusion_criteria: str
    exclusion_criteria: str
    examples: tuple[str, ...]
    notes: str
    color: str
    sort_order: int


@dataclass(frozen=True)
class _ImportedDocument:
    title: str
    description: str
    source_version_number: int
    source_status: str
    codes: tuple[_ImportedCode, ...]


class CodebookService:
    def __init__(self, root: Path | str, project_id: str) -> None:
        try:
            self.database = QualitativeProjectDatabase(root, project_id)
        except (TypeError, ValueError) as exc:
            raise CodebookValidationError("Invalid qualitative project id") from exc
        self.project_id = self.database.project_id

    def create_codebook(
        self,
        *,
        researcher_id: str,
        title: str,
        description: str = "",
    ) -> CodebookRecord:
        normalized_title = _required_trimmed_text(title, "title")
        normalized_description = _text(description, "description")
        codebook_id = new_qualitative_id("codebook")
        now = _utc_now()
        with self._write() as connection:
            self._require_active_researcher(connection, researcher_id)
            connection.execute(
                """
                insert into codebooks (
                  codebook_id, project_id, title, description,
                  created_by, updated_by, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    codebook_id,
                    self.project_id,
                    normalized_title,
                    normalized_description,
                    researcher_id,
                    researcher_id,
                    now,
                    now,
                ),
            )
            self._append_audit(
                connection,
                actor_id=researcher_id,
                event_type="codebook.created",
                subject_type="codebook",
                subject_id=codebook_id,
                metadata={},
                created_at=now,
            )
            return self._require_codebook(connection, codebook_id)

    def list_codebooks(self) -> tuple[CodebookRecord, ...]:
        with self._read() as connection:
            self._require_project(connection)
            rows = connection.execute(
                """
                select * from codebooks where project_id = ?
                """,
                (self.project_id,),
            ).fetchall()
            records = tuple(self._codebook_record(row) for row in rows)
        return tuple(
            sorted(
                records,
                key=lambda record: (record.title.casefold(), record.codebook_id),
            )
        )

    def create_draft(
        self,
        *,
        researcher_id: str,
        codebook_id: str,
    ) -> CodebookVersionSnapshot:
        _identifier(codebook_id, "codebook_id")
        version_id = new_qualitative_id("codebook_version")
        now = _utc_now()
        with self._write() as connection:
            self._require_active_researcher(connection, researcher_id)
            self._require_codebook(connection, codebook_id)
            existing = connection.execute(
                """
                select 1 from codebook_versions
                where project_id = ? and codebook_id = ? limit 1
                """,
                (self.project_id, codebook_id),
            ).fetchone()
            if existing is not None:
                raise CodebookConflictError("Codebook already has a version")
            connection.execute(
                """
                insert into codebook_versions (
                  codebook_version_id, project_id, codebook_id, version_number,
                  status, based_on_version_id, created_by, created_at, frozen_at
                ) values (?, ?, ?, 1, 'draft', null, ?, ?, null)
                """,
                (version_id, self.project_id, codebook_id, researcher_id, now),
            )
            self._touch_codebook(connection, codebook_id, researcher_id, now)
            self._append_audit(
                connection,
                actor_id=researcher_id,
                event_type="codebook.version.created",
                subject_type="codebook_version",
                subject_id=version_id,
                metadata={"codebook_id": codebook_id, "version_number": 1},
                created_at=now,
            )
            return self._snapshot(connection, codebook_id, version_id)

    def derive_draft(
        self,
        *,
        researcher_id: str,
        codebook_id: str,
        based_on_version_id: str,
    ) -> CodebookVersionSnapshot:
        _identifier(codebook_id, "codebook_id")
        _identifier(based_on_version_id, "based_on_version_id")
        version_id = new_qualitative_id("codebook_version")
        now = _utc_now()
        with self._write() as connection:
            self._require_active_researcher(connection, researcher_id)
            self._require_codebook(connection, codebook_id)
            source = self._require_version(
                connection,
                codebook_id,
                based_on_version_id,
            )
            if source.status != "frozen":
                raise CodebookConflictError(
                    "A draft can be derived only from a frozen version"
                )
            source_snapshot = self._snapshot(
                connection,
                codebook_id,
                based_on_version_id,
            )
            next_version = int(
                connection.execute(
                    """
                    select coalesce(max(version_number), 0) + 1
                    from codebook_versions
                    where project_id = ? and codebook_id = ?
                    """,
                    (self.project_id, codebook_id),
                ).fetchone()[0]
            )
            connection.execute(
                """
                insert into codebook_versions (
                  codebook_version_id, project_id, codebook_id, version_number,
                  status, based_on_version_id, created_by, created_at, frozen_at
                ) values (?, ?, ?, ?, 'draft', ?, ?, ?, null)
                """,
                (
                    version_id,
                    self.project_id,
                    codebook_id,
                    next_version,
                    based_on_version_id,
                    researcher_id,
                    now,
                ),
            )
            copied_ids: dict[str, str] = {}
            for source_code in source_snapshot.codes:
                copied_id = new_qualitative_id("code")
                copied_ids[source_code.code_id] = copied_id
                connection.execute(
                    """
                    insert into codes (
                      code_id, project_id, codebook_version_id, stable_code_key,
                      parent_code_id, label, definition, inclusion_criteria,
                      exclusion_criteria, examples_json, notes, color, sort_order,
                      created_by, created_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        copied_id,
                        self.project_id,
                        version_id,
                        source_code.stable_code_key,
                        copied_ids.get(source_code.parent_code_id),
                        source_code.label,
                        source_code.definition,
                        source_code.inclusion_criteria,
                        source_code.exclusion_criteria,
                        _examples_json(source_code.examples),
                        source_code.notes,
                        source_code.color,
                        source_code.sort_order,
                        researcher_id,
                        now,
                        now,
                    ),
                )
            self._touch_codebook(connection, codebook_id, researcher_id, now)
            self._append_audit(
                connection,
                actor_id=researcher_id,
                event_type="codebook.version.derived",
                subject_type="codebook_version",
                subject_id=version_id,
                metadata={
                    "based_on_version_id": based_on_version_id,
                    "code_count": len(source_snapshot.codes),
                    "codebook_id": codebook_id,
                    "version_number": next_version,
                },
                created_at=now,
            )
            return self._snapshot(connection, codebook_id, version_id)

    def add_code(
        self,
        *,
        researcher_id: str,
        codebook_id: str,
        codebook_version_id: str,
        stable_code_key: str,
        label: str,
        parent_code_id: str | None = None,
        definition: str = "",
        inclusion_criteria: str = "",
        exclusion_criteria: str = "",
        examples: Sequence[str] = (),
        notes: str = "",
        color: str = "",
        sort_order: int = 0,
    ) -> CodeRecord:
        values = _normalize_code_values(
            label=label,
            parent_code_id=parent_code_id,
            definition=definition,
            inclusion_criteria=inclusion_criteria,
            exclusion_criteria=exclusion_criteria,
            examples=examples,
            notes=notes,
            color=color,
            sort_order=sort_order,
        )
        normalized_key = _required_trimmed_text(stable_code_key, "stable_code_key")
        _identifier(codebook_id, "codebook_id")
        _identifier(codebook_version_id, "codebook_version_id")
        code_id = new_qualitative_id("code")
        now = _utc_now()
        with self._write() as connection:
            self._require_active_researcher(connection, researcher_id)
            self._require_codebook(connection, codebook_id)
            version = self._require_version(
                connection, codebook_id, codebook_version_id
            )
            self._require_draft(version)
            self._require_parent(
                connection,
                codebook_version_id,
                values["parent_code_id"],
            )
            duplicate = connection.execute(
                """
                select 1 from codes
                where project_id = ? and codebook_version_id = ?
                  and stable_code_key = ?
                """,
                (self.project_id, codebook_version_id, normalized_key),
            ).fetchone()
            if duplicate is not None:
                raise CodebookConflictError("stable_code_key already exists in version")
            connection.execute(
                """
                insert into codes (
                  code_id, project_id, codebook_version_id, stable_code_key,
                  parent_code_id, label, definition, inclusion_criteria,
                  exclusion_criteria, examples_json, notes, color, sort_order,
                  created_by, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    code_id,
                    self.project_id,
                    codebook_version_id,
                    normalized_key,
                    values["parent_code_id"],
                    values["label"],
                    values["definition"],
                    values["inclusion_criteria"],
                    values["exclusion_criteria"],
                    _examples_json(values["examples"]),
                    values["notes"],
                    values["color"],
                    values["sort_order"],
                    researcher_id,
                    now,
                    now,
                ),
            )
            self._touch_codebook(connection, codebook_id, researcher_id, now)
            self._append_audit(
                connection,
                actor_id=researcher_id,
                event_type="codebook.code.created",
                subject_type="code",
                subject_id=code_id,
                metadata={
                    "codebook_id": codebook_id,
                    "codebook_version_id": codebook_version_id,
                },
                created_at=now,
            )
            return self._require_code(connection, codebook_version_id, code_id)

    def update_code(
        self,
        *,
        researcher_id: str,
        codebook_id: str,
        codebook_version_id: str,
        code_id: str,
        label: str,
        parent_code_id: str | None,
        definition: str,
        inclusion_criteria: str,
        exclusion_criteria: str,
        examples: Sequence[str],
        notes: str,
        color: str,
        sort_order: int,
    ) -> CodeRecord:
        values = _normalize_code_values(
            label=label,
            parent_code_id=parent_code_id,
            definition=definition,
            inclusion_criteria=inclusion_criteria,
            exclusion_criteria=exclusion_criteria,
            examples=examples,
            notes=notes,
            color=color,
            sort_order=sort_order,
        )
        _identifier(codebook_id, "codebook_id")
        _identifier(codebook_version_id, "codebook_version_id")
        _identifier(code_id, "code_id")
        now = _utc_now()
        with self._write() as connection:
            self._require_active_researcher(connection, researcher_id)
            self._require_codebook(connection, codebook_id)
            version = self._require_version(
                connection, codebook_id, codebook_version_id
            )
            self._require_draft(version)
            self._require_code(connection, codebook_version_id, code_id)
            self._require_parent(
                connection,
                codebook_version_id,
                values["parent_code_id"],
            )
            self._reject_cycle(
                connection,
                codebook_version_id=codebook_version_id,
                code_id=code_id,
                proposed_parent_id=values["parent_code_id"],
            )
            connection.execute(
                """
                update codes
                set parent_code_id = ?, label = ?, definition = ?,
                    inclusion_criteria = ?, exclusion_criteria = ?,
                    examples_json = ?, notes = ?, color = ?, sort_order = ?,
                    updated_at = ?
                where project_id = ? and codebook_version_id = ? and code_id = ?
                """,
                (
                    values["parent_code_id"],
                    values["label"],
                    values["definition"],
                    values["inclusion_criteria"],
                    values["exclusion_criteria"],
                    _examples_json(values["examples"]),
                    values["notes"],
                    values["color"],
                    values["sort_order"],
                    now,
                    self.project_id,
                    codebook_version_id,
                    code_id,
                ),
            )
            self._touch_codebook(connection, codebook_id, researcher_id, now)
            self._append_audit(
                connection,
                actor_id=researcher_id,
                event_type="codebook.code.updated",
                subject_type="code",
                subject_id=code_id,
                metadata={
                    "codebook_id": codebook_id,
                    "codebook_version_id": codebook_version_id,
                },
                created_at=now,
            )
            return self._require_code(connection, codebook_version_id, code_id)

    def freeze_version(
        self,
        *,
        researcher_id: str,
        codebook_id: str,
        codebook_version_id: str,
    ) -> CodebookVersionSnapshot:
        _identifier(codebook_id, "codebook_id")
        _identifier(codebook_version_id, "codebook_version_id")
        now = _utc_now()
        with self._write() as connection:
            self._require_active_researcher(connection, researcher_id)
            self._require_codebook(connection, codebook_id)
            version = self._require_version(
                connection, codebook_id, codebook_version_id
            )
            if version.status == "frozen":
                return self._snapshot(connection, codebook_id, codebook_version_id)
            if version.status != "draft":
                raise CodebookConflictError("Codebook version has an invalid status")
            snapshot = self._snapshot(connection, codebook_id, codebook_version_id)
            if not snapshot.codes:
                raise CodebookValidationError("Cannot freeze an empty codebook version")
            connection.execute(
                """
                update codebook_versions set status = 'frozen', frozen_at = ?
                where project_id = ? and codebook_id = ? and codebook_version_id = ?
                """,
                (now, self.project_id, codebook_id, codebook_version_id),
            )
            self._touch_codebook(connection, codebook_id, researcher_id, now)
            self._append_audit(
                connection,
                actor_id=researcher_id,
                event_type="codebook.version.frozen",
                subject_type="codebook_version",
                subject_id=codebook_version_id,
                metadata={
                    "code_count": len(snapshot.codes),
                    "codebook_id": codebook_id,
                    "version_number": version.version_number,
                },
                created_at=now,
            )
            return self._snapshot(connection, codebook_id, codebook_version_id)

    def read_version(
        self,
        *,
        codebook_id: str,
        codebook_version_id: str,
    ) -> CodebookVersionSnapshot:
        _identifier(codebook_id, "codebook_id")
        _identifier(codebook_version_id, "codebook_version_id")
        with self._read() as connection:
            return self._snapshot(connection, codebook_id, codebook_version_id)

    def export_version(
        self,
        *,
        codebook_id: str,
        codebook_version_id: str,
    ) -> dict[str, object]:
        snapshot = self.read_version(
            codebook_id=codebook_id,
            codebook_version_id=codebook_version_id,
        )
        stable_keys = {code.code_id: code.stable_code_key for code in snapshot.codes}
        return {
            "format": "nlp-skill-agents.codebook-version",
            "format_version": 1,
            "codebook": {
                "title": snapshot.codebook.title,
                "description": snapshot.codebook.description,
            },
            "version": {
                "source_version_number": snapshot.version.version_number,
                "source_status": snapshot.version.status,
            },
            "codes": [
                {
                    "stable_code_key": code.stable_code_key,
                    "parent_stable_code_key": stable_keys.get(code.parent_code_id),
                    "label": code.label,
                    "definition": code.definition,
                    "inclusion_criteria": code.inclusion_criteria,
                    "exclusion_criteria": code.exclusion_criteria,
                    "examples": list(code.examples),
                    "notes": code.notes,
                    "color": code.color,
                    "sort_order": code.sort_order,
                }
                for code in snapshot.codes
            ],
        }

    def import_version(
        self,
        *,
        researcher_id: str,
        document: object,
    ) -> CodebookVersionSnapshot:
        imported = _validate_import_document(document)
        codebook_id = new_qualitative_id("codebook")
        version_id = new_qualitative_id("codebook_version")
        now = _utc_now()
        with self._write() as connection:
            self._require_active_researcher(connection, researcher_id)
            connection.execute(
                """
                insert into codebooks (
                  codebook_id, project_id, title, description,
                  created_by, updated_by, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    codebook_id,
                    self.project_id,
                    imported.title,
                    imported.description,
                    researcher_id,
                    researcher_id,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                insert into codebook_versions (
                  codebook_version_id, project_id, codebook_id, version_number,
                  status, based_on_version_id, created_by, created_at, frozen_at
                ) values (?, ?, ?, 1, 'draft', null, ?, ?, null)
                """,
                (version_id, self.project_id, codebook_id, researcher_id, now),
            )
            copied_ids: dict[str, str] = {}
            for imported_code in imported.codes:
                code_id = new_qualitative_id("code")
                copied_ids[imported_code.stable_code_key] = code_id
                connection.execute(
                    """
                    insert into codes (
                      code_id, project_id, codebook_version_id, stable_code_key,
                      parent_code_id, label, definition, inclusion_criteria,
                      exclusion_criteria, examples_json, notes, color, sort_order,
                      created_by, created_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        code_id,
                        self.project_id,
                        version_id,
                        imported_code.stable_code_key,
                        copied_ids.get(imported_code.parent_stable_code_key),
                        imported_code.label,
                        imported_code.definition,
                        imported_code.inclusion_criteria,
                        imported_code.exclusion_criteria,
                        _examples_json(imported_code.examples),
                        imported_code.notes,
                        imported_code.color,
                        imported_code.sort_order,
                        researcher_id,
                        now,
                        now,
                    ),
                )
            self._append_audit(
                connection,
                actor_id=researcher_id,
                event_type="codebook.version.imported",
                subject_type="codebook_version",
                subject_id=version_id,
                metadata={
                    "code_count": len(imported.codes),
                    "codebook_id": codebook_id,
                    "source_status": imported.source_status,
                    "source_version_number": imported.source_version_number,
                },
                created_at=now,
            )
            return self._snapshot(connection, codebook_id, version_id)

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        try:
            with self.database.read() as connection:
                connection.row_factory = sqlite3.Row
                yield connection
        except FileNotFoundError as exc:
            raise CodebookNotFoundError("Qualitative project not found") from exc
        except sqlite3.DatabaseError as exc:
            raise CodebookConflictError(
                "Qualitative codebook storage is unavailable or corrupt"
            ) from exc

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        try:
            with self.database.transaction() as connection:
                connection.row_factory = sqlite3.Row
                yield connection
        except FileNotFoundError as exc:
            raise CodebookNotFoundError("Qualitative project not found") from exc
        except sqlite3.IntegrityError as exc:
            raise _translated_integrity_error(exc) from exc
        except sqlite3.DatabaseError as exc:
            raise CodebookConflictError(
                "Qualitative codebook storage is unavailable or corrupt"
            ) from exc

    def _require_active_researcher(
        self,
        connection: sqlite3.Connection,
        researcher_id: str,
    ) -> None:
        _identifier(researcher_id, "researcher_id")
        row = connection.execute(
            """
            select active from researchers
            where project_id = ? and researcher_id = ?
            """,
            (self.project_id, researcher_id),
        ).fetchone()
        if row is None:
            raise CodebookNotFoundError("Researcher not found")
        if int(row["active"]) != 1:
            raise CodebookConflictError("Researcher is inactive")

    def _require_project(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "select 1 from qualitative_projects where project_id = ?",
            (self.project_id,),
        ).fetchone()
        if row is None:
            raise CodebookNotFoundError("Qualitative project is not initialized")

    def _require_codebook(
        self,
        connection: sqlite3.Connection,
        codebook_id: str,
    ) -> CodebookRecord:
        row = connection.execute(
            """
            select * from codebooks where project_id = ? and codebook_id = ?
            """,
            (self.project_id, codebook_id),
        ).fetchone()
        if row is None:
            raise CodebookNotFoundError("Codebook not found")
        return self._codebook_record(row)

    def _require_version(
        self,
        connection: sqlite3.Connection,
        codebook_id: str,
        codebook_version_id: str,
    ) -> CodebookVersionRecord:
        row = connection.execute(
            """
            select * from codebook_versions
            where project_id = ? and codebook_id = ? and codebook_version_id = ?
            """,
            (self.project_id, codebook_id, codebook_version_id),
        ).fetchone()
        if row is None:
            raise CodebookNotFoundError("Codebook version not found")
        return self._version_record(row)

    def _require_code(
        self,
        connection: sqlite3.Connection,
        codebook_version_id: str,
        code_id: str,
    ) -> CodeRecord:
        row = connection.execute(
            """
            select * from codes
            where project_id = ? and codebook_version_id = ? and code_id = ?
            """,
            (self.project_id, codebook_version_id, code_id),
        ).fetchone()
        if row is None:
            raise CodebookNotFoundError("Code not found")
        return self._code_record(row)

    def _require_parent(
        self,
        connection: sqlite3.Connection,
        codebook_version_id: str,
        parent_code_id: str | None,
    ) -> None:
        if parent_code_id is None:
            return
        row = connection.execute(
            """
            select codebook_version_id from codes
            where project_id = ? and code_id = ?
            """,
            (self.project_id, parent_code_id),
        ).fetchone()
        if row is None:
            raise CodebookNotFoundError("Parent code not found")
        if row["codebook_version_id"] != codebook_version_id:
            raise CodebookValidationError(
                "Parent code must belong to the same codebook version"
            )

    def _reject_cycle(
        self,
        connection: sqlite3.Connection,
        *,
        codebook_version_id: str,
        code_id: str,
        proposed_parent_id: str | None,
    ) -> None:
        if proposed_parent_id == code_id:
            raise CodebookValidationError("A code cannot parent itself")
        current = proposed_parent_id
        visited: set[str] = set()
        while current is not None:
            if current == code_id:
                raise CodebookValidationError("Code hierarchy would contain a cycle")
            if current in visited:
                raise CodebookConflictError("Stored code hierarchy contains a cycle")
            visited.add(current)
            row = connection.execute(
                """
                select parent_code_id from codes
                where project_id = ? and codebook_version_id = ? and code_id = ?
                """,
                (self.project_id, codebook_version_id, current),
            ).fetchone()
            if row is None:
                raise CodebookConflictError(
                    "Stored code hierarchy references a missing code"
                )
            current = row["parent_code_id"]

    def _snapshot(
        self,
        connection: sqlite3.Connection,
        codebook_id: str,
        codebook_version_id: str,
    ) -> CodebookVersionSnapshot:
        codebook = self._require_codebook(connection, codebook_id)
        version = self._require_version(connection, codebook_id, codebook_version_id)
        rows = connection.execute(
            """
            select * from codes
            where project_id = ? and codebook_version_id = ?
            """,
            (self.project_id, codebook_version_id),
        ).fetchall()
        codes = tuple(self._code_record(row) for row in rows)
        ordered = _ordered_codes(codes, stored=True)
        return CodebookVersionSnapshot(
            codebook=codebook, version=version, codes=ordered
        )

    def _codebook_record(self, row: sqlite3.Row) -> CodebookRecord:
        record = CodebookRecord(
            codebook_id=_stored_text(row["codebook_id"], "codebook_id"),
            project_id=_stored_text(row["project_id"], "project_id"),
            title=_stored_text(row["title"], "title"),
            description=_stored_text(row["description"], "description"),
            created_by=_stored_text(row["created_by"], "created_by"),
            updated_by=_stored_text(row["updated_by"], "updated_by"),
            created_at=_stored_text(row["created_at"], "created_at"),
            updated_at=_stored_text(row["updated_at"], "updated_at"),
        )
        if record.project_id != self.project_id or not record.title.strip():
            raise CodebookConflictError("Stored codebook record is invalid")
        return record

    def _version_record(self, row: sqlite3.Row) -> CodebookVersionRecord:
        version_number = row["version_number"]
        status = _stored_text(row["status"], "status")
        frozen_at = _stored_optional_text(row["frozen_at"], "frozen_at")
        record = CodebookVersionRecord(
            codebook_version_id=_stored_text(
                row["codebook_version_id"], "codebook_version_id"
            ),
            project_id=_stored_text(row["project_id"], "project_id"),
            codebook_id=_stored_text(row["codebook_id"], "codebook_id"),
            version_number=version_number,
            status=status,
            based_on_version_id=_stored_optional_text(
                row["based_on_version_id"], "based_on_version_id"
            ),
            created_by=_stored_text(row["created_by"], "created_by"),
            created_at=_stored_text(row["created_at"], "created_at"),
            frozen_at=frozen_at,
        )
        if (
            record.project_id != self.project_id
            or isinstance(version_number, bool)
            or not isinstance(version_number, int)
            or version_number < 1
            or status not in {"draft", "frozen"}
            or (status == "draft" and frozen_at is not None)
            or (status == "frozen" and not isinstance(frozen_at, str))
        ):
            raise CodebookConflictError("Stored codebook version is invalid")
        return record

    def _code_record(self, row: sqlite3.Row) -> CodeRecord:
        examples_json = _stored_text(row["examples_json"], "examples_json")
        try:
            examples_value = json.loads(examples_json)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CodebookConflictError("Stored code examples are invalid") from exc
        if not isinstance(examples_value, list) or not all(
            isinstance(example, str) for example in examples_value
        ):
            raise CodebookConflictError("Stored code examples are invalid")
        sort_order = row["sort_order"]
        record = CodeRecord(
            code_id=_stored_text(row["code_id"], "code_id"),
            project_id=_stored_text(row["project_id"], "project_id"),
            codebook_version_id=_stored_text(
                row["codebook_version_id"], "codebook_version_id"
            ),
            stable_code_key=_stored_text(
                row["stable_code_key"], "stable_code_key"
            ),
            parent_code_id=_stored_optional_text(
                row["parent_code_id"], "parent_code_id"
            ),
            label=_stored_text(row["label"], "label"),
            definition=_stored_text(row["definition"], "definition"),
            inclusion_criteria=_stored_text(
                row["inclusion_criteria"], "inclusion_criteria"
            ),
            exclusion_criteria=_stored_text(
                row["exclusion_criteria"], "exclusion_criteria"
            ),
            examples=tuple(examples_value),
            notes=_stored_text(row["notes"], "notes"),
            color=_stored_text(row["color"], "color"),
            sort_order=sort_order,
            created_by=_stored_text(row["created_by"], "created_by"),
            created_at=_stored_text(row["created_at"], "created_at"),
            updated_at=_stored_text(row["updated_at"], "updated_at"),
        )
        if (
            record.project_id != self.project_id
            or not record.stable_code_key.strip()
            or not record.label.strip()
            or isinstance(sort_order, bool)
            or not isinstance(sort_order, int)
            or sort_order < 0
        ):
            raise CodebookConflictError("Stored code record is invalid")
        return record

    @staticmethod
    def _require_draft(version: CodebookVersionRecord) -> None:
        if version.status == "frozen":
            raise CodebookImmutableError("Frozen codebook version is immutable")
        if version.status != "draft":
            raise CodebookConflictError("Codebook version has an invalid status")

    def _touch_codebook(
        self,
        connection: sqlite3.Connection,
        codebook_id: str,
        researcher_id: str,
        updated_at: str,
    ) -> None:
        connection.execute(
            """
            update codebooks set updated_by = ?, updated_at = ?
            where project_id = ? and codebook_id = ?
            """,
            (researcher_id, updated_at, self.project_id, codebook_id),
        )

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


def _validate_import_document(document: object) -> _ImportedDocument:
    root = _mapping(document, "document")
    _exact_keys(
        root, {"format", "format_version", "codebook", "version", "codes"}, "document"
    )
    if root["format"] != "nlp-skill-agents.codebook-version":
        raise CodebookValidationError("Unsupported codebook document format")
    if isinstance(root["format_version"], bool) or root["format_version"] != 1:
        raise CodebookValidationError("Unsupported codebook format_version")

    codebook = _mapping(root["codebook"], "codebook")
    _exact_keys(codebook, {"title", "description"}, "codebook")
    title = _required_trimmed_text(codebook["title"], "codebook.title")
    description = _text(codebook["description"], "codebook.description")

    version = _mapping(root["version"], "version")
    _exact_keys(version, {"source_version_number", "source_status"}, "version")
    source_version_number = version["source_version_number"]
    if (
        isinstance(source_version_number, bool)
        or not isinstance(source_version_number, int)
        or source_version_number < 1
    ):
        raise CodebookValidationError(
            "version.source_version_number must be a positive integer"
        )
    source_status = version["source_status"]
    if not isinstance(source_status, str) or source_status not in {"draft", "frozen"}:
        raise CodebookValidationError("version.source_status is invalid")

    raw_codes = root["codes"]
    if not isinstance(raw_codes, list):
        raise CodebookValidationError("codes must be a list")
    imported_codes: list[_ImportedCode] = []
    expected_code_keys = {
        "stable_code_key",
        "parent_stable_code_key",
        "label",
        "definition",
        "inclusion_criteria",
        "exclusion_criteria",
        "examples",
        "notes",
        "color",
        "sort_order",
    }
    for index, value in enumerate(raw_codes):
        item = _mapping(value, f"codes[{index}]")
        _exact_keys(item, expected_code_keys, f"codes[{index}]")
        parent_key = item["parent_stable_code_key"]
        if parent_key is not None:
            parent_key = _required_trimmed_text(
                parent_key,
                f"codes[{index}].parent_stable_code_key",
            )
        imported_codes.append(
            _ImportedCode(
                stable_code_key=_required_trimmed_text(
                    item["stable_code_key"],
                    f"codes[{index}].stable_code_key",
                ),
                parent_stable_code_key=parent_key,
                label=_required_trimmed_text(item["label"], f"codes[{index}].label"),
                definition=_text(item["definition"], f"codes[{index}].definition"),
                inclusion_criteria=_text(
                    item["inclusion_criteria"],
                    f"codes[{index}].inclusion_criteria",
                ),
                exclusion_criteria=_text(
                    item["exclusion_criteria"],
                    f"codes[{index}].exclusion_criteria",
                ),
                examples=_examples(
                    item["examples"], f"codes[{index}].examples", require_list=True
                ),
                notes=_text(item["notes"], f"codes[{index}].notes"),
                color=_text(item["color"], f"codes[{index}].color"),
                sort_order=_sort_order(item["sort_order"]),
            )
        )
    return _ImportedDocument(
        title=title,
        description=description,
        source_version_number=source_version_number,
        source_status=source_status,
        codes=_ordered_import_codes(tuple(imported_codes)),
    )


def _ordered_codes(
    codes: tuple[CodeRecord, ...],
    *,
    stored: bool,
) -> tuple[CodeRecord, ...]:
    by_id: dict[str, CodeRecord] = {}
    stable_keys: set[str] = set()
    children: dict[str | None, list[CodeRecord]] = {}
    for code in codes:
        if code.code_id in by_id or code.stable_code_key in stable_keys:
            raise CodebookConflictError(
                "Stored codebook contains duplicate code identity"
            )
        by_id[code.code_id] = code
        stable_keys.add(code.stable_code_key)
    for code in codes:
        if code.parent_code_id is not None and code.parent_code_id not in by_id:
            raise CodebookConflictError(
                "Stored code hierarchy references a missing code"
            )
        children.setdefault(code.parent_code_id, []).append(code)
    for siblings in children.values():
        siblings.sort(key=_code_sort_key)
    ordered: list[CodeRecord] = []
    stack = list(reversed(children.get(None, [])))
    while stack:
        code = stack.pop()
        ordered.append(code)
        stack.extend(reversed(children.get(code.code_id, [])))
    if len(ordered) != len(codes):
        message = (
            "Stored code hierarchy contains a cycle"
            if stored
            else "Code hierarchy contains a cycle"
        )
        raise CodebookConflictError(message)
    return tuple(ordered)


def _ordered_import_codes(
    codes: tuple[_ImportedCode, ...],
) -> tuple[_ImportedCode, ...]:
    by_key: dict[str, _ImportedCode] = {}
    children: dict[str | None, list[_ImportedCode]] = {}
    for code in codes:
        if code.stable_code_key in by_key:
            raise CodebookValidationError(
                "stable_code_key must be unique within a version"
            )
        by_key[code.stable_code_key] = code
    for code in codes:
        parent = code.parent_stable_code_key
        if parent == code.stable_code_key:
            raise CodebookValidationError("A code cannot parent itself")
        if parent is not None and parent not in by_key:
            raise CodebookValidationError("parent_stable_code_key does not exist")
        children.setdefault(parent, []).append(code)
    for siblings in children.values():
        siblings.sort(
            key=lambda code: (
                code.sort_order,
                code.label.casefold(),
                code.stable_code_key,
            )
        )
    ordered: list[_ImportedCode] = []
    stack = list(reversed(children.get(None, [])))
    while stack:
        code = stack.pop()
        ordered.append(code)
        stack.extend(reversed(children.get(code.stable_code_key, [])))
    if len(ordered) != len(codes):
        raise CodebookValidationError("Code hierarchy contains a cycle")
    return tuple(ordered)


def _normalize_code_values(
    *,
    label: object,
    parent_code_id: object,
    definition: object,
    inclusion_criteria: object,
    exclusion_criteria: object,
    examples: object,
    notes: object,
    color: object,
    sort_order: object,
) -> dict[str, Any]:
    normalized_parent: str | None
    if parent_code_id is None:
        normalized_parent = None
    else:
        normalized_parent = _identifier(parent_code_id, "parent_code_id")
    return {
        "label": _required_trimmed_text(label, "label"),
        "parent_code_id": normalized_parent,
        "definition": _text(definition, "definition"),
        "inclusion_criteria": _text(inclusion_criteria, "inclusion_criteria"),
        "exclusion_criteria": _text(exclusion_criteria, "exclusion_criteria"),
        "examples": _examples(examples, "examples", require_list=False),
        "notes": _text(notes, "notes"),
        "color": _text(color, "color"),
        "sort_order": _sort_order(sort_order),
    }


def _mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CodebookValidationError(f"{field_name} must be an object")
    return value


def _exact_keys(
    value: Mapping[str, object],
    expected: set[str],
    field_name: str,
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise CodebookValidationError(
            f"{field_name} has invalid keys: {'; '.join(details)}"
        )


def _required_trimmed_text(value: object, field_name: str) -> str:
    normalized = _text(value, field_name).strip()
    if not normalized:
        raise CodebookValidationError(f"{field_name} must be non-empty")
    return normalized


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise CodebookValidationError(f"{field_name} must be a string")
    return value


def _stored_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise CodebookConflictError(f"Stored codebook {field_name} is invalid")
    return value


def _stored_optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _stored_text(value, field_name)


def _identifier(value: object, field_name: str) -> str:
    return _required_trimmed_text(value, field_name)


def _examples(
    value: object,
    field_name: str,
    *,
    require_list: bool,
) -> tuple[str, ...]:
    if require_list:
        valid_container = isinstance(value, list)
    else:
        valid_container = isinstance(value, Sequence) and not isinstance(
            value, (str, bytes)
        )
    if not valid_container or not all(isinstance(example, str) for example in value):
        raise CodebookValidationError(f"{field_name} must contain only strings")
    return tuple(value)


def _sort_order(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CodebookValidationError("sort_order must be a non-negative integer")
    return value


def _examples_json(examples: Sequence[str]) -> str:
    return json.dumps(list(examples), ensure_ascii=False, separators=(",", ":"))


def _code_sort_key(code: CodeRecord) -> tuple[int, str, str, str]:
    return (
        code.sort_order,
        code.label.casefold(),
        code.stable_code_key,
        code.code_id,
    )


def _translated_integrity_error(
    exc: sqlite3.IntegrityError,
) -> CodebookConflictError | CodebookValidationError:
    message = str(exc).casefold()
    if "immutable" in message:
        return CodebookImmutableError("Frozen codebook version is immutable")
    if "unique" in message:
        return CodebookConflictError("Codebook identity conflicts with existing state")
    if "foreign key" in message:
        return CodebookValidationError("Codebook references invalid project state")
    if "check constraint" in message or "not null" in message:
        return CodebookValidationError("Codebook value violates the storage contract")
    return CodebookConflictError("Codebook storage constraint conflict")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
