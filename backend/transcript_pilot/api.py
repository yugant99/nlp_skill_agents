from __future__ import annotations

import json
import os
import tempfile
import threading
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.analysis.transcripts import extract_transcript_text
from backend.transcript_pilot.protocol import (
    DEFAULT_AUTHORIZED_COST_USD,
    GLOBAL_COST_CEILING_USD,
    MAX_TRANSCRIPT_BYTES,
    PROTOCOL_VERSION,
)
from backend.transcript_pilot.provider import provider_contract
from backend.transcript_pilot.service import (
    SAMPLE_TRANSCRIPT,
    TranscriptPilotError,
    TranscriptPilotRuntime,
    TranscriptPilotService,
)
from backend.transcript_pilot.store import TranscriptPilotStoreError


router = APIRouter(prefix="/api/transcript-pilot", tags=["transcript-pilot"])
MAX_SOURCE_FILE_BYTES = 5_000_000
_RUNTIME_LOCK = threading.Lock()


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StudyCreateRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    researcher_id: str = Field(min_length=3, max_length=96)
    researcher_name: str = Field(min_length=1, max_length=160)
    study_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9-]{0,95}$",
    )


class JobCreateRequest(StrictRequest):
    researcher_id: str
    input_revision_id: str
    idempotency_key: str = Field(min_length=8, max_length=128)
    authorized_cost_usd: str = DEFAULT_AUTHORIZED_COST_USD
    confirmation: Literal["authorize-four-specialists-per-chunk"]


class CancelRequest(StrictRequest):
    researcher_id: str
    confirmation: Literal["cancel-transcript-pilot-job"]


class DecisionRequest(StrictRequest):
    researcher_id: str
    action: Literal["accept", "keep_original", "edit"]
    edited_text: str = Field(default="", max_length=500)
    expected_decision_version: Annotated[int, Field(strict=True, ge=0)]


class CommitRequest(StrictRequest):
    researcher_id: str
    expected_active_revision_id: str
    confirmation: Literal["create-supervised-child-revision"]


class RestoreRequest(StrictRequest):
    researcher_id: str
    expected_active_revision_id: str
    confirmation: Literal["restore-immutable-original"]


@router.on_event("startup")
def recover_transcript_pilot_jobs() -> None:
    _runtime().recover()


@router.get("/config")
def get_transcript_pilot_config() -> dict:
    return {
        "product": "Researcher-Supervised Transcript Revision Pilot",
        "protocol_version": PROTOCOL_VERSION,
        "provider_contract": provider_contract(),
        "default_authorized_cost_usd": DEFAULT_AUTHORIZED_COST_USD,
        "global_cost_ceiling_usd": GLOBAL_COST_CEILING_USD,
        "max_transcript_bytes": MAX_TRANSCRIPT_BYTES,
        "max_source_file_bytes": MAX_SOURCE_FILE_BYTES,
        "supported_formats": [".txt", ".docx"],
        "data_classifications": ["synthetic", "authorized-deidentified"],
        "identifiable_data_supported": False,
        "remote_calls_per_chunk": 4,
    }


@router.get("/sample")
def get_transcript_pilot_sample() -> dict:
    return {
        "source_filename": "professor-synthetic-sample.txt",
        "data_classification": "synthetic",
        "transcript": SAMPLE_TRANSCRIPT,
    }


@router.get("/studies")
def list_transcript_pilot_studies() -> dict:
    return {"studies": _service().list_studies()}


@router.post("/studies", status_code=status.HTTP_201_CREATED)
def create_transcript_pilot_study(request: StudyCreateRequest) -> dict:
    try:
        study = _service().create_study(**request.model_dump())
    except TranscriptPilotError as exc:
        _raise_http_error(exc)
    return {"study": study}


@router.get("/studies/{study_id}/sources")
def list_transcript_pilot_sources(study_id: str) -> dict:
    try:
        return {"sources": _service().store.list_sources(study_id)}
    except TranscriptPilotStoreError as exc:
        _raise_http_error(TranscriptPilotError(exc.code, exc.public_message))


@router.post(
    "/studies/{study_id}/sources",
    status_code=status.HTTP_201_CREATED,
)
async def import_transcript_pilot_source(
    study_id: str,
    file: UploadFile = File(...),
    researcher_id: str = Form(...),
    data_classification: Literal["synthetic", "authorized-deidentified"] = Form(...),
    authorization_basis: str = Form(..., min_length=12, max_length=1000),
    remote_egress_authorized: bool = Form(...),
    contains_direct_identifiers: bool = Form(...),
    protocol_version: str = Form(...),
) -> dict:
    try:
        extracted_text, source_bytes, source_media_type = await _extract_upload(file)
        source = _service().import_source(
            study_id=study_id,
            researcher_id=researcher_id,
            source_filename=file.filename or "transcript.txt",
            source_media_type=source_media_type,
            source_bytes=source_bytes,
            extracted_text=extracted_text,
            data_classification=data_classification,
            authorization_basis=authorization_basis,
            remote_egress_authorized=remote_egress_authorized,
            contains_direct_identifiers=contains_direct_identifiers,
            protocol_version=protocol_version,
        )
    except TranscriptPilotError as exc:
        _raise_http_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"source": source}


@router.get("/sources/{source_id}")
def get_transcript_pilot_source(source_id: str) -> dict:
    try:
        return {"source": _service().load_source(source_id, include_active_text=True)}
    except TranscriptPilotError as exc:
        _raise_http_error(exc)


@router.post(
    "/sources/{source_id}/jobs",
    status_code=status.HTTP_202_ACCEPTED,
)
def create_transcript_pilot_job(source_id: str, request: JobCreateRequest) -> dict:
    payload = request.model_dump(exclude={"confirmation"})
    try:
        job = _service().create_job(source_id=source_id, **payload)
        if job["status"] == "queued":
            _runtime().submit(job["job_id"])
    except TranscriptPilotError as exc:
        _raise_http_error(exc)
    return {"job": job}


@router.get("/jobs")
def list_transcript_pilot_jobs(
    source_id: str | None = Query(default=None),
) -> dict:
    try:
        return {"jobs": _service().store.list_jobs(source_id=source_id)}
    except TranscriptPilotStoreError as exc:
        _raise_http_error(TranscriptPilotError(exc.code, exc.public_message))


@router.get("/jobs/{job_id}")
def get_transcript_pilot_job(job_id: str) -> dict:
    try:
        job = _service().store.load_job(job_id)
        if job["status"] == "queued":
            _runtime().submit(job_id)
        return {"job": job}
    except TranscriptPilotStoreError as exc:
        _raise_http_error(TranscriptPilotError(exc.code, exc.public_message))


@router.post("/jobs/{job_id}/cancel")
def cancel_transcript_pilot_job(job_id: str, request: CancelRequest) -> dict:
    try:
        return {
            "job": _service().store.request_cancel(job_id, request.researcher_id)
        }
    except TranscriptPilotStoreError as exc:
        _raise_http_error(TranscriptPilotError(exc.code, exc.public_message))


@router.put("/jobs/{job_id}/proposals/{proposal_id}/decision")
def save_transcript_pilot_decision(
    job_id: str,
    proposal_id: str,
    request: DecisionRequest,
) -> dict:
    try:
        job = _service().save_decision(
            job_id=job_id,
            proposal_id=proposal_id,
            **request.model_dump(),
        )
    except TranscriptPilotError as exc:
        _raise_http_error(exc)
    return {"job": job}


@router.post("/jobs/{job_id}/commit")
def commit_transcript_pilot_job(job_id: str, request: CommitRequest) -> dict:
    payload = request.model_dump(exclude={"confirmation"})
    try:
        job = _service().commit(job_id=job_id, **payload)
    except TranscriptPilotError as exc:
        _raise_http_error(exc)
    return {"job": job}


@router.post("/sources/{source_id}/restore-original")
def restore_transcript_pilot_original(source_id: str, request: RestoreRequest) -> dict:
    payload = request.model_dump(exclude={"confirmation"})
    try:
        source = _service().restore_original(
            source_id=source_id,
            **payload,
        )
    except TranscriptPilotError as exc:
        _raise_http_error(exc)
    return {"source": source}


@router.get("/jobs/{job_id}/exports/transcript.txt")
def export_transcript_pilot_text(job_id: str) -> Response:
    try:
        transcript = _service().committed_transcript(job_id)
    except TranscriptPilotError as exc:
        _raise_http_error(exc)
    return Response(
        content=transcript + "\n",
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{job_id}-accepted-transcript.txt"'
        },
    )


@router.get("/jobs/{job_id}/exports/receipt.json")
def export_transcript_pilot_receipt(job_id: str) -> JSONResponse:
    try:
        receipt = _service().receipt_export(job_id)
    except (TranscriptPilotError, TranscriptPilotStoreError) as exc:
        if isinstance(exc, TranscriptPilotStoreError):
            exc = TranscriptPilotError(exc.code, exc.public_message)
        _raise_http_error(exc)
    return JSONResponse(
        content=receipt,
        headers={
            "Content-Disposition": f'attachment; filename="{job_id}-audit-receipt.json"'
        },
    )


async def _extract_upload(file: UploadFile) -> tuple[str, bytes, str]:
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in {".txt", ".docx"}:
        raise ValueError("Only TXT and DOCX transcript files are supported")
    source_bytes = await file.read(MAX_SOURCE_FILE_BYTES + 1)
    if len(source_bytes) > MAX_SOURCE_FILE_BYTES:
        raise ValueError("Transcript source file exceeds the 5 MB pilot limit")
    if not source_bytes:
        raise ValueError("Transcript source file is empty")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temporary:
        temporary.write(source_bytes)
        temporary_path = Path(temporary.name)
    try:
        extracted = extract_transcript_text(temporary_path)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("Transcript file could not be decoded as valid TXT or DOCX") from exc
    finally:
        temporary_path.unlink(missing_ok=True)
    return extracted, source_bytes, file.content_type or "application/octet-stream"


def _raise_http_error(exc: TranscriptPilotError):
    not_found = {
        "source_not_found",
        "revision_not_found",
        "job_not_found",
        "proposal_not_found",
        "commit_not_found",
    }
    conflicts = {
        "idempotency_conflict",
        "stale_source_revision",
        "stale_review_decision",
        "job_state_conflict",
        "call_state_conflict",
        "review_incomplete",
        "no_revision_change",
        "commit_conflict",
    }
    if exc.code in not_found:
        status_code = 404
    elif exc.code in conflicts:
        status_code = 409
    elif exc.code.startswith("provider_") or exc.code.startswith("preflight_"):
        status_code = 503
    else:
        status_code = 422
    raise HTTPException(status_code=status_code, detail=exc.public_message) from exc


@lru_cache(maxsize=4)
def _service_for_root(root_text: str) -> TranscriptPilotService:
    return TranscriptPilotService(Path(root_text))


@lru_cache(maxsize=4)
def _runtime_for_root(root_text: str) -> TranscriptPilotRuntime:
    with _RUNTIME_LOCK:
        return TranscriptPilotRuntime(_service_for_root(root_text))


def _root_text() -> str:
    return str(Path(os.environ.get("NLP_SKILL_AGENTS_DATA_DIR", "local_data")).resolve())


def _service() -> TranscriptPilotService:
    return _service_for_root(_root_text())


def _runtime() -> TranscriptPilotRuntime:
    return _runtime_for_root(_root_text())
