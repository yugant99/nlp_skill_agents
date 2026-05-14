from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.analysis.diagnostics import analyze_transcript_quality
from backend.analysis.pipeline import execute_analysis
from backend.analysis.skill_packs import (
    SkillPack,
    SkillPackValidationError,
    load_skill_pack,
    parse_skill_pack,
    parse_skill_pack_document,
)
from backend.analysis.transcripts import StudyConfig, extract_transcript_text
from backend.storage.local_store import LocalRunStore, StoredRun


app = FastAPI(title="NLP Skill Agents", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TextRunRequest(BaseModel):
    source_filename: str = Field(default="pasted_transcript.txt", min_length=1)
    content: str = Field(min_length=1)
    config: dict = Field(default_factory=dict)


class SkillPackTextRequest(BaseModel):
    filename: str = Field(default="skill_pack.json", min_length=1)
    content: str = Field(min_length=1)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "storage": "local"}


@app.get("/api/skill-packs/default")
def default_skill_pack() -> dict:
    return load_skill_pack("default_transcript_metrics").raw


@app.post("/api/skill-packs/validate")
def validate_skill_pack(payload: dict) -> dict:
    try:
        pack = parse_skill_pack(payload)
    except SkillPackValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"valid": True, "skill_pack": _skill_pack_summary(pack)}


@app.post("/api/skill-packs/validate-text")
def validate_skill_pack_text(request: SkillPackTextRequest) -> dict:
    try:
        payload = parse_skill_pack_document(request.content, request.filename)
        pack = parse_skill_pack(payload)
    except (json.JSONDecodeError, SkillPackValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "valid": True,
        "skill_pack": _skill_pack_summary(pack),
        "payload": payload,
    }


@app.post("/api/runs")
async def create_run(
    config: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
) -> dict:
    try:
        parsed_config = _study_config_from_json(config)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="config must be valid JSON") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".txt", ".docx"}:
        raise HTTPException(status_code=400, detail="Only DOCX and TXT uploads are supported")

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)
    try:
        content = extract_transcript_text(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    run = _execute_or_400(
        content,
        parsed_config,
        source_filename=file.filename or "transcript",
    )
    stored = LocalRunStore(_local_data_root()).persist_run(run)
    return _run_response(run, stored)


@app.post("/api/runs/text")
def create_text_run(request: TextRunRequest) -> dict:
    try:
        config = _study_config_from_payload(request.config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    run = _execute_or_400(
        request.content,
        config,
        source_filename=request.source_filename,
    )
    stored = LocalRunStore(_local_data_root()).persist_run(run)
    return _run_response(run, stored)


@app.get("/api/runs")
def list_runs() -> dict:
    return {"runs": LocalRunStore(_local_data_root()).list_runs()}


@app.get("/api/runs/{run_id}/exports/{filename}")
def download_export(run_id: str, filename: str) -> FileResponse:
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(status_code=404, detail="Export not found")
    export_path = LocalRunStore(_local_data_root()).export_path(run_id, filename)
    if not export_path.exists() or export_path.suffix != ".csv":
        raise HTTPException(status_code=404, detail="Export not found")
    return FileResponse(
        export_path,
        media_type="text/csv",
        filename=filename,
    )


def _study_config_from_json(config_json: str) -> StudyConfig:
    return _study_config_from_payload(json.loads(config_json))


def _execute_or_400(content: str, config: StudyConfig, source_filename: str):
    try:
        return execute_analysis(content, config, source_filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _study_config_from_payload(payload: dict) -> StudyConfig:
    pack = _skill_pack_from_payload(payload)
    pack_metric_ids = [metric.id for metric in pack.metrics] if pack else []
    pack_speaker_prefixes = pack.speaker_prefixes if pack else {}
    pack_speaker_labels = pack.speaker_roles if pack else {}
    pack_disfluencies = pack.disfluency_tokens if pack else []
    pack_concepts = pack.concept_lexicons if pack else {}
    pack_cues = pack.nonverbal_cues if pack else {}

    return StudyConfig(
        participant_id=str(payload.get("participant_id", "")),
        speaker_prefixes={
            **pack_speaker_prefixes,
            **dict(payload.get("speaker_prefixes", {})),
        },
        speaker_labels={
            **pack_speaker_labels,
            **dict(payload.get("speaker_labels", {})),
        },
        selected_metrics=list(payload.get("selected_metrics") or pack_metric_ids),
        disfluency_tokens=list(payload.get("disfluency_tokens") or pack_disfluencies),
        concept_lexicons={
            **pack_concepts,
            **dict(payload.get("concept_lexicons", {})),
        },
        nonverbal_cues={
            **pack_cues,
            **dict(payload.get("nonverbal_cues", {})),
        },
        skill_pack_id=pack.id if pack else "",
        skill_pack_name=pack.name if pack else "",
        skill_pack_version=pack.version if pack else "",
    )


def _run_response(run, stored: StoredRun) -> dict:
    return {
        "run_id": run.run_id,
        "source_filename": run.source_filename,
        "created_at": run.created_at,
        "turn_count": len(run.transcript.turns),
        "skill_pack": _run_skill_pack_payload(run),
        "diagnostics": analyze_transcript_quality(run.transcript).to_dict(),
        "results": [asdict(result) for result in run.results],
        "stored": {
            "run_dir": str(stored.run_dir),
            "export_dir": str(stored.export_dir),
            "results_json": str(stored.results_json),
        },
        "exports": [
            {
                "metric_id": result.metric_id,
                "filename": f"{result.metric_id}.csv",
                "download_url": f"/api/runs/{run.run_id}/exports/{result.metric_id}.csv",
            }
            for result in run.results
        ],
    }


def _skill_pack_from_payload(payload: dict) -> SkillPack | None:
    if "skill_pack" in payload:
        return parse_skill_pack(payload["skill_pack"])
    skill_pack_id = payload.get("skill_pack_id")
    if skill_pack_id:
        return load_skill_pack(str(skill_pack_id))
    return None


def _skill_pack_summary(pack: SkillPack) -> dict:
    return {
        "id": pack.id,
        "name": pack.name,
        "version": pack.version,
        "metric_ids": [metric.id for metric in pack.metrics],
        "speaker_roles": pack.speaker_roles,
        "speaker_prefixes": pack.speaker_prefixes,
        "disfluency_tokens": pack.disfluency_tokens,
        "concept_lexicons": pack.concept_lexicons,
        "nonverbal_cues": pack.nonverbal_cues,
    }


def _run_skill_pack_payload(run) -> dict[str, str] | None:
    config = run.transcript.config
    if not config.skill_pack_id:
        return None
    return {
        "id": config.skill_pack_id,
        "name": config.skill_pack_name,
        "version": config.skill_pack_version,
    }


def _local_data_root() -> Path:
    return Path(os.environ.get("NLP_SKILL_AGENTS_DATA_DIR", "local_data"))


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]
