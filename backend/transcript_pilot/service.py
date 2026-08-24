from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, cast
from uuid import uuid4

from backend.evidence.identifiers import (
    source_import_identity,
    transcript_evidence_identity,
)
from backend.professor_demo.provider import (
    SPECIALIST_SPECS,
    LunaProviderError,
    ProviderCallReceipt,
    SpecialistId,
    SpecialistResult,
)
from backend.professor_demo.service import merge_specialist_results
from backend.qualitative import QualitativeProjectDatabase
from backend.qualitative.research_reviews import ResearchReviewService
from backend.storage.evidence_catalog import EvidenceCatalog, EvidenceImportRecord
from backend.storage.evidence_text_blob_store import EvidenceTextBlobStore
from backend.storage.source_blob_store import SourceBlobStore
from backend.storage.study_store import StudyWorkspaceStore
from backend.transcript_pilot.privacy import enforce_egress_boundary
from backend.transcript_pilot.protocol import (
    PRODUCT_VERSION,
    PROTOCOL_VERSION,
    DataClassification,
    TranscriptChunk,
    canonical_transcript_lines,
    chunk_transcript,
    protocol_fingerprint,
)
from backend.transcript_pilot.provider import (
    LunaTranscriptClient,
    PilotProviderAmbiguousError,
    provider_contract,
    request_sha256,
)
from backend.transcript_pilot.store import (
    TranscriptPilotStore,
    TranscriptPilotStoreError,
)


SAMPLE_TRANSCRIPT = """[00:00] Q: Could you walk me through the delay?
[00:05] A: Um, I got there at nine—no, nine fifteen. [door closes]
[00:12] Q: What happened next?
[00:15] A: I... I waited about forty minutes. (long pause)"""


class TranscriptPilotError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


ClientFactory = Callable[[DataClassification], LunaTranscriptClient]


class TranscriptPilotService:
    def __init__(
        self,
        root: Path | str = "local_data",
        *,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.root = Path(root)
        self.store = TranscriptPilotStore(self.root)
        self.evidence_catalog = EvidenceCatalog(self.root)
        self.source_blobs = SourceBlobStore(self.root)
        self.text_blobs = EvidenceTextBlobStore(self.root)
        self.client_factory = client_factory or (
            lambda classification: LunaTranscriptClient(classification=classification)
        )

    def create_study(
        self,
        *,
        name: str,
        description: str,
        researcher_id: str,
        researcher_name: str,
        study_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name, "description": description}
        if study_id:
            payload["id"] = study_id
        try:
            study = StudyWorkspaceStore(self.root).create_study(payload)
            QualitativeProjectDatabase(self.root, study.id).initialize(
                researcher_id=researcher_id,
                researcher_name=researcher_name,
            )
            researcher = ResearchReviewService(self.root, study.id).read_researcher(
                researcher_id
            )
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise TranscriptPilotError(
                "study_setup_failed",
                "The study and researcher could not be initialized",
            ) from exc
        return {
            "study_id": study.id,
            "name": study.name,
            "description": study.description,
            "created_at": study.created_at,
            "researcher": {
                "researcher_id": researcher.researcher_id,
                "display_name": researcher.display_name,
                "role": researcher.role,
                "active": researcher.active,
            },
            "sources": self.store.list_sources(study.id),
        }

    def list_studies(self) -> list[dict[str, Any]]:
        studies: list[dict[str, Any]] = []
        for study in StudyWorkspaceStore(self.root).list_studies():
            sources = self.store.list_sources(study.id)
            researchers: list[dict[str, Any]] = []
            try:
                page = ResearchReviewService(self.root, study.id).list_researchers(
                    active=True,
                    limit=50,
                )
                researchers = [
                    {
                        "researcher_id": item.researcher_id,
                        "display_name": item.display_name,
                        "role": item.role,
                        "active": item.active,
                    }
                    for item in page.researchers
                ]
            except (FileNotFoundError, RuntimeError, ValueError):
                researchers = []
            studies.append(
                {
                    "study_id": study.id,
                    "name": study.name,
                    "description": study.description,
                    "created_at": study.created_at,
                    "researchers": researchers,
                    "sources": sources,
                }
            )
        return studies

    def import_source(
        self,
        *,
        study_id: str,
        researcher_id: str,
        source_filename: str,
        source_media_type: str,
        source_bytes: bytes,
        extracted_text: str,
        data_classification: DataClassification,
        authorization_basis: str,
        remote_egress_authorized: bool,
        contains_direct_identifiers: bool,
        protocol_version: str,
    ) -> dict[str, Any]:
        if protocol_version != PROTOCOL_VERSION:
            raise TranscriptPilotError(
                "protocol_version_unsupported",
                "Reload the pilot before importing with an older protocol",
            )
        if not source_bytes:
            raise TranscriptPilotError("source_empty", "Transcript file is empty")
        try:
            StudyWorkspaceStore(self.root).load_study(study_id)
            researcher = ResearchReviewService(self.root, study_id).read_researcher(
                researcher_id
            )
            if not researcher.active:
                raise ValueError("researcher is inactive")
            lines = canonical_transcript_lines(extracted_text)
            canonical_text = "\n".join(lines)
            privacy_findings = enforce_egress_boundary(
                transcript=canonical_text,
                classification=data_classification,
                contains_direct_identifiers=contains_direct_identifiers,
                remote_egress_authorized=remote_egress_authorized,
                authorization_basis=authorization_basis,
            )
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            message = str(exc).strip() or "Transcript intake failed"
            raise TranscriptPilotError("source_validation_failed", message) from exc

        transcript_identity = transcript_evidence_identity(canonical_text)
        safe_filename = _safe_filename(source_filename)
        source_blob_sha256 = hashlib.sha256(source_bytes).hexdigest()
        intake_request_sha256 = _json_sha256(
            {
                "study_id": study_id,
                "researcher_id": researcher_id,
                "source_filename": safe_filename,
                "source_media_type": source_media_type,
                "source_blob_sha256": source_blob_sha256,
                "transcript_sha256": transcript_identity.transcript_sha256,
                "data_classification": data_classification,
                "authorization_basis": authorization_basis.strip(),
                "protocol_version": PROTOCOL_VERSION,
            }
        )
        try:
            intake = self.store.reserve_intake(
                request_sha256=intake_request_sha256,
                study_id=study_id,
                researcher_id=researcher_id,
            )
            imported_at = str(intake["created_at"])
            self.source_blobs.store(source_bytes, source_blob_sha256)
            self.text_blobs.store(
                canonical_text,
                transcript_identity.transcript_sha256,
            )
            self.evidence_catalog.record_import(
                EvidenceImportRecord(
                    import_id=str(intake["import_id"]),
                    run_id=str(intake["run_id"]),
                    pipeline="transcript_revision_pilot_intake",
                    project_source_id=str(intake["source_id"]),
                    workspace_id=study_id,
                    source_id=transcript_identity.source_id,
                    source_filename=safe_filename,
                    source_media_type=source_media_type,
                    source_blob_sha256=source_blob_sha256,
                    transcript_revision_id=transcript_identity.transcript_revision_id,
                    transcript_sha256=transcript_identity.transcript_sha256,
                    parent_transcript_revision_id="",
                    imported_at=imported_at,
                )
            )
            source = self.store.create_source(
                {
                    "source_id": intake["source_id"],
                    "study_id": study_id,
                    "researcher_id": researcher_id,
                    "researcher_name": researcher.display_name,
                    "source_filename": safe_filename,
                    "source_media_type": source_media_type,
                    "source_blob_sha256": source_blob_sha256,
                    "original_transcript_sha256": transcript_identity.transcript_sha256,
                    "original_revision_id": transcript_identity.transcript_revision_id,
                    "data_classification": data_classification,
                    "authorization_basis": authorization_basis.strip(),
                    "privacy_scan": [finding.__dict__ for finding in privacy_findings],
                    "protocol_version": PROTOCOL_VERSION,
                    "line_count": len(lines),
                    "byte_count": len(canonical_text.encode("utf-8")),
                    "created_at": imported_at,
                }
            )
            self.store.complete_intake(
                request_sha256=intake_request_sha256,
                source_id=str(source["source_id"]),
            )
        except TranscriptPilotStoreError as exc:
            raise TranscriptPilotError(exc.code, exc.public_message) from exc
        except (OSError, RuntimeError, ValueError) as exc:
            raise TranscriptPilotError(
                "source_persistence_failed",
                "The transcript source could not be stored safely",
            ) from exc
        return self.load_source(str(source["source_id"]), include_active_text=True)

    def load_source(self, source_id: str, *, include_active_text: bool = False) -> dict[str, Any]:
        try:
            source = self.store.load_source(source_id)
            if include_active_text:
                revision = _revision(source, source["active_revision_id"])
                active_transcript = self.text_blobs.read_verified(
                    revision["transcript_sha256"]
                )
                chunks = chunk_transcript(active_transcript)
                source["active_transcript"] = active_transcript
                source["chunk_count"] = len(chunks)
                source["planned_call_count"] = len(chunks) * len(SPECIALIST_SPECS)
            return source
        except TranscriptPilotStoreError as exc:
            raise TranscriptPilotError(exc.code, exc.public_message) from exc
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise TranscriptPilotError(
                "source_unreadable",
                "The transcript source could not be verified",
            ) from exc

    def create_job(
        self,
        *,
        source_id: str,
        researcher_id: str,
        input_revision_id: str,
        idempotency_key: str,
        authorized_cost_usd: str,
        confirmation: str,
    ) -> dict[str, Any]:
        code_commit, code_dirty = _current_code_identity()
        if not _code_identity_is_verified(code_commit, code_dirty):
            raise TranscriptPilotError(
                "executable_contract_unverified",
                "Transcript egress requires a clean, committed pilot checkout",
            )
        if confirmation != "authorize-four-specialists-per-chunk":
            raise TranscriptPilotError(
                "egress_confirmation_invalid",
                "Explicit four-specialist egress confirmation is required",
            )
        source = self.load_source(source_id)
        if source["researcher_id"] != researcher_id:
            raise TranscriptPilotError(
                "researcher_mismatch",
                "The researcher does not own this pilot source",
            )
        revision = _revision(source, input_revision_id)
        try:
            transcript = self.text_blobs.read_verified(revision["transcript_sha256"])
            enforce_egress_boundary(
                transcript=transcript,
                classification=cast(DataClassification, source["data_classification"]),
                contains_direct_identifiers=False,
                remote_egress_authorized=(
                    confirmation == "authorize-four-specialists-per-chunk"
                ),
                authorization_basis=str(source["authorization_basis"]),
            )
            chunks = chunk_transcript(transcript)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise TranscriptPilotError(
                "source_unreadable",
                "The active transcript revision could not be verified",
            ) from exc
        contract = provider_contract()
        request_payload = {
            "source_id": source_id,
            "input_revision_id": input_revision_id,
            "input_transcript_sha256": revision["transcript_sha256"],
            "researcher_id": researcher_id,
            "authorized_cost_usd": authorized_cost_usd,
            "authorization_confirmation": confirmation,
            "protocol_sha256": protocol_fingerprint(),
            "provider_contract": contract,
        }
        request_digest = _json_sha256(request_payload)
        authorization_digest = _json_sha256(
            {
                "source_id": source_id,
                "revision_id": input_revision_id,
                "transcript_sha256": revision["transcript_sha256"],
                "classification": source["data_classification"],
                "authorization_basis": source["authorization_basis"],
                "authorization_actor_id": researcher_id,
                "authorization_confirmation": confirmation,
                "protocol_version": source["protocol_version"],
            }
        )
        provenance = {
            **contract,
            "protocol_sha256": protocol_fingerprint(),
            "authorization_sha256": authorization_digest,
            "authorization_revision_id": input_revision_id,
            "authorization_transcript_sha256": revision["transcript_sha256"],
            "authorization_actor_id": researcher_id,
            "authorization_confirmation": confirmation,
            "input_transcript_sha256": revision["transcript_sha256"],
            "source_blob_sha256": source["source_blob_sha256"],
            "code_commit": code_commit,
            "code_dirty": code_dirty,
            "product_version": PRODUCT_VERSION,
        }
        try:
            return self.store.create_job(
                source_id=source_id,
                input_revision_id=input_revision_id,
                researcher_id=researcher_id,
                idempotency_key=idempotency_key,
                request_sha256=request_digest,
                authorized_cost_usd=authorized_cost_usd,
                authorization_confirmation=confirmation,
                authorization_transcript_sha256=revision["transcript_sha256"],
                chunks=chunks,
                provenance=provenance,
            )
        except TranscriptPilotStoreError as exc:
            raise TranscriptPilotError(exc.code, exc.public_message) from exc

    def execute_job(self, job_id: str) -> None:
        try:
            if not self.store.claim_job(job_id):
                return
            job = self.store.load_job(job_id)
            classification = cast(DataClassification, job["source"]["data_classification"])
            client = self.client_factory(classification)
            chunks = [_chunk_from_payload(item) for item in job["chunks"]]

            try:
                preflight = client.preflight_job(
                    chunks,
                    authorized_cost_usd=job["authorized_cost_usd"],
                ).model_dump(mode="json")
            except LunaProviderError as exc:
                self.store.fail_job(job_id, exc.code, exc.public_message)
                return
            preflight_id = self.store.set_preflight(job_id, preflight)

            if self.store.cancellation_requested(job_id):
                self.store.mark_cancelled(job_id)
                return

            for chunk in chunks:
                current = self.store.load_job(job_id)
                chunk_payload = current["chunks"][chunk.chunk_index]
                for call in chunk_payload["calls"]:
                    if call["status"] != "pending":
                        continue
                    if self.store.cancellation_requested(job_id):
                        self.store.mark_cancelled(job_id)
                        return
                    spec = _specialist_spec(str(call["specialist_id"]))
                    fingerprint = request_sha256(client, spec, list(chunk.lines))
                    self.store.begin_call(
                        job_id,
                        chunk.chunk_index,
                        spec.specialist_id,
                        fingerprint,
                        preflight_id,
                    )
                    receipt: ProviderCallReceipt | None = None
                    try:
                        result, receipt = client.call_specialist(spec, list(chunk.lines))
                        _validate_product_result(result, receipt, len(chunk.lines))
                        self.store.complete_call(
                            job_id=job_id,
                            chunk_index=chunk.chunk_index,
                            specialist_id=spec.specialist_id,
                            status="valid",
                            result=result.model_dump(mode="json"),
                            receipt=receipt.model_dump(mode="json"),
                        )
                    except PilotProviderAmbiguousError as exc:
                        receipt = exc.receipt or _unknown_receipt()
                        self.store.complete_call(
                            job_id=job_id,
                            chunk_index=chunk.chunk_index,
                            specialist_id=spec.specialist_id,
                            status="ambiguous",
                            result=None,
                            receipt=receipt.model_dump(mode="json"),
                            error_code=exc.code,
                            error_message=exc.public_message,
                        )
                        return
                    except LunaProviderError as exc:
                        receipt = exc.receipt or _unknown_receipt()
                        self.store.complete_call(
                            job_id=job_id,
                            chunk_index=chunk.chunk_index,
                            specialist_id=spec.specialist_id,
                            status="error",
                            result=None,
                            receipt=receipt.model_dump(mode="json"),
                            error_code=exc.code,
                            error_message=exc.public_message,
                        )
                    except ValueError:
                        self.store.complete_call(
                            job_id=job_id,
                            chunk_index=chunk.chunk_index,
                            specialist_id=spec.specialist_id,
                            status="error",
                            result=None,
                            receipt=(receipt or _unknown_receipt()).model_dump(mode="json"),
                            error_code="specialist_result_invalid",
                            error_message=f"{spec.label} failed strict pilot validation",
                        )

                refreshed = self.store.load_job(job_id)
                refreshed_chunk = refreshed["chunks"][chunk.chunk_index]
                valid_calls = [
                    item for item in refreshed_chunk["calls"] if item["status"] == "valid"
                ]
                if len(valid_calls) != len(SPECIALIST_SPECS):
                    self.store.finish_chunk(
                        job_id,
                        chunk.chunk_index,
                        merged_lines=None,
                        error_code="chunk_specialists_incomplete",
                        error_message="One or more chunk specialists failed validation",
                    )
                    continue
                results = _results_from_calls(valid_calls)
                try:
                    merged_text = merge_specialist_results(list(chunk.lines), results)
                except ValueError:
                    self.store.finish_chunk(
                        job_id,
                        chunk.chunk_index,
                        merged_lines=None,
                        error_code="chunk_merge_failed",
                        error_message="Validated chunk results could not be merged safely",
                    )
                else:
                    self.store.finish_chunk(
                        job_id,
                        chunk.chunk_index,
                        merged_lines=merged_text.splitlines(),
                    )

            finished = self.store.load_job(job_id)
            if any(item["status"] != "completed" for item in finished["chunks"]):
                self.store.fail_job(
                    job_id,
                    "specialist_run_failed",
                    "One or more four-specialist chunks did not complete safely",
                )
                return
            if not finished["usage"]["accounting_complete"]:
                self.store.fail_job(
                    job_id,
                    "usage_accounting_incomplete",
                    "Provider usage accounting was incomplete",
                )
                return
            if self.store.cancellation_requested(job_id):
                self.store.mark_cancelled(job_id)
                return
            proposals = _build_proposals(finished)
            self.store.create_proposals(job_id, proposals)
        except TranscriptPilotStoreError as exc:
            self._fail_or_cancel_job(job_id, exc.code, exc.public_message)
        except Exception:
            self._fail_or_cancel_job(
                job_id,
                "worker_failed",
                "The local transcript worker stopped unexpectedly",
            )

    def recover_interrupted_jobs(self) -> list[str]:
        queued_job_ids = self.store.recover_interrupted_jobs()
        code_commit, code_dirty = _current_code_identity()
        current_contract = {
            **provider_contract(),
            "protocol_sha256": protocol_fingerprint(),
            "code_commit": code_commit,
            "code_dirty": code_dirty,
        }
        compatible: list[str] = []
        for job_id in queued_job_ids:
            job = self.store.load_job(job_id)
            provenance = job.get("provenance")
            contract_matches = isinstance(provenance, dict) and all(
                provenance.get(key) == value
                for key, value in current_contract.items()
            )
            clean_code_identity = _code_identity_is_verified(
                code_commit,
                code_dirty,
            )
            if contract_matches and clean_code_identity:
                compatible.append(job_id)
            else:
                self.store.mark_recovery_contract_attention(job_id)
        return compatible

    def _fail_or_cancel_job(self, job_id: str, code: str, message: str) -> None:
        try:
            self.store.fail_job(job_id, code, message)
        except Exception:
            pass

    def save_decision(
        self,
        *,
        job_id: str,
        proposal_id: str,
        researcher_id: str,
        action: str,
        edited_text: str,
        expected_decision_version: int,
    ) -> dict[str, Any]:
        if action not in {"accept", "keep_original", "edit"}:
            raise TranscriptPilotError("decision_invalid", "Review decision is invalid")
        try:
            return self.store.save_decision(
                job_id=job_id,
                proposal_id=proposal_id,
                researcher_id=researcher_id,
                action=cast(Any, action),
                edited_text=edited_text,
                expected_decision_version=expected_decision_version,
            )
        except TranscriptPilotStoreError as exc:
            raise TranscriptPilotError(exc.code, exc.public_message) from exc

    def commit(
        self,
        *,
        job_id: str,
        researcher_id: str,
        expected_active_revision_id: str,
    ) -> dict[str, Any]:
        proposals, job = self.store.review_material(job_id)
        if job["researcher_id"] != researcher_id:
            raise TranscriptPilotError("researcher_mismatch", "Researcher identity does not match")
        final_lines: list[str] = []
        for proposal in proposals:
            if not proposal["changed"]:
                final_lines.append(str(proposal["original_text"]))
                continue
            decision = proposal["decision"]
            if decision is None:
                raise TranscriptPilotError(
                    "review_incomplete",
                    "Every changed line needs a researcher decision before commit",
                )
            action = decision["action"]
            if action == "accept":
                final_lines.append(str(proposal["proposed_text"]))
            elif action == "keep_original":
                final_lines.append(str(proposal["original_text"]))
            elif action == "edit":
                final_lines.append(str(decision["edited_text"]))
            else:
                raise TranscriptPilotError("decision_invalid", "Stored review decision is invalid")
        final_transcript = "\n".join(final_lines)
        try:
            if canonical_transcript_lines(final_transcript) != final_lines:
                raise ValueError("reviewed transcript changed during canonicalization")
        except ValueError as exc:
            raise TranscriptPilotError(
                "reviewed_transcript_invalid",
                "The reviewed transcript does not satisfy the versioned line protocol",
            ) from exc
        identity = transcript_evidence_identity(final_transcript)
        if identity.transcript_revision_id == expected_active_revision_id:
            raise TranscriptPilotError(
                "no_revision_change",
                "The reviewed transcript matches the active revision; there is nothing to commit",
            )
        source = job["source"]
        existing_commit = job.get("commit")
        reserved_import_id = (
            str(existing_commit["import_id"])
            if isinstance(existing_commit, dict)
            else source_import_identity(
                final_transcript,
                source_bytes=final_transcript.encode("utf-8"),
                source_media_type="text/plain",
                project_source_id=source["source_id"],
            ).import_id
        )
        try:
            commit = self.store.prepare_commit(
                job_id=job_id,
                researcher_id=researcher_id,
                expected_active_revision_id=expected_active_revision_id,
                revision_id=identity.transcript_revision_id,
                transcript_sha256=identity.transcript_sha256,
                import_id=reserved_import_id,
                review_snapshot_sha256=str(job["review_snapshot_sha256"]),
            )
            if commit["status"] == "completed":
                return self.store.load_job(job_id)
            self.text_blobs.store(final_transcript, identity.transcript_sha256)
            self.evidence_catalog.record_import(
                EvidenceImportRecord(
                    import_id=commit["import_id"],
                    run_id=job_id,
                    pipeline="transcript_revision_pilot",
                    project_source_id=commit["source_id"],
                    workspace_id=job["study_id"],
                    source_id=identity.source_id,
                    source_filename=source["source_filename"],
                    source_media_type=source["source_media_type"],
                    source_blob_sha256=source["source_blob_sha256"],
                    transcript_revision_id=commit["revision_id"],
                    transcript_sha256=commit["transcript_sha256"],
                    parent_transcript_revision_id=commit["parent_revision_id"],
                    imported_at=commit["created_at"],
                )
            )
            return self.store.finalize_commit(job_id)
        except TranscriptPilotStoreError as exc:
            raise TranscriptPilotError(exc.code, exc.public_message) from exc
        except (OSError, RuntimeError, ValueError) as exc:
            raise TranscriptPilotError(
                "commit_publication_failed",
                "The reviewed revision was preserved for safe commit recovery",
            ) from exc

    def restore_original(
        self,
        *,
        source_id: str,
        researcher_id: str,
        expected_active_revision_id: str,
    ) -> dict[str, Any]:
        try:
            return self.store.restore_original(
                source_id=source_id,
                researcher_id=researcher_id,
                expected_active_revision_id=expected_active_revision_id,
            )
        except TranscriptPilotStoreError as exc:
            raise TranscriptPilotError(exc.code, exc.public_message) from exc

    def committed_transcript(self, job_id: str) -> str:
        job = self.store.load_job(job_id)
        commit = job["commit"]
        if job["status"] != "committed" or commit is None:
            raise TranscriptPilotError("commit_not_found", "No committed transcript exists for this job")
        return self.text_blobs.read_verified(commit["transcript_sha256"])

    def receipt_export(self, job_id: str) -> dict[str, Any]:
        job = self.store.load_job(job_id)
        return {
            "format": "nlp-skill-agents.transcript-pilot-receipt.v1",
            "generated_at": _utc_now(),
            "job": job,
            "audit_events": self.store.audit_events(job_id=job_id),
            "boundary": {
                "accepted_input": ["synthetic", "authorized-deidentified"],
                "identifiable_data_supported": False,
                "autonomous_planner": False,
                "remote_calls_per_chunk": 4,
                "human_commit_required": True,
            },
        }


class TranscriptPilotRuntime:
    """One daemon worker for the local single-user pilot."""

    def __init__(self, service: TranscriptPilotService) -> None:
        self.service = service
        self._queue: queue.Queue[str] = queue.Queue()
        self._scheduled: set[str] = set()
        self._lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._worker,
            name="transcript-pilot-worker",
            daemon=True,
        )
        self._thread.start()

    def recover(self) -> None:
        queued_job_ids = self.service.recover_interrupted_jobs()
        for commit in self.service.store.publishing_commits():
            try:
                self.service.commit(
                    job_id=str(commit["job_id"]),
                    researcher_id=str(commit["created_by"]),
                    expected_active_revision_id=str(commit["parent_revision_id"]),
                )
            except TranscriptPilotError as exc:
                self.service.store.mark_commit_recovery_attention(
                    str(commit["job_id"]),
                    code="commit_recovery_failed",
                    message=exc.public_message,
                )
        for job_id in queued_job_ids:
            self.submit(job_id)

    def submit(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._scheduled:
                return
            self._scheduled.add(job_id)
            self._queue.put(job_id)

    def _worker(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                self.service.execute_job(job_id)
            finally:
                with self._lock:
                    self._scheduled.discard(job_id)
                self._queue.task_done()


def _build_proposals(job: dict[str, Any]) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    now = _utc_now()
    for chunk in job["chunks"]:
        valid_calls = [item for item in chunk["calls"] if item["status"] == "valid"]
        results = _results_from_calls(valid_calls)
        for local_index, (original, proposed) in enumerate(
            zip(chunk["lines"], chunk["merged_lines"], strict=True)
        ):
            global_index = int(chunk["start_line_index"]) + local_index
            evidence: dict[str, Any] = {}
            for specialist_id, result in results.items():
                line = next(item for item in result.lines if item.line_index == local_index)
                evidence[specialist_id] = line.model_dump(mode="json")
            proposals.append(
                {
                    "proposal_id": f"tpp_{uuid4().hex}",
                    "line_index": global_index,
                    "original_text": original,
                    "proposed_text": proposed,
                    "changed": original != proposed,
                    "evidence": evidence,
                    "created_at": now,
                }
            )
    return proposals


def _results_from_calls(calls: list[dict[str, Any]]) -> dict[SpecialistId, SpecialistResult]:
    results: dict[SpecialistId, SpecialistResult] = {}
    for call in calls:
        spec = _specialist_spec(str(call["specialist_id"]))
        result = spec.result_model.model_validate(call["result"], strict=True)
        results[spec.specialist_id] = cast(SpecialistResult, result)
    return results


def _validate_product_result(
    result: SpecialistResult,
    receipt: ProviderCallReceipt,
    line_count: int,
) -> None:
    indexes = [item.line_index for item in result.lines]
    if len(indexes) != line_count or set(indexes) != set(range(line_count)):
        raise ValueError("specialist result does not cover the chunk exactly once")
    if _contains_line_break(result.model_dump(mode="json")):
        raise ValueError("specialist result contains a line-breaking field")
    if receipt.router_attempt_count != 1:
        raise ValueError("pilot requires exactly one proven provider attempt")
    if receipt.cache_hit is not False:
        raise ValueError("pilot requires a proven non-cached response")
    if receipt.router_pipeline_stages:
        raise ValueError("pilot forbids router pipeline stages")
    if receipt.reasoning_tokens != 0:
        raise ValueError("pilot requires zero reasoning tokens")
    if not receipt.accounting_complete:
        raise ValueError("pilot usage accounting is incomplete")


def _contains_line_break(value: Any) -> bool:
    if isinstance(value, str):
        return "\n" in value or "\r" in value
    if isinstance(value, dict):
        return any(_contains_line_break(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_line_break(item) for item in value)
    return False


def _chunk_from_payload(payload: dict[str, Any]) -> TranscriptChunk:
    return TranscriptChunk(
        chunk_index=int(payload["chunk_index"]),
        start_line_index=int(payload["start_line_index"]),
        lines=tuple(str(line) for line in payload["lines"]),
    )


def _specialist_spec(specialist_id: str):
    for spec in SPECIALIST_SPECS:
        if spec.specialist_id == specialist_id:
            return spec
    raise TranscriptPilotStoreError("specialist_invalid", "Stored specialist identity is invalid")


def _unknown_receipt() -> ProviderCallReceipt:
    return ProviderCallReceipt(
        model_requested=provider_contract()["model"],
        endpoint_requested=provider_contract()["endpoint"],
        generation_id=None,
        model_returned=None,
        provider_returned=None,
        router_attempt_count=None,
        cache_hit=None,
        finish_reason=None,
        prompt_tokens=None,
        completion_tokens=None,
        reasoning_tokens=None,
        total_tokens=None,
        cost_usd=None,
        accounting_complete=False,
        latency_ms=0,
    )


def _revision(source: dict[str, Any], revision_id: str) -> dict[str, Any]:
    for revision in source.get("revisions", []):
        if revision["revision_id"] == revision_id:
            return revision
    raise TranscriptPilotError(
        "revision_not_found",
        "Transcript revision does not belong to this source",
    )


def _safe_filename(value: str) -> str:
    name = Path(value or "transcript.txt").name.strip()
    if not name or name in {".", ".."}:
        return "transcript.txt"
    return name[:240]


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _current_code_identity() -> tuple[str, str]:
    return (
        os.environ.get("TRANSCRIPT_PILOT_CODE_COMMIT", "unknown").strip(),
        os.environ.get("TRANSCRIPT_PILOT_CODE_DIRTY", "unknown").strip(),
    )


def _code_identity_is_verified(code_commit: str, code_dirty: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", code_commit)) and (
        code_dirty == "false"
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
