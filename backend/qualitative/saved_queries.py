from __future__ import annotations

import base64
import binascii
import json
import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from backend.qualitative.database import (
    QualitativeDatabaseConflict,
    QualitativeProjectDatabase,
    new_qualitative_id,
)
from backend.storage.evidence_catalog import EvidenceCatalog
from backend.storage.sqlite_migrations import SchemaCompatibilityError
from backend.storage.study_batch_operation_store import StudyBatchOperationConflict
from backend.storage.workspace_lock import WorkspaceLockError, workspace_mutation_lock


_ENTITY_ID = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_SAVED_QUERY_ID = re.compile(r"^qry_[0-9a-f]{32}$")
_AUDIT_EVENT_ID = re.compile(r"^qae_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CURSOR = re.compile(r"^[A-Za-z0-9_-]{1,4096}$")
_QUERY_KIND = "coding_reference_filter"
_DEFINITION_VERSION = 1
_FILTER_KEYS = {
    "project_source_id",
    "codebook_version_id",
    "code_id",
    "created_by",
    "include_removed",
}
_MAX_EXTERNAL_ID_LENGTH = 256
_MAX_EXTERNAL_ID_BYTES = 1024
_MAX_TITLE_LENGTH = 256
_MAX_TITLE_BYTES = 1024
_MAX_TIMESTAMP_LENGTH = 64
_MAX_PROJECT_QUERIES = 10_000
_MAX_PAGE_SIZE = 50
_MAX_CURSOR_DECODED_BYTES = 3072
_MAX_AUDIT_METADATA_BYTES = 1024
_REQUEST_DOMAIN = b"nlp-skill-agents.saved-query-create.v1\0"


class SavedQueryNotFoundError(LookupError):
    pass


class SavedQueryValidationError(ValueError):
    pass


class SavedQueryConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class SavedQueryFilters:
    project_source_id: str | None
    codebook_version_id: str | None
    code_id: str | None
    created_by: str | None
    include_removed: bool


@dataclass(frozen=True)
class SavedQueryDefinition:
    kind: str
    version: int
    filters: SavedQueryFilters


@dataclass(frozen=True)
class SavedQueryRecord:
    saved_query_id: str
    project_id: str
    title: str
    definition: SavedQueryDefinition
    created_by: str
    created_at: str


@dataclass(frozen=True)
class SavedQueryPage:
    saved_queries: tuple[SavedQueryRecord, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class _StoredSavedQuery:
    record: SavedQueryRecord
    request_sha256: str


class SavedQueryService:
    def __init__(self, root: Path | str, project_id: str) -> None:
        try:
            self.database = QualitativeProjectDatabase(root, project_id)
        except (TypeError, ValueError) as exc:
            raise SavedQueryValidationError(
                "Invalid qualitative project id"
            ) from exc
        self.root = Path(root)
        self.project_id = self.database.project_id

    def create_saved_query(
        self,
        *,
        saved_query_id: str,
        researcher_id: str,
        title: str,
        definition: Mapping[str, object],
    ) -> SavedQueryRecord:
        normalized_id = _input_saved_query_id(saved_query_id)
        actor_id = _input_entity_id(researcher_id, "researcher_id")
        normalized_title = _input_title(title)
        normalized_definition = _input_definition(definition)
        request_digest = _request_sha256(
            project_id=self.project_id,
            saved_query_id=normalized_id,
            researcher_id=actor_id,
            title=normalized_title,
            definition=normalized_definition,
        )

        initial = self._read_family()
        existing = _find_stored(initial, normalized_id)
        if existing is not None:
            self._require_exact_request(
                existing,
                saved_query_id=normalized_id,
                researcher_id=actor_id,
                title=normalized_title,
                definition=normalized_definition,
                request_sha256=request_digest,
            )

        stored_sources = _source_ids(initial)
        self._validate_source_ids(stored_sources, missing_is_not_found=False)
        requested_source = normalized_definition.filters.project_source_id
        if requested_source is not None and requested_source not in stored_sources:
            self._validate_source_ids(
                (requested_source,),
                missing_is_not_found=existing is None,
            )

        with self._write() as connection:
            current = self._load_family_local(connection)
            current_existing = _find_stored(current, normalized_id)
            if current_existing is not None:
                self._require_exact_request(
                    current_existing,
                    saved_query_id=normalized_id,
                    researcher_id=actor_id,
                    title=normalized_title,
                    definition=normalized_definition,
                    request_sha256=request_digest,
                )
                return current_existing.record

            self._require_active_researcher(connection, actor_id)
            self._validate_definition_local(
                connection,
                normalized_definition,
                missing_is_not_found=True,
            )
            if len(current) >= _MAX_PROJECT_QUERIES:
                raise SavedQueryValidationError(
                    "Project saved-query capacity would be exceeded"
                )

            now = _utc_now()
            connection.execute(
                """
                insert into saved_queries (
                  saved_query_id, project_id, title, query_kind,
                  definition_version, filters_json, request_sha256,
                  created_by, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_id,
                    self.project_id,
                    normalized_title,
                    normalized_definition.kind,
                    normalized_definition.version,
                    _canonical_filters_json(normalized_definition.filters),
                    request_digest,
                    actor_id,
                    now,
                ),
            )
            self._append_audit(
                connection,
                actor_id=actor_id,
                saved_query_id=normalized_id,
                created_at=now,
            )
            accepted = self._load_family_local(connection)
            stored = _find_stored(accepted, normalized_id)
            if stored is None:
                raise SavedQueryConflictError(
                    "Saved query was not stored atomically"
                )
            self._require_exact_request(
                stored,
                saved_query_id=normalized_id,
                researcher_id=actor_id,
                title=normalized_title,
                definition=normalized_definition,
                request_sha256=request_digest,
            )
            return stored.record

    def read_saved_query(self, saved_query_id: str) -> SavedQueryRecord:
        normalized_id = _input_saved_query_id(saved_query_id)
        family = self._read_validated_family()
        stored = _find_stored(family, normalized_id)
        if stored is None:
            raise SavedQueryNotFoundError("Saved query not found")
        return stored.record

    def list_saved_queries(
        self,
        *,
        created_by: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> SavedQueryPage:
        normalized_creator = (
            None
            if created_by is None
            else _input_entity_id(created_by, "created_by")
        )
        normalized_limit = _input_limit(limit)
        cursor_filters: dict[str, object] = {"created_by": normalized_creator}
        after = (
            None
            if cursor is None
            else _decode_cursor(
                cursor,
                project_id=self.project_id,
                filters=cursor_filters,
            )
        )

        family = self._read_validated_family()
        records = tuple(stored.record for stored in family)
        if after is not None:
            anchor = next(
                (
                    record
                    for record in records
                    if record.saved_query_id == after[1]
                ),
                None,
            )
            if anchor is None:
                raise SavedQueryNotFoundError("Saved-query cursor anchor not found")
            if anchor.created_at != after[0]:
                raise SavedQueryConflictError(
                    "Saved-query cursor anchor conflicts with stored state"
                )
            if normalized_creator is not None and anchor.created_by != normalized_creator:
                raise SavedQueryConflictError(
                    "Saved-query cursor anchor conflicts with its filter"
                )

        matching = tuple(
            record
            for record in records
            if (
                normalized_creator is None or record.created_by == normalized_creator
            )
            and (
                after is None
                or (record.created_at, record.saved_query_id) > after
            )
        )
        page_records = matching[:normalized_limit]
        has_more = len(matching) > normalized_limit
        next_cursor = None
        if has_more and page_records:
            last = page_records[-1]
            next_cursor = _encode_cursor(
                project_id=self.project_id,
                filters=cursor_filters,
                created_at=last.created_at,
                saved_query_id=last.saved_query_id,
            )
        return SavedQueryPage(
            saved_queries=page_records,
            next_cursor=next_cursor,
        )

    def validate_project_state(self) -> None:
        self._read_validated_family()

    def _read_family(self) -> tuple[_StoredSavedQuery, ...]:
        with self._read() as connection:
            return self._load_family_local(connection)

    def _read_validated_family(self) -> tuple[_StoredSavedQuery, ...]:
        family = self._read_family()
        self._validate_source_ids(_source_ids(family), missing_is_not_found=False)
        return family

    def _load_family_local(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[_StoredSavedQuery, ...]:
        self._require_project(connection)
        records: list[_StoredSavedQuery] = []
        for row in connection.execute(
            """
            select * from saved_queries
            order by created_at, saved_query_id
            """
        ):
            if len(records) >= _MAX_PROJECT_QUERIES:
                raise SavedQueryConflictError(
                    "Stored project saved-query capacity is exceeded"
                )
            records.append(self._stored_query(row))
        family = tuple(records)
        self._validate_local_relations(connection, family)
        self._validate_audits(connection, family)
        return family

    def _stored_query(self, row: sqlite3.Row) -> _StoredSavedQuery:
        saved_query_id = _stored_saved_query_id(row["saved_query_id"])
        project_id = _stored_text(row["project_id"], "project_id")
        if project_id != self.project_id:
            raise SavedQueryConflictError(
                "Stored saved query belongs to another project"
            )
        title = _stored_title(row["title"])
        query_kind = _stored_text(row["query_kind"], "query_kind")
        if query_kind != _QUERY_KIND:
            raise SavedQueryConflictError("Stored saved-query kind is invalid")
        definition_version = row["definition_version"]
        if type(definition_version) is not int or definition_version != 1:
            raise SavedQueryConflictError(
                "Stored saved-query definition version is invalid"
            )
        filters = _stored_filters(row["filters_json"])
        request_digest = _stored_sha256(row["request_sha256"], "request_sha256")
        created_by = _stored_entity_id(row["created_by"], "created_by")
        created_at = _stored_timestamp(row["created_at"], "created_at")
        definition = SavedQueryDefinition(
            kind=query_kind,
            version=definition_version,
            filters=filters,
        )
        expected_digest = _request_sha256(
            project_id=project_id,
            saved_query_id=saved_query_id,
            researcher_id=created_by,
            title=title,
            definition=definition,
        )
        if request_digest != expected_digest:
            raise SavedQueryConflictError(
                "Stored saved-query retry identity is invalid"
            )
        return _StoredSavedQuery(
            record=SavedQueryRecord(
                saved_query_id=saved_query_id,
                project_id=project_id,
                title=title,
                definition=definition,
                created_by=created_by,
                created_at=created_at,
            ),
            request_sha256=request_digest,
        )

    def _validate_local_relations(
        self,
        connection: sqlite3.Connection,
        family: Sequence[_StoredSavedQuery],
    ) -> None:
        researchers: set[str] = set()
        versions: set[str] = set()
        codes: set[str] = set()
        for stored in family:
            record = stored.record
            researchers.add(record.created_by)
            filters = record.definition.filters
            if filters.created_by is not None:
                researchers.add(filters.created_by)
            if filters.codebook_version_id is not None:
                versions.add(filters.codebook_version_id)
            if filters.code_id is not None:
                codes.add(filters.code_id)

        for researcher_id in sorted(researchers):
            self._require_researcher(
                connection,
                researcher_id,
                missing_is_not_found=False,
            )
        for version_id in sorted(versions):
            self._require_codebook_version(
                connection,
                version_id,
                missing_is_not_found=False,
            )
        code_versions: dict[str, str] = {}
        for code_id in sorted(codes):
            code_versions[code_id] = self._require_code(
                connection,
                code_id,
                missing_is_not_found=False,
            )
        for stored in family:
            filters = stored.record.definition.filters
            if (
                filters.codebook_version_id is not None
                and filters.code_id is not None
                and code_versions[filters.code_id] != filters.codebook_version_id
            ):
                raise SavedQueryConflictError(
                    "Stored saved-query code filter is inconsistent"
                )

    def _validate_definition_local(
        self,
        connection: sqlite3.Connection,
        definition: SavedQueryDefinition,
        *,
        missing_is_not_found: bool,
    ) -> None:
        filters = definition.filters
        if filters.created_by is not None:
            self._require_researcher(
                connection,
                filters.created_by,
                missing_is_not_found=missing_is_not_found,
            )
        if filters.codebook_version_id is not None:
            self._require_codebook_version(
                connection,
                filters.codebook_version_id,
                missing_is_not_found=missing_is_not_found,
            )
        code_version = None
        if filters.code_id is not None:
            code_version = self._require_code(
                connection,
                filters.code_id,
                missing_is_not_found=missing_is_not_found,
            )
        if (
            filters.codebook_version_id is not None
            and code_version is not None
            and code_version != filters.codebook_version_id
        ):
            if missing_is_not_found:
                raise SavedQueryNotFoundError(
                    "Saved-query code dependency was not found"
                )
            raise SavedQueryConflictError(
                "Stored saved-query code dependency is inconsistent"
            )

    def _require_project(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "select project_id from qualitative_projects where project_id = ?",
            (self.project_id,),
        ).fetchone()
        if row is None:
            raise SavedQueryNotFoundError(
                "Qualitative project is not initialized"
            )
        if _stored_text(row["project_id"], "project_id") != self.project_id:
            raise SavedQueryConflictError(
                "Stored qualitative project identity is invalid"
            )

    def _require_researcher(
        self,
        connection: sqlite3.Connection,
        researcher_id: str,
        *,
        missing_is_not_found: bool,
    ) -> bool:
        row = connection.execute(
            """
            select project_id, researcher_id, active from researchers
            where project_id = ? and researcher_id = ?
            """,
            (self.project_id, researcher_id),
        ).fetchone()
        if row is None:
            if missing_is_not_found:
                raise SavedQueryNotFoundError("Researcher not found")
            raise SavedQueryConflictError(
                "Stored saved-query researcher is unavailable"
            )
        if (
            _stored_text(row["project_id"], "researcher project_id")
            != self.project_id
            or _stored_entity_id(row["researcher_id"], "researcher_id")
            != researcher_id
            or type(row["active"]) is not int
            or row["active"] not in (0, 1)
        ):
            raise SavedQueryConflictError(
                "Stored saved-query researcher is invalid"
            )
        return bool(row["active"])

    def _require_active_researcher(
        self,
        connection: sqlite3.Connection,
        researcher_id: str,
    ) -> None:
        if not self._require_researcher(
            connection,
            researcher_id,
            missing_is_not_found=True,
        ):
            raise SavedQueryConflictError("Researcher is inactive")

    def _require_codebook_version(
        self,
        connection: sqlite3.Connection,
        codebook_version_id: str,
        *,
        missing_is_not_found: bool,
    ) -> None:
        row = connection.execute(
            """
            select project_id, codebook_version_id from codebook_versions
            where project_id = ? and codebook_version_id = ?
            """,
            (self.project_id, codebook_version_id),
        ).fetchone()
        if row is None:
            if missing_is_not_found:
                raise SavedQueryNotFoundError("Codebook version not found")
            raise SavedQueryConflictError(
                "Stored saved-query codebook version is unavailable"
            )
        if (
            _stored_text(row["project_id"], "codebook project_id")
            != self.project_id
            or _stored_entity_id(
                row["codebook_version_id"],
                "codebook_version_id",
            )
            != codebook_version_id
        ):
            raise SavedQueryConflictError(
                "Stored saved-query codebook version is invalid"
            )

    def _require_code(
        self,
        connection: sqlite3.Connection,
        code_id: str,
        *,
        missing_is_not_found: bool,
    ) -> str:
        row = connection.execute(
            """
            select project_id, codebook_version_id, code_id from codes
            where project_id = ? and code_id = ?
            """,
            (self.project_id, code_id),
        ).fetchone()
        if row is None:
            if missing_is_not_found:
                raise SavedQueryNotFoundError("Code not found")
            raise SavedQueryConflictError(
                "Stored saved-query code is unavailable"
            )
        if (
            _stored_text(row["project_id"], "code project_id") != self.project_id
            or _stored_entity_id(row["code_id"], "code_id") != code_id
        ):
            raise SavedQueryConflictError("Stored saved-query code is invalid")
        return _stored_entity_id(
            row["codebook_version_id"],
            "codebook_version_id",
        )

    def _validate_audits(
        self,
        connection: sqlite3.Connection,
        family: Sequence[_StoredSavedQuery],
    ) -> None:
        by_id = {stored.record.saved_query_id: stored for stored in family}
        candidates: dict[str, list[sqlite3.Row]] = {
            saved_query_id: [] for saved_query_id in by_id
        }
        unmatched = False
        candidate_count = 0
        for row in connection.execute(
            "select * from qualitative_audit_events order by event_id"
        ):
            subject_id = _normalized_marker(row["subject_id"])
            is_candidate = (
                subject_id in by_id
                or _normalized_marker(row["event_type"]) == "saved_query.created"
                or _normalized_marker(row["subject_type"]) == "saved_query"
                or _has_marker_prefix(row["subject_id"], "qry_")
                or _has_marker_prefix(row["event_type"], "saved_query.")
                or _has_marker_prefix(row["subject_type"], "saved_query")
            )
            if not is_candidate:
                continue
            candidate_count += 1
            if candidate_count > _MAX_PROJECT_QUERIES:
                raise SavedQueryConflictError(
                    "Stored saved-query audits exceed capacity"
                )
            if subject_id in candidates:
                candidates[subject_id].append(row)
            else:
                unmatched = True

        if unmatched:
            raise SavedQueryConflictError(
                "Stored saved-query audit has no matching query"
            )
        for saved_query_id, stored in by_id.items():
            rows = candidates[saved_query_id]
            if len(rows) != 1:
                raise SavedQueryConflictError(
                    "Stored saved-query audit identity is ambiguous"
                )
            self._validate_audit_row(rows[0], stored.record)

    def _validate_audit_row(
        self,
        row: sqlite3.Row,
        record: SavedQueryRecord,
    ) -> None:
        event_id = _stored_text(row["event_id"], "audit event_id")
        metadata = _stored_text(row["metadata_json"], "audit metadata_json")
        if (
            not _AUDIT_EVENT_ID.fullmatch(event_id)
            or len(metadata.encode("utf-8")) > _MAX_AUDIT_METADATA_BYTES
            or _stored_text(row["project_id"], "audit project_id")
            != self.project_id
            or _stored_entity_id(row["actor_id"], "audit actor_id")
            != record.created_by
            or _stored_text(row["event_type"], "audit event_type")
            != "saved_query.created"
            or _stored_text(row["subject_type"], "audit subject_type")
            != "saved_query"
            or _stored_saved_query_id(row["subject_id"])
            != record.saved_query_id
            or metadata
            != _canonical_json(
                {
                    "definition_version": 1,
                    "query_kind": _QUERY_KIND,
                }
            )
            or _stored_timestamp(row["created_at"], "audit created_at")
            != record.created_at
        ):
            raise SavedQueryConflictError(
                "Stored saved-query audit is invalid"
            )

    def _append_audit(
        self,
        connection: sqlite3.Connection,
        *,
        actor_id: str,
        saved_query_id: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            insert into qualitative_audit_events (
              event_id, project_id, actor_id, event_type,
              subject_type, subject_id, metadata_json, created_at
            ) values (?, ?, ?, 'saved_query.created',
                      'saved_query', ?, ?, ?)
            """,
            (
                new_qualitative_id("audit_event"),
                self.project_id,
                actor_id,
                saved_query_id,
                _canonical_json(
                    {
                        "definition_version": 1,
                        "query_kind": _QUERY_KIND,
                    }
                ),
                created_at,
            ),
        )

    def _validate_source_ids(
        self,
        source_ids: Sequence[str],
        *,
        missing_is_not_found: bool,
    ) -> None:
        unique_ids = tuple(dict.fromkeys(source_ids))
        if not unique_ids:
            return
        try:
            with workspace_mutation_lock(self.root):
                catalog = EvidenceCatalog(self.root)
                for source_id in unique_ids:
                    try:
                        history = catalog.source_history(source_id)
                    except FileNotFoundError as exc:
                        if missing_is_not_found:
                            raise SavedQueryNotFoundError(
                                "Project source not found"
                            ) from exc
                        raise SavedQueryConflictError(
                            "Stored saved-query source is unavailable"
                        ) from exc
                    if not isinstance(history, dict) or set(history) != {
                        "source",
                        "revisions",
                    }:
                        raise SavedQueryConflictError(
                            "Stored saved-query source history is invalid"
                        )
                    source = history["source"]
                    revisions = history["revisions"]
                    if (
                        not isinstance(source, dict)
                        or set(source)
                        != {"project_source_id", "workspace_id", "created_at"}
                        or source.get("project_source_id") != source_id
                        or source.get("workspace_id") != self.project_id
                        or not isinstance(revisions, list)
                        or not revisions
                    ):
                        if missing_is_not_found:
                            raise SavedQueryNotFoundError(
                                "Project source not found"
                            )
                        raise SavedQueryConflictError(
                            "Stored saved-query source ownership is invalid"
                        )
        except (SavedQueryNotFoundError, SavedQueryConflictError):
            raise
        except SchemaCompatibilityError as exc:
            raise SavedQueryConflictError(
                "Evidence storage schema is unsupported"
            ) from exc
        except (
            WorkspaceLockError,
            OSError,
            sqlite3.Error,
            TypeError,
            ValueError,
            KeyError,
        ) as exc:
            raise SavedQueryConflictError(
                "Evidence storage is unavailable or invalid"
            ) from exc

    def _require_exact_request(
        self,
        stored: _StoredSavedQuery,
        *,
        saved_query_id: str,
        researcher_id: str,
        title: str,
        definition: SavedQueryDefinition,
        request_sha256: str,
    ) -> None:
        record = stored.record
        if (
            record.saved_query_id != saved_query_id
            or record.project_id != self.project_id
            or record.title != title
            or record.definition != definition
            or record.created_by != researcher_id
            or stored.request_sha256 != request_sha256
        ):
            raise SavedQueryConflictError(
                "Saved-query identity conflicts with stored state"
            )

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        try:
            with self.database.read() as connection:
                connection.row_factory = sqlite3.Row
                yield connection
        except FileNotFoundError as exc:
            raise SavedQueryNotFoundError(
                "Qualitative project not found"
            ) from exc
        except (
            QualitativeDatabaseConflict,
            SchemaCompatibilityError,
            StudyBatchOperationConflict,
        ) as exc:
            raise SavedQueryConflictError(
                "Saved-query storage is unavailable or corrupt"
            ) from exc
        except sqlite3.DatabaseError as exc:
            raise SavedQueryConflictError(
                "Saved-query storage is unavailable or corrupt"
            ) from exc

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        try:
            with self.database.transaction() as connection:
                connection.row_factory = sqlite3.Row
                yield connection
        except FileNotFoundError as exc:
            raise SavedQueryNotFoundError(
                "Qualitative project not found"
            ) from exc
        except sqlite3.IntegrityError as exc:
            raise SavedQueryConflictError(
                "Saved-query storage constraint conflict"
            ) from exc
        except (
            QualitativeDatabaseConflict,
            SchemaCompatibilityError,
            StudyBatchOperationConflict,
        ) as exc:
            raise SavedQueryConflictError(
                "Saved-query storage is unavailable or corrupt"
            ) from exc
        except sqlite3.DatabaseError as exc:
            raise SavedQueryConflictError(
                "Saved-query storage is unavailable or corrupt"
            ) from exc


def _input_saved_query_id(value: object) -> str:
    if type(value) is not str or not _SAVED_QUERY_ID.fullmatch(value):
        raise SavedQueryValidationError("saved_query_id is invalid")
    return value


def _input_entity_id(value: object, field_name: str) -> str:
    if type(value) is not str or not _ENTITY_ID.fullmatch(value):
        raise SavedQueryValidationError(f"{field_name} is invalid")
    return value


def _input_external_id(value: object) -> str:
    if type(value) is not str:
        raise SavedQueryValidationError("project_source_id is invalid")
    _require_valid_unicode(value, "project_source_id", input_value=True)
    try:
        byte_length = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise SavedQueryValidationError("project_source_id is invalid") from exc
    if (
        not value
        or value != value.strip()
        or len(value) > _MAX_EXTERNAL_ID_LENGTH
        or byte_length > _MAX_EXTERNAL_ID_BYTES
    ):
        raise SavedQueryValidationError("project_source_id is invalid")
    return value


def _input_title(value: object) -> str:
    if type(value) is not str:
        raise SavedQueryValidationError("title is invalid")
    _require_valid_unicode(value, "title", input_value=True)
    try:
        byte_length = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise SavedQueryValidationError("title is invalid") from exc
    if (
        not value
        or value != value.strip()
        or len(value) > _MAX_TITLE_LENGTH
        or byte_length > _MAX_TITLE_BYTES
    ):
        raise SavedQueryValidationError("title is invalid")
    return value


def _input_definition(value: object) -> SavedQueryDefinition:
    if type(value) is not dict or set(value) != {"kind", "version", "filters"}:
        raise SavedQueryValidationError("definition is invalid")
    if value["kind"] != _QUERY_KIND or type(value["kind"]) is not str:
        raise SavedQueryValidationError("definition kind is invalid")
    if type(value["version"]) is not int or value["version"] != 1:
        raise SavedQueryValidationError("definition version is invalid")
    filters_value = value["filters"]
    if type(filters_value) is not dict or set(filters_value) != _FILTER_KEYS:
        raise SavedQueryValidationError("definition filters are invalid")
    project_source_id = filters_value["project_source_id"]
    codebook_version_id = filters_value["codebook_version_id"]
    code_id = filters_value["code_id"]
    created_by = filters_value["created_by"]
    include_removed = filters_value["include_removed"]
    return SavedQueryDefinition(
        kind=_QUERY_KIND,
        version=1,
        filters=SavedQueryFilters(
            project_source_id=(
                None
                if project_source_id is None
                else _input_external_id(project_source_id)
            ),
            codebook_version_id=(
                None
                if codebook_version_id is None
                else _input_entity_id(
                    codebook_version_id,
                    "codebook_version_id",
                )
            ),
            code_id=(
                None
                if code_id is None
                else _input_entity_id(code_id, "code_id")
            ),
            created_by=(
                None
                if created_by is None
                else _input_entity_id(created_by, "created_by")
            ),
            include_removed=_input_boolean(include_removed, "include_removed"),
        ),
    )


def _input_boolean(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise SavedQueryValidationError(f"{field_name} must be a boolean")
    return value


def _input_limit(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_PAGE_SIZE:
        raise SavedQueryValidationError("limit must be between 1 and 50")
    return value


def _stored_saved_query_id(value: object) -> str:
    stored = _stored_text(value, "saved_query_id")
    if not _SAVED_QUERY_ID.fullmatch(stored):
        raise SavedQueryConflictError("Stored saved-query identity is invalid")
    return stored


def _stored_entity_id(value: object, field_name: str) -> str:
    stored = _stored_text(value, field_name)
    if not _ENTITY_ID.fullmatch(stored):
        raise SavedQueryConflictError(f"Stored {field_name} is invalid")
    return stored


def _stored_text(value: object, field_name: str) -> str:
    if type(value) is not str:
        raise SavedQueryConflictError(f"Stored {field_name} is invalid")
    _require_valid_unicode(value, field_name, input_value=False)
    return value


def _stored_title(value: object) -> str:
    stored = _stored_text(value, "title")
    try:
        byte_length = len(stored.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise SavedQueryConflictError("Stored title is invalid") from exc
    if (
        not stored
        or stored != stored.strip()
        or len(stored) > _MAX_TITLE_LENGTH
        or byte_length > _MAX_TITLE_BYTES
    ):
        raise SavedQueryConflictError("Stored title is invalid")
    return stored


def _stored_filters(value: object) -> SavedQueryFilters:
    stored = _stored_text(value, "filters_json")
    try:
        parsed = json.loads(
            stored,
            object_pairs_hook=_json_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        _DuplicateJSONKey,
        OverflowError,
        RecursionError,
        ValueError,
    ) as exc:
        raise SavedQueryConflictError(
            "Stored saved-query filters are invalid"
        ) from exc
    try:
        definition = _input_definition(
            {
                "kind": _QUERY_KIND,
                "version": 1,
                "filters": parsed,
            }
        )
    except SavedQueryValidationError as exc:
        raise SavedQueryConflictError(
            "Stored saved-query filters are invalid"
        ) from exc
    if _canonical_filters_json(definition.filters) != stored:
        raise SavedQueryConflictError(
            "Stored saved-query filters are noncanonical"
        )
    return definition.filters


def _stored_sha256(value: object, field_name: str) -> str:
    stored = _stored_text(value, field_name)
    if not _SHA256.fullmatch(stored):
        raise SavedQueryConflictError(f"Stored {field_name} is invalid")
    return stored


def _stored_timestamp(value: object, field_name: str) -> str:
    stored = _stored_text(value, field_name)
    if (
        not stored
        or len(stored) > _MAX_TIMESTAMP_LENGTH
        or stored != stored.strip()
    ):
        raise SavedQueryConflictError(f"Stored {field_name} is invalid")
    try:
        parsed = datetime.fromisoformat(stored)
    except ValueError as exc:
        raise SavedQueryConflictError(f"Stored {field_name} is invalid") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset() != UTC.utcoffset(parsed)
        or parsed.isoformat() != stored
    ):
        raise SavedQueryConflictError(f"Stored {field_name} is invalid")
    return stored


def _canonical_filters_json(filters: SavedQueryFilters) -> str:
    return _canonical_json(_filters_payload(filters), ensure_ascii=False)


def _filters_payload(filters: SavedQueryFilters) -> dict[str, object]:
    return {
        "project_source_id": filters.project_source_id,
        "codebook_version_id": filters.codebook_version_id,
        "code_id": filters.code_id,
        "created_by": filters.created_by,
        "include_removed": filters.include_removed,
    }


def _definition_payload(definition: SavedQueryDefinition) -> dict[str, object]:
    return {
        "kind": definition.kind,
        "version": definition.version,
        "filters": _filters_payload(definition.filters),
    }


def _request_sha256(
    *,
    project_id: str,
    saved_query_id: str,
    researcher_id: str,
    title: str,
    definition: SavedQueryDefinition,
) -> str:
    payload = {
        "definition": _definition_payload(definition),
        "project_id": project_id,
        "researcher_id": researcher_id,
        "saved_query_id": saved_query_id,
        "title": title,
    }
    canonical = _canonical_json(payload, ensure_ascii=False).encode("utf-8")
    return sha256(_REQUEST_DOMAIN + canonical).hexdigest()


def _canonical_json(value: object, *, ensure_ascii: bool = True) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=ensure_ascii,
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise SavedQueryConflictError("Canonical saved-query JSON is invalid") from exc


def _require_valid_unicode(
    value: str,
    field_name: str,
    *,
    input_value: bool,
) -> None:
    invalid = "\0" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    if invalid:
        if input_value:
            raise SavedQueryValidationError(f"{field_name} contains invalid Unicode")
        raise SavedQueryConflictError(
            f"Stored {field_name} contains invalid Unicode"
        )


def _source_ids(family: Sequence[_StoredSavedQuery]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            stored.record.definition.filters.project_source_id
            for stored in family
            if stored.record.definition.filters.project_source_id is not None
        )
    )


def _find_stored(
    family: Sequence[_StoredSavedQuery],
    saved_query_id: str,
) -> _StoredSavedQuery | None:
    return next(
        (
            stored
            for stored in family
            if stored.record.saved_query_id == saved_query_id
        ),
        None,
    )


def _normalized_marker(value: object) -> str | None:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(value, str):
        return None
    return value.strip().casefold()


def _has_marker_prefix(value: object, prefix: str) -> bool:
    if isinstance(value, str):
        return value.strip().casefold().startswith(prefix)
    if isinstance(value, bytes):
        return value.strip().lower().startswith(prefix.encode("ascii"))
    return False


class _DuplicateJSONKey(ValueError):
    pass


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJSONKey(key)
        value[key] = item
    return value


def _encode_cursor(
    *,
    project_id: str,
    filters: dict[str, object],
    created_at: str,
    saved_query_id: str,
) -> str:
    payload = {
        "anchor": {
            "created_at": created_at,
            "saved_query_id": saved_query_id,
        },
        "endpoint": "saved_queries",
        "filters": filters,
        "project_id": project_id,
        "version": 1,
    }
    encoded = _canonical_json(payload, ensure_ascii=False).encode("utf-8")
    if len(encoded) > _MAX_CURSOR_DECODED_BYTES:
        raise SavedQueryConflictError("Saved-query cursor exceeds capacity")
    cursor = base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")
    if len(cursor) > 4096:
        raise SavedQueryConflictError("Saved-query cursor exceeds capacity")
    return cursor


def _decode_cursor(
    value: object,
    *,
    project_id: str,
    filters: dict[str, object],
) -> tuple[str, str]:
    if type(value) is not str or not _CURSOR.fullmatch(value):
        raise SavedQueryValidationError("cursor is invalid")
    try:
        padding = "=" * ((4 - len(value) % 4) % 4)
        decoded = base64.b64decode(
            (value + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        if len(decoded) > _MAX_CURSOR_DECODED_BYTES:
            raise ValueError("cursor decoded size is invalid")
        payload = json.loads(
            decoded.decode("utf-8"),
            object_pairs_hook=_json_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
        _DuplicateJSONKey,
        OverflowError,
        RecursionError,
        ValueError,
    ) as exc:
        raise SavedQueryValidationError("cursor is invalid") from exc
    if type(payload) is not dict or set(payload) != {
        "anchor",
        "endpoint",
        "filters",
        "project_id",
        "version",
    }:
        raise SavedQueryValidationError("cursor is invalid")
    if (
        type(payload["version"]) is not int
        or payload["version"] != 1
        or type(payload["project_id"]) is not str
        or payload["project_id"] != project_id
        or type(payload["endpoint"]) is not str
        or payload["endpoint"] != "saved_queries"
        or type(payload["filters"]) is not dict
        or payload["filters"] != filters
    ):
        raise SavedQueryValidationError("cursor is invalid")
    anchor = payload["anchor"]
    if type(anchor) is not dict or set(anchor) != {
        "created_at",
        "saved_query_id",
    }:
        raise SavedQueryValidationError("cursor is invalid")
    created_at = _input_cursor_timestamp(anchor["created_at"])
    saved_query_id = _input_saved_query_id(anchor["saved_query_id"])
    canonical = _encode_cursor(
        project_id=project_id,
        filters=filters,
        created_at=created_at,
        saved_query_id=saved_query_id,
    )
    if canonical != value:
        raise SavedQueryValidationError("cursor is invalid")
    return created_at, saved_query_id


def _input_cursor_timestamp(value: object) -> str:
    if type(value) is not str:
        raise SavedQueryValidationError("cursor is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SavedQueryValidationError("cursor is invalid") from exc
    if (
        not value
        or len(value) > _MAX_TIMESTAMP_LENGTH
        or value != value.strip()
        or parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset() != UTC.utcoffset(parsed)
        or parsed.isoformat() != value
    ):
        raise SavedQueryValidationError("cursor is invalid")
    return value


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "SavedQueryConflictError",
    "SavedQueryDefinition",
    "SavedQueryFilters",
    "SavedQueryNotFoundError",
    "SavedQueryPage",
    "SavedQueryRecord",
    "SavedQueryService",
    "SavedQueryValidationError",
]
