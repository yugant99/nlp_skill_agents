from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal

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
    agent_job_evidence_to_payload,
    agent_job_to_payload,
    create_metric_plugin_build_job,
    create_segmentation_rewrite_job,
)
from backend.extensions.plugin_requests import (
    PluginRequestStore,
    create_plugin_request,
    plugin_request_from_payload,
    plugin_request_to_payload,
)
from backend.llm.openrouter import OpenRouterError
from backend.segmentation.evaluator import evaluate_segmented_draft
from backend.segmentation.models import SyntheticSegmentationCase
from backend.segmentation.pipeline import (
    PatchOperation,
    SegmentationRunStore,
    segmentation_corpus_run_to_payload,
    segmentation_run_to_payload,
)
from backend.segmentation.rulebook import build_cunit_rulebook_summary
from backend.segmentation.synthetic import build_synthetic_case, list_synthetic_cases
from backend.storage.local_store import LocalRunStore, StoredRun
from backend.storage.audit_log import AuditLogStore
from backend.storage.deployment_profiles import check_deployment_profile
from backend.storage.library_store import LibraryStore
from backend.storage.study_store import MAX_STUDY_PARTICIPANTS, StudyWorkspaceStore


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


class AgentJobEvidenceCreateRequest(BaseModel):
    gate: str = Field(min_length=1)
    command: str = Field(default="")
    status: str = Field(min_length=1)
    summary: str = Field(default="")


class SegmentationEvaluateRequest(BaseModel):
    case_id: str = Field(min_length=1)
    draft_text: str = Field(min_length=1)


class SegmentationRunCreateRequest(BaseModel):
    source_filename: str = Field(default="descript_export.txt", min_length=1)
    descript_text: str = Field(min_length=1)
    rule_ids: list[str] = Field(default_factory=list)
    source: Literal["researcher_provided", "synthetic"] = "researcher_provided"


class SegmentationCorpusRunCreateRequest(BaseModel):
    seed: int = Field(default=0, ge=0)


class SegmentationRunAnalysisRequest(BaseModel):
    config: dict = Field(default_factory=dict)


class SegmentationPatchRequest(BaseModel):
    operation: str = Field(min_length=1)
    event_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    reason: str = Field(default="")


class SegmentationSpecialistPatchRequest(BaseModel):
    patches: list[SegmentationPatchRequest] = Field(default_factory=list)


class StudyCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str = Field(default="")


class StudySchemaRequest(BaseModel):
    participant_count: int = Field(
        default=1,
        ge=1,
        le=MAX_STUDY_PARTICIPANTS,
    )
    conditions: list[str] | str = Field(default_factory=lambda: ["home", "lab"])
    week_count: int = Field(default=1, ge=1, le=52)
    custom_fields: list[str] = Field(default_factory=list)


class StudyTextTranscript(BaseModel):
    source_filename: str = Field(min_length=1)
    content: str = Field(min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)


class StudyTextBatchRequest(BaseModel):
    skill_pack_version_id: str = Field(min_length=1)
    transcripts: list[StudyTextTranscript] = Field(min_length=1)


class LibraryApprovalRequest(BaseModel):
    payload: dict
    reviewer: str = Field(default="local-reviewer")
    notes: str = Field(default="")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "storage": "local"}


@app.get("/api/skill-packs/default")
def default_skill_pack() -> dict:
    return load_skill_pack("default_transcript_metrics").raw


@app.get("/api/metric-plugins")
def list_metric_plugins() -> dict:
    return {"plugins": metric_plugin_catalog()}


@app.get("/api/audit-events")
def list_audit_events(limit: int = 100) -> dict:
    return {"events": AuditLogStore(_local_data_root()).list_events(limit=limit)}


@app.get("/api/deployment-profile/{profile}")
def get_deployment_profile(profile: str) -> dict:
    return check_deployment_profile(profile)


@app.get("/api/library")
def list_library_entries() -> dict:
    return {
        "entries": [
            _library_entry_payload(entry)
            for entry in LibraryStore(_local_data_root()).list_entries()
        ]
    }


@app.post("/api/library/skill-packs")
def approve_library_skill_pack(request: LibraryApprovalRequest) -> dict:
    try:
        parse_skill_pack(request.payload)
        entry = LibraryStore(_local_data_root()).approve_skill_pack(
            request.payload,
            reviewer=request.reviewer,
            notes=request.notes,
        )
    except (SkillPackValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"entry": _library_entry_payload(entry)}


@app.post("/api/library/metric-plugins")
def approve_library_metric_plugin(request: LibraryApprovalRequest) -> dict:
    try:
        entry = LibraryStore(_local_data_root()).approve_metric_plugin(
            request.payload,
            reviewer=request.reviewer,
            notes=request.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"entry": _library_entry_payload(entry)}


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


@app.post("/api/agent-jobs/{job_id}/evidence")
def add_agent_job_evidence(job_id: str, request: AgentJobEvidenceCreateRequest) -> dict:
    try:
        evidence = AgentJobStore(_local_data_root()).add_evidence(
            job_id,
            request.model_dump(),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Agent job not found") from exc
    return {"evidence": agent_job_evidence_to_payload(evidence)}


@app.get("/api/agent-jobs/{job_id}/evidence")
def list_agent_job_evidence(job_id: str) -> dict:
    try:
        evidence = AgentJobStore(_local_data_root()).list_evidence(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Agent job not found") from exc
    return {
        "evidence": [
            agent_job_evidence_to_payload(item)
            for item in evidence
        ]
    }


@app.get("/api/segmentation/cases")
def list_segmentation_cases() -> dict:
    return {
        "cases": [_segmentation_case_payload(case) for case in list_synthetic_cases()]
    }


@app.get("/api/segmentation/cases/{case_id}")
def get_segmentation_case(case_id: str) -> dict:
    try:
        case = build_synthetic_case(case_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Segmentation case not found") from exc
    return {"case": _segmentation_case_payload(case)}


@app.get("/api/segmentation/rulebook")
def get_segmentation_rulebook() -> dict:
    return {"rulebook": asdict(build_cunit_rulebook_summary())}


@app.post("/api/segmentation/evaluate")
def evaluate_segmentation_draft(request: SegmentationEvaluateRequest) -> dict:
    try:
        case = build_synthetic_case(request.case_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Segmentation case not found") from exc
    evaluation = evaluate_segmented_draft(
        request.draft_text,
        expected_rule_ids=case.rule_ids,
        forbidden_tokens=case.official_source_guard_tokens,
    )
    return {
        "case_id": case.case_id,
        "source": "synthetic",
        "evaluation": asdict(evaluation),
    }


@app.post("/api/segmentation/cases/{case_id}/rewrite-job")
def create_segmentation_rewrite_job_endpoint(case_id: str) -> dict:
    try:
        build_synthetic_case(case_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Segmentation case not found") from exc
    job_store = AgentJobStore(_local_data_root())
    job = create_segmentation_rewrite_job(case_id, store=job_store)
    return {
        "job": agent_job_to_payload(job),
        "artifact_path": str(job_store.jobs_dir / f"{job.id}.json"),
    }


@app.post("/api/segmentation/runs")
def create_segmentation_run(request: SegmentationRunCreateRequest) -> dict:
    try:
        run = SegmentationRunStore(_local_data_root()).create_run(
            source_filename=request.source_filename,
            descript_text=request.descript_text,
            rule_ids=request.rule_ids,
            source=request.source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"run": segmentation_run_to_payload(run)}


@app.post("/api/segmentation/corpus-runs")
def create_segmentation_corpus_run(
    request: SegmentationCorpusRunCreateRequest,
) -> dict:
    corpus_run = SegmentationRunStore(_local_data_root()).create_corpus_run(
        seed=request.seed,
    )
    return {"corpus_run": segmentation_corpus_run_to_payload(corpus_run)}


@app.get("/api/segmentation/corpus-runs")
def list_segmentation_corpus_runs() -> dict:
    return {
        "corpus_runs": [
            segmentation_corpus_run_to_payload(corpus_run)
            for corpus_run in SegmentationRunStore(_local_data_root()).list_corpus_runs()
        ]
    }


@app.get("/api/segmentation/runs")
def list_segmentation_runs() -> dict:
    return {
        "runs": [
            segmentation_run_to_payload(run)
            for run in SegmentationRunStore(_local_data_root()).list_runs()
        ]
    }


@app.post("/api/segmentation/runs/files")
async def create_segmentation_file_run(
    rule_ids: Annotated[str, Form()] = "[]",
    file: UploadFile = File(...),
) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix != ".txt":
        raise HTTPException(
            status_code=400,
            detail="Only TXT segmentation uploads are supported",
        )
    try:
        parsed_rule_ids = _segmentation_rule_ids_from_json(rule_ids)
        content = (await file.read()).decode("utf-8")
        run = SegmentationRunStore(_local_data_root()).create_run(
            source_filename=file.filename or "descript_export.txt",
            descript_text=content,
            rule_ids=parsed_rule_ids,
            source="researcher_provided",
        )
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail="Segmentation upload must be UTF-8 text",
        ) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"run": segmentation_run_to_payload(run)}


@app.get("/api/segmentation/runs/{run_id}")
def get_segmentation_run(run_id: str) -> dict:
    try:
        run = SegmentationRunStore(_local_data_root()).load_run(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Segmentation run not found") from exc
    return {"run": segmentation_run_to_payload(run)}


@app.post("/api/segmentation/runs/{run_id}/verify")
def verify_segmentation_run(run_id: str) -> dict:
    try:
        run = SegmentationRunStore(_local_data_root()).verify_run(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Segmentation run not found") from exc
    return {"run": segmentation_run_to_payload(run)}


@app.post("/api/segmentation/runs/{run_id}/analysis")
def analyze_segmentation_run(
    run_id: str,
    request: SegmentationRunAnalysisRequest,
) -> dict:
    try:
        segmentation_run = SegmentationRunStore(_local_data_root()).load_run(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Segmentation run not found") from exc
    if segmentation_run.status != "verified":
        raise HTTPException(
            status_code=400,
            detail="Segmentation run must be verified before analysis",
        )
    try:
        config = _segmentation_analysis_config(request.config)
    except (SkillPackValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    source_filename = f"{Path(segmentation_run.source_filename).stem}_segmented.txt"
    run = _execute_or_400(
        segmentation_run.merged_draft,
        config,
        source_filename=source_filename,
    )
    stored = LocalRunStore(_local_data_root()).persist_run(run)
    return _run_response(run, stored)


@app.post("/api/segmentation/runs/{run_id}/specialists/{specialist_id}/patches")
def submit_segmentation_specialist_patches(
    run_id: str,
    specialist_id: str,
    request: SegmentationSpecialistPatchRequest,
) -> dict:
    try:
        run = SegmentationRunStore(_local_data_root()).apply_specialist_patches(
            run_id,
            specialist_id=specialist_id,
            patches=[
                PatchOperation(
                    operation=patch.operation,
                    event_index=patch.event_index,
                    text=patch.text,
                    reason=patch.reason,
                )
                for patch in request.patches
            ],
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Segmentation run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"run": segmentation_run_to_payload(run)}


@app.post("/api/segmentation/runs/{run_id}/rewrite-job")
def create_segmentation_run_rewrite_job(run_id: str) -> dict:
    try:
        run = SegmentationRunStore(_local_data_root()).load_run(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Segmentation run not found") from exc
    if not run.failure_routes:
        raise HTTPException(
            status_code=400,
            detail="Segmentation run has no failed rules to rewrite",
        )
    job_store = AgentJobStore(_local_data_root())
    job = create_segmentation_rewrite_job(
        run.run_id,
        failed_rule_ids=[route["rule_id"] for route in run.failure_routes],
        target_specialist_ids=[
            route["specialist_id"] for route in run.failure_routes
        ],
        store=job_store,
    )
    return {
        "job": agent_job_to_payload(job),
        "artifact_path": str(job_store.jobs_dir / f"{job.id}.json"),
    }


@app.get("/api/segmentation/runs/{run_id}/exports/{filename}")
def download_segmentation_run_export(run_id: str, filename: str) -> FileResponse:
    store = SegmentationRunStore(_local_data_root())
    try:
        if filename == "final_transcript.txt":
            export_path = store.write_final_transcript(run_id)
            media_type = "text/plain"
        elif filename == "evidence.json":
            export_path = store.write_evidence_bundle(run_id)
            media_type = "application/json"
        else:
            raise HTTPException(status_code=404, detail="Export not found")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Segmentation run not found") from exc
    return FileResponse(
        export_path,
        media_type=media_type,
        filename=filename,
    )


@app.get("/api/segmentation/runs/{run_id}/specialists/{filename}")
def download_segmentation_specialist_packet(run_id: str, filename: str) -> FileResponse:
    if "/" in filename or "\\" in filename or not filename.endswith(".html"):
        raise HTTPException(status_code=404, detail="Specialist packet not found")
    try:
        SegmentationRunStore(_local_data_root()).load_run(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Segmentation run not found") from exc
    packet_path = (
        _local_data_root()
        / "segmentation_runs"
        / run_id
        / "specialists"
        / filename
    )
    if not packet_path.exists():
        raise HTTPException(status_code=404, detail="Specialist packet not found")
    return FileResponse(
        packet_path,
        media_type="text/html",
        filename=filename,
    )


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


@app.put("/api/studies/{study_id}/schema")
def update_study_schema(study_id: str, request: StudySchemaRequest) -> dict:
    try:
        schema = StudyWorkspaceStore(_local_data_root()).save_study_schema(
            study_id,
            request.model_dump(),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Study not found") from exc
    return {"schema": _study_schema_payload(schema)}


@app.get("/api/studies/{study_id}/schema")
def get_study_schema(study_id: str) -> dict:
    try:
        schema = StudyWorkspaceStore(_local_data_root()).load_study_schema(study_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Study schema not found") from exc
    return {"schema": _study_schema_payload(schema)}


@app.get("/api/studies/{study_id}/batches")
def list_study_batches(study_id: str) -> dict:
    try:
        batches = StudyWorkspaceStore(_local_data_root()).list_batches(study_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Study not found") from exc
    return {"batches": [_study_batch_summary_payload(batch) for batch in batches]}


@app.get("/api/studies/{study_id}/batches/{batch_id}")
def get_study_batch(study_id: str, batch_id: str) -> dict:
    try:
        batch = StudyWorkspaceStore(_local_data_root()).load_batch(study_id, batch_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Study batch not found") from exc
    return _study_batch_payload(batch)


@app.get("/api/studies/{study_id}/batches/{batch_id}/runs")
def list_study_batch_runs(study_id: str, batch_id: str) -> dict:
    try:
        runs = StudyWorkspaceStore(_local_data_root()).list_batch_runs(
            study_id,
            batch_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Study batch not found") from exc
    return {"runs": runs}


@app.get("/api/studies/{study_id}/batches/{batch_id}/runs/{run_id}")
def get_study_batch_run(study_id: str, batch_id: str, run_id: str) -> dict:
    try:
        run = StudyWorkspaceStore(_local_data_root()).load_batch_run(
            study_id,
            batch_id,
            run_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Study batch run not found") from exc
    return {"run": run}


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


@app.post("/api/studies/{study_id}/batches/files")
async def create_study_file_batch(
    study_id: str,
    skill_pack_version_id: Annotated[str, Form()],
    files: Annotated[list[UploadFile], File()],
    metadata: Annotated[str, Form()] = "{}",
) -> dict:
    try:
        parsed_metadata = _batch_metadata_from_json(metadata)
        transcripts = [
            {
                "source_filename": file.filename or f"transcript_{index + 1}",
                "content": await _extract_upload_text(file),
                "metadata": parsed_metadata.get(file.filename or "", {}),
            }
            for index, file in enumerate(files)
        ]
        batch = StudyWorkspaceStore(_local_data_root()).run_text_batch(
            study_id,
            skill_pack_version_id,
            transcripts,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Study artifact not found") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _study_batch_payload(batch)


@app.post("/api/studies/{study_id}/bundle")
def export_study_bundle(study_id: str) -> dict:
    try:
        bundle = StudyWorkspaceStore(_local_data_root()).export_study_bundle(study_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Study not found") from exc
    return {"bundle": _study_bundle_payload(bundle)}


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


def _batch_metadata_from_json(raw_metadata: str) -> dict[str, dict[str, str]]:
    payload = json.loads(raw_metadata or "{}")
    if not isinstance(payload, dict):
        raise ValueError("metadata must be a JSON object keyed by filename")
    normalized: dict[str, dict[str, str]] = {}
    for filename, metadata in payload.items():
        if not isinstance(metadata, dict):
            continue
        normalized[str(filename)] = {
            str(key): str(value)
            for key, value in metadata.items()
            if str(key).strip() and str(value).strip()
        }
    return normalized


def _segmentation_rule_ids_from_json(raw_rule_ids: str) -> list[str]:
    payload = json.loads(raw_rule_ids or "[]")
    if not isinstance(payload, list):
        raise ValueError("rule_ids must be a JSON array")
    return [str(rule_id) for rule_id in payload if str(rule_id).strip()]


async def _extract_upload_text(file: UploadFile) -> str:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".txt", ".docx"}:
        raise ValueError("Only DOCX and TXT uploads are supported")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)
    try:
        return extract_transcript_text(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


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


def _segmentation_analysis_config(payload: dict) -> StudyConfig:
    default_payload = {
        "skill_pack": load_skill_pack("default_transcript_metrics").raw,
        "speaker_prefixes": {
            "caregiver": ["Av", "AvN", "PN"],
            "participant": ["P"],
        },
    }
    merged_payload = {
        **default_payload,
        **payload,
        "speaker_prefixes": {
            **default_payload["speaker_prefixes"],
            **dict(payload.get("speaker_prefixes", {})),
        },
    }
    return _study_config_from_payload(merged_payload)


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


def _segmentation_case_payload(case: SyntheticSegmentationCase) -> dict:
    payload = asdict(case)
    payload["source"] = "synthetic"
    return payload


def _library_entry_payload(entry) -> dict:
    return {
        "id": entry.id,
        "version": entry.version,
        "entry_type": entry.entry_type,
        "artifact_path": str(entry.artifact_path),
        "approved_by": entry.approved_by,
        "notes": entry.notes,
        "created_at": entry.created_at,
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


def _study_schema_payload(schema) -> dict:
    return {
        "study_id": schema.study_id,
        "participant_count": schema.participant_count,
        "participants": schema.participants,
        "conditions": schema.conditions,
        "week_count": schema.week_count,
        "weeks": schema.weeks,
        "custom_fields": schema.custom_fields,
        "updated_at": schema.updated_at,
    }


def _study_batch_summary_payload(batch) -> dict:
    return {
        "study_id": batch.study_id,
        "batch_id": batch.batch_id,
        "skill_pack_version_id": batch.skill_pack_version_id,
        "run_count": batch.run_count,
        "failure_count": batch.failure_count,
        "aggregate_dir": str(batch.aggregate_dir),
        "created_at": batch.created_at,
    }


def _study_batch_payload(batch) -> dict:
    aggregate_results_json = batch.aggregate_dir / "aggregate_results.json"
    aggregate_payload = json.loads(aggregate_results_json.read_text(encoding="utf-8"))
    exports = [
        {
            "metric_id": path.stem,
            "filename": path.name,
            "path": str(path),
        }
        for path in sorted(batch.aggregate_dir.glob("*.csv"))
    ]
    return {
        "batch": _study_batch_summary_payload(batch),
        "aggregate_results_json": str(aggregate_results_json),
        "study_schema": aggregate_payload.get("study_schema"),
        "failures": aggregate_payload.get("failures", []),
        "results": aggregate_payload["results"],
        "exports": exports,
    }


def _study_bundle_payload(bundle) -> dict:
    return {
        "study_id": bundle.study_id,
        "bundle_id": bundle.bundle_id,
        "bundle_dir": str(bundle.bundle_dir),
        "manifest_path": str(bundle.manifest_path),
        "created_at": bundle.created_at,
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
