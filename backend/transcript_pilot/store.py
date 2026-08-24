from __future__ import annotations

import json
import re
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from backend.professor_demo.provider import SPECIALIST_SPECS
from backend.storage.sqlite_migrations import (
    Migration,
    apply_migrations,
    schema_status,
)
from backend.storage.workspace_lock import workspace_mutation_lock
from backend.transcript_pilot.protocol import TranscriptChunk


JobStatus = Literal[
    "queued",
    "preflight",
    "running",
    "needs_review",
    "committing",
    "committed",
    "failed",
    "cancelled",
    "needs_attention",
]

_ENTITY_ID = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_STUDY_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,95}$")
_PROJECT_SOURCE_ID = re.compile(r"^psrc_[0-9a-f]{32}$")
_REVISION_ID = re.compile(r"^trv_[0-9a-f]{32}$")
_JOB_ID = re.compile(r"^tpj_[0-9a-f]{32}$")
_PROPOSAL_ID = re.compile(r"^tpp_[0-9a-f]{32}$")
_COMMIT_ID = re.compile(r"^tpc_[0-9a-f]{32}$")


class TranscriptPilotStoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


class TranscriptPilotStore:
    def __init__(self, root: Path | str = "local_data") -> None:
        self.root = Path(root)
        self.pilot_root = self.root / "transcript_pilot"
        self.db_path = self.pilot_root / "transcript_pilot.sqlite3"

    def create_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_pattern(str(payload["study_id"]), _STUDY_ID, "study_id")
        _require_id(str(payload["researcher_id"]), "researcher_id")
        _require_pattern(str(payload["source_id"]), _PROJECT_SOURCE_ID, "source_id")
        _require_pattern(
            str(payload["original_revision_id"]), _REVISION_ID, "revision_id"
        )
        with self.transaction() as connection:
            connection.execute(
                """
                insert into pilot_sources (
                  source_id, study_id, researcher_id, researcher_name,
                  source_filename, source_media_type, source_blob_sha256,
                  original_transcript_sha256, original_revision_id,
                  active_revision_id, data_classification, authorization_basis,
                  privacy_scan_json, protocol_version, line_count, byte_count,
                  created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["source_id"],
                    payload["study_id"],
                    payload["researcher_id"],
                    payload["researcher_name"],
                    payload["source_filename"],
                    payload["source_media_type"],
                    payload["source_blob_sha256"],
                    payload["original_transcript_sha256"],
                    payload["original_revision_id"],
                    payload["original_revision_id"],
                    payload["data_classification"],
                    payload["authorization_basis"],
                    _json(payload.get("privacy_scan", [])),
                    payload["protocol_version"],
                    payload["line_count"],
                    payload["byte_count"],
                    payload["created_at"],
                    payload["created_at"],
                ),
            )
            connection.execute(
                """
                insert into pilot_revisions (
                  revision_id, source_id, parent_revision_id, transcript_sha256,
                  revision_kind, job_id, created_by, created_at, activated_at
                ) values (?, ?, '', ?, 'original', '', ?, ?, ?)
                """,
                (
                    payload["original_revision_id"],
                    payload["source_id"],
                    payload["original_transcript_sha256"],
                    payload["researcher_id"],
                    payload["created_at"],
                    payload["created_at"],
                ),
            )
            self._audit(
                connection,
                study_id=payload["study_id"],
                source_id=payload["source_id"],
                job_id="",
                researcher_id=payload["researcher_id"],
                event_type="transcript.source.imported",
                metadata={
                    "revision_id": payload["original_revision_id"],
                    "classification": payload["data_classification"],
                    "source_blob_sha256": payload["source_blob_sha256"],
                    "transcript_sha256": payload["original_transcript_sha256"],
                },
            )
        return self.load_source(str(payload["source_id"]))

    def load_source(self, source_id: str) -> dict[str, Any]:
        _require_pattern(source_id, _PROJECT_SOURCE_ID, "source_id")
        with self.read() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "select * from pilot_sources where source_id = ?", (source_id,)
            ).fetchone()
            if row is None:
                raise TranscriptPilotStoreError("source_not_found", "Transcript source was not found")
            revisions = connection.execute(
                """
                select revision_id, parent_revision_id, transcript_sha256,
                       revision_kind, job_id, created_by, created_at, activated_at
                from pilot_revisions where source_id = ?
                order by created_at, revision_id
                """,
                (source_id,),
            ).fetchall()
            jobs = connection.execute(
                """
                select job_id, status, input_revision_id, chunk_count,
                       created_at, finished_at, committed_revision_id
                from pilot_jobs where source_id = ?
                order by created_at desc, job_id desc
                """,
                (source_id,),
            ).fetchall()
        payload = _source_row(row)
        payload["revisions"] = [dict(item) for item in revisions]
        payload["jobs"] = [dict(item) for item in jobs]
        return payload

    def list_sources(self, study_id: str) -> list[dict[str, Any]]:
        _require_pattern(study_id, _STUDY_ID, "study_id")
        with self.read() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                select * from pilot_sources where study_id = ?
                order by created_at desc, source_id desc
                """,
                (study_id,),
            ).fetchall()
        return [_source_row(row) for row in rows]

    def create_job(
        self,
        *,
        source_id: str,
        input_revision_id: str,
        researcher_id: str,
        idempotency_key: str,
        request_sha256: str,
        authorized_cost_usd: str,
        chunks: list[TranscriptChunk],
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        _require_pattern(source_id, _PROJECT_SOURCE_ID, "source_id")
        _require_pattern(input_revision_id, _REVISION_ID, "input_revision_id")
        _require_id(researcher_id, "researcher_id")
        if not 8 <= len(idempotency_key) <= 128:
            raise TranscriptPilotStoreError(
                "idempotency_key_invalid",
                "Idempotency key must contain 8 to 128 characters",
            )
        if not chunks:
            raise TranscriptPilotStoreError("chunks_invalid", "Transcript chunks are missing")
        now = _utc_now()
        with self.transaction() as connection:
            connection.row_factory = sqlite3.Row
            source = connection.execute(
                "select * from pilot_sources where source_id = ?", (source_id,)
            ).fetchone()
            if source is None:
                raise TranscriptPilotStoreError("source_not_found", "Transcript source was not found")
            if source["active_revision_id"] != input_revision_id:
                raise TranscriptPilotStoreError(
                    "stale_source_revision",
                    "The transcript active revision changed; reload before running",
                )
            if source["researcher_id"] != researcher_id:
                raise TranscriptPilotStoreError(
                    "researcher_mismatch",
                    "The researcher does not own this pilot source",
                )
            existing = connection.execute(
                """
                select job_id, request_sha256 from pilot_jobs
                where source_id = ? and idempotency_key = ?
                """,
                (source_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    raise TranscriptPilotStoreError(
                        "idempotency_conflict",
                        "Idempotency key was already used for a different run request",
                    )
                job_id = str(existing["job_id"])
            else:
                job_id = f"tpj_{uuid4().hex}"
                connection.execute(
                    """
                    insert into pilot_jobs (
                      job_id, source_id, study_id, input_revision_id,
                      researcher_id, idempotency_key, request_sha256, status,
                      chunk_count, planned_call_count, authorized_cost_usd,
                      preflight_json, provenance_json, cancel_requested,
                      error_code, error_message, committed_revision_id,
                      created_at, started_at, finished_at
                    ) values (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, '', ?, 0,
                              '', '', '', ?, '', '')
                    """,
                    (
                        job_id,
                        source_id,
                        source["study_id"],
                        input_revision_id,
                        researcher_id,
                        idempotency_key,
                        request_sha256,
                        len(chunks),
                        len(chunks) * len(SPECIALIST_SPECS),
                        authorized_cost_usd,
                        _json(provenance),
                        now,
                    ),
                )
                for chunk in chunks:
                    connection.execute(
                        """
                        insert into pilot_chunks (
                          job_id, chunk_index, start_line_index, end_line_index,
                          chunk_sha256, lines_json, status, merged_lines_json,
                          error_code, error_message
                        ) values (?, ?, ?, ?, ?, ?, 'pending', '', '', '')
                        """,
                        (
                            job_id,
                            chunk.chunk_index,
                            chunk.start_line_index,
                            chunk.end_line_index,
                            chunk.sha256,
                            _json(list(chunk.lines)),
                        ),
                    )
                    for specialist_ordinal, spec in enumerate(SPECIALIST_SPECS):
                        connection.execute(
                            """
                            insert into pilot_calls (
                              job_id, chunk_index, specialist_id,
                              specialist_ordinal, status, request_sha256,
                              result_json, receipt_json, error_code, error_message,
                              started_at, finished_at
                            ) values (?, ?, ?, ?, 'pending', '', '', '', '', '', '', '')
                            """,
                            (
                                job_id,
                                chunk.chunk_index,
                                spec.specialist_id,
                                specialist_ordinal,
                            ),
                        )
                self._audit(
                    connection,
                    study_id=str(source["study_id"]),
                    source_id=source_id,
                    job_id=job_id,
                    researcher_id=researcher_id,
                    event_type="transcript.job.queued",
                    metadata={
                        "input_revision_id": input_revision_id,
                        "chunk_count": len(chunks),
                        "planned_call_count": len(chunks) * len(SPECIALIST_SPECS),
                        "authorized_cost_usd": authorized_cost_usd,
                    },
                )
        return self.load_job(job_id)

    def load_job(self, job_id: str) -> dict[str, Any]:
        _require_pattern(job_id, _JOB_ID, "job_id")
        with self.read() as connection:
            connection.row_factory = sqlite3.Row
            job = connection.execute(
                "select * from pilot_jobs where job_id = ?", (job_id,)
            ).fetchone()
            if job is None:
                raise TranscriptPilotStoreError("job_not_found", "Transcript job was not found")
            source = connection.execute(
                "select * from pilot_sources where source_id = ?", (job["source_id"],)
            ).fetchone()
            chunks = connection.execute(
                "select * from pilot_chunks where job_id = ? order by chunk_index",
                (job_id,),
            ).fetchall()
            calls = connection.execute(
                """
                select * from pilot_calls where job_id = ?
                order by chunk_index, specialist_ordinal
                """,
                (job_id,),
            ).fetchall()
            proposals = connection.execute(
                """
                select * from pilot_proposals where job_id = ?
                order by line_index
                """,
                (job_id,),
            ).fetchall()
            decisions = connection.execute(
                """
                select d.* from pilot_review_decisions d
                join pilot_proposals p using (proposal_id)
                where p.job_id = ?
                order by p.line_index, d.decision_version
                """,
                (job_id,),
            ).fetchall()
            commit = connection.execute(
                "select * from pilot_commits where job_id = ?", (job_id,)
            ).fetchone()
        return _job_payload(job, source, chunks, calls, proposals, decisions, commit)

    def list_jobs(self, *, source_id: str | None = None) -> list[dict[str, Any]]:
        with self.read() as connection:
            connection.row_factory = sqlite3.Row
            if source_id is None:
                rows = connection.execute(
                    "select job_id from pilot_jobs order by created_at desc, job_id desc"
                ).fetchall()
            else:
                _require_pattern(source_id, _PROJECT_SOURCE_ID, "source_id")
                rows = connection.execute(
                    """
                    select job_id from pilot_jobs where source_id = ?
                    order by created_at desc, job_id desc
                    """,
                    (source_id,),
                ).fetchall()
        return [self.load_job(str(row["job_id"])) for row in rows]

    def recover_interrupted_jobs(self) -> list[str]:
        with self.transaction() as connection:
            calling_jobs = {
                str(row[0])
                for row in connection.execute(
                    "select distinct job_id from pilot_calls where status = 'calling'"
                ).fetchall()
            }
            now = _utc_now()
            if calling_jobs:
                placeholders = ",".join("?" for _ in calling_jobs)
                parameters = tuple(sorted(calling_jobs))
                connection.execute(
                    f"""
                    update pilot_calls set status = 'ambiguous', finished_at = ?,
                           error_code = 'provider_outcome_ambiguous',
                           error_message = 'Provider outcome is unknown after local restart'
                    where status = 'calling' and job_id in ({placeholders})
                    """,
                    (now, *parameters),
                )
                connection.execute(
                    f"""
                    update pilot_jobs set status = 'needs_attention', finished_at = ?,
                           error_code = 'provider_outcome_ambiguous',
                           error_message = 'A provider call was in flight during restart'
                    where job_id in ({placeholders})
                    """,
                    (now, *parameters),
                )
            connection.execute(
                """
                update pilot_jobs set status = 'queued'
                where status in ('preflight', 'running')
                  and job_id not in (
                    select distinct job_id from pilot_calls where status = 'ambiguous'
                  )
                """
            )
            queued = [
                str(row[0])
                for row in connection.execute(
                    "select job_id from pilot_jobs where status = 'queued' order by created_at"
                ).fetchall()
            ]
        return queued

    def claim_job(self, job_id: str) -> bool:
        with self.transaction() as connection:
            row = connection.execute(
                "select status, started_at from pilot_jobs where job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return False
            if row[0] != "queued":
                return False
            now = _utc_now()
            updated = connection.execute(
                """
                update pilot_jobs set status = 'preflight',
                       started_at = case when started_at = '' then ? else started_at end
                where job_id = ? and status = 'queued'
                """,
                (now, job_id),
            ).rowcount
            return updated == 1

    def set_preflight(self, job_id: str, preflight: dict[str, Any]) -> None:
        with self.transaction() as connection:
            updated = connection.execute(
                """
                update pilot_jobs set preflight_json = ?, status = 'running'
                where job_id = ? and status = 'preflight'
                """,
                (_json(preflight), job_id),
            ).rowcount
            if updated != 1:
                raise TranscriptPilotStoreError("job_state_conflict", "Transcript job state changed")

    def begin_call(
        self,
        job_id: str,
        chunk_index: int,
        specialist_id: str,
        request_sha256: str,
    ) -> None:
        with self.transaction() as connection:
            job = connection.execute(
                "select status, cancel_requested from pilot_jobs where job_id = ?", (job_id,)
            ).fetchone()
            if job is None or job[0] != "running" or bool(job[1]):
                raise TranscriptPilotStoreError("job_state_conflict", "Transcript job cannot start a call")
            connection.execute(
                "update pilot_chunks set status = 'running' where job_id = ? and chunk_index = ?",
                (job_id, chunk_index),
            )
            updated = connection.execute(
                """
                update pilot_calls set status = 'calling', request_sha256 = ?,
                       started_at = ?, finished_at = '', error_code = '', error_message = ''
                where job_id = ? and chunk_index = ? and specialist_id = ?
                  and status = 'pending'
                """,
                (request_sha256, _utc_now(), job_id, chunk_index, specialist_id),
            ).rowcount
            if updated != 1:
                raise TranscriptPilotStoreError("call_state_conflict", "Specialist call state changed")

    def complete_call(
        self,
        *,
        job_id: str,
        chunk_index: int,
        specialist_id: str,
        status: Literal["valid", "error", "ambiguous"],
        result: dict[str, Any] | None,
        receipt: dict[str, Any],
        error_code: str = "",
        error_message: str = "",
    ) -> None:
        with self.transaction() as connection:
            updated = connection.execute(
                """
                update pilot_calls set status = ?, result_json = ?, receipt_json = ?,
                       error_code = ?, error_message = ?, finished_at = ?
                where job_id = ? and chunk_index = ? and specialist_id = ?
                  and status = 'calling'
                """,
                (
                    status,
                    _json(result) if result is not None else "",
                    _json(receipt),
                    error_code,
                    error_message,
                    _utc_now(),
                    job_id,
                    chunk_index,
                    specialist_id,
                ),
            ).rowcount
            if updated != 1:
                raise TranscriptPilotStoreError("call_state_conflict", "Specialist call state changed")
            if status == "ambiguous":
                connection.execute(
                    """
                    update pilot_jobs set status = 'needs_attention', finished_at = ?,
                           error_code = 'provider_outcome_ambiguous',
                           error_message = 'A provider call may have completed without a receipt'
                    where job_id = ?
                    """,
                    (_utc_now(), job_id),
                )

    def finish_chunk(
        self,
        job_id: str,
        chunk_index: int,
        *,
        merged_lines: list[str] | None,
        error_code: str = "",
        error_message: str = "",
    ) -> None:
        status = "completed" if merged_lines is not None else "failed"
        with self.transaction() as connection:
            connection.execute(
                """
                update pilot_chunks set status = ?, merged_lines_json = ?,
                       error_code = ?, error_message = ?
                where job_id = ? and chunk_index = ?
                """,
                (
                    status,
                    _json(merged_lines) if merged_lines is not None else "",
                    error_code,
                    error_message,
                    job_id,
                    chunk_index,
                ),
            )

    def create_proposals(self, job_id: str, proposals: list[dict[str, Any]]) -> None:
        with self.transaction() as connection:
            job = connection.execute(
                "select status, study_id, source_id, researcher_id, cancel_requested from pilot_jobs where job_id = ?",
                (job_id,),
            ).fetchone()
            if job is None or job[0] != "running" or bool(job[4]):
                raise TranscriptPilotStoreError("job_state_conflict", "Transcript job state changed")
            for proposal in proposals:
                connection.execute(
                    """
                    insert into pilot_proposals (
                      proposal_id, job_id, line_index, original_text,
                      proposed_text, changed, evidence_json, created_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        proposal["proposal_id"],
                        job_id,
                        proposal["line_index"],
                        proposal["original_text"],
                        proposal["proposed_text"],
                        1 if proposal["changed"] else 0,
                        _json(proposal["evidence"]),
                        proposal["created_at"],
                    ),
                )
            now = _utc_now()
            connection.execute(
                """
                update pilot_jobs set status = 'needs_review', finished_at = ?,
                       error_code = '', error_message = '' where job_id = ?
                """,
                (now, job_id),
            )
            self._audit(
                connection,
                study_id=str(job[1]),
                source_id=str(job[2]),
                job_id=job_id,
                researcher_id=str(job[3]),
                event_type="transcript.job.ready_for_review",
                metadata={
                    "proposal_count": len(proposals),
                    "changed_line_count": sum(bool(item["changed"]) for item in proposals),
                },
            )

    def request_cancel(self, job_id: str, researcher_id: str) -> dict[str, Any]:
        with self.transaction() as connection:
            job = connection.execute(
                "select study_id, source_id, researcher_id, status from pilot_jobs where job_id = ?",
                (job_id,),
            ).fetchone()
            if job is None:
                raise TranscriptPilotStoreError("job_not_found", "Transcript job was not found")
            if job[2] != researcher_id:
                raise TranscriptPilotStoreError("researcher_mismatch", "Researcher identity does not match")
            if job[3] not in ("queued", "preflight", "running"):
                raise TranscriptPilotStoreError("job_state_conflict", "Transcript job cannot be cancelled")
            connection.execute(
                "update pilot_jobs set cancel_requested = 1 where job_id = ?", (job_id,)
            )
            self._audit(
                connection,
                study_id=str(job[0]),
                source_id=str(job[1]),
                job_id=job_id,
                researcher_id=researcher_id,
                event_type="transcript.job.cancel_requested",
                metadata={},
            )
        return self.load_job(job_id)

    def cancellation_requested(self, job_id: str) -> bool:
        with self.read() as connection:
            row = connection.execute(
                "select cancel_requested from pilot_jobs where job_id = ?", (job_id,)
            ).fetchone()
        return row is not None and bool(row[0])

    def mark_cancelled(self, job_id: str) -> None:
        now = _utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                update pilot_calls set status = 'cancelled', finished_at = ?
                where job_id = ? and status = 'pending'
                """,
                (now, job_id),
            )
            connection.execute(
                """
                update pilot_chunks set status = 'cancelled'
                where job_id = ? and status in ('pending', 'running')
                """,
                (job_id,),
            )
            connection.execute(
                """
                update pilot_jobs set status = 'cancelled', finished_at = ?,
                       error_code = 'cancelled_by_researcher',
                       error_message = 'The researcher cancelled this run'
                where job_id = ?
                """,
                (now, job_id),
            )

    def fail_job(self, job_id: str, code: str, message: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                update pilot_jobs set status = 'failed', finished_at = ?,
                       error_code = ?, error_message = ?
                where job_id = ? and status not in ('committed', 'cancelled')
                """,
                (_utc_now(), code, message, job_id),
            )

    def save_decision(
        self,
        *,
        job_id: str,
        proposal_id: str,
        researcher_id: str,
        action: Literal["accept", "keep_original", "edit"],
        edited_text: str,
        expected_decision_version: int,
    ) -> dict[str, Any]:
        _require_pattern(proposal_id, _PROPOSAL_ID, "proposal_id")
        with self.transaction() as connection:
            connection.row_factory = sqlite3.Row
            job = connection.execute(
                "select study_id, source_id, researcher_id, status from pilot_jobs where job_id = ?",
                (job_id,),
            ).fetchone()
            if job is None:
                raise TranscriptPilotStoreError("job_not_found", "Transcript job was not found")
            if job["researcher_id"] != researcher_id:
                raise TranscriptPilotStoreError("researcher_mismatch", "Researcher identity does not match")
            if job["status"] != "needs_review":
                raise TranscriptPilotStoreError("job_state_conflict", "Transcript job is not reviewable")
            proposal = connection.execute(
                "select changed from pilot_proposals where proposal_id = ? and job_id = ?",
                (proposal_id, job_id),
            ).fetchone()
            if proposal is None:
                raise TranscriptPilotStoreError("proposal_not_found", "Transcript proposal was not found")
            if not bool(proposal["changed"]):
                raise TranscriptPilotStoreError("proposal_unchanged", "Unchanged lines do not require a decision")
            current = connection.execute(
                "select coalesce(max(decision_version), 0) from pilot_review_decisions where proposal_id = ?",
                (proposal_id,),
            ).fetchone()[0]
            if int(current) != expected_decision_version:
                raise TranscriptPilotStoreError(
                    "stale_review_decision",
                    "This line decision changed; reload before saving",
                )
            normalized_edit = edited_text.strip()
            if action == "edit" and not normalized_edit:
                raise TranscriptPilotStoreError("edited_text_required", "Edited text is required")
            if action != "edit" and normalized_edit:
                raise TranscriptPilotStoreError("edited_text_unexpected", "Edited text is only allowed for edit decisions")
            decision_id = f"tpd_{uuid4().hex}"
            next_version = expected_decision_version + 1
            now = _utc_now()
            connection.execute(
                """
                insert into pilot_review_decisions (
                  decision_id, proposal_id, decision_version, researcher_id,
                  action, edited_text, created_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    proposal_id,
                    next_version,
                    researcher_id,
                    action,
                    normalized_edit,
                    now,
                ),
            )
            self._audit(
                connection,
                study_id=str(job["study_id"]),
                source_id=str(job["source_id"]),
                job_id=job_id,
                researcher_id=researcher_id,
                event_type="transcript.proposal.decided",
                metadata={
                    "proposal_id": proposal_id,
                    "action": action,
                    "decision_version": next_version,
                    "edited_text_sha256": _sha256(normalized_edit) if normalized_edit else "",
                },
            )
        return self.load_job(job_id)

    def prepare_commit(
        self,
        *,
        job_id: str,
        researcher_id: str,
        expected_active_revision_id: str,
        revision_id: str,
        transcript_sha256: str,
        import_id: str,
    ) -> dict[str, Any]:
        _require_pattern(revision_id, _REVISION_ID, "revision_id")
        now = _utc_now()
        with self.transaction() as connection:
            connection.row_factory = sqlite3.Row
            job = connection.execute(
                "select * from pilot_jobs where job_id = ?", (job_id,)
            ).fetchone()
            if job is None:
                raise TranscriptPilotStoreError("job_not_found", "Transcript job was not found")
            source = connection.execute(
                "select * from pilot_sources where source_id = ?", (job["source_id"],)
            ).fetchone()
            if job["researcher_id"] != researcher_id:
                raise TranscriptPilotStoreError("researcher_mismatch", "Researcher identity does not match")
            existing = connection.execute(
                "select * from pilot_commits where job_id = ?", (job_id,)
            ).fetchone()
            if existing is not None:
                if (
                    existing["revision_id"],
                    existing["transcript_sha256"],
                    existing["created_by"],
                ) != (revision_id, transcript_sha256, researcher_id):
                    raise TranscriptPilotStoreError("commit_conflict", "Stored commit identity conflicts")
                return dict(existing)
            if job["status"] != "needs_review":
                raise TranscriptPilotStoreError("job_state_conflict", "Transcript job is not ready to commit")
            if job["input_revision_id"] != expected_active_revision_id:
                raise TranscriptPilotStoreError(
                    "stale_source_revision",
                    "This job can only create a child of the revision it processed",
                )
            if source["active_revision_id"] != expected_active_revision_id:
                raise TranscriptPilotStoreError(
                    "stale_source_revision",
                    "The active transcript revision changed; reload before committing",
                )
            existing_revision = connection.execute(
                "select * from pilot_revisions where source_id = ? and revision_id = ?",
                (job["source_id"], revision_id),
            ).fetchone()
            if existing_revision is not None:
                raise TranscriptPilotStoreError(
                    "revision_already_exists",
                    "This exact transcript already exists as a revision for this source",
                )
            unresolved = connection.execute(
                """
                select count(*) from pilot_proposals p
                where p.job_id = ? and p.changed = 1
                  and not exists (
                    select 1 from pilot_review_decisions d
                    where d.proposal_id = p.proposal_id
                  )
                """,
                (job_id,),
            ).fetchone()[0]
            if int(unresolved):
                raise TranscriptPilotStoreError(
                    "review_incomplete",
                    "Every changed line needs a researcher decision before commit",
                )
            commit_id = f"tpc_{uuid4().hex}"
            connection.execute(
                """
                insert into pilot_commits (
                  commit_id, job_id, source_id, parent_revision_id, revision_id,
                  transcript_sha256, import_id, status, created_by, created_at,
                  completed_at
                ) values (?, ?, ?, ?, ?, ?, ?, 'publishing', ?, ?, '')
                """,
                (
                    commit_id,
                    job_id,
                    job["source_id"],
                    expected_active_revision_id,
                    revision_id,
                    transcript_sha256,
                    import_id,
                    researcher_id,
                    now,
                ),
            )
            connection.execute(
                "update pilot_jobs set status = 'committing' where job_id = ?", (job_id,)
            )
        return {
            "commit_id": commit_id,
            "job_id": job_id,
            "source_id": str(job["source_id"]),
            "parent_revision_id": expected_active_revision_id,
            "revision_id": revision_id,
            "transcript_sha256": transcript_sha256,
            "import_id": import_id,
            "status": "publishing",
            "created_by": researcher_id,
            "created_at": now,
            "completed_at": "",
        }

    def finalize_commit(self, job_id: str) -> dict[str, Any]:
        with self.transaction() as connection:
            connection.row_factory = sqlite3.Row
            commit = connection.execute(
                "select * from pilot_commits where job_id = ?", (job_id,)
            ).fetchone()
            if commit is None:
                raise TranscriptPilotStoreError("commit_not_found", "Transcript commit was not found")
            if commit["status"] == "completed":
                return dict(commit)
            if commit["status"] != "publishing":
                raise TranscriptPilotStoreError("commit_conflict", "Transcript commit state conflicts")
            job = connection.execute(
                "select study_id, source_id, researcher_id from pilot_jobs where job_id = ?",
                (job_id,),
            ).fetchone()
            source = connection.execute(
                "select active_revision_id from pilot_sources where source_id = ?",
                (commit["source_id"],),
            ).fetchone()
            if source is None or source["active_revision_id"] != commit["parent_revision_id"]:
                raise TranscriptPilotStoreError("stale_source_revision", "Active revision changed during commit")
            now = _utc_now()
            existing_revision = connection.execute(
                "select * from pilot_revisions where source_id = ? and revision_id = ?",
                (commit["source_id"], commit["revision_id"]),
            ).fetchone()
            if existing_revision is None:
                connection.execute(
                    """
                    insert into pilot_revisions (
                      revision_id, source_id, parent_revision_id, transcript_sha256,
                      revision_kind, job_id, created_by, created_at, activated_at
                    ) values (?, ?, ?, ?, 'researcher-reviewed', ?, ?, ?, ?)
                    """,
                    (
                        commit["revision_id"],
                        commit["source_id"],
                        commit["parent_revision_id"],
                        commit["transcript_sha256"],
                        job_id,
                        commit["created_by"],
                        commit["created_at"],
                        now,
                    ),
                )
            elif (
                existing_revision["parent_revision_id"],
                existing_revision["transcript_sha256"],
                existing_revision["revision_kind"],
                existing_revision["job_id"],
                existing_revision["created_by"],
                existing_revision["created_at"],
            ) != (
                commit["parent_revision_id"],
                commit["transcript_sha256"],
                "researcher-reviewed",
                job_id,
                commit["created_by"],
                commit["created_at"],
            ):
                raise TranscriptPilotStoreError(
                    "revision_identity_conflict",
                    "Stored transcript revision lineage conflicts with this commit",
                )
            connection.execute(
                "update pilot_sources set active_revision_id = ?, updated_at = ? where source_id = ?",
                (commit["revision_id"], now, commit["source_id"]),
            )
            connection.execute(
                "update pilot_commits set status = 'completed', completed_at = ? where job_id = ?",
                (now, job_id),
            )
            connection.execute(
                """
                update pilot_jobs set status = 'committed', committed_revision_id = ?,
                       finished_at = ? where job_id = ?
                """,
                (commit["revision_id"], now, job_id),
            )
            self._audit(
                connection,
                study_id=str(job[0]),
                source_id=str(job[1]),
                job_id=job_id,
                researcher_id=str(job[2]),
                event_type="transcript.revision.committed",
                metadata={
                    "parent_revision_id": commit["parent_revision_id"],
                    "revision_id": commit["revision_id"],
                    "transcript_sha256": commit["transcript_sha256"],
                },
            )
        return self.load_job(job_id)

    def restore_original(
        self,
        *,
        source_id: str,
        researcher_id: str,
        expected_active_revision_id: str,
    ) -> dict[str, Any]:
        with self.transaction() as connection:
            connection.row_factory = sqlite3.Row
            source = connection.execute(
                "select * from pilot_sources where source_id = ?", (source_id,)
            ).fetchone()
            if source is None:
                raise TranscriptPilotStoreError("source_not_found", "Transcript source was not found")
            if source["researcher_id"] != researcher_id:
                raise TranscriptPilotStoreError("researcher_mismatch", "Researcher identity does not match")
            if source["active_revision_id"] != expected_active_revision_id:
                raise TranscriptPilotStoreError("stale_source_revision", "Active revision changed; reload")
            now = _utc_now()
            connection.execute(
                "update pilot_sources set active_revision_id = original_revision_id, updated_at = ? where source_id = ?",
                (now, source_id),
            )
            self._audit(
                connection,
                study_id=str(source["study_id"]),
                source_id=source_id,
                job_id="",
                researcher_id=researcher_id,
                event_type="transcript.revision.original_restored",
                metadata={
                    "from_revision_id": expected_active_revision_id,
                    "to_revision_id": source["original_revision_id"],
                },
            )
        return self.load_source(source_id)

    def review_material(self, job_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        payload = self.load_job(job_id)
        return list(payload["proposals"]), payload

    def audit_events(
        self,
        *,
        study_id: str | None = None,
        source_id: str | None = None,
        job_id: str | None = None,
    ) -> list[dict[str, Any]]:
        filters: list[str] = []
        values: list[str] = []
        for column, value in (
            ("study_id", study_id),
            ("source_id", source_id),
            ("job_id", job_id),
        ):
            if value is not None:
                filters.append(f"{column} = ?")
                values.append(value)
        where = " where " + " and ".join(filters) if filters else ""
        with self.read() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "select * from pilot_audit_events"
                + where
                + " order by created_at, event_id",
                tuple(values),
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            event = dict(row)
            event["metadata"] = _parse_json(event.pop("metadata_json"), {})
            events.append(event)
        return events

    def migration_status(self) -> list[dict[str, object]]:
        with workspace_mutation_lock(self.root):
            with self._prepared_connection() as connection:
                return schema_status(connection)

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        with workspace_mutation_lock(self.root):
            with self._prepared_connection() as connection:
                connection.execute("pragma query_only = on")
                yield connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with workspace_mutation_lock(self.root):
            with self._prepared_connection() as connection:
                connection.execute("begin immediate")
                try:
                    yield connection
                except BaseException:
                    connection.rollback()
                    raise
                else:
                    connection.commit()

    @contextmanager
    def _prepared_connection(self) -> Iterator[sqlite3.Connection]:
        self.pilot_root.mkdir(parents=True, exist_ok=True)
        if self.db_path.exists() or self.db_path.is_symlink():
            if not stat.S_ISREG(self.db_path.lstat().st_mode):
                raise TranscriptPilotStoreError("storage_invalid", "Transcript pilot database is invalid")
        connection = sqlite3.connect(self.db_path, timeout=30)
        try:
            connection.execute("pragma foreign_keys = on")
            connection.execute("pragma trusted_schema = off")
            apply_migrations(
                connection,
                database_name="transcript_pilot",
                migrations=_MIGRATIONS,
            )
            quick_check = connection.execute("pragma quick_check").fetchone()
            if quick_check != ("ok",):
                raise TranscriptPilotStoreError("storage_invalid", "Transcript pilot database failed integrity checks")
            yield connection
        finally:
            connection.close()

    def _audit(
        self,
        connection: sqlite3.Connection,
        *,
        study_id: str,
        source_id: str,
        job_id: str,
        researcher_id: str,
        event_type: str,
        metadata: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            insert into pilot_audit_events (
              event_id, study_id, source_id, job_id, researcher_id,
              event_type, metadata_json, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"tpa_{uuid4().hex}",
                study_id,
                source_id,
                job_id,
                researcher_id,
                event_type,
                _json(metadata),
                _utc_now(),
            ),
        )


def _source_row(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["privacy_scan"] = _parse_json(payload.pop("privacy_scan_json"), [])
    return payload


def _job_payload(
    job: sqlite3.Row,
    source: sqlite3.Row,
    chunks: list[sqlite3.Row],
    calls: list[sqlite3.Row],
    proposals: list[sqlite3.Row],
    decisions: list[sqlite3.Row],
    commit: sqlite3.Row | None,
) -> dict[str, Any]:
    payload = dict(job)
    payload["preflight"] = _parse_json(payload.pop("preflight_json"), None)
    payload["provenance"] = _parse_json(payload.pop("provenance_json"), {})
    source_payload = _source_row(source)
    source_payload.pop("revisions", None)
    source_payload.pop("jobs", None)
    payload["source"] = source_payload

    call_payloads: list[dict[str, Any]] = []
    for row in calls:
        item = dict(row)
        item["result"] = _parse_json(item.pop("result_json"), None)
        item["receipt"] = _parse_json(item.pop("receipt_json"), None)
        call_payloads.append(item)

    payload["chunks"] = []
    for row in chunks:
        item = dict(row)
        item["lines"] = _parse_json(item.pop("lines_json"), [])
        item["merged_lines"] = _parse_json(item.pop("merged_lines_json"), None)
        item["calls"] = [
            call for call in call_payloads if call["chunk_index"] == item["chunk_index"]
        ]
        payload["chunks"].append(item)

    decisions_by_proposal: dict[str, list[dict[str, Any]]] = {}
    for row in decisions:
        decisions_by_proposal.setdefault(str(row["proposal_id"]), []).append(dict(row))
    proposal_payloads: list[dict[str, Any]] = []
    for row in proposals:
        item = dict(row)
        item["changed"] = bool(item["changed"])
        item["evidence"] = _parse_json(item.pop("evidence_json"), {})
        history = decisions_by_proposal.get(str(item["proposal_id"]), [])
        item["decision_history"] = history
        item["decision"] = history[-1] if history else None
        item["decision_version"] = history[-1]["decision_version"] if history else 0
        proposal_payloads.append(item)
    payload["proposals"] = proposal_payloads
    payload["commit"] = dict(commit) if commit is not None else None

    completed_statuses = {"valid", "error", "ambiguous"}
    attempted_statuses = completed_statuses | {"calling"}
    receipts = [item["receipt"] for item in call_payloads if item["receipt"]]
    costs = [
        str(receipt["cost_usd"])
        for receipt in receipts
        if receipt.get("cost_usd") is not None
    ]
    known_cost = sum((_decimal(value) for value in costs), _decimal("0"))
    accounting_complete = (
        len(receipts) == len(call_payloads)
        and all(bool(receipt.get("accounting_complete")) for receipt in receipts)
        and all(item["status"] == "valid" for item in call_payloads)
    )
    changed = [item for item in proposal_payloads if item["changed"]]
    payload["progress"] = {
        "planned_call_count": len(call_payloads),
        "attempted_call_count": sum(item["status"] in attempted_statuses for item in call_payloads),
        "completed_call_count": sum(item["status"] in completed_statuses for item in call_payloads),
        "valid_call_count": sum(item["status"] == "valid" for item in call_payloads),
        "completed_chunk_count": sum(item["status"] == "completed" for item in payload["chunks"]),
        "changed_line_count": len(changed),
        "reviewed_line_count": sum(item["decision"] is not None for item in changed),
        "unresolved_line_count": sum(item["decision"] is None for item in changed),
    }
    payload["usage"] = {
        "known_cost_subtotal_usd": format(known_cost, "f"),
        "total_cost_usd": format(known_cost, "f") if accounting_complete else None,
        "accounting_complete": accounting_complete,
        "prompt_tokens": _sum_receipt_field(receipts, "prompt_tokens"),
        "completion_tokens": _sum_receipt_field(receipts, "completion_tokens"),
        "reasoning_tokens": _sum_receipt_field(receipts, "reasoning_tokens"),
        "total_tokens": _sum_receipt_field(receipts, "total_tokens"),
        "currency": "USD",
    }
    return payload


def _sum_receipt_field(receipts: list[dict[str, Any]], field: str) -> int | None:
    values = [receipt.get(field) for receipt in receipts]
    if not values or any(type(value) is not int for value in values):
        return None
    return sum(values)


def _migrate_v1(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        create table pilot_sources (
          source_id text primary key,
          study_id text not null,
          researcher_id text not null,
          researcher_name text not null,
          source_filename text not null,
          source_media_type text not null,
          source_blob_sha256 text not null,
          original_transcript_sha256 text not null,
          original_revision_id text not null,
          active_revision_id text not null,
          data_classification text not null check (
            data_classification in ('synthetic', 'authorized-deidentified')
          ),
          authorization_basis text not null,
          privacy_scan_json text not null,
          protocol_version text not null,
          line_count integer not null check (line_count > 0),
          byte_count integer not null check (byte_count > 0),
          created_at text not null,
          updated_at text not null
        );

        create table pilot_revisions (
          revision_id text not null,
          source_id text not null references pilot_sources on delete restrict,
          parent_revision_id text not null,
          transcript_sha256 text not null,
          revision_kind text not null check (
            revision_kind in ('original', 'researcher-reviewed')
          ),
          job_id text not null,
          created_by text not null,
          created_at text not null,
          activated_at text not null,
          primary key (source_id, revision_id)
        );

        create table pilot_jobs (
          job_id text primary key,
          source_id text not null references pilot_sources on delete restrict,
          study_id text not null,
          input_revision_id text not null,
          researcher_id text not null,
          idempotency_key text not null,
          request_sha256 text not null,
          status text not null check (status in (
            'queued', 'preflight', 'running', 'needs_review', 'committing',
            'committed', 'failed', 'cancelled', 'needs_attention'
          )),
          chunk_count integer not null check (chunk_count > 0),
          planned_call_count integer not null check (planned_call_count > 0),
          authorized_cost_usd text not null,
          preflight_json text not null,
          provenance_json text not null,
          cancel_requested integer not null check (cancel_requested in (0, 1)),
          error_code text not null,
          error_message text not null,
          committed_revision_id text not null,
          created_at text not null,
          started_at text not null,
          finished_at text not null,
          unique (source_id, idempotency_key)
        );

        create table pilot_chunks (
          job_id text not null references pilot_jobs on delete restrict,
          chunk_index integer not null check (chunk_index >= 0),
          start_line_index integer not null check (start_line_index >= 0),
          end_line_index integer not null check (end_line_index >= start_line_index),
          chunk_sha256 text not null,
          lines_json text not null,
          status text not null check (
            status in ('pending', 'running', 'completed', 'failed', 'cancelled')
          ),
          merged_lines_json text not null,
          error_code text not null,
          error_message text not null,
          primary key (job_id, chunk_index)
        );

        create table pilot_calls (
          job_id text not null,
          chunk_index integer not null,
          specialist_id text not null,
          specialist_ordinal integer not null,
          status text not null check (
            status in ('pending', 'calling', 'valid', 'error', 'ambiguous', 'cancelled')
          ),
          request_sha256 text not null,
          result_json text not null,
          receipt_json text not null,
          error_code text not null,
          error_message text not null,
          started_at text not null,
          finished_at text not null,
          primary key (job_id, chunk_index, specialist_id),
          foreign key (job_id, chunk_index)
            references pilot_chunks (job_id, chunk_index) on delete restrict
        );

        create table pilot_proposals (
          proposal_id text primary key,
          job_id text not null references pilot_jobs on delete restrict,
          line_index integer not null check (line_index >= 0),
          original_text text not null,
          proposed_text text not null,
          changed integer not null check (changed in (0, 1)),
          evidence_json text not null,
          created_at text not null,
          unique (job_id, line_index)
        );

        create table pilot_review_decisions (
          decision_id text primary key,
          proposal_id text not null references pilot_proposals on delete restrict,
          decision_version integer not null check (decision_version > 0),
          researcher_id text not null,
          action text not null check (action in ('accept', 'keep_original', 'edit')),
          edited_text text not null,
          created_at text not null,
          unique (proposal_id, decision_version)
        );

        create table pilot_commits (
          commit_id text primary key,
          job_id text not null unique references pilot_jobs on delete restrict,
          source_id text not null references pilot_sources on delete restrict,
          parent_revision_id text not null,
          revision_id text not null,
          transcript_sha256 text not null,
          import_id text not null unique,
          status text not null check (status in ('publishing', 'completed')),
          created_by text not null,
          created_at text not null,
          completed_at text not null
        );

        create table pilot_audit_events (
          event_id text primary key,
          study_id text not null,
          source_id text not null,
          job_id text not null,
          researcher_id text not null,
          event_type text not null,
          metadata_json text not null,
          created_at text not null
        );

        create index pilot_jobs_source_status_idx
          on pilot_jobs (source_id, status, created_at);
        create index pilot_calls_job_status_idx
          on pilot_calls (job_id, status, chunk_index, specialist_ordinal);
        create index pilot_proposals_job_idx
          on pilot_proposals (job_id, line_index);
        create index pilot_audit_study_idx
          on pilot_audit_events (study_id, created_at, event_id);
        """
    )


_MIGRATIONS = (Migration(1, "researcher-supervised transcript pilot", _migrate_v1),)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _parse_json(value: str, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise TranscriptPilotStoreError("storage_invalid", "Stored transcript pilot JSON is invalid") from exc


def _require_id(value: str, label: str) -> None:
    if not _ENTITY_ID.fullmatch(value):
        raise TranscriptPilotStoreError(f"{label}_invalid", f"{label} is invalid")


def _require_pattern(value: str, pattern: re.Pattern[str], label: str) -> None:
    if not pattern.fullmatch(value):
        raise TranscriptPilotStoreError(f"{label}_invalid", f"{label} is invalid")


def _sha256(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decimal(value: str):
    from decimal import Decimal

    return Decimal(value)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
