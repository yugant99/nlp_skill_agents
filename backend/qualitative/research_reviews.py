from __future__ import annotations

import base64
import binascii
import json
import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from backend.qualitative.coding_references import CodingReferenceRecord
from backend.qualitative.database import (
    QualitativeDatabaseConflict,
    QualitativeProjectDatabase,
    new_qualitative_id,
)
from backend.storage.evidence_target_registry import (
    EvidenceTargetConflictError,
    EvidenceTargetNotFoundError,
    EvidenceTargetRegistry,
)
from backend.storage.sqlite_migrations import SchemaCompatibilityError
from backend.storage.study_batch_operation_store import StudyBatchOperationConflict
from backend.storage.workspace_lock import WorkspaceLockError, workspace_mutation_lock


_ENTITY_ID = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_AGENT_SUGGESTION_ID = re.compile(r"^ags_[0-9a-f]{32}$")
_REVIEWER_DECISION_ID = re.compile(r"^rvd_[0-9a-f]{32}$")
_CODING_REFERENCE_ID = re.compile(r"^cdr_[0-9a-f]{32}$")
_AUDIT_EVENT_ID = re.compile(r"^qae_[0-9a-f]{32}$")
_EVIDENCE_IDS = {
    "transcript_revision_id": re.compile(r"^trv_[0-9a-f]{32}$"),
    "evidence_set_id": re.compile(r"^evs_[0-9a-f]{32}$"),
    "passage_id": re.compile(r"^psg_[0-9a-f]{32}$"),
    "cunit_id": re.compile(r"^cun_[0-9a-f]{32}$"),
}
_ORIGIN_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_CURSOR = re.compile(r"^[A-Za-z0-9_-]{1,4096}$")
_ROLES = {"researcher", "reviewer", "administrator"}
_ORIGIN_KINDS = {"synthetic_fixture", "imported_agent_output"}
_TARGET_KINDS = {"passage", "cunit"}
_DECISIONS = {"accepted", "edited", "rejected", "deferred"}
_TERMINAL_DECISIONS = {"accepted", "edited", "rejected"}
_PROVENANCE = {"bootstrap", "registered", "legacy_unverified"}
_MAX_EXTERNAL_ID_LENGTH = 256
_MAX_DISPLAY_NAME_LENGTH = 256
_MAX_TIMESTAMP_LENGTH = 64
_MAX_PAGE_SIZE = 50


class ReviewNotFoundError(LookupError):
    pass


class ReviewValidationError(ValueError):
    pass


class ReviewConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResearcherRecord:
    project_id: str
    researcher_id: str
    display_name: str
    role: str
    active: bool
    created_at: str
    updated_at: str
    provenance_classification: str
    provenance_actor_id: str | None


@dataclass(frozen=True)
class AgentSuggestionRecord:
    agent_suggestion_id: str
    project_id: str
    origin_kind: str
    origin_id: str
    origin_suggestion_key: str
    project_source_id: str
    transcript_revision_id: str
    evidence_set_id: str
    target_kind: str
    passage_id: str
    cunit_id: str
    start_offset: int
    end_offset: int
    codebook_version_id: str
    code_id: str
    created_by: str
    created_at: str


@dataclass(frozen=True)
class ReviewerDecisionRecord:
    reviewer_decision_id: str
    project_id: str
    agent_suggestion_id: str
    decision_number: int
    decision: str
    coding_reference_id: str | None
    reviewed_by: str
    created_at: str


@dataclass(frozen=True)
class AgentSuggestionSnapshot:
    suggestion: AgentSuggestionRecord
    current_decision: ReviewerDecisionRecord | None
    review_status: str


@dataclass(frozen=True)
class ResearcherPage:
    researchers: tuple[ResearcherRecord, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class AgentSuggestionPage:
    agent_suggestions: tuple[AgentSuggestionSnapshot, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class ReviewerDecisionPage:
    reviewer_decisions: tuple[ReviewerDecisionRecord, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class _ExternalTarget:
    project_source_id: str
    transcript_revision_id: str
    evidence_set_id: str
    target_kind: str
    passage_id: str
    cunit_id: str
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class _LocalSuggestionState:
    suggestion: AgentSuggestionRecord
    decisions: tuple[ReviewerDecisionRecord, ...]
    coding_references: tuple[CodingReferenceRecord, ...]

    @property
    def snapshot(self) -> AgentSuggestionSnapshot:
        current = self.decisions[-1] if self.decisions else None
        return AgentSuggestionSnapshot(
            suggestion=self.suggestion,
            current_decision=current,
            review_status="unreviewed" if current is None else current.decision,
        )


@dataclass(frozen=True)
class _ResearcherStorage:
    project_id: str
    researcher_id: str
    display_name: str
    role: str
    active: bool
    created_at: str
    updated_at: str


class ResearchReviewService:
    def __init__(self, root: Path | str, project_id: str) -> None:
        try:
            self.database = QualitativeProjectDatabase(root, project_id)
        except (TypeError, ValueError) as exc:
            raise ReviewValidationError("Invalid qualitative project id") from exc
        self.root = Path(root)
        self.project_id = self.database.project_id

    def create_researcher(
        self,
        *,
        researcher_id: str,
        actor_id: str,
        display_name: str,
        role: str,
    ) -> ResearcherRecord:
        target_id = _input_entity_id(researcher_id, "researcher_id")
        normalized_actor = _input_entity_id(actor_id, "actor_id")
        normalized_name = _input_display_name(display_name)
        normalized_role = _input_role(role)

        with self._write() as connection:
            self._require_project(connection)
            existing_row = self._researcher_row(connection, target_id)
            if existing_row is not None:
                existing = self._researcher_record_with_provenance(
                    connection,
                    existing_row,
                )
                if self._researcher_replay_matches(
                    existing,
                    actor_id=normalized_actor,
                    display_name=normalized_name,
                    role=normalized_role,
                ):
                    return existing
                raise ReviewConflictError(
                    "Researcher registration conflicts with stored state"
                )

            self._require_active_researcher(connection, normalized_actor)
            now = _utc_now()
            connection.execute(
                """
                insert into researchers (
                  researcher_id, project_id, display_name, role,
                  active, created_at, updated_at
                ) values (?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    target_id,
                    self.project_id,
                    normalized_name,
                    normalized_role,
                    now,
                    now,
                ),
            )
            self._append_audit(
                connection,
                actor_id=normalized_actor,
                event_type="qualitative.researcher.created",
                subject_type="researcher",
                subject_id=target_id,
                metadata={"role": normalized_role},
                created_at=now,
            )
            stored_row = self._researcher_row(connection, target_id)
            if stored_row is None:
                raise ReviewConflictError("Researcher registration was not stored")
            return self._researcher_record_with_provenance(connection, stored_row)

    def read_researcher(self, researcher_id: str) -> ResearcherRecord:
        normalized_id = _input_entity_id(researcher_id, "researcher_id")
        with self._read() as connection:
            self._require_project(connection)
            return self._require_researcher(connection, normalized_id)

    def list_researchers(
        self,
        *,
        role: str | None = None,
        active: bool | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ResearcherPage:
        normalized_role = None if role is None else _input_role(role)
        if active is not None and type(active) is not bool:
            raise ReviewValidationError("active must be a boolean")
        normalized_limit = _input_limit(limit)
        cursor_filters = {"active": active, "role": normalized_role}
        after = (
            None
            if cursor is None
            else _decode_cursor(
                cursor,
                project_id=self.project_id,
                resource="researchers",
                filters=cursor_filters,
            )
        )

        filters = ["project_id = ?"]
        parameters: list[object] = [self.project_id]
        if normalized_role is not None:
            filters.append("role = ?")
            parameters.append(normalized_role)
        if active is not None:
            filters.append("active = ?")
            parameters.append(1 if active else 0)

        with self._read() as connection:
            self._require_project(connection)
            if after is not None:
                anchor = self._require_researcher(connection, after[1])
                if (
                    anchor.created_at != after[0]
                    or (
                        normalized_role is not None
                        and anchor.role != normalized_role
                    )
                    or (active is not None and anchor.active != active)
                ):
                    raise ReviewNotFoundError("Researcher cursor anchor not found")
                filters.append(
                    "(created_at > ? or (created_at = ? and researcher_id > ?))"
                )
                parameters.extend((after[0], after[0], after[1]))
            rows = connection.execute(
                "select * from researchers where "
                + " and ".join(filters)
                + " order by created_at, researcher_id limit ?",
                (*parameters, normalized_limit + 1),
            ).fetchall()
            records = tuple(
                self._researcher_record_with_provenance(connection, row)
                for row in rows
            )

        has_more = len(records) > normalized_limit
        page = records[:normalized_limit]
        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = _encode_cursor(
                project_id=self.project_id,
                resource="researchers",
                filters=cursor_filters,
                after={"created_at": last.created_at, "id": last.researcher_id},
            )
        return ResearcherPage(researchers=page, next_cursor=next_cursor)

    def create_agent_suggestion(
        self,
        *,
        agent_suggestion_id: str,
        origin_kind: str,
        origin_id: str,
        origin_suggestion_key: str,
        researcher_id: str,
        project_source_id: str,
        transcript_revision_id: str,
        evidence_set_id: str,
        target_kind: str,
        passage_id: str,
        cunit_id: str,
        start_offset: int,
        end_offset: int,
        codebook_version_id: str,
        code_id: str,
    ) -> AgentSuggestionSnapshot:
        request = _normalize_suggestion_request(
            agent_suggestion_id=agent_suggestion_id,
            origin_kind=origin_kind,
            origin_id=origin_id,
            origin_suggestion_key=origin_suggestion_key,
            researcher_id=researcher_id,
            project_source_id=project_source_id,
            transcript_revision_id=transcript_revision_id,
            evidence_set_id=evidence_set_id,
            target_kind=target_kind,
            passage_id=passage_id,
            cunit_id=cunit_id,
            start_offset=start_offset,
            end_offset=end_offset,
            codebook_version_id=codebook_version_id,
            code_id=code_id,
        )

        initial_state: _LocalSuggestionState | None = None
        with self._read() as connection:
            self._require_project(connection)
            existing = self._suggestion_by_identities(connection, request)
            if existing is not None:
                self._assert_suggestion_request(existing, request)
                initial_state = self._load_suggestion_state(connection, existing)

        self._validate_external_targets(
            (
                (_target_from_request(request),)
                if initial_state is None
                else _external_targets_for_state(initial_state)
            ),
            missing_is_not_found=initial_state is None,
        )

        with self._write() as connection:
            self._require_project(connection)
            existing = self._suggestion_by_identities(connection, request)
            if existing is not None:
                self._assert_suggestion_request(existing, request)
                current = self._load_suggestion_state(connection, existing)
                if initial_state is not None and current != initial_state:
                    raise ReviewConflictError(
                        "Suggestion state changed during external preflight"
                    )
                if initial_state is None and current.decisions:
                    raise ReviewConflictError(
                        "Suggestion state changed during external preflight"
                    )
                return current.snapshot

            self._require_active_researcher(connection, request["created_by"])
            self._require_frozen_code(
                connection,
                codebook_version_id=request["codebook_version_id"],
                code_id=request["code_id"],
                missing_is_not_found=True,
            )
            now = _utc_now()
            connection.execute(
                """
                insert into agent_coding_suggestions (
                  agent_suggestion_id, project_id, origin_kind, origin_id,
                  origin_suggestion_key, project_source_id,
                  transcript_revision_id, evidence_set_id, target_kind,
                  passage_id, cunit_id, start_offset, end_offset,
                  codebook_version_id, code_id, created_by, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request["agent_suggestion_id"],
                    self.project_id,
                    request["origin_kind"],
                    request["origin_id"],
                    request["origin_suggestion_key"],
                    request["project_source_id"],
                    request["transcript_revision_id"],
                    request["evidence_set_id"],
                    request["target_kind"],
                    request["passage_id"],
                    request["cunit_id"],
                    request["start_offset"],
                    request["end_offset"],
                    request["codebook_version_id"],
                    request["code_id"],
                    request["created_by"],
                    now,
                ),
            )
            self._append_audit(
                connection,
                actor_id=request["created_by"],
                event_type="qualitative.agent_suggestion.created",
                subject_type="agent_suggestion",
                subject_id=request["agent_suggestion_id"],
                metadata={"origin_kind": request["origin_kind"]},
                created_at=now,
            )
            stored = self._require_suggestion(
                connection,
                request["agent_suggestion_id"],
            )
            return self._load_suggestion_state(connection, stored).snapshot

    def read_agent_suggestion(
        self,
        agent_suggestion_id: str,
    ) -> AgentSuggestionSnapshot:
        normalized_id = _input_agent_suggestion_id(agent_suggestion_id)
        with self._read() as connection:
            self._require_project(connection)
            suggestion = self._require_suggestion(connection, normalized_id)
            state = self._load_suggestion_state(connection, suggestion)
        self._validate_external_targets(
            _external_targets_for_state(state),
            missing_is_not_found=False,
        )
        return state.snapshot

    def list_agent_suggestions(
        self,
        *,
        project_source_id: str | None = None,
        codebook_version_id: str | None = None,
        code_id: str | None = None,
        created_by: str | None = None,
        origin_kind: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> AgentSuggestionPage:
        normalized_source = (
            None
            if project_source_id is None
            else _input_external_id(project_source_id)
        )
        normalized_version = (
            None
            if codebook_version_id is None
            else _input_entity_id(codebook_version_id, "codebook_version_id")
        )
        normalized_code = (
            None if code_id is None else _input_entity_id(code_id, "code_id")
        )
        normalized_creator = (
            None
            if created_by is None
            else _input_entity_id(created_by, "created_by")
        )
        normalized_origin = (
            None if origin_kind is None else _input_origin_kind(origin_kind)
        )
        normalized_limit = _input_limit(limit)
        cursor_filters = {
            "code_id": normalized_code,
            "codebook_version_id": normalized_version,
            "created_by": normalized_creator,
            "origin_kind": normalized_origin,
            "project_source_id": normalized_source,
        }
        after = (
            None
            if cursor is None
            else _decode_cursor(
                cursor,
                project_id=self.project_id,
                resource="agent_suggestions",
                filters=cursor_filters,
            )
        )

        filters = ["project_id = ?"]
        parameters: list[object] = [self.project_id]
        for column, value in (
            ("project_source_id", normalized_source),
            ("codebook_version_id", normalized_version),
            ("code_id", normalized_code),
            ("created_by", normalized_creator),
            ("origin_kind", normalized_origin),
        ):
            if value is not None:
                filters.append(f"{column} = ?")
                parameters.append(value)

        with self._read() as connection:
            self._require_project(connection)
            if after is not None:
                anchor = self._require_suggestion(connection, after[1])
                self._load_suggestion_state(connection, anchor)
                if anchor.created_at != after[0] or not _suggestion_matches_filters(
                    anchor,
                    project_source_id=normalized_source,
                    codebook_version_id=normalized_version,
                    code_id=normalized_code,
                    created_by=normalized_creator,
                    origin_kind=normalized_origin,
                ):
                    raise ReviewNotFoundError("Suggestion cursor anchor not found")
                filters.append(
                    "(created_at > ? or "
                    "(created_at = ? and agent_suggestion_id > ?))"
                )
                parameters.extend((after[0], after[0], after[1]))
            rows = connection.execute(
                "select * from agent_coding_suggestions where "
                + " and ".join(filters)
                + " order by created_at, agent_suggestion_id limit ?",
                (*parameters, normalized_limit + 1),
            ).fetchall()
            states = tuple(
                self._load_suggestion_state(
                    connection,
                    self._suggestion_record(row),
                )
                for row in rows
            )

        page_states = states[:normalized_limit]
        targets: list[_ExternalTarget] = []
        for state in page_states:
            targets.extend(_external_targets_for_state(state))
        self._validate_external_targets(targets, missing_is_not_found=False)

        snapshots = tuple(state.snapshot for state in page_states)
        next_cursor = None
        if len(states) > normalized_limit and snapshots:
            last = snapshots[-1].suggestion
            next_cursor = _encode_cursor(
                project_id=self.project_id,
                resource="agent_suggestions",
                filters=cursor_filters,
                after={"created_at": last.created_at, "id": last.agent_suggestion_id},
            )
        return AgentSuggestionPage(
            agent_suggestions=snapshots,
            next_cursor=next_cursor,
        )

    def append_reviewer_decision(
        self,
        *,
        reviewer_decision_id: str,
        agent_suggestion_id: str,
        researcher_id: str,
        expected_decision_number: int,
        decision: str,
        coding_reference_id: str | None = None,
    ) -> ReviewerDecisionRecord:
        decision_id = _input_reviewer_decision_id(reviewer_decision_id)
        suggestion_id = _input_agent_suggestion_id(agent_suggestion_id)
        reviewer_id = _input_entity_id(researcher_id, "researcher_id")
        expected = _input_expected_decision_number(expected_decision_number)
        normalized_decision = _input_decision(decision)
        normalized_reference_id = _input_decision_reference(
            normalized_decision,
            coding_reference_id,
        )

        with self._read() as connection:
            self._require_project(connection)
            existing_by_id = self._decision_row(connection, decision_id)
            if existing_by_id is not None:
                existing_record = self._decision_record(existing_by_id)
                if existing_record.agent_suggestion_id != suggestion_id:
                    raise ReviewConflictError(
                        "Reviewer decision identity is already in use"
                    )
            suggestion = self._require_suggestion(connection, suggestion_id)
            initial = self._load_suggestion_state(connection, suggestion)
            self._require_researcher(connection, reviewer_id)
            result = self._request_result_reference(
                connection,
                coding_reference_id=normalized_reference_id,
            )
            mode = self._decision_mode(
                initial,
                reviewer_decision_id=decision_id,
                reviewer_id=reviewer_id,
                expected_decision_number=expected,
                decision=normalized_decision,
                coding_reference_id=normalized_reference_id,
            )
            self._validate_decision_candidate(
                suggestion=initial.suggestion,
                reviewer_id=reviewer_id,
                decision=normalized_decision,
                result=result,
                decision_created_at=(
                    initial.decisions[-1].created_at if mode == "retry" else None
                ),
                require_active_result=mode == "new",
            )

        preflight_targets = list(_external_targets_for_state(initial))
        if result is not None:
            preflight_targets.append(_target_from_coding_reference(result))
        self._validate_external_targets(
            preflight_targets,
            missing_is_not_found=False,
        )

        with self._write() as connection:
            self._require_project(connection)
            suggestion = self._require_suggestion(connection, suggestion_id)
            current = self._load_suggestion_state(connection, suggestion)
            if current.suggestion != initial.suggestion:
                raise ReviewConflictError(
                    "Suggestion changed during reviewer decision preflight"
                )
            if current.decisions[: len(initial.decisions)] != initial.decisions:
                raise ReviewConflictError(
                    "Decision history changed during reviewer decision preflight"
                )
            current_mode = self._decision_mode(
                current,
                reviewer_decision_id=decision_id,
                reviewer_id=reviewer_id,
                expected_decision_number=expected,
                decision=normalized_decision,
                coding_reference_id=normalized_reference_id,
            )
            current_result = self._request_result_reference(
                connection,
                coding_reference_id=normalized_reference_id,
            )
            if current_mode == "retry":
                self._validate_decision_candidate(
                    suggestion=current.suggestion,
                    reviewer_id=reviewer_id,
                    decision=normalized_decision,
                    result=current_result,
                    decision_created_at=current.decisions[-1].created_at,
                    require_active_result=False,
                )
                return current.decisions[-1]

            self._require_active_researcher(connection, reviewer_id)
            now = _utc_now()
            self._validate_decision_candidate(
                suggestion=current.suggestion,
                reviewer_id=reviewer_id,
                decision=normalized_decision,
                result=current_result,
                decision_created_at=now,
                require_active_result=True,
            )
            decision_number = expected + 1
            connection.execute(
                """
                insert into reviewer_decisions (
                  reviewer_decision_id, project_id, agent_suggestion_id,
                  decision_number, decision, coding_reference_id,
                  reviewed_by, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    self.project_id,
                    suggestion_id,
                    decision_number,
                    normalized_decision,
                    normalized_reference_id,
                    reviewer_id,
                    now,
                ),
            )
            self._append_audit(
                connection,
                actor_id=reviewer_id,
                event_type="qualitative.reviewer_decision.created",
                subject_type="reviewer_decision",
                subject_id=decision_id,
                metadata={
                    "agent_suggestion_id": suggestion_id,
                    "coding_reference_id": normalized_reference_id,
                    "decision": normalized_decision,
                    "decision_number": decision_number,
                },
                created_at=now,
            )
            final = self._load_suggestion_state(connection, suggestion)
            if not final.decisions or final.decisions[-1].reviewer_decision_id != decision_id:
                raise ReviewConflictError("Reviewer decision was not stored")
            return final.decisions[-1]

    def list_reviewer_decisions(
        self,
        agent_suggestion_id: str,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ReviewerDecisionPage:
        suggestion_id = _input_agent_suggestion_id(agent_suggestion_id)
        normalized_limit = _input_limit(limit)
        cursor_filters = {"agent_suggestion_id": suggestion_id}
        after = (
            None
            if cursor is None
            else _decode_cursor(
                cursor,
                project_id=self.project_id,
                resource="reviewer_decisions",
                filters=cursor_filters,
            )
        )
        with self._read() as connection:
            self._require_project(connection)
            suggestion = self._require_suggestion(connection, suggestion_id)
            state = self._load_suggestion_state(connection, suggestion)

        self._validate_external_targets(
            _external_targets_for_state(state),
            missing_is_not_found=False,
        )
        decisions = state.decisions
        start_index = 0
        if after is not None:
            anchor = next(
                (
                    item
                    for item in decisions
                    if item.reviewer_decision_id == after[1]
                    and item.decision_number == after[0]
                ),
                None,
            )
            if anchor is None:
                raise ReviewNotFoundError("Decision cursor anchor not found")
            start_index = decisions.index(anchor) + 1
        remaining = decisions[start_index:]
        page = remaining[:normalized_limit]
        next_cursor = None
        if len(remaining) > normalized_limit and page:
            last = page[-1]
            next_cursor = _encode_cursor(
                project_id=self.project_id,
                resource="reviewer_decisions",
                filters=cursor_filters,
                after={
                    "decision_number": last.decision_number,
                    "id": last.reviewer_decision_id,
                },
            )
        return ReviewerDecisionPage(
            reviewer_decisions=page,
            next_cursor=next_cursor,
        )

    def validate_project_state(self) -> None:
        external_targets: set[_ExternalTarget] = set()
        with self._read() as connection:
            self._require_project(connection)
            for row in connection.execute(
                """
                select * from researchers
                where project_id = ? order by created_at, researcher_id
                """,
                (self.project_id,),
            ):
                self._researcher_record_with_provenance(connection, row)

            for row in connection.execute(
                """
                select * from agent_coding_suggestions
                where project_id = ? order by created_at, agent_suggestion_id
                """,
                (self.project_id,),
            ):
                suggestion = self._suggestion_record(row)
                self._validate_suggestion_local(connection, suggestion)
                external_targets.add(_target_from_suggestion(suggestion))

            decision_cursor = connection.execute(
                """
                select * from reviewer_decisions
                where project_id = ?
                order by agent_suggestion_id, decision_number,
                         reviewer_decision_id
                """,
                (self.project_id,),
            )
            current_suggestion: AgentSuggestionRecord | None = None
            expected_number = 0
            previous_at: datetime | None = None
            terminal_seen = False
            for row in decision_cursor:
                decision_record = self._decision_record(row)
                if (
                    current_suggestion is None
                    or decision_record.agent_suggestion_id
                    != current_suggestion.agent_suggestion_id
                ):
                    current_suggestion = self._require_suggestion(
                        connection,
                        decision_record.agent_suggestion_id,
                    )
                    expected_number = 1
                    previous_at = _timestamp_instant(
                        current_suggestion.created_at
                    )
                    terminal_seen = False
                if (
                    decision_record.decision_number != expected_number
                    or terminal_seen
                    or previous_at is None
                    or _timestamp_instant(decision_record.created_at) < previous_at
                ):
                    raise ReviewConflictError("Stored decision chain is invalid")
                self._require_researcher(
                    connection,
                    decision_record.reviewed_by,
                )
                self._validate_decision_audit(connection, decision_record)
                result = self._request_result_reference(
                    connection,
                    coding_reference_id=decision_record.coding_reference_id,
                    missing_is_not_found=False,
                )
                self._validate_decision_candidate(
                    suggestion=current_suggestion,
                    reviewer_id=decision_record.reviewed_by,
                    decision=decision_record.decision,
                    result=result,
                    decision_created_at=decision_record.created_at,
                    require_active_result=False,
                )
                if result is not None:
                    external_targets.add(_target_from_coding_reference(result))
                expected_number += 1
                previous_at = _timestamp_instant(decision_record.created_at)
                terminal_seen = decision_record.decision in _TERMINAL_DECISIONS
            self._validate_unmatched_review_audits(connection)

        self._validate_external_targets(
            tuple(external_targets),
            missing_is_not_found=False,
        )

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        try:
            with self.database.read() as connection:
                connection.row_factory = sqlite3.Row
                yield connection
        except FileNotFoundError as exc:
            raise ReviewNotFoundError("Qualitative project not found") from exc
        except (
            QualitativeDatabaseConflict,
            SchemaCompatibilityError,
            StudyBatchOperationConflict,
        ) as exc:
            raise ReviewConflictError(
                "Qualitative review storage is unavailable or corrupt"
            ) from exc
        except sqlite3.DatabaseError as exc:
            raise ReviewConflictError(
                "Qualitative review storage is unavailable or corrupt"
            ) from exc

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        try:
            with self.database.transaction() as connection:
                connection.row_factory = sqlite3.Row
                yield connection
        except FileNotFoundError as exc:
            raise ReviewNotFoundError("Qualitative project not found") from exc
        except sqlite3.IntegrityError as exc:
            raise ReviewConflictError("Review storage constraint conflict") from exc
        except (
            QualitativeDatabaseConflict,
            SchemaCompatibilityError,
            StudyBatchOperationConflict,
        ) as exc:
            raise ReviewConflictError(
                "Qualitative review storage is unavailable or corrupt"
            ) from exc
        except sqlite3.DatabaseError as exc:
            raise ReviewConflictError(
                "Qualitative review storage is unavailable or corrupt"
            ) from exc

    def _require_project(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "select project_id from qualitative_projects where project_id = ?",
            (self.project_id,),
        ).fetchone()
        if row is None:
            raise ReviewNotFoundError("Qualitative project is not initialized")
        if _stored_text(row["project_id"], "project_id") != self.project_id:
            raise ReviewConflictError("Stored qualitative project is invalid")

    def _researcher_row(
        self,
        connection: sqlite3.Connection,
        researcher_id: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            select * from researchers
            where project_id = ? and researcher_id = ?
            """,
            (self.project_id, researcher_id),
        ).fetchone()

    def _researcher_storage(self, row: sqlite3.Row) -> _ResearcherStorage:
        project_id = _stored_text(row["project_id"], "researcher project_id")
        researcher_id = _stored_entity_id(
            row["researcher_id"],
            "researcher_id",
        )
        display_name = _stored_display_name(row["display_name"])
        role = _stored_role(row["role"])
        active_value = row["active"]
        if type(active_value) is not int or active_value not in (0, 1):
            raise ReviewConflictError("Stored researcher active state is invalid")
        created_at, created_instant = _stored_timestamp_with_instant(
            row["created_at"],
            "researcher created_at",
        )
        updated_at, updated_instant = _stored_timestamp_with_instant(
            row["updated_at"],
            "researcher updated_at",
        )
        if updated_instant < created_instant:
            raise ReviewConflictError("Stored researcher timestamps are invalid")
        if project_id != self.project_id:
            raise ReviewConflictError("Stored researcher belongs to another project")
        return _ResearcherStorage(
            project_id=project_id,
            researcher_id=researcher_id,
            display_name=display_name,
            role=role,
            active=bool(active_value),
            created_at=created_at,
            updated_at=updated_at,
        )

    def _researcher_record_with_provenance(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> ResearcherRecord:
        stored = self._researcher_storage(row)
        classification, actor_id = self._classify_researcher(connection, stored)
        return ResearcherRecord(
            project_id=stored.project_id,
            researcher_id=stored.researcher_id,
            display_name=stored.display_name,
            role=stored.role,
            active=stored.active,
            created_at=stored.created_at,
            updated_at=stored.updated_at,
            provenance_classification=classification,
            provenance_actor_id=actor_id,
        )

    def _require_researcher(
        self,
        connection: sqlite3.Connection,
        researcher_id: str,
    ) -> ResearcherRecord:
        row = self._researcher_row(connection, researcher_id)
        if row is None:
            raise ReviewNotFoundError("Researcher not found")
        record = self._researcher_record_with_provenance(connection, row)
        if record.researcher_id != researcher_id:
            raise ReviewConflictError("Stored researcher identity is invalid")
        return record

    def _require_active_researcher(
        self,
        connection: sqlite3.Connection,
        researcher_id: str,
    ) -> ResearcherRecord:
        record = self._require_researcher(connection, researcher_id)
        if not record.active:
            raise ReviewConflictError("Researcher is inactive")
        return record

    def _classify_researcher(
        self,
        connection: sqlite3.Connection,
        researcher: _ResearcherStorage,
    ) -> tuple[str, str | None]:
        bootstrap_rows: list[sqlite3.Row] = []
        registration_rows: list[sqlite3.Row] = []
        for row in connection.execute(
            "select * from qualitative_audit_events order by event_id"
        ):
            event_marker = _normalized_marker(row["event_type"])
            subject_marker = _normalized_marker(row["subject_type"])
            actor_marker = _normalized_marker(row["actor_id"])
            subject_id_marker = _normalized_marker(row["subject_id"])
            if (
                event_marker == "qualitative.project.initialized"
                and actor_marker == researcher.researcher_id
            ):
                bootstrap_rows.append(row)
            if (
                subject_id_marker == researcher.researcher_id
                and (
                    event_marker == "qualitative.researcher.created"
                    or subject_marker == "researcher"
                    or _has_marker_prefix(row["event_type"], "qualitative.researcher.")
                    or _has_marker_prefix(row["subject_type"], "researcher")
                    or _has_marker_prefix(row["subject_id"], "res_")
                )
            ):
                registration_rows.append(row)

        if bootstrap_rows and registration_rows:
            raise ReviewConflictError("Researcher provenance is ambiguous")
        if len(bootstrap_rows) > 1 or len(registration_rows) > 1:
            raise ReviewConflictError("Researcher provenance is ambiguous")
        if bootstrap_rows:
            self._validate_bootstrap_audit(bootstrap_rows[0], researcher)
            return "bootstrap", researcher.researcher_id
        if registration_rows:
            actor_id = self._validate_registration_audit(
                connection,
                registration_rows[0],
                researcher,
            )
            return "registered", actor_id
        return "legacy_unverified", None

    def _validate_bootstrap_audit(
        self,
        row: sqlite3.Row,
        researcher: _ResearcherStorage,
    ) -> None:
        expected_id = _bootstrap_event_id(self.project_id, researcher.researcher_id)
        if (
            _stored_text(row["event_id"], "audit event_id") != expected_id
            or _stored_text(row["project_id"], "audit project_id")
            != self.project_id
            or _stored_text(row["actor_id"], "audit actor_id")
            != researcher.researcher_id
            or _stored_text(row["event_type"], "audit event_type")
            != "qualitative.project.initialized"
            or _stored_text(row["subject_type"], "audit subject_type") != "project"
            or _stored_text(row["subject_id"], "audit subject_id")
            != self.project_id
            or _stored_text(row["metadata_json"], "audit metadata_json") != "{}"
            or _stored_timestamp(row["created_at"], "audit created_at")
            != researcher.created_at
            or researcher.updated_at != researcher.created_at
        ):
            raise ReviewConflictError("Bootstrap researcher audit is invalid")

    def _validate_registration_audit(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        researcher: _ResearcherStorage,
    ) -> str:
        event_id = _stored_text(row["event_id"], "audit event_id")
        actor_id = _stored_entity_id(row["actor_id"], "audit actor_id")
        expected_metadata = _canonical_json({"role": researcher.role})
        if (
            not _AUDIT_EVENT_ID.fullmatch(event_id)
            or _stored_text(row["project_id"], "audit project_id")
            != self.project_id
            or _stored_text(row["event_type"], "audit event_type")
            != "qualitative.researcher.created"
            or _stored_text(row["subject_type"], "audit subject_type")
            != "researcher"
            or _stored_text(row["subject_id"], "audit subject_id")
            != researcher.researcher_id
            or _stored_text(row["metadata_json"], "audit metadata_json")
            != expected_metadata
            or _stored_timestamp(row["created_at"], "audit created_at")
            != researcher.created_at
        ):
            raise ReviewConflictError("Researcher registration audit is invalid")
        actor_row = self._researcher_row(connection, actor_id)
        if actor_row is None:
            raise ReviewConflictError("Researcher registration actor is unavailable")
        self._researcher_storage(actor_row)
        return actor_id

    def _researcher_replay_matches(
        self,
        researcher: ResearcherRecord,
        *,
        actor_id: str,
        display_name: str,
        role: str,
    ) -> bool:
        common = (
            researcher.display_name == display_name
            and researcher.role == role
            and researcher.active
            and researcher.created_at == researcher.updated_at
        )
        if researcher.provenance_classification == "registered":
            return common and researcher.provenance_actor_id == actor_id
        if researcher.provenance_classification == "bootstrap":
            return (
                common
                and actor_id == researcher.researcher_id
                and researcher.provenance_actor_id == researcher.researcher_id
            )
        if researcher.provenance_classification == "legacy_unverified":
            return False
        raise ReviewConflictError("Stored researcher provenance is invalid")

    def _suggestion_by_identities(
        self,
        connection: sqlite3.Connection,
        request: dict[str, object],
    ) -> AgentSuggestionRecord | None:
        id_row = connection.execute(
            """
            select * from agent_coding_suggestions
            where project_id = ? and agent_suggestion_id = ?
            """,
            (self.project_id, request["agent_suggestion_id"]),
        ).fetchone()
        origin_row = connection.execute(
            """
            select * from agent_coding_suggestions
            where project_id = ? and origin_kind = ? and origin_id = ?
              and origin_suggestion_key = ?
            """,
            (
                self.project_id,
                request["origin_kind"],
                request["origin_id"],
                request["origin_suggestion_key"],
            ),
        ).fetchone()
        id_record = None if id_row is None else self._suggestion_record(id_row)
        origin_record = (
            None if origin_row is None else self._suggestion_record(origin_row)
        )
        if (
            id_record is not None
            and origin_record is not None
            and id_record != origin_record
        ):
            raise ReviewConflictError("Suggestion identities resolve differently")
        return id_record if id_record is not None else origin_record

    def _assert_suggestion_request(
        self,
        record: AgentSuggestionRecord,
        request: dict[str, object],
    ) -> None:
        expected = {
            "agent_suggestion_id": record.agent_suggestion_id,
            "origin_kind": record.origin_kind,
            "origin_id": record.origin_id,
            "origin_suggestion_key": record.origin_suggestion_key,
            "project_source_id": record.project_source_id,
            "transcript_revision_id": record.transcript_revision_id,
            "evidence_set_id": record.evidence_set_id,
            "target_kind": record.target_kind,
            "passage_id": record.passage_id,
            "cunit_id": record.cunit_id,
            "start_offset": record.start_offset,
            "end_offset": record.end_offset,
            "codebook_version_id": record.codebook_version_id,
            "code_id": record.code_id,
            "created_by": record.created_by,
        }
        if expected != request:
            raise ReviewConflictError("Suggestion identity is already in use")

    def _require_suggestion(
        self,
        connection: sqlite3.Connection,
        agent_suggestion_id: str,
    ) -> AgentSuggestionRecord:
        row = connection.execute(
            """
            select * from agent_coding_suggestions
            where project_id = ? and agent_suggestion_id = ?
            """,
            (self.project_id, agent_suggestion_id),
        ).fetchone()
        if row is None:
            raise ReviewNotFoundError("Agent suggestion not found")
        return self._suggestion_record(row)

    def _suggestion_record(self, row: sqlite3.Row) -> AgentSuggestionRecord:
        target_kind = _stored_target_kind(row["target_kind"])
        cunit_id = _stored_text(row["cunit_id"], "suggestion cunit_id")
        if target_kind == "passage":
            if cunit_id != "":
                raise ReviewConflictError("Stored suggestion target is invalid")
        else:
            cunit_id = _stored_evidence_id(cunit_id, "cunit_id")
        start_offset = _stored_integer(row["start_offset"], "start_offset")
        end_offset = _stored_integer(row["end_offset"], "end_offset")
        if start_offset < 0 or end_offset <= start_offset:
            raise ReviewConflictError("Stored suggestion offsets are invalid")
        record = AgentSuggestionRecord(
            agent_suggestion_id=_stored_agent_suggestion_id(
                row["agent_suggestion_id"]
            ),
            project_id=_stored_text(row["project_id"], "suggestion project_id"),
            origin_kind=_stored_origin_kind(row["origin_kind"]),
            origin_id=_stored_origin_id(row["origin_id"], "origin_id"),
            origin_suggestion_key=_stored_origin_id(
                row["origin_suggestion_key"],
                "origin_suggestion_key",
            ),
            project_source_id=_stored_external_id(row["project_source_id"]),
            transcript_revision_id=_stored_evidence_id(
                row["transcript_revision_id"],
                "transcript_revision_id",
            ),
            evidence_set_id=_stored_evidence_id(
                row["evidence_set_id"],
                "evidence_set_id",
            ),
            target_kind=target_kind,
            passage_id=_stored_evidence_id(row["passage_id"], "passage_id"),
            cunit_id=cunit_id,
            start_offset=start_offset,
            end_offset=end_offset,
            codebook_version_id=_stored_entity_id(
                row["codebook_version_id"],
                "codebook_version_id",
            ),
            code_id=_stored_entity_id(row["code_id"], "code_id"),
            created_by=_stored_entity_id(row["created_by"], "created_by"),
            created_at=_stored_timestamp(row["created_at"], "suggestion created_at"),
        )
        if record.project_id != self.project_id:
            raise ReviewConflictError("Stored suggestion belongs to another project")
        return record

    def _validate_suggestion_local(
        self,
        connection: sqlite3.Connection,
        suggestion: AgentSuggestionRecord,
    ) -> None:
        self._require_researcher(connection, suggestion.created_by)
        self._require_frozen_code(
            connection,
            codebook_version_id=suggestion.codebook_version_id,
            code_id=suggestion.code_id,
            missing_is_not_found=False,
        )
        candidates = tuple(
            self._audit_candidates_for_subject(
                connection,
                subject_id=suggestion.agent_suggestion_id,
            )
        )
        if len(candidates) != 1:
            raise ReviewConflictError("Suggestion audit history is invalid")
        row = candidates[0]
        expected_metadata = _canonical_json(
            {"origin_kind": suggestion.origin_kind}
        )
        if (
            not _AUDIT_EVENT_ID.fullmatch(
                _stored_text(row["event_id"], "audit event_id")
            )
            or _stored_text(row["project_id"], "audit project_id")
            != self.project_id
            or _stored_text(row["actor_id"], "audit actor_id")
            != suggestion.created_by
            or _stored_text(row["event_type"], "audit event_type")
            != "qualitative.agent_suggestion.created"
            or _stored_text(row["subject_type"], "audit subject_type")
            != "agent_suggestion"
            or _stored_text(row["subject_id"], "audit subject_id")
            != suggestion.agent_suggestion_id
            or _stored_text(row["metadata_json"], "audit metadata_json")
            != expected_metadata
            or _stored_timestamp(row["created_at"], "audit created_at")
            != suggestion.created_at
        ):
            raise ReviewConflictError("Suggestion audit history is invalid")

    def _load_suggestion_state(
        self,
        connection: sqlite3.Connection,
        suggestion: AgentSuggestionRecord,
    ) -> _LocalSuggestionState:
        self._validate_suggestion_local(connection, suggestion)
        decisions = tuple(
            self._decision_record(row)
            for row in connection.execute(
                """
                select * from reviewer_decisions
                where project_id = ? and agent_suggestion_id = ?
                order by decision_number, reviewer_decision_id
                """,
                (self.project_id, suggestion.agent_suggestion_id),
            )
        )
        coding_references = self._validate_decision_chain(
            connection,
            suggestion,
            decisions,
        )
        return _LocalSuggestionState(
            suggestion=suggestion,
            decisions=decisions,
            coding_references=coding_references,
        )

    def _decision_row(
        self,
        connection: sqlite3.Connection,
        reviewer_decision_id: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            select * from reviewer_decisions
            where project_id = ? and reviewer_decision_id = ?
            """,
            (self.project_id, reviewer_decision_id),
        ).fetchone()

    def _decision_record(self, row: sqlite3.Row) -> ReviewerDecisionRecord:
        coding_value = row["coding_reference_id"]
        coding_reference_id = (
            None
            if coding_value is None
            else _stored_coding_reference_id(coding_value)
        )
        decision_number = _stored_integer(
            row["decision_number"],
            "decision_number",
        )
        if decision_number <= 0:
            raise ReviewConflictError("Stored decision number is invalid")
        decision = _stored_decision(row["decision"])
        if (decision in {"accepted", "edited"}) != (
            coding_reference_id is not None
        ):
            raise ReviewConflictError("Stored decision result shape is invalid")
        record = ReviewerDecisionRecord(
            reviewer_decision_id=_stored_reviewer_decision_id(
                row["reviewer_decision_id"]
            ),
            project_id=_stored_text(row["project_id"], "decision project_id"),
            agent_suggestion_id=_stored_agent_suggestion_id(
                row["agent_suggestion_id"]
            ),
            decision_number=decision_number,
            decision=decision,
            coding_reference_id=coding_reference_id,
            reviewed_by=_stored_entity_id(row["reviewed_by"], "reviewed_by"),
            created_at=_stored_timestamp(row["created_at"], "decision created_at"),
        )
        if record.project_id != self.project_id:
            raise ReviewConflictError("Stored decision belongs to another project")
        return record

    def _validate_decision_chain(
        self,
        connection: sqlite3.Connection,
        suggestion: AgentSuggestionRecord,
        decisions: Sequence[ReviewerDecisionRecord],
    ) -> tuple[CodingReferenceRecord, ...]:
        result_records: list[CodingReferenceRecord] = []
        previous_at = _timestamp_instant(suggestion.created_at)
        terminal_seen = False
        for expected_number, decision in enumerate(decisions, start=1):
            if (
                decision.agent_suggestion_id != suggestion.agent_suggestion_id
                or decision.decision_number != expected_number
                or terminal_seen
            ):
                raise ReviewConflictError("Stored decision chain is invalid")
            created_instant = _timestamp_instant(decision.created_at)
            if created_instant < previous_at:
                raise ReviewConflictError("Stored decision chronology is invalid")
            previous_at = created_instant
            self._require_researcher(connection, decision.reviewed_by)
            self._validate_decision_audit(connection, decision)
            result = self._request_result_reference(
                connection,
                coding_reference_id=decision.coding_reference_id,
                missing_is_not_found=False,
            )
            self._validate_decision_candidate(
                suggestion=suggestion,
                reviewer_id=decision.reviewed_by,
                decision=decision.decision,
                result=result,
                decision_created_at=decision.created_at,
                require_active_result=False,
            )
            if result is not None:
                result_records.append(result)
            terminal_seen = decision.decision in _TERMINAL_DECISIONS
        return tuple(result_records)

    def _validate_decision_audit(
        self,
        connection: sqlite3.Connection,
        decision: ReviewerDecisionRecord,
    ) -> None:
        candidates = tuple(
            self._audit_candidates_for_subject(
                connection,
                subject_id=decision.reviewer_decision_id,
            )
        )
        expected_metadata = _canonical_json(
            {
                "agent_suggestion_id": decision.agent_suggestion_id,
                "coding_reference_id": decision.coding_reference_id,
                "decision": decision.decision,
                "decision_number": decision.decision_number,
            }
        )
        if len(candidates) != 1:
            raise ReviewConflictError("Decision audit history is invalid")
        row = candidates[0]
        if (
            not _AUDIT_EVENT_ID.fullmatch(
                _stored_text(row["event_id"], "audit event_id")
            )
            or _stored_text(row["project_id"], "audit project_id")
            != self.project_id
            or _stored_text(row["actor_id"], "audit actor_id")
            != decision.reviewed_by
            or _stored_text(row["event_type"], "audit event_type")
            != "qualitative.reviewer_decision.created"
            or _stored_text(row["subject_type"], "audit subject_type")
            != "reviewer_decision"
            or _stored_text(row["subject_id"], "audit subject_id")
            != decision.reviewer_decision_id
            or _stored_text(row["metadata_json"], "audit metadata_json")
            != expected_metadata
            or _stored_timestamp(row["created_at"], "audit created_at")
            != decision.created_at
        ):
            raise ReviewConflictError("Decision audit history is invalid")

    def _request_result_reference(
        self,
        connection: sqlite3.Connection,
        *,
        coding_reference_id: str | None,
        missing_is_not_found: bool = True,
    ) -> CodingReferenceRecord | None:
        if coding_reference_id is None:
            return None
        row = connection.execute(
            """
            select * from coding_references
            where project_id = ? and coding_reference_id = ?
            """,
            (self.project_id, coding_reference_id),
        ).fetchone()
        if row is None:
            if missing_is_not_found:
                raise ReviewNotFoundError("Coding reference not found")
            raise ReviewConflictError("Stored decision result is unavailable")
        record = self._coding_reference_record(row)
        self._validate_coding_reference_local(connection, record)
        return record

    def _coding_reference_record(
        self,
        row: sqlite3.Row,
    ) -> CodingReferenceRecord:
        target_kind = _stored_target_kind(row["target_kind"])
        cunit_id = _stored_text(row["cunit_id"], "coding reference cunit_id")
        if target_kind == "passage":
            if cunit_id != "":
                raise ReviewConflictError("Stored coding reference target is invalid")
        else:
            cunit_id = _stored_evidence_id(cunit_id, "cunit_id")
        start_offset = _stored_integer(row["start_offset"], "start_offset")
        end_offset = _stored_integer(row["end_offset"], "end_offset")
        if start_offset < 0 or end_offset <= start_offset:
            raise ReviewConflictError("Stored coding reference offsets are invalid")
        removed_by_value = row["removed_by"]
        removed_at_value = row["removed_at"]
        removed_by = (
            None
            if removed_by_value is None
            else _stored_entity_id(removed_by_value, "removed_by")
        )
        removed_at = (
            None
            if removed_at_value is None
            else _stored_aware_timestamp(
                removed_at_value,
                "coding reference removed_at",
            )
        )
        if (removed_by is None) != (removed_at is None):
            raise ReviewConflictError(
                "Stored coding reference removal state is invalid"
            )
        created_at = _stored_aware_timestamp(
            row["created_at"],
            "coding reference created_at",
        )
        if (
            removed_at is not None
            and _timestamp_instant(removed_at) < _timestamp_instant(created_at)
        ):
            raise ReviewConflictError(
                "Stored coding reference removal chronology is invalid"
            )
        record = CodingReferenceRecord(
            coding_reference_id=_stored_coding_reference_id(
                row["coding_reference_id"]
            ),
            project_id=_stored_text(
                row["project_id"],
                "coding reference project_id",
            ),
            project_source_id=_stored_external_id(row["project_source_id"]),
            transcript_revision_id=_stored_evidence_id(
                row["transcript_revision_id"],
                "transcript_revision_id",
            ),
            evidence_set_id=_stored_evidence_id(
                row["evidence_set_id"],
                "evidence_set_id",
            ),
            target_kind=target_kind,
            passage_id=_stored_evidence_id(row["passage_id"], "passage_id"),
            cunit_id=cunit_id,
            start_offset=start_offset,
            end_offset=end_offset,
            codebook_version_id=_stored_entity_id(
                row["codebook_version_id"],
                "codebook_version_id",
            ),
            code_id=_stored_entity_id(row["code_id"], "code_id"),
            created_by=_stored_entity_id(row["created_by"], "created_by"),
            created_at=created_at,
            removed_by=removed_by,
            removed_at=removed_at,
        )
        if record.project_id != self.project_id:
            raise ReviewConflictError(
                "Stored coding reference belongs to another project"
            )
        return record

    def _validate_coding_reference_local(
        self,
        connection: sqlite3.Connection,
        record: CodingReferenceRecord,
    ) -> None:
        self._require_frozen_code(
            connection,
            codebook_version_id=record.codebook_version_id,
            code_id=record.code_id,
            missing_is_not_found=False,
        )
        self._require_researcher(connection, record.created_by)
        if record.removed_by is not None:
            self._require_researcher(connection, record.removed_by)
        candidates = tuple(
            self._audit_candidates_for_subject(
                connection,
                subject_id=record.coding_reference_id,
            )
        )
        expected: dict[str, tuple[str, str, str]] = {
            "coding_reference.created": (
                record.created_by,
                record.created_at,
                _canonical_json(
                    {
                        "code_id": record.code_id,
                        "codebook_version_id": record.codebook_version_id,
                        "evidence_set_id": record.evidence_set_id,
                        "target_kind": record.target_kind,
                    }
                ),
            )
        }
        if record.removed_by is not None and record.removed_at is not None:
            expected["coding_reference.removed"] = (
                record.removed_by,
                record.removed_at,
                "{}",
            )
        actual: dict[str, tuple[str, str, str]] = {}
        for row in candidates:
            event_id = _stored_text(row["event_id"], "audit event_id")
            event_type = _stored_text(row["event_type"], "audit event_type")
            if (
                not _AUDIT_EVENT_ID.fullmatch(event_id)
                or _stored_text(row["project_id"], "audit project_id")
                != self.project_id
                or _stored_text(row["subject_type"], "audit subject_type")
                != "coding_reference"
                or _stored_text(row["subject_id"], "audit subject_id")
                != record.coding_reference_id
                or event_type in actual
            ):
                raise ReviewConflictError(
                    "Coding reference audit history is invalid"
                )
            actual[event_type] = (
                _stored_entity_id(row["actor_id"], "audit actor_id"),
                _stored_aware_timestamp(row["created_at"], "audit created_at"),
                _stored_text(row["metadata_json"], "audit metadata_json"),
            )
        if actual != expected:
            raise ReviewConflictError("Coding reference audit history is invalid")

    def _require_frozen_code(
        self,
        connection: sqlite3.Connection,
        *,
        codebook_version_id: str,
        code_id: str,
        missing_is_not_found: bool,
    ) -> None:
        version = connection.execute(
            """
            select project_id, codebook_version_id, status
            from codebook_versions
            where project_id = ? and codebook_version_id = ?
            """,
            (self.project_id, codebook_version_id),
        ).fetchone()
        if version is None:
            if missing_is_not_found:
                raise ReviewNotFoundError("Codebook version not found")
            raise ReviewConflictError("Stored codebook version is unavailable")
        if (
            _stored_text(version["project_id"], "codebook version project_id")
            != self.project_id
            or _stored_entity_id(
                version["codebook_version_id"],
                "codebook_version_id",
            )
            != codebook_version_id
        ):
            raise ReviewConflictError("Stored codebook version is invalid")
        status = _stored_text(version["status"], "codebook version status")
        if status not in {"draft", "frozen"} or status != "frozen":
            raise ReviewConflictError("Review coding requires a frozen code")
        code = connection.execute(
            """
            select project_id, codebook_version_id, code_id from codes
            where project_id = ? and codebook_version_id = ? and code_id = ?
            """,
            (self.project_id, codebook_version_id, code_id),
        ).fetchone()
        if code is None:
            conflicting = connection.execute(
                "select project_id, codebook_version_id from codes where code_id = ?",
                (code_id,),
            ).fetchone()
            if conflicting is not None:
                raise ReviewConflictError("Code belongs to another version")
            if missing_is_not_found:
                raise ReviewNotFoundError("Code not found")
            raise ReviewConflictError("Stored code is unavailable")
        if (
            _stored_text(code["project_id"], "code project_id") != self.project_id
            or _stored_entity_id(
                code["codebook_version_id"],
                "codebook_version_id",
            )
            != codebook_version_id
            or _stored_entity_id(code["code_id"], "code_id") != code_id
        ):
            raise ReviewConflictError("Stored code is invalid")

    def _decision_mode(
        self,
        state: _LocalSuggestionState,
        *,
        reviewer_decision_id: str,
        reviewer_id: str,
        expected_decision_number: int,
        decision: str,
        coding_reference_id: str | None,
    ) -> str:
        latest_number = len(state.decisions)
        if latest_number == expected_decision_number + 1:
            latest = state.decisions[-1]
            if (
                latest.reviewer_decision_id == reviewer_decision_id
                and latest.reviewed_by == reviewer_id
                and latest.decision == decision
                and latest.coding_reference_id == coding_reference_id
            ):
                return "retry"
            raise ReviewConflictError("Reviewer decision retry is divergent")
        if latest_number != expected_decision_number:
            raise ReviewConflictError("Reviewer decision expectation is stale")
        if state.decisions and state.decisions[-1].decision in _TERMINAL_DECISIONS:
            raise ReviewConflictError("Suggestion already has a terminal decision")
        if any(
            item.reviewer_decision_id == reviewer_decision_id
            for item in state.decisions
        ):
            raise ReviewConflictError("Reviewer decision identity is already in use")
        return "new"

    def _validate_decision_candidate(
        self,
        *,
        suggestion: AgentSuggestionRecord,
        reviewer_id: str,
        decision: str,
        result: CodingReferenceRecord | None,
        decision_created_at: str | None,
        require_active_result: bool,
    ) -> None:
        if decision in {"rejected", "deferred"}:
            if result is not None:
                raise ReviewConflictError("Decision result shape is invalid")
            return
        if result is None:
            raise ReviewConflictError("Decision result is unavailable")
        if result.created_by != reviewer_id:
            raise ReviewConflictError("Decision result belongs to another reviewer")
        if require_active_result and result.removed_at is not None:
            raise ReviewConflictError("Decision result has been removed")
        suggestion_created = _timestamp_instant(suggestion.created_at)
        result_created = _timestamp_instant(result.created_at)
        if result_created < suggestion_created:
            raise ReviewConflictError("Decision result predates the suggestion")
        if (
            decision_created_at is not None
            and result_created > _timestamp_instant(decision_created_at)
        ):
            raise ReviewConflictError("Decision result postdates the decision")

        suggestion_lineage = (
            suggestion.project_source_id,
            suggestion.transcript_revision_id,
            suggestion.evidence_set_id,
        )
        result_lineage = (
            result.project_source_id,
            result.transcript_revision_id,
            result.evidence_set_id,
        )
        suggestion_candidate = (
            suggestion.target_kind,
            suggestion.passage_id,
            suggestion.cunit_id,
            suggestion.start_offset,
            suggestion.end_offset,
            suggestion.codebook_version_id,
            suggestion.code_id,
        )
        result_candidate = (
            result.target_kind,
            result.passage_id,
            result.cunit_id,
            result.start_offset,
            result.end_offset,
            result.codebook_version_id,
            result.code_id,
        )
        if decision == "accepted":
            if suggestion_lineage != result_lineage or suggestion_candidate != result_candidate:
                raise ReviewConflictError(
                    "Accepted result does not exactly match the suggestion"
                )
            return
        if decision == "edited":
            if suggestion_lineage != result_lineage:
                raise ReviewConflictError(
                    "Edited result does not share suggestion lineage"
                )
            if suggestion_candidate == result_candidate:
                raise ReviewConflictError("Edited result is unchanged")
            return
        raise ReviewConflictError("Stored decision value is invalid")

    def _audit_candidates_for_subject(
        self,
        connection: sqlite3.Connection,
        *,
        subject_id: str,
    ) -> Iterator[sqlite3.Row]:
        for row in connection.execute(
            "select * from qualitative_audit_events order by event_id"
        ):
            if _normalized_marker(row["subject_id"]) != subject_id:
                continue
            # A domain identity is globally attributable to exactly one audit
            # family.  Once the subject identity matches, malformed binary,
            # padded, or case-folded markers are candidates and must fail the
            # strict audit comparison instead of being skipped.
            yield row

    def _append_audit(
        self,
        connection: sqlite3.Connection,
        *,
        actor_id: str,
        event_type: str,
        subject_type: str,
        subject_id: str,
        metadata: dict[str, object],
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
                _canonical_json(metadata),
                created_at,
            ),
        )

    def _validate_external_targets(
        self,
        targets: Sequence[_ExternalTarget],
        *,
        missing_is_not_found: bool,
    ) -> None:
        unique_targets = tuple(dict.fromkeys(targets))
        if not unique_targets:
            return
        try:
            with workspace_mutation_lock(self.root):
                registry = EvidenceTargetRegistry(self.root)
                for target in unique_targets:
                    resolved = registry.resolve(
                        workspace_id=self.project_id,
                        project_source_id=target.project_source_id,
                        transcript_revision_id=target.transcript_revision_id,
                        evidence_set_id=target.evidence_set_id,
                        passage_id=target.passage_id,
                        cunit_id=target.cunit_id,
                    )
                    expected = {
                        "workspace_id": self.project_id,
                        "project_source_id": target.project_source_id,
                        "transcript_revision_id": target.transcript_revision_id,
                        "evidence_set_id": target.evidence_set_id,
                        "target_kind": target.target_kind,
                        "passage_id": target.passage_id,
                        "cunit_id": target.cunit_id,
                    }
                    for field_name, expected_value in expected.items():
                        actual = getattr(resolved, field_name, None)
                        if not isinstance(actual, str) or actual != expected_value:
                            raise ReviewConflictError(
                                "Resolved evidence target identity is invalid"
                            )
                    text = getattr(resolved, "text", None)
                    text_length = getattr(resolved, "text_length", None)
                    if (
                        not isinstance(text, str)
                        or type(text_length) is not int
                        or text_length != len(text)
                    ):
                        raise ReviewConflictError(
                            "Resolved evidence target content is invalid"
                        )
                    if not (
                        0
                        <= target.start_offset
                        < target.end_offset
                        <= len(text)
                    ):
                        if missing_is_not_found:
                            raise ReviewValidationError(
                                "Suggestion offsets are outside the evidence target"
                            )
                        raise ReviewConflictError(
                            "Stored review offsets are outside the evidence target"
                        )
                    if not text[target.start_offset : target.end_offset]:
                        if missing_is_not_found:
                            raise ReviewValidationError(
                                "Suggestion selection must be non-empty"
                            )
                        raise ReviewConflictError(
                            "Stored review selection is empty"
                        )
        except ReviewValidationError:
            raise
        except ReviewConflictError:
            raise
        except EvidenceTargetNotFoundError as exc:
            if missing_is_not_found:
                raise ReviewNotFoundError("Evidence target not found") from exc
            raise ReviewConflictError(
                "Stored review evidence is unavailable"
            ) from exc
        except (EvidenceTargetConflictError, SchemaCompatibilityError) as exc:
            raise ReviewConflictError(
                "Evidence target storage is unavailable or invalid"
            ) from exc
        except (
            WorkspaceLockError,
            OSError,
            sqlite3.Error,
            TypeError,
            ValueError,
            KeyError,
        ) as exc:
            raise ReviewConflictError(
                "Evidence target storage is unavailable or invalid"
            ) from exc

    def _validate_unmatched_review_audits(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        for row in connection.execute(
            "select * from qualitative_audit_events order by event_id"
        ):
            if (
                _has_marker_prefix(row["subject_id"], "ags_")
                or _has_marker_prefix(row["subject_type"], "agent_suggestion")
                or _has_marker_prefix(
                    row["event_type"],
                    "qualitative.agent_suggestion.",
                )
            ):
                subject_id = _stored_agent_suggestion_id(row["subject_id"])
                suggestion_row = connection.execute(
                    """
                    select * from agent_coding_suggestions
                    where project_id = ? and agent_suggestion_id = ?
                    """,
                    (self.project_id, subject_id),
                ).fetchone()
                if suggestion_row is None:
                    raise ReviewConflictError("Unmatched suggestion audit exists")
                suggestion = self._suggestion_record(suggestion_row)
                self._validate_suggestion_local(connection, suggestion)
                continue
            if (
                _has_marker_prefix(row["subject_id"], "rvd_")
                or _has_marker_prefix(row["subject_type"], "reviewer_decision")
                or _has_marker_prefix(
                    row["event_type"],
                    "qualitative.reviewer_decision.",
                )
            ):
                subject_id = _stored_reviewer_decision_id(row["subject_id"])
                decision_row = self._decision_row(connection, subject_id)
                if decision_row is None:
                    raise ReviewConflictError("Unmatched decision audit exists")
                self._validate_decision_audit(
                    connection,
                    self._decision_record(decision_row),
                )
                continue
            if (
                _has_marker_prefix(row["subject_type"], "researcher")
                or _has_marker_prefix(row["event_type"], "qualitative.researcher.")
            ):
                subject_id = _stored_entity_id(row["subject_id"], "researcher id")
                researcher_row = self._researcher_row(connection, subject_id)
                if researcher_row is None:
                    raise ReviewConflictError("Unmatched researcher audit exists")
                researcher = self._researcher_record_with_provenance(
                    connection,
                    researcher_row,
                )
                if researcher.provenance_classification != "registered":
                    raise ReviewConflictError("Researcher audit history is invalid")
                continue
            if _has_marker_prefix(row["event_type"], "qualitative.project."):
                actor_id = _stored_entity_id(row["actor_id"], "bootstrap actor_id")
                researcher_row = self._researcher_row(connection, actor_id)
                if researcher_row is None:
                    raise ReviewConflictError("Bootstrap audit actor is unavailable")
                researcher = self._researcher_record_with_provenance(
                    connection,
                    researcher_row,
                )
                if researcher.provenance_classification != "bootstrap":
                    raise ReviewConflictError("Bootstrap audit history is invalid")


def _normalize_suggestion_request(
    *,
    agent_suggestion_id: object,
    origin_kind: object,
    origin_id: object,
    origin_suggestion_key: object,
    researcher_id: object,
    project_source_id: object,
    transcript_revision_id: object,
    evidence_set_id: object,
    target_kind: object,
    passage_id: object,
    cunit_id: object,
    start_offset: object,
    end_offset: object,
    codebook_version_id: object,
    code_id: object,
) -> dict[str, object]:
    normalized_kind = _input_target_kind(target_kind)
    normalized_cunit = _input_cunit_id(cunit_id, normalized_kind)
    normalized_start, normalized_end = _input_offsets(start_offset, end_offset)
    return {
        "agent_suggestion_id": _input_agent_suggestion_id(agent_suggestion_id),
        "origin_kind": _input_origin_kind(origin_kind),
        "origin_id": _input_origin_id(origin_id, "origin_id"),
        "origin_suggestion_key": _input_origin_id(
            origin_suggestion_key,
            "origin_suggestion_key",
        ),
        "project_source_id": _input_external_id(project_source_id),
        "transcript_revision_id": _input_evidence_id(
            transcript_revision_id,
            "transcript_revision_id",
        ),
        "evidence_set_id": _input_evidence_id(
            evidence_set_id,
            "evidence_set_id",
        ),
        "target_kind": normalized_kind,
        "passage_id": _input_evidence_id(passage_id, "passage_id"),
        "cunit_id": normalized_cunit,
        "start_offset": normalized_start,
        "end_offset": normalized_end,
        "codebook_version_id": _input_entity_id(
            codebook_version_id,
            "codebook_version_id",
        ),
        "code_id": _input_entity_id(code_id, "code_id"),
        "created_by": _input_entity_id(researcher_id, "researcher_id"),
    }


def _input_entity_id(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not _ENTITY_ID.fullmatch(value)
        or value != value.strip()
    ):
        raise ReviewValidationError(
            f"{field_name} must be a stable lowercase identifier"
        )
    return value


def _input_agent_suggestion_id(value: object) -> str:
    if not isinstance(value, str) or not _AGENT_SUGGESTION_ID.fullmatch(value):
        raise ReviewValidationError("agent_suggestion_id is invalid")
    return value


def _input_reviewer_decision_id(value: object) -> str:
    if not isinstance(value, str) or not _REVIEWER_DECISION_ID.fullmatch(value):
        raise ReviewValidationError("reviewer_decision_id is invalid")
    return value


def _input_coding_reference_id(value: object) -> str:
    if not isinstance(value, str) or not _CODING_REFERENCE_ID.fullmatch(value):
        raise ReviewValidationError("coding_reference_id is invalid")
    return value


def _input_origin_kind(value: object) -> str:
    if not isinstance(value, str) or value not in _ORIGIN_KINDS:
        raise ReviewValidationError("origin_kind is invalid")
    return value


def _input_origin_id(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not _ORIGIN_ID.fullmatch(value)
        or len(value.encode("utf-8")) > 128
    ):
        raise ReviewValidationError(f"{field_name} is invalid")
    return value


def _input_external_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_EXTERNAL_ID_LENGTH
        or value != value.strip()
    ):
        raise ReviewValidationError("project_source_id is invalid")
    return value


def _input_evidence_id(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not _EVIDENCE_IDS[field_name].fullmatch(value)
    ):
        raise ReviewValidationError(f"{field_name} is invalid")
    return value


def _input_target_kind(value: object) -> str:
    if not isinstance(value, str) or value not in _TARGET_KINDS:
        raise ReviewValidationError("target_kind is invalid")
    return value


def _input_cunit_id(value: object, target_kind: str) -> str:
    if not isinstance(value, str):
        raise ReviewValidationError("cunit_id must be a string")
    if target_kind == "passage":
        if value != "":
            raise ReviewValidationError("Passage suggestions require empty cunit_id")
        return value
    return _input_evidence_id(value, "cunit_id")


def _input_offsets(start_offset: object, end_offset: object) -> tuple[int, int]:
    if (
        type(start_offset) is not int
        or type(end_offset) is not int
        or start_offset < 0
        or end_offset <= start_offset
    ):
        raise ReviewValidationError(
            "Suggestion offsets must define a positive integer span"
        )
    return start_offset, end_offset


def _input_display_name(value: object) -> str:
    if not isinstance(value, str):
        raise ReviewValidationError("display_name must be a string")
    _require_valid_unicode(value, "display_name", input_value=True)
    normalized = value.strip()
    if not normalized or len(normalized) > _MAX_DISPLAY_NAME_LENGTH:
        raise ReviewValidationError("display_name is invalid")
    return normalized


def _input_role(value: object) -> str:
    if not isinstance(value, str) or value not in _ROLES:
        raise ReviewValidationError("role is invalid")
    return value


def _input_decision(value: object) -> str:
    if not isinstance(value, str) or value not in _DECISIONS:
        raise ReviewValidationError("decision is invalid")
    return value


def _input_decision_reference(
    decision: str,
    coding_reference_id: object,
) -> str | None:
    if decision in {"accepted", "edited"}:
        return _input_coding_reference_id(coding_reference_id)
    if coding_reference_id is not None:
        raise ReviewValidationError(
            "Rejected and deferred decisions cannot include a coding reference"
        )
    return None


def _input_expected_decision_number(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ReviewValidationError(
            "expected_decision_number must be a non-negative integer"
        )
    return value


def _input_limit(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_PAGE_SIZE:
        raise ReviewValidationError("limit must be an integer from 1 through 50")
    return value


def _stored_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ReviewConflictError(f"Stored {field_name} is invalid")
    return value


def _stored_entity_id(value: object, field_name: str) -> str:
    stored = _stored_text(value, field_name)
    if not _ENTITY_ID.fullmatch(stored) or stored != stored.strip():
        raise ReviewConflictError(f"Stored {field_name} is invalid")
    return stored


def _stored_agent_suggestion_id(value: object) -> str:
    stored = _stored_text(value, "agent_suggestion_id")
    if not _AGENT_SUGGESTION_ID.fullmatch(stored):
        raise ReviewConflictError("Stored suggestion identity is invalid")
    return stored


def _stored_reviewer_decision_id(value: object) -> str:
    stored = _stored_text(value, "reviewer_decision_id")
    if not _REVIEWER_DECISION_ID.fullmatch(stored):
        raise ReviewConflictError("Stored decision identity is invalid")
    return stored


def _stored_coding_reference_id(value: object) -> str:
    stored = _stored_text(value, "coding_reference_id")
    if not _CODING_REFERENCE_ID.fullmatch(stored):
        raise ReviewConflictError("Stored coding reference identity is invalid")
    return stored


def _stored_origin_kind(value: object) -> str:
    stored = _stored_text(value, "origin_kind")
    if stored not in _ORIGIN_KINDS:
        raise ReviewConflictError("Stored suggestion origin is invalid")
    return stored


def _stored_origin_id(value: object, field_name: str) -> str:
    stored = _stored_text(value, field_name)
    if not _ORIGIN_ID.fullmatch(stored) or len(stored.encode("utf-8")) > 128:
        raise ReviewConflictError(f"Stored {field_name} is invalid")
    return stored


def _stored_external_id(value: object) -> str:
    stored = _stored_text(value, "project_source_id")
    if (
        not stored
        or len(stored) > _MAX_EXTERNAL_ID_LENGTH
        or stored != stored.strip()
    ):
        raise ReviewConflictError("Stored project_source_id is invalid")
    return stored


def _stored_evidence_id(value: object, field_name: str) -> str:
    stored = _stored_text(value, field_name)
    if not _EVIDENCE_IDS[field_name].fullmatch(stored):
        raise ReviewConflictError(f"Stored {field_name} is invalid")
    return stored


def _stored_target_kind(value: object) -> str:
    stored = _stored_text(value, "target_kind")
    if stored not in _TARGET_KINDS:
        raise ReviewConflictError("Stored target_kind is invalid")
    return stored


def _stored_decision(value: object) -> str:
    stored = _stored_text(value, "decision")
    if stored not in _DECISIONS:
        raise ReviewConflictError("Stored decision is invalid")
    return stored


def _stored_role(value: object) -> str:
    stored = _stored_text(value, "researcher role")
    if stored not in _ROLES:
        raise ReviewConflictError("Stored researcher role is invalid")
    return stored


def _stored_display_name(value: object) -> str:
    stored = _stored_text(value, "researcher display_name")
    _require_valid_unicode(stored, "display_name", input_value=False)
    if (
        not stored
        or stored != stored.strip()
        or len(stored) > _MAX_DISPLAY_NAME_LENGTH
    ):
        raise ReviewConflictError("Stored researcher display_name is invalid")
    return stored


def _stored_integer(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise ReviewConflictError(f"Stored {field_name} is invalid")
    return value


def _stored_timestamp(value: object, field_name: str) -> str:
    stored, _ = _stored_timestamp_with_instant(value, field_name)
    return stored


def _stored_timestamp_with_instant(
    value: object,
    field_name: str,
) -> tuple[str, datetime]:
    stored, parsed = _stored_aware_timestamp_with_instant(value, field_name)
    if parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ReviewConflictError(f"Stored {field_name} is invalid")
    return stored, parsed


def _stored_aware_timestamp(value: object, field_name: str) -> str:
    stored, _ = _stored_aware_timestamp_with_instant(value, field_name)
    return stored


def _stored_aware_timestamp_with_instant(
    value: object,
    field_name: str,
) -> tuple[str, datetime]:
    stored = _stored_text(value, field_name)
    if (
        not stored
        or len(stored) > _MAX_TIMESTAMP_LENGTH
        or stored != stored.strip()
    ):
        raise ReviewConflictError(f"Stored {field_name} is invalid")
    normalized = f"{stored[:-1]}+00:00" if stored.endswith("Z") else stored
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ReviewConflictError(f"Stored {field_name} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReviewConflictError(f"Stored {field_name} is invalid")
    return stored, parsed


def _timestamp_instant(value: str) -> datetime:
    return _stored_aware_timestamp_with_instant(value, "timestamp")[1]


def _require_valid_unicode(
    value: str,
    field_name: str,
    *,
    input_value: bool,
) -> None:
    invalid = "\0" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    if invalid:
        if input_value:
            raise ReviewValidationError(f"{field_name} contains invalid Unicode")
        raise ReviewConflictError(f"Stored {field_name} contains invalid Unicode")


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ReviewConflictError("Canonical audit metadata is invalid") from exc


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


def _bootstrap_event_id(project_id: str, researcher_id: str) -> str:
    digest = sha256(f"{project_id}\0{researcher_id}".encode("utf-8")).hexdigest()
    return f"qae_init_{digest[:32]}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _target_from_request(request: dict[str, object]) -> _ExternalTarget:
    return _ExternalTarget(
        project_source_id=str(request["project_source_id"]),
        transcript_revision_id=str(request["transcript_revision_id"]),
        evidence_set_id=str(request["evidence_set_id"]),
        target_kind=str(request["target_kind"]),
        passage_id=str(request["passage_id"]),
        cunit_id=str(request["cunit_id"]),
        start_offset=int(request["start_offset"]),
        end_offset=int(request["end_offset"]),
    )


def _target_from_suggestion(record: AgentSuggestionRecord) -> _ExternalTarget:
    return _ExternalTarget(
        project_source_id=record.project_source_id,
        transcript_revision_id=record.transcript_revision_id,
        evidence_set_id=record.evidence_set_id,
        target_kind=record.target_kind,
        passage_id=record.passage_id,
        cunit_id=record.cunit_id,
        start_offset=record.start_offset,
        end_offset=record.end_offset,
    )


def _target_from_coding_reference(record: CodingReferenceRecord) -> _ExternalTarget:
    return _ExternalTarget(
        project_source_id=record.project_source_id,
        transcript_revision_id=record.transcript_revision_id,
        evidence_set_id=record.evidence_set_id,
        target_kind=record.target_kind,
        passage_id=record.passage_id,
        cunit_id=record.cunit_id,
        start_offset=record.start_offset,
        end_offset=record.end_offset,
    )


def _external_targets_for_state(
    state: _LocalSuggestionState,
) -> tuple[_ExternalTarget, ...]:
    return (
        _target_from_suggestion(state.suggestion),
        *(
            _target_from_coding_reference(record)
            for record in state.coding_references
        ),
    )


def _suggestion_matches_filters(
    suggestion: AgentSuggestionRecord,
    *,
    project_source_id: str | None,
    codebook_version_id: str | None,
    code_id: str | None,
    created_by: str | None,
    origin_kind: str | None,
) -> bool:
    return (
        (project_source_id is None or suggestion.project_source_id == project_source_id)
        and (
            codebook_version_id is None
            or suggestion.codebook_version_id == codebook_version_id
        )
        and (code_id is None or suggestion.code_id == code_id)
        and (created_by is None or suggestion.created_by == created_by)
        and (origin_kind is None or suggestion.origin_kind == origin_kind)
    )


class _DuplicateCursorKey(ValueError):
    pass


def _cursor_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateCursorKey(key)
        value[key] = item
    return value


def _encode_cursor(
    *,
    project_id: str,
    resource: str,
    filters: dict[str, object],
    after: dict[str, object],
) -> str:
    payload = {
        "after": after,
        "filters": filters,
        "project_id": project_id,
        "resource": resource,
        "v": 1,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _decode_cursor(
    value: object,
    *,
    project_id: str,
    resource: str,
    filters: dict[str, object],
) -> tuple[str | int, str]:
    if not isinstance(value, str) or not _CURSOR.fullmatch(value):
        raise ReviewValidationError("cursor is invalid")
    try:
        padding = "=" * ((4 - len(value) % 4) % 4)
        decoded = base64.b64decode(
            (value + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(
            decoded.decode("utf-8"),
            object_pairs_hook=_cursor_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
        _DuplicateCursorKey,
        ValueError,
    ) as exc:
        raise ReviewValidationError("cursor is invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "after",
        "filters",
        "project_id",
        "resource",
        "v",
    }:
        raise ReviewValidationError("cursor is invalid")
    if (
        type(payload["v"]) is not int
        or payload["v"] != 1
        or not isinstance(payload["project_id"], str)
        or payload["project_id"] != project_id
        or not isinstance(payload["resource"], str)
        or payload["resource"] != resource
        or not isinstance(payload["filters"], dict)
        or not _strict_json_equal(payload["filters"], filters)
    ):
        raise ReviewValidationError("cursor is invalid")
    after = payload["after"]
    if not isinstance(after, dict):
        raise ReviewValidationError("cursor is invalid")
    if resource in {"researchers", "agent_suggestions"}:
        if set(after) != {"created_at", "id"}:
            raise ReviewValidationError("cursor is invalid")
        created_at = _input_cursor_timestamp(after["created_at"])
        cursor_id = after["id"]
        if resource == "researchers":
            cursor_id = _input_entity_id(cursor_id, "cursor id")
        else:
            cursor_id = _input_agent_suggestion_id(cursor_id)
        result: tuple[str | int, str] = (created_at, cursor_id)
    elif resource == "reviewer_decisions":
        if set(after) != {"decision_number", "id"}:
            raise ReviewValidationError("cursor is invalid")
        number = after["decision_number"]
        if type(number) is not int or number <= 0:
            raise ReviewValidationError("cursor is invalid")
        result = (number, _input_reviewer_decision_id(after["id"]))
    else:
        raise ReviewValidationError("cursor is invalid")
    canonical = _encode_cursor(
        project_id=project_id,
        resource=resource,
        filters=filters,
        after=after,
    )
    if canonical != value:
        raise ReviewValidationError("cursor is invalid")
    return result


def _input_cursor_timestamp(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_TIMESTAMP_LENGTH
        or value != value.strip()
    ):
        raise ReviewValidationError("cursor is invalid")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ReviewValidationError("cursor is invalid") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset() != UTC.utcoffset(parsed)
    ):
        raise ReviewValidationError("cursor is invalid")
    return value


def _strict_json_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        if left.keys() != right.keys():
            return False
        return all(_strict_json_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _strict_json_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right
