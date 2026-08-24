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

from backend.professor_demo.provider import (
    ENDPOINT_TAG,
    MAX_COMPLETION_TOKENS,
    MODEL_ID,
    PROMPT_TOKEN_CEILING,
    PROVIDER_NAME,
    SPECIALIST_SPECS,
    is_canonical_luna_model,
)
from backend.storage.sqlite_migrations import (
    Migration,
    add_text_column_if_missing,
    apply_migrations,
    schema_status,
)
from backend.storage.workspace_lock import workspace_mutation_lock
from backend.transcript_pilot.protocol import (
    GLOBAL_COST_CEILING_USD,
    TranscriptChunk,
    canonical_transcript_lines,
)


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
_PREFLIGHT_ID = re.compile(r"^tpf_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class TranscriptPilotStoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


class _CommitAlreadyCompleted(RuntimeError):
    pass


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
            connection.row_factory = sqlite3.Row
            existing = connection.execute(
                "select * from pilot_sources where source_id = ?",
                (payload["source_id"],),
            ).fetchone()
            if existing is not None:
                expected_source = (
                    payload["study_id"],
                    payload["researcher_id"],
                    payload["researcher_name"],
                    payload["source_filename"],
                    payload["source_media_type"],
                    payload["source_blob_sha256"],
                    payload["original_transcript_sha256"],
                    payload["original_revision_id"],
                    payload["data_classification"],
                    payload["authorization_basis"],
                    _json(payload.get("privacy_scan", [])),
                    payload["protocol_version"],
                    payload["line_count"],
                    payload["byte_count"],
                    payload["created_at"],
                )
                stored_source = (
                    existing["study_id"],
                    existing["researcher_id"],
                    existing["researcher_name"],
                    existing["source_filename"],
                    existing["source_media_type"],
                    existing["source_blob_sha256"],
                    existing["original_transcript_sha256"],
                    existing["original_revision_id"],
                    existing["data_classification"],
                    existing["authorization_basis"],
                    existing["privacy_scan_json"],
                    existing["protocol_version"],
                    existing["line_count"],
                    existing["byte_count"],
                    existing["created_at"],
                )
                original_revision = connection.execute(
                    """
                    select parent_revision_id, transcript_sha256, revision_kind,
                           job_id, created_by, created_at
                    from pilot_revisions
                    where source_id = ? and revision_id = ?
                    """,
                    (payload["source_id"], payload["original_revision_id"]),
                ).fetchone()
                if stored_source != expected_source or (
                    tuple(original_revision) if original_revision is not None else None
                ) != (
                    "",
                    payload["original_transcript_sha256"],
                    "original",
                    "",
                    payload["researcher_id"],
                    payload["created_at"],
                ):
                    raise TranscriptPilotStoreError(
                        "source_identity_conflict",
                        "Stored transcript source identity conflicts with this intake",
                    )
            else:
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

    def reserve_intake(
        self,
        *,
        request_sha256: str,
        study_id: str,
        researcher_id: str,
    ) -> dict[str, Any]:
        _require_pattern(request_sha256, _SHA256, "request_sha256")
        _require_pattern(study_id, _STUDY_ID, "study_id")
        _require_id(researcher_id, "researcher_id")
        with self.transaction() as connection:
            connection.row_factory = sqlite3.Row
            existing = connection.execute(
                "select * from pilot_intakes where request_sha256 = ?",
                (request_sha256,),
            ).fetchone()
            if existing is not None:
                if (existing["study_id"], existing["researcher_id"]) != (
                    study_id,
                    researcher_id,
                ):
                    raise TranscriptPilotStoreError(
                        "intake_identity_conflict",
                        "Stored transcript intake identity conflicts with this request",
                    )
                return dict(existing)
            now = _utc_now()
            intake = {
                "request_sha256": request_sha256,
                "study_id": study_id,
                "researcher_id": researcher_id,
                "source_id": f"psrc_{uuid4().hex}",
                "import_id": f"imp_{uuid4().hex}",
                "run_id": f"tpi_{uuid4().hex}",
                "status": "reserved",
                "created_at": now,
                "completed_at": "",
            }
            connection.execute(
                """
                insert into pilot_intakes (
                  request_sha256, study_id, researcher_id, source_id,
                  import_id, run_id, status, created_at, completed_at
                ) values (?, ?, ?, ?, ?, ?, 'reserved', ?, '')
                """,
                (
                    request_sha256,
                    study_id,
                    researcher_id,
                    intake["source_id"],
                    intake["import_id"],
                    intake["run_id"],
                    now,
                ),
            )
            return intake

    def complete_intake(self, *, request_sha256: str, source_id: str) -> None:
        _require_pattern(request_sha256, _SHA256, "request_sha256")
        _require_pattern(source_id, _PROJECT_SOURCE_ID, "source_id")
        with self.transaction() as connection:
            row = connection.execute(
                "select source_id, status from pilot_intakes where request_sha256 = ?",
                (request_sha256,),
            ).fetchone()
            if row is None or row[0] != source_id:
                raise TranscriptPilotStoreError(
                    "intake_identity_conflict",
                    "Stored transcript intake identity conflicts with this source",
                )
            if row[1] == "completed":
                return
            updated = connection.execute(
                """
                update pilot_intakes set status = 'completed', completed_at = ?
                where request_sha256 = ? and status = 'reserved'
                """,
                (_utc_now(), request_sha256),
            ).rowcount
            if updated != 1:
                raise TranscriptPilotStoreError(
                    "intake_state_conflict",
                    "Transcript intake state changed before completion",
                )

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
        authorization_confirmation: str,
        authorization_transcript_sha256: str,
        chunks: list[TranscriptChunk],
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        _require_pattern(source_id, _PROJECT_SOURCE_ID, "source_id")
        _require_pattern(input_revision_id, _REVISION_ID, "input_revision_id")
        _require_id(researcher_id, "researcher_id")
        _require_pattern(
            authorization_transcript_sha256,
            _SHA256,
            "authorization_transcript_sha256",
        )
        if authorization_confirmation != "authorize-four-specialists-per-chunk":
            raise TranscriptPilotStoreError(
                "egress_confirmation_invalid",
                "Explicit four-specialist egress confirmation is required",
            )
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
                if source["active_revision_id"] != input_revision_id:
                    raise TranscriptPilotStoreError(
                        "stale_source_revision",
                        "The transcript active revision changed; reload before running",
                    )
                job_id = f"tpj_{uuid4().hex}"
                connection.execute(
                    """
                    insert into pilot_jobs (
                      job_id, source_id, study_id, input_revision_id,
                      researcher_id, idempotency_key, request_sha256, status,
                      chunk_count, planned_call_count, authorized_cost_usd,
                      authorization_confirmation, authorization_actor_id,
                      authorization_revision_id, authorization_transcript_sha256,
                      authorization_at,
                      preflight_json, provenance_json, cancel_requested,
                      error_code, error_message, committed_revision_id,
                      created_at, started_at, finished_at
                    ) values (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?,
                              '', ?, 0, '', '', '', ?, '', '')
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
                        authorization_confirmation,
                        researcher_id,
                        input_revision_id,
                        authorization_transcript_sha256,
                        now,
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
            preflights = connection.execute(
                """
                select * from pilot_preflights where job_id = ?
                order by attempt_number
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
            audit_events = connection.execute(
                """
                select * from pilot_audit_events where job_id = ?
                order by created_at, event_id
                """,
                (job_id,),
            ).fetchall()
        payload = _job_payload(
            job,
            source,
            chunks,
            calls,
            preflights,
            proposals,
            decisions,
            commit,
        )
        payload["audit_events"] = [_audit_event_payload(row) for row in audit_events]
        return payload

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
                    update pilot_chunks set status = 'failed',
                           error_code = 'provider_outcome_ambiguous',
                           error_message = 'Provider outcome is unknown after local restart'
                    where job_id in ({placeholders}) and exists (
                      select 1 from pilot_calls c
                      where c.job_id = pilot_chunks.job_id
                        and c.chunk_index = pilot_chunks.chunk_index
                        and c.status = 'calling'
                    )
                    """,
                    parameters,
                )
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
                update pilot_calls set status = 'cancelled', finished_at = ?
                where status = 'pending' and job_id in (
                  select job_id from pilot_jobs
                  where cancel_requested = 1
                    and status in ('queued', 'preflight', 'running')
                )
                """,
                (now,),
            )
            connection.execute(
                """
                update pilot_chunks set status = 'cancelled'
                where status in ('pending', 'running') and job_id in (
                  select job_id from pilot_jobs
                  where cancel_requested = 1
                    and status in ('queued', 'preflight', 'running')
                )
                """
            )
            connection.execute(
                """
                update pilot_jobs set status = 'cancelled', finished_at = ?,
                       error_code = 'cancelled_by_researcher',
                       error_message = 'The researcher cancelled this run'
                where cancel_requested = 1
                  and status in ('queued', 'preflight', 'running')
                """,
                (now,),
            )
            connection.execute(
                """
                update pilot_jobs set status = 'queued', preflight_json = ''
                where status in ('preflight', 'running')
                  and cancel_requested = 0
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

    def publishing_commits(self) -> list[dict[str, Any]]:
        with self.read() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                select commit_id, job_id, source_id, parent_revision_id,
                       revision_id, transcript_sha256, import_id, status,
                       created_by, created_at, completed_at
                from pilot_commits where status = 'publishing'
                order by created_at, commit_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_recovery_contract_attention(self, job_id: str) -> None:
        with self.transaction() as connection:
            connection.row_factory = sqlite3.Row
            job = connection.execute(
                """
                select study_id, source_id, researcher_id, status
                from pilot_jobs where job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if job is None or job["status"] != "queued":
                return
            connection.execute(
                """
                update pilot_jobs set status = 'needs_attention',
                       error_code = 'execution_contract_changed',
                       error_message = 'The executable provider or protocol contract changed after this run started'
                where job_id = ? and status = 'queued'
                """,
                (job_id,),
            )
            self._audit(
                connection,
                study_id=str(job["study_id"]),
                source_id=str(job["source_id"]),
                job_id=job_id,
                researcher_id=str(job["researcher_id"]),
                event_type="transcript.job.recovery_blocked",
                metadata={"reason": "execution_contract_changed"},
            )

    def mark_commit_recovery_attention(
        self,
        job_id: str,
        *,
        code: str,
        message: str,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                update pilot_jobs set status = 'needs_attention',
                       error_code = ?, error_message = ?
                where job_id = ? and status in ('committing', 'needs_attention')
                  and exists (
                    select 1 from pilot_commits c
                    where c.job_id = pilot_jobs.job_id and c.status = 'publishing'
                  )
                """,
                (code, message, job_id),
            )

    def claim_job(self, job_id: str) -> bool:
        with self.transaction() as connection:
            row = connection.execute(
                "select status, started_at, cancel_requested from pilot_jobs where job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return False
            if row[0] != "queued" or bool(row[2]):
                return False
            now = _utc_now()
            updated = connection.execute(
                """
                update pilot_jobs set status = 'preflight',
                       started_at = case when started_at = '' then ? else started_at end
                where job_id = ? and status = 'queued' and cancel_requested = 0
                """,
                (now, job_id),
            ).rowcount
            return updated == 1

    def set_preflight(self, job_id: str, preflight: dict[str, Any]) -> str:
        with self.transaction() as connection:
            job = connection.execute(
                """
                select status, planned_call_count, authorized_cost_usd
                from pilot_jobs where job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if job is None or job[0] != "preflight":
                raise TranscriptPilotStoreError(
                    "job_state_conflict", "Transcript job state changed"
                )
            if not _preflight_satisfies_job_contract(
                preflight,
                planned_call_count=int(job[1]),
                authorized_cost_usd=str(job[2]),
            ):
                raise TranscriptPilotStoreError(
                    "preflight_contract_invalid",
                    "Provider preflight does not prove the job cost and routing contract",
                )
            attempt_number = int(
                connection.execute(
                    """
                    select coalesce(max(attempt_number), 0)
                    from pilot_preflights where job_id = ?
                    """,
                    (job_id,),
                ).fetchone()[0]
            ) + 1
            preflight_id = f"tpf_{uuid4().hex}"
            connection.execute(
                """
                insert into pilot_preflights (
                  preflight_id, job_id, attempt_number, receipt_json, created_at
                ) values (?, ?, ?, ?, ?)
                """,
                (
                    preflight_id,
                    job_id,
                    attempt_number,
                    _json(preflight),
                    _utc_now(),
                ),
            )
            updated = connection.execute(
                """
                update pilot_jobs set preflight_json = ?, status = 'running'
                where job_id = ? and status = 'preflight'
                """,
                (_json(preflight), job_id),
            ).rowcount
            if updated != 1:
                raise TranscriptPilotStoreError("job_state_conflict", "Transcript job state changed")
            return preflight_id

    def begin_call(
        self,
        job_id: str,
        chunk_index: int,
        specialist_id: str,
        request_sha256: str,
        preflight_id: str,
    ) -> None:
        _require_pattern(preflight_id, _PREFLIGHT_ID, "preflight_id")
        with self.transaction() as connection:
            job = connection.execute(
                """
                select status, cancel_requested, planned_call_count,
                       authorized_cost_usd
                from pilot_jobs where job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if job is None or job[0] != "running" or bool(job[1]):
                raise TranscriptPilotStoreError("job_state_conflict", "Transcript job cannot start a call")
            preflight = connection.execute(
                """
                select receipt_json from pilot_preflights
                where preflight_id = ? and job_id = ?
                """,
                (preflight_id, job_id),
            ).fetchone()
            if preflight is None:
                raise TranscriptPilotStoreError(
                    "preflight_identity_conflict",
                    "Specialist call is not bound to this job preflight",
                )
            preflight_receipt = _parse_json(str(preflight[0]), {})
            if not _preflight_satisfies_job_contract(
                preflight_receipt,
                planned_call_count=int(job[2]),
                authorized_cost_usd=str(job[3]),
            ):
                raise TranscriptPilotStoreError(
                    "preflight_contract_invalid",
                    "Specialist calls require a current bounded provider preflight",
                )
            completed_calls = connection.execute(
                """
                select c.receipt_json, p.receipt_json
                from pilot_calls c
                join pilot_preflights p
                  on p.preflight_id = c.preflight_id and p.job_id = c.job_id
                where c.job_id = ? and c.status in ('valid', 'error', 'ambiguous')
                """,
                (job_id,),
            ).fetchall()
            known_cost = _decimal("0")
            for completed_receipt_json, bound_json in completed_calls:
                completed_receipt = _parse_json(str(completed_receipt_json), {})
                bound = _parse_json(str(bound_json), {})
                if not _receipt_satisfies_preflight_budget(completed_receipt, bound):
                    raise TranscriptPilotStoreError(
                        "usage_accounting_incomplete",
                        "A prior provider receipt cannot authorize another specialist call",
                    )
                known_cost += _decimal(str(completed_receipt["cost_usd"]))
            pending_call_count = int(
                connection.execute(
                    "select count(*) from pilot_calls where job_id = ? and status = 'pending'",
                    (job_id,),
                ).fetchone()[0]
            )
            prospective_cost = known_cost + (
                _decimal(str(preflight_receipt["max_cost_per_call_usd"]))
                * _decimal(str(pending_call_count))
            )
            if prospective_cost >= _decimal(str(job[3])):
                raise TranscriptPilotStoreError(
                    "cost_authorization_exceeded",
                    "Another specialist call would exceed the researcher-authorized cost",
                )
            connection.execute(
                "update pilot_chunks set status = 'running' where job_id = ? and chunk_index = ?",
                (job_id, chunk_index),
            )
            updated = connection.execute(
                """
                update pilot_calls set status = 'calling', request_sha256 = ?,
                       preflight_id = ?,
                       started_at = ?, finished_at = '', error_code = '', error_message = ''
                where job_id = ? and chunk_index = ? and specialist_id = ?
                  and status = 'pending'
                """,
                (
                    request_sha256,
                    preflight_id,
                    _utc_now(),
                    job_id,
                    chunk_index,
                    specialist_id,
                ),
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
            call_preflight = connection.execute(
                """
                select c.preflight_id, p.receipt_json, j.authorized_cost_usd
                from pilot_calls c
                join pilot_preflights p
                  on p.preflight_id = c.preflight_id and p.job_id = c.job_id
                join pilot_jobs j on j.job_id = c.job_id
                where c.job_id = ? and c.chunk_index = ?
                  and c.specialist_id = ? and c.status = 'calling'
                """,
                (job_id, chunk_index, specialist_id),
            ).fetchone()
            if call_preflight is None:
                raise TranscriptPilotStoreError(
                    "call_state_conflict",
                    "Specialist call is not bound to a current provider preflight",
                )
            bound = _parse_json(str(call_preflight[1]), {})
            completed_rows = connection.execute(
                """
                select receipt_json from pilot_calls
                where job_id = ? and status in ('valid', 'error', 'ambiguous')
                """,
                (job_id,),
            ).fetchall()
            cumulative_cost = _known_receipt_cost(completed_rows)
            current_cost = _receipt_cost(receipt)
            try:
                max_call_cost = _decimal(str(bound["max_cost_per_call_usd"]))
                authorized_cost = _decimal(str(call_preflight[2]))
            except (KeyError, ValueError, ArithmeticError):
                max_call_cost = None
                authorized_cost = None
            cost_bound_breached = current_cost is not None and (
                max_call_cost is None
                or not max_call_cost.is_finite()
                or current_cost > max_call_cost
                or (
                    cumulative_cost is not None
                    and authorized_cost is not None
                    and cumulative_cost + current_cost > authorized_cost
                )
            )
            if status == "valid":
                chunk = connection.execute(
                    """
                    select lines_json from pilot_chunks
                    where job_id = ? and chunk_index = ?
                    """,
                    (job_id, chunk_index),
                ).fetchone()
                spec = next(
                    (
                        item
                        for item in SPECIALIST_SPECS
                        if item.specialist_id == specialist_id
                    ),
                    None,
                )
                try:
                    parsed_result = (
                        spec.result_model.model_validate(result, strict=True)
                        if spec is not None and result is not None
                        else None
                    )
                except ValueError as exc:
                    raise TranscriptPilotStoreError(
                        "valid_call_invalid",
                        "A valid specialist call must contain its strict result",
                    ) from exc
                expected_line_count = (
                    len(_parse_json(str(chunk[0]), [])) if chunk is not None else 0
                )
                indexes = (
                    [item.line_index for item in parsed_result.lines]
                    if parsed_result is not None
                    else []
                )
                if (
                    parsed_result is None
                    or _contains_line_break(result)
                    or parsed_result.specialist_id != specialist_id
                    or len(indexes) != expected_line_count
                    or set(indexes) != set(range(expected_line_count))
                ):
                    raise TranscriptPilotStoreError(
                        "valid_call_invalid",
                        "A valid specialist call must contain its strict result and receipt",
                    )
                receipt_contract_valid = _receipt_satisfies_product_contract(receipt)
                receipt_budget_valid = _receipt_satisfies_preflight_budget(
                    receipt,
                    bound,
                )
                cumulative_authorized = (
                    cumulative_cost is not None
                    and current_cost is not None
                    and cumulative_cost + current_cost
                    <= _decimal(str(call_preflight[2]))
                )
                if not receipt_contract_valid:
                    status = "error"
                    result = None
                    error_code = "provider_receipt_invalid"
                    error_message = "The provider receipt violated the bounded call contract"
                elif not receipt_budget_valid or not cumulative_authorized:
                    status = "error"
                    result = None
                    error_code = "provider_cost_bound_exceeded"
                    error_message = "The provider receipt exceeded the authorized cost bound"
            elif result is not None:
                raise TranscriptPilotStoreError(
                    "call_result_unexpected",
                    "Only a valid specialist call can persist a result",
                )
            if cost_bound_breached:
                status = "error"
                result = None
                error_code = "provider_cost_bound_exceeded"
                error_message = "The provider receipt exceeded the authorized cost bound"
            if status == "valid":
                generation_id = str(receipt["generation_id"])
                prior_generation_ids = {
                    str(_parse_json(str(row[0]), {}).get("generation_id", ""))
                    for row in completed_rows
                }
                if generation_id in prior_generation_ids:
                    status = "error"
                    result = None
                    error_code = "provider_generation_reused"
                    error_message = "Each specialist call requires a distinct provider generation"
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
                    update pilot_chunks set status = 'failed', error_code = ?,
                           error_message = ?
                    where job_id = ? and chunk_index = ?
                    """,
                    (
                        error_code or "provider_outcome_ambiguous",
                        error_message or "A provider call outcome is unknown",
                        job_id,
                        chunk_index,
                    ),
                )
                connection.execute(
                    """
                    update pilot_jobs set status = 'needs_attention', finished_at = ?,
                           error_code = 'provider_outcome_ambiguous',
                           error_message = 'A provider call may have completed without a receipt'
                    where job_id = ?
                    """,
                    (_utc_now(), job_id),
                )
            elif error_code == "provider_cost_bound_exceeded":
                connection.execute(
                    """
                    update pilot_chunks set status = 'failed', error_code = ?,
                           error_message = ?
                    where job_id = ? and chunk_index = ?
                    """,
                    (error_code, error_message, job_id, chunk_index),
                )
                connection.execute(
                    """
                    update pilot_jobs set status = 'needs_attention', finished_at = ?,
                           error_code = ?, error_message = ?
                    where job_id = ?
                    """,
                    (_utc_now(), error_code, error_message, job_id),
                )
            elif error_code == "provider_generation_reused":
                connection.execute(
                    """
                    update pilot_chunks set status = 'failed', error_code = ?,
                           error_message = ?
                    where job_id = ? and chunk_index = ?
                    """,
                    (error_code, error_message, job_id, chunk_index),
                )
                connection.execute(
                    """
                    update pilot_jobs set status = 'needs_attention', finished_at = ?,
                           error_code = ?, error_message = ?
                    where job_id = ?
                    """,
                    (_utc_now(), error_code, error_message, job_id),
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
            if merged_lines is not None:
                stored = connection.execute(
                    """
                    select lines_json from pilot_chunks
                    where job_id = ? and chunk_index = ?
                    """,
                    (job_id, chunk_index),
                ).fetchone()
                originals = (
                    _parse_json(str(stored[0]), []) if stored is not None else []
                )
                try:
                    canonical = canonical_transcript_lines("\n".join(merged_lines))
                except (TypeError, ValueError) as exc:
                    raise TranscriptPilotStoreError(
                        "merged_chunk_invalid",
                        "Merged chunk does not satisfy the transcript line protocol",
                    ) from exc
                if len(canonical) != len(originals) or canonical != merged_lines:
                    raise TranscriptPilotStoreError(
                        "merged_chunk_invalid",
                        "Merged chunk does not satisfy the transcript line protocol",
                    )
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
                """
                select status, study_id, source_id, researcher_id,
                       cancel_requested, planned_call_count, authorized_cost_usd
                from pilot_jobs where job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if job is None or job[0] != "running" or bool(job[4]):
                raise TranscriptPilotStoreError("job_state_conflict", "Transcript job state changed")
            chunks = connection.execute(
                """
                select chunk_index, start_line_index, status, lines_json,
                       merged_lines_json
                from pilot_chunks where job_id = ? order by chunk_index
                """,
                (job_id,),
            ).fetchall()
            calls = connection.execute(
                """
                select c.chunk_index, c.specialist_id, c.status,
                       c.receipt_json, c.result_json, c.preflight_id,
                       p.receipt_json as preflight_receipt_json
                from pilot_calls c
                left join pilot_preflights p
                  on p.preflight_id = c.preflight_id and p.job_id = c.job_id
                where c.job_id = ?
                order by c.chunk_index, c.specialist_ordinal
                """,
                (job_id,),
            ).fetchall()
            call_receipts = [
                _parse_json(str(call[3]), {})
                for call in calls
            ]
            generation_ids = [
                receipt.get("generation_id")
                for receipt in call_receipts
            ]
            known_call_cost = _known_receipt_cost([(call[3],) for call in calls])
            if (
                not chunks
                or any(chunk[2] != "completed" for chunk in chunks)
                or len(calls) != int(job[5])
                or any(call[2] != "valid" for call in calls)
                or any(
                    call[6] is None
                    or not _receipt_satisfies_preflight_budget(
                        _parse_json(str(call[3]), {}),
                        _parse_json(str(call[6]), {}),
                    )
                    for call in calls
                )
                or any(not isinstance(value, str) or not value for value in generation_ids)
                or len(generation_ids) != len(set(generation_ids))
                or known_call_cost is None
                or known_call_cost > _decimal(str(job[6]))
            ):
                raise TranscriptPilotStoreError(
                    "review_material_incomplete",
                    "Only fully validated and accounted specialist results can enter review",
                )

            call_results = {
                (int(call[0]), str(call[1])): _parse_json(str(call[4]), {})
                for call in calls
            }
            expected_proposals: dict[int, dict[str, Any]] = {}
            for chunk in chunks:
                chunk_index = int(chunk[0])
                start_line_index = int(chunk[1])
                originals = _parse_json(str(chunk[3]), [])
                merged = _parse_json(str(chunk[4]), [])
                if (
                    not isinstance(originals, list)
                    or not isinstance(merged, list)
                    or len(originals) != len(merged)
                ):
                    raise TranscriptPilotStoreError(
                        "review_material_incomplete",
                        "Stored chunk lines cannot produce review material",
                    )
                for local_index, (original, proposed) in enumerate(
                    zip(originals, merged, strict=True)
                ):
                    evidence: dict[str, Any] = {}
                    for spec in SPECIALIST_SPECS:
                        result = call_results.get((chunk_index, spec.specialist_id))
                        result_lines = result.get("lines") if isinstance(result, dict) else None
                        line = next(
                            (
                                item
                                for item in result_lines
                                if isinstance(item, dict)
                                and item.get("line_index") == local_index
                            ),
                            None,
                        ) if isinstance(result_lines, list) else None
                        if line is None:
                            raise TranscriptPilotStoreError(
                                "review_material_incomplete",
                                "Stored specialist evidence cannot produce review material",
                            )
                        evidence[spec.specialist_id] = line
                    global_index = start_line_index + local_index
                    expected_proposals[global_index] = {
                        "original_text": original,
                        "proposed_text": proposed,
                        "changed": original != proposed,
                        "evidence": evidence,
                    }

            if (
                len(proposals) != len(expected_proposals)
                or any(type(item.get("line_index")) is not int for item in proposals)
                or {int(item["line_index"]) for item in proposals}
                != set(expected_proposals)
                or any(
                    type(item.get("changed")) is not bool
                    or {
                        "original_text": item.get("original_text"),
                        "proposed_text": item.get("proposed_text"),
                        "changed": item.get("changed"),
                        "evidence": item.get("evidence"),
                    }
                    != expected_proposals[int(item["line_index"])]
                    for item in proposals
                )
            ):
                raise TranscriptPilotStoreError(
                    "review_material_conflict",
                    "Proposed review material does not match stored specialist output",
                )
            proposal_created_at = _utc_now()
            for proposal in proposals:
                _require_pattern(str(proposal["proposal_id"]), _PROPOSAL_ID, "proposal_id")
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
                        proposal_created_at,
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
            if job[3] == "queued":
                now = _utc_now()
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
                    update pilot_jobs set cancel_requested = 1, status = 'cancelled',
                           finished_at = ?, error_code = 'cancelled_by_researcher',
                           error_message = 'The researcher cancelled this run'
                    where job_id = ? and status = 'queued'
                    """,
                    (now, job_id),
                )
            else:
                connection.execute(
                    """
                    update pilot_jobs set cancel_requested = 1
                    where job_id = ? and status in ('preflight', 'running')
                    """,
                    (job_id,),
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
            updated = connection.execute(
                """
                update pilot_jobs set status = 'cancelled', finished_at = ?,
                       error_code = 'cancelled_by_researcher',
                       error_message = 'The researcher cancelled this run'
                where job_id = ? and cancel_requested = 1
                  and status in ('queued', 'preflight', 'running')
                """,
                (now, job_id),
            ).rowcount
            if updated != 1:
                current = connection.execute(
                    "select status from pilot_jobs where job_id = ?", (job_id,)
                ).fetchone()
                if current is not None and current[0] == "cancelled":
                    return
                raise TranscriptPilotStoreError(
                    "job_state_conflict",
                    "Transcript job can no longer be cancelled",
                )
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

    def fail_job(self, job_id: str, code: str, message: str) -> None:
        with self.transaction() as connection:
            job = connection.execute(
                "select status, cancel_requested from pilot_jobs where job_id = ?",
                (job_id,),
            ).fetchone()
            if job is None or job[0] not in ("queued", "preflight", "running"):
                return
            now = _utc_now()
            calling = connection.execute(
                """
                select chunk_index from pilot_calls
                where job_id = ? and status = 'calling'
                """,
                (job_id,),
            ).fetchall()
            if calling:
                connection.execute(
                    """
                    update pilot_chunks set status = 'failed',
                           error_code = 'provider_outcome_ambiguous',
                           error_message = 'A provider call outcome is unknown'
                    where job_id = ? and exists (
                      select 1 from pilot_calls c
                      where c.job_id = pilot_chunks.job_id
                        and c.chunk_index = pilot_chunks.chunk_index
                        and c.status = 'calling'
                    )
                    """,
                    (job_id,),
                )
                connection.execute(
                    """
                    update pilot_calls set status = 'ambiguous', finished_at = ?,
                           error_code = 'provider_outcome_ambiguous',
                           error_message = 'A provider call outcome is unknown'
                    where job_id = ? and status = 'calling'
                    """,
                    (now, job_id),
                )
                connection.execute(
                    """
                    update pilot_jobs set status = 'needs_attention', finished_at = ?,
                           error_code = 'provider_outcome_ambiguous',
                           error_message = 'A provider call may have completed without a receipt'
                    where job_id = ?
                    """,
                    (now, job_id),
                )
                return
            if bool(job[1]):
                connection.execute(
                    """
                    update pilot_jobs set status = 'cancelled', finished_at = ?,
                           error_code = 'cancelled_by_researcher',
                           error_message = 'The researcher cancelled this run'
                    where job_id = ? and cancel_requested = 1
                      and status in ('queued', 'preflight', 'running')
                    """,
                    (now, job_id),
                )
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
                return
            connection.execute(
                """
                update pilot_jobs set status = 'failed', finished_at = ?,
                       error_code = ?, error_message = ?
                where job_id = ? and cancel_requested = 0
                  and status in ('queued', 'preflight', 'running')
                """,
                (now, code, message, job_id),
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
            if action == "edit" and ("\n" in normalized_edit or "\r" in normalized_edit):
                raise TranscriptPilotStoreError(
                    "edited_text_invalid",
                    "A line edit cannot add a transcript line break",
                )
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
        review_snapshot_sha256: str,
    ) -> dict[str, Any]:
        _require_pattern(revision_id, _REVISION_ID, "revision_id")
        _require_pattern(
            review_snapshot_sha256,
            _SHA256,
            "review_snapshot_sha256",
        )
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
                    existing["source_id"],
                    existing["parent_revision_id"],
                    existing["revision_id"],
                    existing["transcript_sha256"],
                    existing["import_id"],
                    existing["created_by"],
                    existing["review_snapshot_sha256"],
                ) != (
                    job["source_id"],
                    expected_active_revision_id,
                    revision_id,
                    transcript_sha256,
                    import_id,
                    researcher_id,
                    review_snapshot_sha256,
                ):
                    raise TranscriptPilotStoreError("commit_conflict", "Stored commit identity conflicts")
                if existing["status"] == "completed":
                    return dict(existing)
                if (
                    existing["status"] != "publishing"
                    or job["status"] not in ("committing", "needs_attention")
                    or job["input_revision_id"] != existing["parent_revision_id"]
                    or source["active_revision_id"] != existing["parent_revision_id"]
                ):
                    raise TranscriptPilotStoreError(
                        "stale_source_revision",
                        "Stored transcript commit lineage no longer matches the active source",
                    )
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
            current_review_snapshot = _review_snapshot_sha256_from_rows(
                connection.execute(
                    """
                    select p.proposal_id, p.line_index, p.original_text,
                           p.proposed_text, p.changed, d.decision_id,
                           d.decision_version, d.action, d.edited_text
                    from pilot_proposals p
                    left join pilot_review_decisions d
                      on d.proposal_id = p.proposal_id
                     and d.decision_version = (
                       select max(latest.decision_version)
                       from pilot_review_decisions latest
                       where latest.proposal_id = p.proposal_id
                     )
                    where p.job_id = ?
                    order by p.line_index, p.proposal_id
                    """,
                    (job_id,),
                ).fetchall()
            )
            if current_review_snapshot != review_snapshot_sha256:
                raise TranscriptPilotStoreError(
                    "stale_review_snapshot",
                    "A line decision changed; reload before committing",
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
            publishing = connection.execute(
                """
                select job_id from pilot_commits
                where source_id = ? and status = 'publishing'
                """,
                (job["source_id"],),
            ).fetchone()
            if publishing is not None:
                raise TranscriptPilotStoreError(
                    "source_commit_in_progress",
                    "Another reviewed revision is already publishing for this source",
                )
            commit_id = f"tpc_{uuid4().hex}"
            connection.execute(
                """
                insert into pilot_commits (
                  commit_id, job_id, source_id, parent_revision_id, revision_id,
                  transcript_sha256, import_id, status, created_by, created_at,
                  completed_at, review_snapshot_sha256
                ) values (?, ?, ?, ?, ?, ?, ?, 'publishing', ?, ?, '', ?)
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
                    review_snapshot_sha256,
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
            "review_snapshot_sha256": review_snapshot_sha256,
        }

    def finalize_commit(self, job_id: str) -> dict[str, Any]:
        try:
            with self.transaction() as connection:
                connection.row_factory = sqlite3.Row
                commit = connection.execute(
                    "select * from pilot_commits where job_id = ?", (job_id,)
                ).fetchone()
                if commit is None:
                    raise TranscriptPilotStoreError("commit_not_found", "Transcript commit was not found")
                if commit["status"] == "completed":
                    raise _CommitAlreadyCompleted
                if commit["status"] != "publishing":
                    raise TranscriptPilotStoreError("commit_conflict", "Transcript commit state conflicts")
                job = connection.execute(
                    """
                    select study_id, source_id, researcher_id, status
                    from pilot_jobs where job_id = ?
                    """,
                    (job_id,),
                ).fetchone()
                source = connection.execute(
                    "select active_revision_id from pilot_sources where source_id = ?",
                    (commit["source_id"],),
                ).fetchone()
                if source is None or source["active_revision_id"] != commit["parent_revision_id"]:
                    raise TranscriptPilotStoreError("stale_source_revision", "Active revision changed during commit")
                if job is None or job["status"] not in ("committing", "needs_attention"):
                    raise TranscriptPilotStoreError(
                        "commit_conflict",
                        "Transcript job is not in a publishable commit state",
                    )
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
        except _CommitAlreadyCompleted:
            pass
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
            publishing = connection.execute(
                """
                select 1 from pilot_commits
                where source_id = ? and status = 'publishing'
                """,
                (source_id,),
            ).fetchone()
            if publishing is not None:
                raise TranscriptPilotStoreError(
                    "source_commit_in_progress",
                    "A reviewed revision is publishing; restore is temporarily blocked",
                )
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


def _audit_event_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["metadata"] = _parse_json(payload.pop("metadata_json"), {})
    return payload


def _job_payload(
    job: sqlite3.Row,
    source: sqlite3.Row,
    chunks: list[sqlite3.Row],
    calls: list[sqlite3.Row],
    preflights: list[sqlite3.Row],
    proposals: list[sqlite3.Row],
    decisions: list[sqlite3.Row],
    commit: sqlite3.Row | None,
) -> dict[str, Any]:
    payload = dict(job)
    payload["preflight"] = _parse_json(payload.pop("preflight_json"), None)
    payload["preflight_history"] = [
        {
            **dict(row),
            "receipt": _parse_json(str(row["receipt_json"]), {}),
        }
        for row in preflights
    ]
    for item in payload["preflight_history"]:
        item.pop("receipt_json", None)
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
    payload["review_snapshot_sha256"] = _review_snapshot_sha256_from_proposals(
        proposal_payloads
    )
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
    generation_ids = [receipt.get("generation_id") for receipt in receipts]
    preflight_receipts = {
        str(row["preflight_id"]): _parse_json(str(row["receipt_json"]), {})
        for row in preflights
    }
    accounting_complete = (
        len(receipts) == len(call_payloads)
        and all(item["status"] == "valid" for item in call_payloads)
        and all(isinstance(value, str) and value for value in generation_ids)
        and len(generation_ids) == len(set(generation_ids))
        and all(
            item.get("preflight_id") in preflight_receipts
            and _receipt_satisfies_preflight_budget(
                item["receipt"],
                preflight_receipts[str(item["preflight_id"])],
            )
            for item in call_payloads
        )
        and known_cost <= _decimal(str(payload["authorized_cost_usd"]))
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


def _receipt_has_complete_accounting(receipt: Any) -> bool:
    if not isinstance(receipt, dict) or receipt.get("accounting_complete") is not True:
        return False
    cost = receipt.get("cost_usd")
    if not isinstance(cost, str):
        return False
    try:
        parsed_cost = _decimal(cost)
    except Exception:
        return False
    if not parsed_cost.is_finite() or parsed_cost < 0:
        return False
    return all(
        type(receipt.get(field)) is int and int(receipt[field]) >= 0
        for field in (
            "prompt_tokens",
            "completion_tokens",
            "reasoning_tokens",
            "total_tokens",
        )
    )


def _review_snapshot_sha256_from_proposals(
    proposals: list[dict[str, Any]],
) -> str:
    entries: list[dict[str, Any]] = []
    for proposal in proposals:
        decision = proposal.get("decision")
        decision = decision if isinstance(decision, dict) else {}
        entries.append(
            {
                "proposal_id": str(proposal["proposal_id"]),
                "line_index": int(proposal["line_index"]),
                "changed": bool(proposal["changed"]),
                "original_text_sha256": _sha256(str(proposal["original_text"])),
                "proposed_text_sha256": _sha256(str(proposal["proposed_text"])),
                "decision_id": str(decision.get("decision_id", "")),
                "decision_version": int(decision.get("decision_version", 0)),
                "action": str(decision.get("action", "")),
                "edited_text_sha256": (
                    _sha256(str(decision.get("edited_text", "")))
                    if decision.get("edited_text")
                    else ""
                ),
            }
        )
    entries.sort(key=lambda item: (item["line_index"], item["proposal_id"]))
    return _sha256(_json(entries))


def _review_snapshot_sha256_from_rows(rows: list[sqlite3.Row]) -> str:
    proposals: list[dict[str, Any]] = []
    for row in rows:
        decision = (
            {
                "decision_id": row["decision_id"],
                "decision_version": row["decision_version"],
                "action": row["action"],
                "edited_text": row["edited_text"],
            }
            if row["decision_id"] is not None
            else None
        )
        proposals.append(
            {
                "proposal_id": row["proposal_id"],
                "line_index": row["line_index"],
                "original_text": row["original_text"],
                "proposed_text": row["proposed_text"],
                "changed": bool(row["changed"]),
                "decision": decision,
            }
        )
    return _review_snapshot_sha256_from_proposals(proposals)


def _receipt_satisfies_product_contract(receipt: Any) -> bool:
    return (
        _receipt_has_complete_accounting(receipt)
        and receipt.get("model_requested") == MODEL_ID
        and receipt.get("endpoint_requested") == ENDPOINT_TAG
        and isinstance(receipt.get("generation_id"), str)
        and bool(receipt["generation_id"])
        and isinstance(receipt.get("model_returned"), str)
        and is_canonical_luna_model(receipt["model_returned"])
        and receipt.get("provider_returned") == PROVIDER_NAME
        and receipt.get("router_attempt_count") == 1
        and receipt.get("cache_hit") is False
        and receipt.get("router_pipeline_stages") == []
        and receipt.get("finish_reason") == "stop"
        and receipt.get("reasoning_tokens") == 0
        and receipt.get("prompt_tokens") <= PROMPT_TOKEN_CEILING
        and receipt.get("completion_tokens") <= MAX_COMPLETION_TOKENS
        and receipt.get("total_tokens")
        == receipt.get("prompt_tokens") + receipt.get("completion_tokens")
        and type(receipt.get("latency_ms")) is int
        and receipt["latency_ms"] >= 0
    )


def _preflight_satisfies_job_contract(
    preflight: Any,
    *,
    planned_call_count: int,
    authorized_cost_usd: str,
) -> bool:
    if not isinstance(preflight, dict):
        return False
    try:
        request_price = _decimal(str(preflight["request_price_per_call_usd"]))
        prompt_price = _decimal(str(preflight["prompt_price_per_token_usd"]))
        completion_price = _decimal(
            str(preflight["completion_price_per_token_usd"])
        )
        max_cost = _decimal(str(preflight["max_cost_per_call_usd"]))
        estimated = _decimal(str(preflight["estimated_max_cost_usd"]))
        authorized = _decimal(str(preflight["authorized_cost_usd"]))
        job_authorized = _decimal(authorized_cost_usd)
        global_ceiling = _decimal(str(preflight["global_cost_ceiling_usd"]))
        max_prompt = preflight["max_prompt_tokens_per_call"]
        max_completion = preflight["max_completion_tokens_per_call"]
        preflight_call_count = preflight["planned_call_count"]
        chunk_count = preflight["chunk_count"]
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return False
    decimals = (
        request_price,
        prompt_price,
        completion_price,
        max_cost,
        estimated,
        authorized,
        job_authorized,
        global_ceiling,
    )
    if any(not value.is_finite() or value < 0 for value in decimals):
        return False
    if (
        type(chunk_count) is not int
        or type(preflight_call_count) is not int
        or type(max_prompt) is not int
        or type(max_completion) is not int
    ):
        return False
    calculated_max = (
        request_price
        + _decimal(str(max_prompt)) * prompt_price
        + _decimal(str(max_completion)) * completion_price
    )
    return (
        preflight.get("model") == MODEL_ID
        and preflight.get("provider") == PROVIDER_NAME
        and preflight.get("endpoint") == ENDPOINT_TAG
        and preflight.get("endpoint_is_zdr") is True
        and preflight.get("required_parameters_supported") is True
        and preflight.get("metadata_request_count") == 3
        and chunk_count > 0
        and preflight_call_count == planned_call_count
        and preflight_call_count == chunk_count * len(SPECIALIST_SPECS)
        and max_prompt == PROMPT_TOKEN_CEILING
        and max_completion == MAX_COMPLETION_TOKENS
        and request_price == 0
        and max_cost == calculated_max
        and estimated == max_cost * _decimal(str(planned_call_count))
        and authorized == job_authorized
        and 0 <= estimated < authorized <= global_ceiling
        and global_ceiling == _decimal(GLOBAL_COST_CEILING_USD)
        and isinstance(preflight.get("checked_at"), str)
        and bool(preflight["checked_at"])
    )


def _receipt_satisfies_preflight_budget(receipt: Any, preflight: Any) -> bool:
    if not _receipt_satisfies_product_contract(receipt) or not isinstance(
        preflight, dict
    ):
        return False
    try:
        max_prompt = preflight["max_prompt_tokens_per_call"]
        max_completion = preflight["max_completion_tokens_per_call"]
        max_cost = _decimal(str(preflight["max_cost_per_call_usd"]))
        receipt_cost = _decimal(str(receipt["cost_usd"]))
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return False
    return (
        type(max_prompt) is int
        and type(max_completion) is int
        and receipt["prompt_tokens"] <= max_prompt
        and receipt["completion_tokens"] <= max_completion
        and max_cost.is_finite()
        and max_cost >= 0
        and receipt_cost.is_finite()
        and 0 <= receipt_cost <= max_cost
    )


def _receipt_cost(receipt: Any):
    if not isinstance(receipt, dict) or not isinstance(receipt.get("cost_usd"), str):
        return None
    try:
        value = _decimal(receipt["cost_usd"])
    except (ValueError, ArithmeticError):
        return None
    if not value.is_finite() or value < 0:
        return None
    return value


def _known_receipt_cost(rows: list[Any]):
    total = _decimal("0")
    for row in rows:
        receipt = _parse_json(str(row[0]), {})
        cost = _receipt_cost(receipt)
        if cost is None:
            return None
        total += cost
    return total


def _execute_sql_script_transactionally(
    connection: sqlite3.Connection,
    script: str,
) -> None:
    statement = ""
    for line in script.splitlines():
        statement += line + "\n"
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise ValueError("incomplete SQLite migration statement")


def _migrate_v1(connection: sqlite3.Connection) -> None:
    _execute_sql_script_transactionally(
        connection,
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
          authorization_confirmation text not null check (
            authorization_confirmation = 'authorize-four-specialists-per-chunk'
          ),
          authorization_actor_id text not null,
          authorization_revision_id text not null,
          authorization_transcript_sha256 text not null,
          authorization_at text not null,
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


def _migrate_v2(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        create table pilot_intakes (
          request_sha256 text primary key,
          study_id text not null,
          researcher_id text not null,
          source_id text not null unique,
          import_id text not null unique,
          run_id text not null unique,
          status text not null check (status in ('reserved', 'completed')),
          created_at text not null,
          completed_at text not null
        )
        """
    )
    connection.execute(
        """
        create table pilot_preflights (
          preflight_id text primary key,
          job_id text not null references pilot_jobs on delete restrict,
          attempt_number integer not null check (attempt_number > 0),
          receipt_json text not null,
          created_at text not null,
          unique (job_id, attempt_number)
        )
        """
    )
    connection.execute(
        """
        create index pilot_preflights_job_idx
          on pilot_preflights (job_id, attempt_number)
        """
    )
    add_text_column_if_missing(
        connection,
        table="pilot_calls",
        column="preflight_id",
    )
    add_text_column_if_missing(
        connection,
        table="pilot_commits",
        column="review_snapshot_sha256",
    )
    for column in (
        "authorization_confirmation",
        "authorization_actor_id",
        "authorization_revision_id",
        "authorization_transcript_sha256",
        "authorization_at",
    ):
        add_text_column_if_missing(
            connection,
            table="pilot_jobs",
            column=column,
        )
    legacy_jobs = connection.execute(
        """
        select job_id, researcher_id, input_revision_id, created_at,
               provenance_json, status
        from pilot_jobs where authorization_actor_id = ''
        """
    ).fetchall()
    for legacy_job in legacy_jobs:
        provenance = _parse_json(str(legacy_job[4]), {})
        transcript_sha256 = (
            str(provenance.get("input_transcript_sha256", ""))
            if isinstance(provenance, dict)
            else ""
        )
        if not _SHA256.fullmatch(transcript_sha256):
            transcript_sha256 = ""
        connection.execute(
            """
            update pilot_jobs set authorization_actor_id = ?,
                   authorization_revision_id = ?,
                   authorization_transcript_sha256 = ?, authorization_at = ?
            where job_id = ?
            """,
            (
                legacy_job[1],
                legacy_job[2],
                transcript_sha256,
                legacy_job[3],
                legacy_job[0],
            ),
        )
        if legacy_job[5] in ("queued", "preflight", "running", "needs_review"):
            connection.execute(
                """
                update pilot_jobs set status = 'needs_attention',
                       error_code = 'legacy_authorization_unprovable',
                       error_message = 'This legacy run has no exact researcher egress confirmation'
                where job_id = ?
                """,
                (legacy_job[0],),
            )
    jobs = connection.execute(
        "select job_id, preflight_json from pilot_jobs where preflight_json != ''"
    ).fetchall()
    for job in jobs:
        preflight_id = f"tpf_{_sha256(str(job[0]) + chr(0) + str(job[1]))[:32]}"
        connection.execute(
            """
            insert into pilot_preflights (
              preflight_id, job_id, attempt_number, receipt_json, created_at
            )
            select ?, job_id, 1, preflight_json,
                   case when started_at != '' then started_at else created_at end
            from pilot_jobs where job_id = ?
            """,
            (preflight_id, job[0]),
        )
        connection.execute(
            """
            update pilot_calls set preflight_id = ?
            where job_id = ? and status != 'pending'
            """,
            (preflight_id, job[0]),
        )
    connection.execute(
        """
        update pilot_jobs set status = 'needs_attention',
               error_code = 'preflight_provenance_missing',
               error_message = 'A prior specialist call has no recoverable preflight provenance'
        where preflight_json = ''
          and status in ('running', 'needs_review')
          and exists (
            select 1 from pilot_calls c
            where c.job_id = pilot_jobs.job_id and c.status != 'pending'
          )
        """
    )
    previous_row_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        legacy_commits = connection.execute(
            """
            select job_id from pilot_commits
            where review_snapshot_sha256 = ''
            """
        ).fetchall()
        for commit in legacy_commits:
            rows = connection.execute(
                """
                select p.proposal_id, p.line_index, p.original_text,
                       p.proposed_text, p.changed, d.decision_id,
                       d.decision_version, d.action, d.edited_text
                from pilot_proposals p
                left join pilot_review_decisions d
                  on d.proposal_id = p.proposal_id
                 and d.decision_version = (
                   select max(latest.decision_version)
                   from pilot_review_decisions latest
                   where latest.proposal_id = p.proposal_id
                 )
                where p.job_id = ?
                order by p.line_index, p.proposal_id
                """,
                (commit["job_id"],),
            ).fetchall()
            connection.execute(
                """
                update pilot_commits set review_snapshot_sha256 = ?
                where job_id = ? and review_snapshot_sha256 = ''
                """,
                (
                    _review_snapshot_sha256_from_rows(rows),
                    commit["job_id"],
                ),
            )
    finally:
        connection.row_factory = previous_row_factory

    duplicate_publishers = connection.execute(
        """
        select source_id from pilot_commits
        where status = 'publishing'
        group by source_id having count(*) > 1
        """
    ).fetchall()
    if duplicate_publishers:
        source_ids = tuple(str(row[0]) for row in duplicate_publishers)
        placeholders = ",".join("?" for _ in source_ids)
        connection.execute(
            f"""
            update pilot_jobs set status = 'needs_attention',
                   error_code = 'legacy_commit_collision',
                   error_message = 'Multiple legacy revisions were publishing for this source'
            where job_id in (
              select job_id from pilot_commits
              where source_id in ({placeholders}) and status = 'publishing'
            )
            """,
            source_ids,
        )
    else:
        connection.execute(
            """
            create unique index pilot_one_publishing_commit_per_source_idx
            on pilot_commits (source_id) where status = 'publishing'
            """
        )


_MIGRATIONS = (
    Migration(1, "researcher-supervised transcript pilot", _migrate_v1),
    Migration(2, "pilot intake and preflight provenance", _migrate_v2),
)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _parse_json(value: str, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise TranscriptPilotStoreError("storage_invalid", "Stored transcript pilot JSON is invalid") from exc


def _contains_line_break(value: Any) -> bool:
    if isinstance(value, str):
        return "\n" in value or "\r" in value
    if isinstance(value, dict):
        return any(_contains_line_break(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_line_break(item) for item in value)
    return False


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
