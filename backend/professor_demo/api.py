from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from backend.professor_demo.service import (
    SAMPLE_TRANSCRIPT,
    ProfessorDemoError,
    ProfessorDemoRun,
    ProfessorDemoService,
)


router = APIRouter(prefix="/api/professor-demo", tags=["professor-demo"])


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ProfessorDemoRunRequest(StrictRequest):
    source: Literal["synthetic-demo"]
    transcript: str


class ProfessorDemoAcceptRequest(StrictRequest):
    confirmation: Literal["accept-generated-demo-revision"]


class ProfessorDemoRevertRequest(StrictRequest):
    confirmation: Literal["restore-original-demo-revision"]


class ProfessorDemoSampleResponse(BaseModel):
    source: Literal["synthetic-demo"]
    transcript: str


@router.get("/sample", response_model=ProfessorDemoSampleResponse)
def get_professor_demo_sample() -> ProfessorDemoSampleResponse:
    return ProfessorDemoSampleResponse(
        source="synthetic-demo",
        transcript=SAMPLE_TRANSCRIPT,
    )


@router.post("/runs", response_model=ProfessorDemoRun)
def create_professor_demo_run(request: ProfessorDemoRunRequest) -> ProfessorDemoRun:
    try:
        return _service().run(request.transcript)
    except ProfessorDemoError as exc:
        status_code = 422 if exc.code == "transcript_invalid" else 503
        raise HTTPException(status_code=status_code, detail=exc.public_message) from exc


@router.get("/runs/{run_id}", response_model=ProfessorDemoRun)
def get_professor_demo_run(run_id: str) -> ProfessorDemoRun:
    try:
        return _service().store.load(run_id)
    except ProfessorDemoError as exc:
        raise HTTPException(status_code=404, detail=exc.public_message) from exc


@router.post("/runs/{run_id}/accept", response_model=ProfessorDemoRun)
def accept_professor_demo_run(
    run_id: str,
    request: ProfessorDemoAcceptRequest,
) -> ProfessorDemoRun:
    del request
    try:
        return _service().store.accept(run_id)
    except ProfessorDemoError as exc:
        status_code = 404 if exc.code == "run_not_found" else 409
        raise HTTPException(status_code=status_code, detail=exc.public_message) from exc


@router.post("/runs/{run_id}/revert", response_model=ProfessorDemoRun)
def revert_professor_demo_run(
    run_id: str,
    request: ProfessorDemoRevertRequest,
) -> ProfessorDemoRun:
    del request
    try:
        return _service().store.revert(run_id)
    except ProfessorDemoError as exc:
        status_code = 404 if exc.code == "run_not_found" else 409
        raise HTTPException(status_code=status_code, detail=exc.public_message) from exc


@router.get("/revisions/latest", response_model=ProfessorDemoRun)
def get_latest_professor_demo_revision() -> ProfessorDemoRun:
    try:
        return _service().store.latest()
    except ProfessorDemoError as exc:
        raise HTTPException(status_code=404, detail=exc.public_message) from exc


def _service() -> ProfessorDemoService:
    local_data_root = Path(os.environ.get("NLP_SKILL_AGENTS_DATA_DIR", "local_data"))
    return ProfessorDemoService(local_data_root)

