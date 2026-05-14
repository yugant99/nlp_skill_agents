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
from backend.analysis.pipeline import execute_analysis, metric_plugin_catalog
from backend.analysis.skill_builder import (
    draft_skill_pack_from_brief,
    draft_skill_pack_with_openrouter,
    refine_skill_pack,
    refine_skill_pack_with_openrouter,
)
from backend.analysis.skill_packs import (
    SkillPack,
    SkillPackValidationError,
    load_skill_pack,
    parse_skill_pack,
    parse_skill_pack_document,
)
from backend.analysis.transcripts import StudyConfig, extract_transcript_text
from backend.extensions.agent_jobs import (
    AgentJobStore,
    agent_job_to_payload,
    create_metric_plugin_build_job,
)
from backend.extensions.plugin_requests import (
    PluginRequestStore,
    create_plugin_request,
    plugin_request_from_payload,
    plugin_request_to_payload,
)
from backend.llm.openrouter import OpenRouterError
from backend.storage.local_store import LocalRunStore, StoredRun
from backend.storage.study_store import StudyWorkspaceStore


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


class SkillPackDraftRequest(BaseModel):
    brief: str = Field(min_length=1)
    name: str | None = Field(default=None)
    authoring_engine: str = Field(default="local")
    model: str | None = Field(default=None)


class SkillPackRefineRequest(BaseModel):
    payload: dict
    instruction: str = Field(min_length=1)
    authoring_engine: str = Field(default="local")
    model: str | None = Field(default=None)


class PluginRequestCreateRequest(BaseModel):
    title: str = Field(min_length=1)
    research_question: str = Field(min_length=1)
    requested_metric_id: str | None = Field(default=None)
    output_columns: list[str] | str = Field(default_factory=list)
    example_transcript: str | None = Field(default=None)
    expected_behavior: str | None = Field(default=None)
    examples: list[dict[str, str]] = Field(default_factory=list)


class AgentJobStatusUpdateRequest(BaseModel):
    status: str = Field(min_length=1)


class StudyCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str = Field(default="")


class StudyTextTranscript(BaseModel):
    source_filename: str = Field(min_length=1)
    content: str = Field(min_length=1)


class StudyTextBatchRequest(BaseModel):
    skill_pack_version_id: str = Field(min_length=1)
    transcripts: list[StudyTextTranscript] = Field(min_length=1)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "storage": "local"}


@app.get("/api/skill-packs/default")
def default_skill_pack() -> dict:
    return load_skill_pack("default_transcript_metrics").raw


@app.get("/api/metric-plugins")
def list_metric_plugins() -> dict:
    return {"plugins": metric_plugin_catalog()}


@app.post("/api/plugin-requests")
def create_metric_plugin_request(request: PluginRequestCreateRequest) -> dict:
    try:
        stored = PluginRequestStore(_local_data_root())
        plugin_request = create_plugin_request(request.model_dump(), store=stored)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "request": plugin_request_to_payload(plugin_request),
        "artifact_path": str(stored.requests_dir / f"{plugin_request.id}.json"),
        "implementation_prompt_path": str(
            stored.requests_dir / plugin_request.id / "implementation_prompt.md"
        ),
    }


@app.get("/api/plugin-requests")
def list_metric_plugin_requests() -> dict:
    return {
        "requests": [
            plugin_request_to_payload(request)
            for request in PluginRequestStore(_local_data_root()).list_requests()
        ]
    }


@app.post("/api/plugin-requests/{request_id}/build-job")
def create_metric_plugin_build_job_endpoint(request_id: str) -> dict:
    request_store = PluginRequestStore(_local_data_root())
    request_path = request_store.requests_dir / f"{request_id}.json"
    if not request_path.exists():
        raise HTTPException(status_code=404, detail="Plugin request not found")
    plugin_request = plugin_request_from_payload(
        json.loads(request_path.read_text(encoding="utf-8"))
    )
    prompt_path = request_store.requests_dir / request_id / "implementation_prompt.md"
    job_store = AgentJobStore(_local_data_root())
    job = create_metric_plugin_build_job(
        plugin_request,
        prompt_path=prompt_path,
        store=job_store,
    )
    return {
        "job": agent_job_to_payload(job),
        "artifact_path": str(job_store.jobs_dir / f"{job.id}.json"),
    }


@app.get("/api/agent-jobs")
def list_agent_jobs() -> dict:
    return {
        "jobs": [
            agent_job_to_payload(job)
            for job in AgentJobStore(_local_data_root()).list_jobs()
        ]
    }


@app.patch("/api/agent-jobs/{job_id}")
def update_agent_job_status(job_id: str, request: AgentJobStatusUpdateRequest) -> dict:
    try:
        job = AgentJobStore(_local_data_root()).update_status(job_id, request.status)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Agent job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"job": agent_job_to_payload(job)}


@app.post("/api/studies")
def create_study(request: StudyCreateRequest) -> dict:
    study = StudyWorkspaceStore(_local_data_root()).create_study(request.model_dump())
    return {"study": _study_payload(study)}


@app.get("/api/studies")
def list_studies() -> dict:
    return {
        "studies": [
            _study_payload(study)
            for study in StudyWorkspaceStore(_local_data_root()).list_studies()
        ]
    }


@app.post("/api/studies/{study_id}/skill-pack-versions")
def create_study_skill_pack_version(study_id: str, payload: dict) -> dict:
    try:
        version = StudyWorkspaceStore(_local_data_root()).add_skill_pack_version(
            study_id,
            payload,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Study not found") from exc
    except (SkillPackValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"version": _study_skill_pack_version_payload(version)}


@app.post("/api/studies/{study_id}/batches/text")
def create_study_text_batch(study_id: str, request: StudyTextBatchRequest) -> dict:
    try:
        batch = StudyWorkspaceStore(_local_data_root()).run_text_batch(
            study_id,
            request.skill_pack_version_id,
            [item.model_dump() for item in request.transcripts],
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Study artifact not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _study_batch_payload(batch)


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


@app.post("/api/skill-packs/draft")
def draft_skill_pack(request: SkillPackDraftRequest) -> dict:
    try:
        if request.authoring_engine == "openrouter":
            draft = draft_skill_pack_with_openrouter(
                request.brief,
                request.name,
                request.model,
            )
        elif request.authoring_engine == "local":
            draft = draft_skill_pack_from_brief(request.brief, request.name)
        else:
            raise HTTPException(status_code=400, detail="Unsupported authoring engine")
        pack = parse_skill_pack(draft.payload)
    except (SkillPackValidationError, ValueError, OpenRouterError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "payload": draft.payload,
        "skill_pack": _skill_pack_summary(pack),
        "warnings": draft.warnings,
        "authoring": _authoring_payload(request.authoring_engine, request.model),
    }


@app.post("/api/skill-packs/refine")
def refine_skill_pack_endpoint(request: SkillPackRefineRequest) -> dict:
    try:
        if request.authoring_engine == "openrouter":
            refined = refine_skill_pack_with_openrouter(
                request.payload,
                request.instruction,
                request.model,
            )
        elif request.authoring_engine == "local":
            refined = refine_skill_pack(request.payload, request.instruction)
        else:
            raise HTTPException(status_code=400, detail="Unsupported authoring engine")
        pack = parse_skill_pack(refined.payload)
    except (SkillPackValidationError, ValueError, OpenRouterError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "payload": refined.payload,
        "skill_pack": _skill_pack_summary(pack),
        "applied_changes": refined.applied_changes,
        "warnings": refined.warnings,
        "authoring": _authoring_payload(request.authoring_engine, request.model),
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


def _study_payload(study) -> dict:
    return {
        "id": study.id,
        "name": study.name,
        "description": study.description,
        "created_at": study.created_at,
    }


def _study_skill_pack_version_payload(version) -> dict:
    return {
        "study_id": version.study_id,
        "version_id": version.version_id,
        "artifact_path": str(version.artifact_path),
        "created_at": version.created_at,
        "skill_pack": {
            "id": version.payload["id"],
            "name": version.payload["name"],
            "version": version.payload["version"],
            "metrics": version.payload.get("metrics", []),
        },
    }


def _study_batch_payload(batch) -> dict:
    aggregate_results_json = batch.aggregate_dir / "aggregate_results.json"
    exports = [
        {
            "metric_id": path.stem,
            "filename": path.name,
            "path": str(path),
        }
        for path in sorted(batch.aggregate_dir.glob("*.csv"))
    ]
    return {
        "batch": {
            "study_id": batch.study_id,
            "batch_id": batch.batch_id,
            "skill_pack_version_id": batch.skill_pack_version_id,
            "run_count": batch.run_count,
            "failure_count": batch.failure_count,
            "aggregate_dir": str(batch.aggregate_dir),
            "created_at": batch.created_at,
        },
        "aggregate_results_json": str(aggregate_results_json),
        "exports": exports,
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


def _authoring_payload(engine: str, model: str | None) -> dict:
    return {
        "engine": engine,
        "model": model or "local",
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
