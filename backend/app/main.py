from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal, NoReturn

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    AgentJob,
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
from backend.professor_demo.api import router as professor_demo_router
from backend.transcript_pilot.api import router as transcript_pilot_router
from backend.qualitative import QualitativeProjectDatabase
from backend.qualitative.cases import (
    CaseConflictError,
    CaseNotFoundError,
    CaseService,
    CaseValidationError,
)
from backend.qualitative.codebooks import (
    CodebookConflictError,
    CodebookImmutableError,
    CodebookNotFoundError,
    CodebookService,
    CodebookValidationError,
)
from backend.qualitative.database import QualitativeDatabaseConflict
from backend.qualitative.coding_references import (
    CodingReferenceConflictError,
    CodingReferenceNotFoundError,
    CodingReferenceService,
    CodingReferenceValidationError,
)
from backend.qualitative.notes import (
    NoteConflictError,
    NoteNotFoundError,
    NoteService,
    NoteValidationError,
)
from backend.qualitative.research_reviews import (
    ResearchReviewService,
    ReviewConflictError,
    ReviewNotFoundError,
    ReviewValidationError,
)
from backend.qualitative.saved_queries import (
    SavedQueryConflictError,
    SavedQueryNotFoundError,
    SavedQueryService,
    SavedQueryValidationError,
)
from backend.segmentation.evaluator import evaluate_segmented_draft
from backend.segmentation.models import SyntheticSegmentationCase
from backend.segmentation.pipeline import (
    PatchOperation,
    SegmentationRunStore,
    SegmentationSnapshotConflict,
    segmentation_corpus_run_to_payload,
    segmentation_run_to_payload,
)
from backend.segmentation.rulebook import build_cunit_rulebook_summary
from backend.segmentation.synthetic import build_synthetic_case, list_synthetic_cases
from backend.storage.local_store import LocalRunStore, StoredRun
from backend.storage.audit_log import AuditLogStore
from backend.storage.deployment_profiles import check_deployment_profile
from backend.storage.evidence_catalog import EvidenceCatalog
from backend.storage.evidence_target_registry import EvidenceTargetConflictError
from backend.storage.library_store import LibraryStore
from backend.storage.project_archive import (
    MAX_ARCHIVE_FILE_BYTES,
    ProjectArchiveConflict,
    ProjectArchiveError,
    ProjectArchiveStore,
)
from backend.storage.segmentation_operation_store import (
    SegmentationOperationConflict,
    SegmentationOperationStore,
)
from backend.storage.source_blob_store import SourceBlobIntegrityError, SourceBlobStore
from backend.storage.sqlite_migrations import SchemaCompatibilityError
from backend.storage.study_batch_operation_store import (
    STUDY_BATCH_ID_PATTERN,
    StudyBatchOperationConflict,
    StudyBatchOperationStore,
)
from backend.storage.study_store import (
    MAX_STUDY_PARTICIPANTS,
    StudyBatchSnapshotConflict,
    StudySkillPackVersionConflict,
    StudyWorkspaceConflict,
    StudyWorkspaceStore,
)


app = FastAPI(title="NLP Skill Agents", version="0.1.0")
app.include_router(professor_demo_router)
app.include_router(transcript_pilot_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def _content_safe_validation_error(
    request: Request,
    exc: RequestValidationError,
):
    path = request.url.path
    if path.startswith("/api/professor-demo/") or path.startswith(
        "/api/transcript-pilot/"
    ):
        return JSONResponse(
            status_code=422,
            content={"detail": "Request validation failed"},
        )
    if path.startswith("/api/studies/") and (
        "/qualitative/coding-references" in path
        or "/qualitative/saved-queries" in path
        or "/qualitative/researchers" in path
        or "/qualitative/agent-suggestions" in path
        or "/qualitative/memos" in path
        or "/qualitative/annotations" in path
        or "/segmentation/runs" in path
    ):
        return JSONResponse(
            status_code=422,
            content={"detail": "Request validation failed"},
        )
    return await request_validation_exception_handler(request, exc)

_CODEBOOK_API_ERRORS = (
    FileNotFoundError,
    ValueError,
    CodebookNotFoundError,
    CodebookValidationError,
    CodebookImmutableError,
    CodebookConflictError,
    SchemaCompatibilityError,
    StudyBatchOperationConflict,
    QualitativeDatabaseConflict,
)

_CASE_API_ERRORS = (
    FileNotFoundError,
    CaseNotFoundError,
    CaseValidationError,
    CaseConflictError,
    SchemaCompatibilityError,
    StudyBatchOperationConflict,
    QualitativeDatabaseConflict,
)

_NOTE_API_ERRORS = (
    NoteValidationError,
    NoteNotFoundError,
    NoteConflictError,
)

_REVIEW_API_ERRORS = (
    ReviewValidationError,
    ReviewNotFoundError,
    ReviewConflictError,
)

_STUDY_SEGMENTATION_CONFLICT_ERRORS = (
    SchemaCompatibilityError,
    SegmentationOperationConflict,
    SegmentationSnapshotConflict,
    SourceBlobIntegrityError,
    EvidenceTargetConflictError,
)
_STUDY_SEGMENTATION_API_ERRORS = _STUDY_SEGMENTATION_CONFLICT_ERRORS + (
    ValueError,
)
_STUDY_SEGMENTATION_READ_ERRORS = (
    FileNotFoundError,
) + _STUDY_SEGMENTATION_API_ERRORS


class TextRunRequest(BaseModel):
    source_filename: str = Field(default="pasted_transcript.txt", min_length=1)
    content: str = Field(min_length=1)
    config: dict = Field(default_factory=dict)
    project_source_id: str = Field(default="")
    parent_transcript_revision_id: str = Field(default="")


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
    project_source_id: str = Field(default="")
    parent_transcript_revision_id: str = Field(default="")


class SegmentationCorpusRunCreateRequest(BaseModel):
    seed: int = Field(default=0, ge=0)


class SegmentationRunAnalysisRequest(BaseModel):
    config: dict = Field(default_factory=dict)


class SegmentationPatchRequest(BaseModel):
    operation: str = Field(min_length=1)
    event_index: Annotated[int, Field(strict=True, ge=0)]
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
    project_source_id: str = Field(default="")
    parent_transcript_revision_id: str = Field(default="")


class StudyTextBatchRequest(BaseModel):
    skill_pack_version_id: str = Field(min_length=1)
    transcripts: list[StudyTextTranscript] = Field(min_length=1)
    batch_id: str | None = Field(
        default=None,
        pattern=STUDY_BATCH_ID_PATTERN,
    )


class QualitativeProjectInitializeRequest(BaseModel):
    researcher_id: str
    researcher_name: str


class CodebookCreateRequest(BaseModel):
    researcher_id: str
    title: str
    description: str = ""


class CodebookImportRequest(BaseModel):
    researcher_id: str
    document: dict[str, object]


class CodebookVersionCreateRequest(BaseModel):
    researcher_id: str
    based_on_version_id: str | None = None


class CodeCreateRequest(BaseModel):
    researcher_id: str
    stable_code_key: str
    label: str
    parent_code_id: str | None = None
    definition: str = ""
    inclusion_criteria: str = ""
    exclusion_criteria: str = ""
    examples: list[str] = Field(default_factory=list)
    notes: str = ""
    color: str = ""
    sort_order: Annotated[int, Field(strict=True)] = 0


class CodeUpdateRequest(BaseModel):
    researcher_id: str
    label: str
    parent_code_id: str | None = None
    definition: str = ""
    inclusion_criteria: str = ""
    exclusion_criteria: str = ""
    examples: list[str] = Field(default_factory=list)
    notes: str = ""
    color: str = ""
    sort_order: Annotated[int, Field(strict=True)] = 0


class CodebookFreezeRequest(BaseModel):
    researcher_id: str


class CaseCreateRequest(BaseModel):
    researcher_id: str
    case_kind: str
    label: str
    description: str = ""


class CaseUpdateRequest(BaseModel):
    researcher_id: str
    case_kind: str
    label: str
    description: str = ""


class AttributeDefinitionCreateRequest(BaseModel):
    researcher_id: str
    attribute_key: str
    label: str
    value_type: str
    allowed_values: list[str] = Field(default_factory=list)
    required: Annotated[bool, Field(strict=True)] = False


class AttributeValueSetRequest(BaseModel):
    researcher_id: str
    value: object


class ResearcherActionRequest(BaseModel):
    researcher_id: str


class SourceLinkActionRequest(BaseModel):
    researcher_id: str
    project_source_id: str


class CodingReferenceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    researcher_id: str
    project_source_id: str
    transcript_revision_id: str
    evidence_set_id: str
    target_kind: str
    passage_id: str
    cunit_id: str = ""
    start_offset: Annotated[int, Field(strict=True)]
    end_offset: Annotated[int, Field(strict=True)]
    codebook_version_id: str
    code_id: str


class CodingReferenceRemoveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    researcher_id: str


class CodingReferenceListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    project_source_id: str | None = None
    codebook_version_id: str | None = None
    code_id: str | None = None
    created_by: str | None = None
    include_removed: bool = False

    @field_validator("include_removed", mode="before")
    @classmethod
    def validate_include_removed(cls, value: object) -> bool:
        if type(value) is bool:
            return value
        if value == "true":
            return True
        if value == "false":
            return False
        raise ValueError("include_removed must be true or false")


class _StrictSavedQueryAPIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SavedQueryFiltersRequest(_StrictSavedQueryAPIModel):
    project_source_id: str | None
    codebook_version_id: str | None
    code_id: str | None
    created_by: str | None
    include_removed: bool


class SavedQueryDefinitionRequest(_StrictSavedQueryAPIModel):
    kind: str
    version: int
    filters: SavedQueryFiltersRequest


class SavedQueryCreateRequest(_StrictSavedQueryAPIModel):
    saved_query_id: str
    researcher_id: str
    title: str
    definition: SavedQueryDefinitionRequest


class SavedQueryListQuery(_StrictSavedQueryAPIModel):
    created_by: str | None = None
    limit: str = "20"
    cursor: str | None = None


class _StrictReviewAPIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ResearcherRegistrationRequest(_StrictReviewAPIModel):
    actor_id: str
    display_name: str
    role: str


class AgentSuggestionCreateRequest(_StrictReviewAPIModel):
    agent_suggestion_id: str
    origin_kind: str
    origin_id: str
    origin_suggestion_key: str
    researcher_id: str
    project_source_id: str
    transcript_revision_id: str
    evidence_set_id: str
    target_kind: str
    passage_id: str
    cunit_id: str
    start_offset: Annotated[int, Field(strict=True)]
    end_offset: Annotated[int, Field(strict=True)]
    codebook_version_id: str
    code_id: str


class ReviewerDecisionCreateRequest(_StrictReviewAPIModel):
    reviewer_decision_id: str
    researcher_id: str
    expected_decision_number: Annotated[int, Field(strict=True)]
    decision: str
    coding_reference_id: str | None = None

    @model_validator(mode="after")
    def validate_coding_reference_presence(self):
        supplied = "coding_reference_id" in self.model_fields_set
        if supplied and self.coding_reference_id is None:
            raise ValueError("coding_reference_id must not be null")
        if self.decision in {"accepted", "edited"}:
            if not supplied:
                raise ValueError(
                    "accepted and edited decisions require coding_reference_id"
                )
        elif self.decision in {"rejected", "deferred"} and supplied:
            raise ValueError(
                "rejected and deferred decisions must omit coding_reference_id"
            )
        return self


class ResearcherListQuery(_StrictReviewAPIModel):
    role: str | None = None
    active: str | None = None
    limit: str = "20"
    cursor: str | None = None


class AgentSuggestionListQuery(_StrictReviewAPIModel):
    project_source_id: str | None = None
    codebook_version_id: str | None = None
    code_id: str | None = None
    created_by: str | None = None
    origin_kind: str | None = None
    limit: str = "20"
    cursor: str | None = None


class ReviewerDecisionListQuery(_StrictReviewAPIModel):
    limit: str = "20"
    cursor: str | None = None


class _StrictNoteAPIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NoteStudyTargetRequest(_StrictNoteAPIModel):
    kind: Literal["study"]


class NoteSourceTargetRequest(_StrictNoteAPIModel):
    kind: Literal["source"]
    project_source_id: str


class NoteCaseTargetRequest(_StrictNoteAPIModel):
    kind: Literal["case"]
    case_id: str


class NoteCodeTargetRequest(_StrictNoteAPIModel):
    kind: Literal["code"]
    codebook_version_id: str
    code_id: str


class NoteExcerptTargetRequest(_StrictNoteAPIModel):
    kind: Literal["excerpt"]
    project_source_id: str
    transcript_revision_id: str
    evidence_set_id: str
    excerpt_target_kind: Literal["passage", "cunit"]
    passage_id: str
    cunit_id: str | None = None
    start_offset: Annotated[int, Field(strict=True)]
    end_offset: Annotated[int, Field(strict=True)]

    @model_validator(mode="after")
    def validate_cunit_shape(self):
        supplied_cunit = "cunit_id" in self.model_fields_set
        if self.excerpt_target_kind == "passage" and supplied_cunit:
            raise ValueError("Passage excerpt targets must omit cunit_id")
        if self.excerpt_target_kind == "cunit" and (
            not supplied_cunit or self.cunit_id is None
        ):
            raise ValueError("C-unit excerpt targets require cunit_id")
        return self


NoteTargetRequest = Annotated[
    NoteStudyTargetRequest
    | NoteSourceTargetRequest
    | NoteCaseTargetRequest
    | NoteCodeTargetRequest
    | NoteExcerptTargetRequest,
    Field(discriminator="kind"),
]


class MemoCreateRequest(_StrictNoteAPIModel):
    researcher_id: str
    title: str
    body: str
    target: NoteTargetRequest


class AnnotationCreateRequest(_StrictNoteAPIModel):
    researcher_id: str
    body: str
    target: NoteTargetRequest


class MemoRevisionRequest(_StrictNoteAPIModel):
    researcher_id: str
    expected_revision_number: Annotated[int, Field(strict=True)]
    title: str
    body: str


class AnnotationRevisionRequest(_StrictNoteAPIModel):
    researcher_id: str
    expected_revision_number: Annotated[int, Field(strict=True)]
    body: str


class NoteRemoveRequest(_StrictNoteAPIModel):
    researcher_id: str


class NoteListQuery(_StrictNoteAPIModel):
    target_kind: Literal["study", "source", "case", "code", "excerpt"] | None = (
        None
    )
    created_by: str | None = None
    include_removed: bool = False
    limit: int = 20
    cursor: str | None = None

    @field_validator("include_removed", mode="before")
    @classmethod
    def validate_include_removed(cls, value: object) -> bool:
        if type(value) is bool:
            return value
        if value == "true":
            return True
        if value == "false":
            return False
        raise ValueError("include_removed must be true or false")

    @field_validator("limit", mode="before")
    @classmethod
    def validate_limit(cls, value: object) -> int:
        return _canonical_note_page_limit(value)


class NoteRevisionListQuery(_StrictNoteAPIModel):
    limit: int = 20
    cursor: str | None = None

    @field_validator("limit", mode="before")
    @classmethod
    def validate_limit(cls, value: object) -> int:
        return _canonical_note_page_limit(value)


class LibraryApprovalRequest(BaseModel):
    payload: dict
    reviewer: str = Field(default="local-reviewer")
    notes: str = Field(default="")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "storage": "local"}


@app.get("/api/storage/schema-status")
def storage_schema_status() -> dict:
    try:
        analysis_migrations = LocalRunStore(_local_data_root()).migration_status()
        evidence_migrations = EvidenceCatalog(_local_data_root()).migration_status()
        segmentation_migrations = SegmentationOperationStore(
            _local_data_root()
        ).migration_status()
    except SchemaCompatibilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "compatible": True,
        "databases": {
            "analysis_runs": {
                "current_version": analysis_migrations[-1]["version"],
                "migrations": analysis_migrations,
            },
            "evidence_catalog": {
                "current_version": evidence_migrations[-1]["version"],
                "migrations": evidence_migrations,
            },
            "segmentation_operations": {
                "current_version": segmentation_migrations[-1]["version"],
                "migrations": segmentation_migrations,
            },
        },
    }


@app.get("/api/storage/analysis-operations")
def list_analysis_operations(
    incomplete_only: bool = False,
    limit: int = 100,
) -> dict:
    return {
        "operations": LocalRunStore(_local_data_root()).list_operations(
            incomplete_only=incomplete_only,
            limit=limit,
        )
    }


@app.get("/api/storage/segmentation-operations")
def list_segmentation_operations(
    incomplete_only: bool = False,
    limit: int = 100,
) -> dict:
    try:
        operations = SegmentationOperationStore(
            _local_data_root()
        ).list_operations(
            incomplete_only=incomplete_only,
            limit=limit,
        )
    except SchemaCompatibilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"operations": operations}


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
        "job": _agent_job_api_payload(job_store, job),
        "artifact_path": str(job_store.jobs_dir / f"{job.id}.json"),
    }


@app.get("/api/agent-jobs")
def list_agent_jobs() -> dict:
    store = AgentJobStore(_local_data_root())
    return {
        "jobs": [
            _agent_job_api_payload(store, job)
            for job in store.list_jobs()
        ]
    }


@app.patch("/api/agent-jobs/{job_id}")
def update_agent_job_status(job_id: str, request: AgentJobStatusUpdateRequest) -> dict:
    store = AgentJobStore(_local_data_root())
    try:
        job = store.update_status(job_id, request.status)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Agent job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"job": _agent_job_api_payload(store, job)}


@app.post("/api/agent-jobs/{job_id}/evidence")
def add_agent_job_evidence(job_id: str, request: AgentJobEvidenceCreateRequest) -> dict:
    try:
        evidence = AgentJobStore(_local_data_root()).add_evidence(
            job_id,
            request.model_dump(),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Agent job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
        "job": _agent_job_api_payload(job_store, job),
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
            project_source_id=request.project_source_id,
            parent_transcript_revision_id=request.parent_transcript_revision_id,
        )
    except (
        SchemaCompatibilityError,
        SegmentationOperationConflict,
        SegmentationSnapshotConflict,
        SourceBlobIntegrityError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"run": segmentation_run_to_payload(run)}


@app.post("/api/segmentation/corpus-runs")
def create_segmentation_corpus_run(
    request: SegmentationCorpusRunCreateRequest,
) -> dict:
    try:
        corpus_run = SegmentationRunStore(_local_data_root()).create_corpus_run(
            seed=request.seed,
        )
    except (
        SchemaCompatibilityError,
        SegmentationOperationConflict,
        SegmentationSnapshotConflict,
        SourceBlobIntegrityError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    project_source_id: Annotated[str, Form()] = "",
    parent_transcript_revision_id: Annotated[str, Form()] = "",
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
        source_bytes = await file.read()
        content = source_bytes.decode("utf-8")
        run = SegmentationRunStore(_local_data_root()).create_run(
            source_filename=file.filename or "descript_export.txt",
            descript_text=content,
            rule_ids=parsed_rule_ids,
            source="researcher_provided",
            source_bytes=source_bytes,
            source_media_type=file.content_type or "application/octet-stream",
            project_source_id=project_source_id,
            parent_transcript_revision_id=parent_transcript_revision_id,
        )
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail="Segmentation upload must be UTF-8 text",
        ) from exc
    except (
        SchemaCompatibilityError,
        SegmentationOperationConflict,
        SegmentationSnapshotConflict,
        SourceBlobIntegrityError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    except (
        SchemaCompatibilityError,
        SegmentationOperationConflict,
        SegmentationSnapshotConflict,
        SourceBlobIntegrityError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    try:
        stored = LocalRunStore(_local_data_root()).persist_run(run)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    except (
        SchemaCompatibilityError,
        SegmentationOperationConflict,
        SegmentationSnapshotConflict,
        SourceBlobIntegrityError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
        "job": _agent_job_api_payload(job_store, job),
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


@app.post("/api/studies/{study_id}/segmentation/runs")
def create_study_segmentation_run(
    study_id: str,
    request: SegmentationRunCreateRequest,
) -> dict:
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        run = SegmentationRunStore(root).create_run(
            source_filename=request.source_filename,
            descript_text=request.descript_text,
            rule_ids=request.rule_ids,
            source=request.source,
            project_source_id=request.project_source_id,
            parent_transcript_revision_id=request.parent_transcript_revision_id,
            workspace_id=study_id,
        )
    except _STUDY_SEGMENTATION_API_ERRORS as exc:
        _raise_study_segmentation_http_error(exc)
    return {"run": segmentation_run_to_payload(run)}


@app.get("/api/studies/{study_id}/segmentation/runs")
def list_study_segmentation_runs(study_id: str) -> dict:
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        runs = SegmentationRunStore(root).list_runs(
            expected_workspace_id=study_id,
        )
    except _STUDY_SEGMENTATION_API_ERRORS as exc:
        _raise_study_segmentation_http_error(exc)
    return {"runs": [segmentation_run_to_payload(run) for run in runs]}


@app.get("/api/studies/{study_id}/segmentation/runs/{run_id}")
def get_study_segmentation_run(study_id: str, run_id: str) -> dict:
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        run = SegmentationRunStore(root).load_run(
            run_id,
            expected_workspace_id=study_id,
        )
    except _STUDY_SEGMENTATION_READ_ERRORS as exc:
        _raise_study_segmentation_http_error(exc)
    return {"run": segmentation_run_to_payload(run)}


@app.post("/api/studies/{study_id}/segmentation/runs/{run_id}/verify")
def verify_study_segmentation_run(study_id: str, run_id: str) -> dict:
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        run = SegmentationRunStore(root).verify_run(
            run_id,
            expected_workspace_id=study_id,
        )
    except _STUDY_SEGMENTATION_READ_ERRORS as exc:
        _raise_study_segmentation_http_error(exc)
    return {"run": segmentation_run_to_payload(run)}


@app.post(
    "/api/studies/{study_id}/segmentation/runs/{run_id}/specialists/"
    "{specialist_id}/patches"
)
def submit_study_segmentation_specialist_patches(
    study_id: str,
    run_id: str,
    specialist_id: str,
    request: SegmentationSpecialistPatchRequest,
) -> dict:
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        run = SegmentationRunStore(root).apply_specialist_patches(
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
            expected_workspace_id=study_id,
        )
    except _STUDY_SEGMENTATION_READ_ERRORS as exc:
        _raise_study_segmentation_http_error(exc)
    return {"run": segmentation_run_to_payload(run)}


@app.post("/api/studies")
def create_study(request: StudyCreateRequest) -> dict:
    try:
        study = StudyWorkspaceStore(_local_data_root()).create_study(
            request.model_dump()
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="Study already exists") from exc
    return {"study": _study_payload(study)}


@app.put("/api/studies/{study_id}/qualitative/project")
def initialize_qualitative_project(
    study_id: str,
    request: QualitativeProjectInitializeRequest,
) -> dict:
    try:
        root = _local_data_root()
        QualitativeProjectDatabase(root, study_id).initialize(
            researcher_id=request.researcher_id,
            researcher_name=request.researcher_name,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Study not found") from exc
    except (SchemaCompatibilityError, StudyBatchOperationConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except QualitativeDatabaseConflict as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            raise HTTPException(status_code=404, detail="Study not found") from exc
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        status_code = 409 if "conflict" in str(exc).casefold() else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return {
        "project": {
            "project_id": study_id,
            "researcher": {
                "researcher_id": request.researcher_id,
                "display_name": request.researcher_name.strip(),
                "role": "researcher",
                "active": True,
            },
        }
    }


@app.get("/api/studies/{study_id}/qualitative/schema-status")
def qualitative_schema_status(study_id: str) -> dict:
    try:
        migrations = QualitativeProjectDatabase(
            _local_data_root(),
            study_id,
        ).migration_status()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Study not found") from exc
    except (
        SchemaCompatibilityError,
        StudyBatchOperationConflict,
        QualitativeDatabaseConflict,
        ValueError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "compatible": True,
        "project_id": study_id,
        "current_version": migrations[-1]["version"],
        "migrations": migrations,
    }


@app.post("/api/studies/{study_id}/qualitative/coding-references")
def create_qualitative_coding_reference(
    study_id: str,
    request: CodingReferenceCreateRequest,
) -> dict:
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        coding_reference = CodingReferenceService(
            root,
            study_id,
        ).create_reference(**request.model_dump())
    except (
        CodingReferenceValidationError,
        CodingReferenceNotFoundError,
        CodingReferenceConflictError,
    ) as exc:
        _raise_coding_reference_http_error(exc)
    return {"coding_reference": _coding_reference_payload(coding_reference)}


@app.get("/api/studies/{study_id}/qualitative/coding-references")
def list_qualitative_coding_references(
    study_id: str,
    raw_request: Request,
    query: Annotated[CodingReferenceListQuery, Query()],
) -> dict:
    _reject_repeated_query_parameters(
        raw_request,
        {
            "project_source_id",
            "codebook_version_id",
            "code_id",
            "created_by",
            "include_removed",
        },
    )
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        coding_references = CodingReferenceService(
            root,
            study_id,
        ).list_references(**query.model_dump())
    except (
        CodingReferenceValidationError,
        CodingReferenceNotFoundError,
        CodingReferenceConflictError,
    ) as exc:
        _raise_coding_reference_http_error(exc)
    return {
        "coding_references": [
            _coding_reference_payload(coding_reference)
            for coding_reference in coding_references
        ]
    }


@app.get(
    "/api/studies/{study_id}/qualitative/coding-references/"
    "{coding_reference_id}"
)
def get_qualitative_coding_reference(
    study_id: str,
    coding_reference_id: str,
) -> dict:
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        coding_reference = CodingReferenceService(
            root,
            study_id,
        ).read_reference(coding_reference_id)
    except (
        CodingReferenceValidationError,
        CodingReferenceNotFoundError,
        CodingReferenceConflictError,
    ) as exc:
        _raise_coding_reference_http_error(exc)
    return {"coding_reference": _coding_reference_payload(coding_reference)}


@app.delete(
    "/api/studies/{study_id}/qualitative/coding-references/"
    "{coding_reference_id}"
)
def remove_qualitative_coding_reference(
    study_id: str,
    coding_reference_id: str,
    request: CodingReferenceRemoveRequest,
) -> dict:
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        coding_reference = CodingReferenceService(
            root,
            study_id,
        ).remove_reference(
            researcher_id=request.researcher_id,
            coding_reference_id=coding_reference_id,
        )
    except (
        CodingReferenceValidationError,
        CodingReferenceNotFoundError,
        CodingReferenceConflictError,
    ) as exc:
        _raise_coding_reference_http_error(exc)
    return {"coding_reference": _coding_reference_payload(coding_reference)}


@app.post("/api/studies/{study_id}/qualitative/saved-queries")
def create_qualitative_saved_query(
    study_id: str,
    request: SavedQueryCreateRequest,
) -> dict:
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        saved_query = SavedQueryService(root, study_id).create_saved_query(
            **request.model_dump()
        )
    except (
        SavedQueryValidationError,
        SavedQueryNotFoundError,
        SavedQueryConflictError,
    ) as exc:
        _raise_saved_query_http_error(exc)
    return {"saved_query": _saved_query_payload(saved_query)}


@app.get("/api/studies/{study_id}/qualitative/saved-queries")
def list_qualitative_saved_queries(
    study_id: str,
    raw_request: Request,
    query: Annotated[SavedQueryListQuery, Query()],
) -> dict:
    _reject_repeated_review_query_parameters(
        raw_request,
        {"created_by", "limit", "cursor"},
    )
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        page = SavedQueryService(root, study_id).list_saved_queries(
            created_by=query.created_by,
            limit=_saved_query_page_limit(query.limit),
            cursor=query.cursor,
        )
    except (
        SavedQueryValidationError,
        SavedQueryNotFoundError,
        SavedQueryConflictError,
    ) as exc:
        _raise_saved_query_http_error(exc)
    return {
        "saved_queries": [
            _saved_query_payload(saved_query)
            for saved_query in page.saved_queries
        ],
        "next_cursor": page.next_cursor,
    }


@app.get(
    "/api/studies/{study_id}/qualitative/saved-queries/{saved_query_id}"
)
def get_qualitative_saved_query(
    study_id: str,
    saved_query_id: str,
) -> dict:
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        saved_query = SavedQueryService(root, study_id).read_saved_query(
            saved_query_id
        )
    except (
        SavedQueryValidationError,
        SavedQueryNotFoundError,
        SavedQueryConflictError,
    ) as exc:
        _raise_saved_query_http_error(exc)
    return {"saved_query": _saved_query_payload(saved_query)}


@app.put(
    "/api/studies/{study_id}/qualitative/researchers/{researcher_id}"
)
def register_qualitative_researcher(
    study_id: str,
    researcher_id: str,
    request: ResearcherRegistrationRequest,
) -> dict:
    root = _local_data_root()
    _require_review_api_study(root, study_id)
    try:
        researcher = ResearchReviewService(root, study_id).create_researcher(
            researcher_id=researcher_id,
            **request.model_dump(),
        )
    except _REVIEW_API_ERRORS as exc:
        _raise_review_http_error(exc)
    return {"researcher": _researcher_payload(researcher)}


@app.get("/api/studies/{study_id}/qualitative/researchers")
def list_qualitative_researchers(
    study_id: str,
    raw_request: Request,
    query: Annotated[ResearcherListQuery, Query()],
) -> dict:
    _reject_repeated_review_query_parameters(
        raw_request,
        {"role", "active", "limit", "cursor"},
    )
    root = _local_data_root()
    _require_review_api_study(root, study_id)
    try:
        page = ResearchReviewService(root, study_id).list_researchers(
            role=query.role,
            active=_review_active_filter(query.active),
            limit=_review_page_limit(query.limit),
            cursor=query.cursor,
        )
    except _REVIEW_API_ERRORS as exc:
        _raise_review_http_error(exc)
    return {
        "researchers": [
            _researcher_payload(researcher) for researcher in page.researchers
        ],
        "next_cursor": page.next_cursor,
    }


@app.get(
    "/api/studies/{study_id}/qualitative/researchers/{researcher_id}"
)
def get_qualitative_researcher(study_id: str, researcher_id: str) -> dict:
    root = _local_data_root()
    _require_review_api_study(root, study_id)
    try:
        researcher = ResearchReviewService(root, study_id).read_researcher(
            researcher_id
        )
    except _REVIEW_API_ERRORS as exc:
        _raise_review_http_error(exc)
    return {"researcher": _researcher_payload(researcher)}


@app.post("/api/studies/{study_id}/qualitative/agent-suggestions")
def create_qualitative_agent_suggestion(
    study_id: str,
    request: AgentSuggestionCreateRequest,
) -> dict:
    root = _local_data_root()
    _require_review_api_study(root, study_id)
    try:
        snapshot = ResearchReviewService(
            root,
            study_id,
        ).create_agent_suggestion(**request.model_dump())
    except _REVIEW_API_ERRORS as exc:
        _raise_review_http_error(exc)
    return {"agent_suggestion": _agent_suggestion_snapshot_payload(snapshot)}


@app.get("/api/studies/{study_id}/qualitative/agent-suggestions")
def list_qualitative_agent_suggestions(
    study_id: str,
    raw_request: Request,
    query: Annotated[AgentSuggestionListQuery, Query()],
) -> dict:
    _reject_repeated_review_query_parameters(
        raw_request,
        {
            "project_source_id",
            "codebook_version_id",
            "code_id",
            "created_by",
            "origin_kind",
            "limit",
            "cursor",
        },
    )
    root = _local_data_root()
    _require_review_api_study(root, study_id)
    try:
        page = ResearchReviewService(root, study_id).list_agent_suggestions(
            project_source_id=query.project_source_id,
            codebook_version_id=query.codebook_version_id,
            code_id=query.code_id,
            created_by=query.created_by,
            origin_kind=query.origin_kind,
            limit=_review_page_limit(query.limit),
            cursor=query.cursor,
        )
    except _REVIEW_API_ERRORS as exc:
        _raise_review_http_error(exc)
    return {
        "agent_suggestions": [
            _agent_suggestion_snapshot_payload(snapshot)
            for snapshot in page.agent_suggestions
        ],
        "next_cursor": page.next_cursor,
    }


@app.get(
    "/api/studies/{study_id}/qualitative/agent-suggestions/"
    "{agent_suggestion_id}"
)
def get_qualitative_agent_suggestion(
    study_id: str,
    agent_suggestion_id: str,
) -> dict:
    root = _local_data_root()
    _require_review_api_study(root, study_id)
    try:
        snapshot = ResearchReviewService(
            root,
            study_id,
        ).read_agent_suggestion(agent_suggestion_id)
    except _REVIEW_API_ERRORS as exc:
        _raise_review_http_error(exc)
    return {"agent_suggestion": _agent_suggestion_snapshot_payload(snapshot)}


@app.post(
    "/api/studies/{study_id}/qualitative/agent-suggestions/"
    "{agent_suggestion_id}/decisions"
)
def append_qualitative_reviewer_decision(
    study_id: str,
    agent_suggestion_id: str,
    request: ReviewerDecisionCreateRequest,
) -> dict:
    root = _local_data_root()
    _require_review_api_study(root, study_id)
    try:
        decision = ResearchReviewService(
            root,
            study_id,
        ).append_reviewer_decision(
            agent_suggestion_id=agent_suggestion_id,
            **request.model_dump(),
        )
    except _REVIEW_API_ERRORS as exc:
        _raise_review_http_error(exc)
    return {"reviewer_decision": _reviewer_decision_payload(decision)}


@app.get(
    "/api/studies/{study_id}/qualitative/agent-suggestions/"
    "{agent_suggestion_id}/decisions"
)
def list_qualitative_reviewer_decisions(
    study_id: str,
    agent_suggestion_id: str,
    raw_request: Request,
    query: Annotated[ReviewerDecisionListQuery, Query()],
) -> dict:
    _reject_repeated_review_query_parameters(
        raw_request,
        {"limit", "cursor"},
    )
    root = _local_data_root()
    _require_review_api_study(root, study_id)
    try:
        page = ResearchReviewService(root, study_id).list_reviewer_decisions(
            agent_suggestion_id,
            limit=_review_page_limit(query.limit),
            cursor=query.cursor,
        )
    except _REVIEW_API_ERRORS as exc:
        _raise_review_http_error(exc)
    return {
        "reviewer_decisions": [
            _reviewer_decision_payload(decision)
            for decision in page.reviewer_decisions
        ],
        "next_cursor": page.next_cursor,
    }


@app.post("/api/studies/{study_id}/qualitative/memos")
def create_qualitative_memo(
    study_id: str,
    request: MemoCreateRequest,
) -> dict:
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        snapshot = NoteService(root, study_id).create_note(
            note_kind="memo",
            researcher_id=request.researcher_id,
            title=request.title,
            body=request.body,
            target=request.target.model_dump(exclude_none=True),
        )
    except _NOTE_API_ERRORS as exc:
        _raise_note_http_error(exc)
    return {"memo": _note_snapshot_payload(snapshot)}


@app.get("/api/studies/{study_id}/qualitative/memos")
def list_qualitative_memos(
    study_id: str,
    raw_request: Request,
    query: Annotated[NoteListQuery, Query()],
) -> dict:
    _reject_repeated_query_parameters(
        raw_request,
        {"target_kind", "created_by", "include_removed", "limit", "cursor"},
    )
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        snapshots, next_cursor = NoteService(root, study_id).list_notes(
            note_kind="memo",
            **query.model_dump(),
        )
    except _NOTE_API_ERRORS as exc:
        _raise_note_http_error(exc)
    return {
        "memos": [_note_snapshot_payload(snapshot) for snapshot in snapshots],
        "next_cursor": next_cursor,
    }


@app.get("/api/studies/{study_id}/qualitative/memos/{memo_id}")
def get_qualitative_memo(study_id: str, memo_id: str) -> dict:
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        snapshot = NoteService(root, study_id).read_note(
            note_kind="memo",
            note_id=memo_id,
        )
    except _NOTE_API_ERRORS as exc:
        _raise_note_http_error(exc)
    return {"memo": _note_snapshot_payload(snapshot)}


@app.post(
    "/api/studies/{study_id}/qualitative/memos/{memo_id}/revisions"
)
def revise_qualitative_memo(
    study_id: str,
    memo_id: str,
    request: MemoRevisionRequest,
) -> dict:
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        snapshot = NoteService(root, study_id).revise_note(
            note_kind="memo",
            note_id=memo_id,
            researcher_id=request.researcher_id,
            expected_revision_number=request.expected_revision_number,
            title=request.title,
            body=request.body,
        )
    except _NOTE_API_ERRORS as exc:
        _raise_note_http_error(exc)
    return {"memo": _note_snapshot_payload(snapshot)}


@app.get(
    "/api/studies/{study_id}/qualitative/memos/{memo_id}/revisions"
)
def list_qualitative_memo_revisions(
    study_id: str,
    memo_id: str,
    raw_request: Request,
    query: Annotated[NoteRevisionListQuery, Query()],
) -> dict:
    _reject_repeated_query_parameters(raw_request, {"limit", "cursor"})
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        revisions, next_cursor = NoteService(root, study_id).list_revisions(
            note_kind="memo",
            note_id=memo_id,
            **query.model_dump(),
        )
    except _NOTE_API_ERRORS as exc:
        _raise_note_http_error(exc)
    return {
        "revisions": [_note_revision_payload(revision) for revision in revisions],
        "next_cursor": next_cursor,
    }


@app.delete("/api/studies/{study_id}/qualitative/memos/{memo_id}")
def remove_qualitative_memo(
    study_id: str,
    memo_id: str,
    request: NoteRemoveRequest,
) -> dict:
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        snapshot = NoteService(root, study_id).remove_note(
            note_kind="memo",
            note_id=memo_id,
            researcher_id=request.researcher_id,
        )
    except _NOTE_API_ERRORS as exc:
        _raise_note_http_error(exc)
    return {"memo": _note_snapshot_payload(snapshot)}


@app.post("/api/studies/{study_id}/qualitative/annotations")
def create_qualitative_annotation(
    study_id: str,
    request: AnnotationCreateRequest,
) -> dict:
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        snapshot = NoteService(root, study_id).create_note(
            note_kind="annotation",
            researcher_id=request.researcher_id,
            title="",
            body=request.body,
            target=request.target.model_dump(exclude_none=True),
        )
    except _NOTE_API_ERRORS as exc:
        _raise_note_http_error(exc)
    return {"annotation": _note_snapshot_payload(snapshot)}


@app.get("/api/studies/{study_id}/qualitative/annotations")
def list_qualitative_annotations(
    study_id: str,
    raw_request: Request,
    query: Annotated[NoteListQuery, Query()],
) -> dict:
    _reject_repeated_query_parameters(
        raw_request,
        {"target_kind", "created_by", "include_removed", "limit", "cursor"},
    )
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        snapshots, next_cursor = NoteService(root, study_id).list_notes(
            note_kind="annotation",
            **query.model_dump(),
        )
    except _NOTE_API_ERRORS as exc:
        _raise_note_http_error(exc)
    return {
        "annotations": [
            _note_snapshot_payload(snapshot) for snapshot in snapshots
        ],
        "next_cursor": next_cursor,
    }


@app.get(
    "/api/studies/{study_id}/qualitative/annotations/{annotation_id}"
)
def get_qualitative_annotation(study_id: str, annotation_id: str) -> dict:
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        snapshot = NoteService(root, study_id).read_note(
            note_kind="annotation",
            note_id=annotation_id,
        )
    except _NOTE_API_ERRORS as exc:
        _raise_note_http_error(exc)
    return {"annotation": _note_snapshot_payload(snapshot)}


@app.post(
    "/api/studies/{study_id}/qualitative/annotations/"
    "{annotation_id}/revisions"
)
def revise_qualitative_annotation(
    study_id: str,
    annotation_id: str,
    request: AnnotationRevisionRequest,
) -> dict:
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        snapshot = NoteService(root, study_id).revise_note(
            note_kind="annotation",
            note_id=annotation_id,
            researcher_id=request.researcher_id,
            expected_revision_number=request.expected_revision_number,
            title="",
            body=request.body,
        )
    except _NOTE_API_ERRORS as exc:
        _raise_note_http_error(exc)
    return {"annotation": _note_snapshot_payload(snapshot)}


@app.get(
    "/api/studies/{study_id}/qualitative/annotations/"
    "{annotation_id}/revisions"
)
def list_qualitative_annotation_revisions(
    study_id: str,
    annotation_id: str,
    raw_request: Request,
    query: Annotated[NoteRevisionListQuery, Query()],
) -> dict:
    _reject_repeated_query_parameters(raw_request, {"limit", "cursor"})
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        revisions, next_cursor = NoteService(root, study_id).list_revisions(
            note_kind="annotation",
            note_id=annotation_id,
            **query.model_dump(),
        )
    except _NOTE_API_ERRORS as exc:
        _raise_note_http_error(exc)
    return {
        "revisions": [_note_revision_payload(revision) for revision in revisions],
        "next_cursor": next_cursor,
    }


@app.delete(
    "/api/studies/{study_id}/qualitative/annotations/{annotation_id}"
)
def remove_qualitative_annotation(
    study_id: str,
    annotation_id: str,
    request: NoteRemoveRequest,
) -> dict:
    root = _local_data_root()
    _require_api_study(root, study_id)
    try:
        snapshot = NoteService(root, study_id).remove_note(
            note_kind="annotation",
            note_id=annotation_id,
            researcher_id=request.researcher_id,
        )
    except _NOTE_API_ERRORS as exc:
        _raise_note_http_error(exc)
    return {"annotation": _note_snapshot_payload(snapshot)}


@app.post("/api/studies/{study_id}/qualitative/cases")
def create_qualitative_case(study_id: str, request: CaseCreateRequest) -> dict:
    try:
        case = CaseService(_local_data_root(), study_id).create_case(
            researcher_id=request.researcher_id,
            case_kind=request.case_kind,
            label=request.label,
            description=request.description,
        )
    except _CASE_API_ERRORS as exc:
        _raise_case_http_error(exc)
    return {"case": _case_payload(case)}


@app.get("/api/studies/{study_id}/qualitative/cases")
def list_qualitative_cases(study_id: str) -> dict:
    try:
        cases = CaseService(_local_data_root(), study_id).list_cases()
    except _CASE_API_ERRORS as exc:
        _raise_case_http_error(exc)
    return {"cases": [_case_payload(case) for case in cases]}


@app.get("/api/studies/{study_id}/qualitative/cases/{case_id}")
def get_qualitative_case(study_id: str, case_id: str) -> dict:
    try:
        snapshot = CaseService(_local_data_root(), study_id).read_case(
            case_id=case_id,
        )
    except _CASE_API_ERRORS as exc:
        _raise_case_http_error(exc)
    return _case_snapshot_payload(snapshot)


@app.put("/api/studies/{study_id}/qualitative/cases/{case_id}")
def update_qualitative_case(
    study_id: str,
    case_id: str,
    request: CaseUpdateRequest,
) -> dict:
    try:
        case = CaseService(_local_data_root(), study_id).update_case(
            researcher_id=request.researcher_id,
            case_id=case_id,
            case_kind=request.case_kind,
            label=request.label,
            description=request.description,
        )
    except _CASE_API_ERRORS as exc:
        _raise_case_http_error(exc)
    return {"case": _case_payload(case)}


@app.post("/api/studies/{study_id}/qualitative/attribute-definitions")
def create_qualitative_attribute_definition(
    study_id: str,
    request: AttributeDefinitionCreateRequest,
) -> dict:
    try:
        definition = CaseService(
            _local_data_root(),
            study_id,
        ).create_attribute_definition(
            researcher_id=request.researcher_id,
            attribute_key=request.attribute_key,
            label=request.label,
            value_type=request.value_type,
            allowed_values=request.allowed_values,
            required=request.required,
        )
    except _CASE_API_ERRORS as exc:
        _raise_case_http_error(exc)
    return {"attribute_definition": _attribute_definition_payload(definition)}


@app.get("/api/studies/{study_id}/qualitative/attribute-definitions")
def list_qualitative_attribute_definitions(study_id: str) -> dict:
    try:
        definitions = CaseService(
            _local_data_root(),
            study_id,
        ).list_attribute_definitions()
    except _CASE_API_ERRORS as exc:
        _raise_case_http_error(exc)
    return {
        "attribute_definitions": [
            _attribute_definition_payload(definition)
            for definition in definitions
        ]
    }


@app.put(
    "/api/studies/{study_id}/qualitative/cases/{case_id}/attributes/"
    "{attribute_definition_id}"
)
def set_qualitative_case_attribute(
    study_id: str,
    case_id: str,
    attribute_definition_id: str,
    request: AttributeValueSetRequest,
) -> dict:
    try:
        attribute_value = CaseService(
            _local_data_root(),
            study_id,
        ).set_attribute_value(
            researcher_id=request.researcher_id,
            case_id=case_id,
            attribute_definition_id=attribute_definition_id,
            value=request.value,
        )
    except _CASE_API_ERRORS as exc:
        _raise_case_http_error(exc)
    return {"attribute_value": _case_attribute_value_payload(attribute_value)}


@app.delete(
    "/api/studies/{study_id}/qualitative/cases/{case_id}/attributes/"
    "{attribute_definition_id}"
)
def clear_qualitative_case_attribute(
    study_id: str,
    case_id: str,
    attribute_definition_id: str,
    request: ResearcherActionRequest,
) -> dict:
    try:
        CaseService(_local_data_root(), study_id).clear_attribute_value(
            researcher_id=request.researcher_id,
            case_id=case_id,
            attribute_definition_id=attribute_definition_id,
        )
    except _CASE_API_ERRORS as exc:
        _raise_case_http_error(exc)
    return {
        "cleared": {
            "case_id": case_id.strip(),
            "attribute_definition_id": attribute_definition_id.strip(),
        }
    }


@app.put("/api/studies/{study_id}/qualitative/cases/{case_id}/sources")
def link_qualitative_case_source(
    study_id: str,
    case_id: str,
    request: SourceLinkActionRequest,
) -> dict:
    try:
        source_link = CaseService(_local_data_root(), study_id).link_source(
            researcher_id=request.researcher_id,
            case_id=case_id,
            project_source_id=request.project_source_id,
        )
    except _CASE_API_ERRORS as exc:
        _raise_case_http_error(exc)
    return {"source_link": _source_case_link_payload(source_link)}


@app.delete("/api/studies/{study_id}/qualitative/cases/{case_id}/sources")
def unlink_qualitative_case_source(
    study_id: str,
    case_id: str,
    request: SourceLinkActionRequest,
) -> dict:
    try:
        CaseService(_local_data_root(), study_id).unlink_source(
            researcher_id=request.researcher_id,
            case_id=case_id,
            project_source_id=request.project_source_id,
        )
    except _CASE_API_ERRORS as exc:
        _raise_case_http_error(exc)
    return {
        "unlinked": {
            "case_id": case_id.strip(),
            "project_source_id": request.project_source_id,
        }
    }


@app.post("/api/studies/{study_id}/qualitative/codebooks")
def create_codebook(study_id: str, request: CodebookCreateRequest) -> dict:
    try:
        codebook = CodebookService(_local_data_root(), study_id).create_codebook(
            researcher_id=request.researcher_id,
            title=request.title,
            description=request.description,
        )
    except _CODEBOOK_API_ERRORS as exc:
        _raise_codebook_http_error(exc)
    return {"codebook": _codebook_payload(codebook)}


@app.get("/api/studies/{study_id}/qualitative/codebooks")
def list_codebooks(study_id: str) -> dict:
    try:
        codebooks = CodebookService(_local_data_root(), study_id).list_codebooks()
    except _CODEBOOK_API_ERRORS as exc:
        _raise_codebook_http_error(exc)
    return {"codebooks": [_codebook_payload(codebook) for codebook in codebooks]}


@app.post("/api/studies/{study_id}/qualitative/codebooks/import")
def import_codebook_version(
    study_id: str,
    request: CodebookImportRequest,
) -> dict:
    try:
        snapshot = CodebookService(_local_data_root(), study_id).import_version(
            researcher_id=request.researcher_id,
            document=request.document,
        )
    except _CODEBOOK_API_ERRORS as exc:
        _raise_codebook_http_error(exc)
    return _codebook_snapshot_payload(snapshot)


@app.post(
    "/api/studies/{study_id}/qualitative/codebooks/{codebook_id}/versions"
)
def create_codebook_version(
    study_id: str,
    codebook_id: str,
    request: CodebookVersionCreateRequest,
) -> dict:
    try:
        service = CodebookService(_local_data_root(), study_id)
        if request.based_on_version_id is None:
            snapshot = service.create_draft(
                researcher_id=request.researcher_id,
                codebook_id=codebook_id,
            )
        else:
            snapshot = service.derive_draft(
                researcher_id=request.researcher_id,
                codebook_id=codebook_id,
                based_on_version_id=request.based_on_version_id,
            )
    except _CODEBOOK_API_ERRORS as exc:
        _raise_codebook_http_error(exc)
    return _codebook_snapshot_payload(snapshot)


@app.get(
    "/api/studies/{study_id}/qualitative/codebooks/{codebook_id}/versions/"
    "{codebook_version_id}"
)
def get_codebook_version(
    study_id: str,
    codebook_id: str,
    codebook_version_id: str,
) -> dict:
    try:
        snapshot = CodebookService(_local_data_root(), study_id).read_version(
            codebook_id=codebook_id,
            codebook_version_id=codebook_version_id,
        )
    except _CODEBOOK_API_ERRORS as exc:
        _raise_codebook_http_error(exc)
    return _codebook_snapshot_payload(snapshot)


@app.get(
    "/api/studies/{study_id}/qualitative/codebooks/{codebook_id}/versions/"
    "{codebook_version_id}/export"
)
def export_codebook_version(
    study_id: str,
    codebook_id: str,
    codebook_version_id: str,
) -> dict:
    try:
        return CodebookService(_local_data_root(), study_id).export_version(
            codebook_id=codebook_id,
            codebook_version_id=codebook_version_id,
        )
    except _CODEBOOK_API_ERRORS as exc:
        _raise_codebook_http_error(exc)


@app.post(
    "/api/studies/{study_id}/qualitative/codebooks/{codebook_id}/versions/"
    "{codebook_version_id}/codes"
)
def add_codebook_code(
    study_id: str,
    codebook_id: str,
    codebook_version_id: str,
    request: CodeCreateRequest,
) -> dict:
    values = request.model_dump()
    researcher_id = values.pop("researcher_id")
    try:
        code = CodebookService(_local_data_root(), study_id).add_code(
            researcher_id=researcher_id,
            codebook_id=codebook_id,
            codebook_version_id=codebook_version_id,
            **values,
        )
    except _CODEBOOK_API_ERRORS as exc:
        _raise_codebook_http_error(exc)
    return {"code": _code_payload(code)}


@app.put(
    "/api/studies/{study_id}/qualitative/codebooks/{codebook_id}/versions/"
    "{codebook_version_id}/codes/{code_id}"
)
def update_codebook_code(
    study_id: str,
    codebook_id: str,
    codebook_version_id: str,
    code_id: str,
    request: CodeUpdateRequest,
) -> dict:
    values = request.model_dump()
    researcher_id = values.pop("researcher_id")
    try:
        code = CodebookService(_local_data_root(), study_id).update_code(
            researcher_id=researcher_id,
            codebook_id=codebook_id,
            codebook_version_id=codebook_version_id,
            code_id=code_id,
            **values,
        )
    except _CODEBOOK_API_ERRORS as exc:
        _raise_codebook_http_error(exc)
    return {"code": _code_payload(code)}


@app.post(
    "/api/studies/{study_id}/qualitative/codebooks/{codebook_id}/versions/"
    "{codebook_version_id}/freeze"
)
def freeze_codebook_version(
    study_id: str,
    codebook_id: str,
    codebook_version_id: str,
    request: CodebookFreezeRequest,
) -> dict:
    try:
        snapshot = CodebookService(_local_data_root(), study_id).freeze_version(
            researcher_id=request.researcher_id,
            codebook_id=codebook_id,
            codebook_version_id=codebook_version_id,
        )
    except _CODEBOOK_API_ERRORS as exc:
        _raise_codebook_http_error(exc)
    return _codebook_snapshot_payload(snapshot)


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
    except (SchemaCompatibilityError, StudyBatchOperationConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    except (
        SchemaCompatibilityError,
        StudyBatchOperationConflict,
        StudyBatchSnapshotConflict,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"batches": [_study_batch_summary_payload(batch) for batch in batches]}


@app.get("/api/studies/{study_id}/batch-operations/schema-status")
def study_batch_operation_schema_status(study_id: str) -> dict:
    try:
        migrations = StudyBatchOperationStore(
            _local_data_root(),
            study_id,
        ).migration_status()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Study not found") from exc
    except (SchemaCompatibilityError, StudyBatchOperationConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "compatible": True,
        "study_id": study_id,
        "current_version": migrations[-1]["version"],
        "migrations": migrations,
    }


@app.get("/api/studies/{study_id}/batch-operations")
def list_study_batch_operations(
    study_id: str,
    incomplete_only: bool = False,
    limit: int = 100,
) -> dict:
    try:
        operations = StudyBatchOperationStore(
            _local_data_root(),
            study_id,
        ).list_operations(
            incomplete_only=incomplete_only,
            limit=limit,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Study not found") from exc
    except (SchemaCompatibilityError, StudyBatchOperationConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"operations": operations}


@app.get("/api/studies/{study_id}/batches/{batch_id}")
def get_study_batch(study_id: str, batch_id: str) -> dict:
    try:
        batch = StudyWorkspaceStore(_local_data_root()).load_batch(study_id, batch_id)
        payload = _study_batch_payload(batch)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Study batch not found") from exc
    except (
        SchemaCompatibilityError,
        StudyBatchOperationConflict,
        StudyBatchSnapshotConflict,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return payload


@app.get("/api/studies/{study_id}/batches/{batch_id}/runs")
def list_study_batch_runs(study_id: str, batch_id: str) -> dict:
    try:
        runs = StudyWorkspaceStore(_local_data_root()).list_batch_runs(
            study_id,
            batch_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Study batch not found") from exc
    except (
        SchemaCompatibilityError,
        StudyBatchOperationConflict,
        StudyBatchSnapshotConflict,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    except (
        SchemaCompatibilityError,
        StudyBatchOperationConflict,
        StudyBatchSnapshotConflict,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    except (
        SchemaCompatibilityError,
        StudyBatchOperationConflict,
        StudySkillPackVersionConflict,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
            batch_id=request.batch_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Study artifact not found") from exc
    except (
        SchemaCompatibilityError,
        SourceBlobIntegrityError,
        StudyBatchOperationConflict,
        StudyBatchSnapshotConflict,
        StudySkillPackVersionConflict,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _study_batch_payload(batch)


@app.post("/api/studies/{study_id}/batches/files")
async def create_study_file_batch(
    study_id: str,
    skill_pack_version_id: Annotated[str, Form()],
    files: Annotated[list[UploadFile], File()],
    metadata: Annotated[str, Form()] = "{}",
    batch_id: Annotated[
        str | None,
        Form(pattern=STUDY_BATCH_ID_PATTERN),
    ] = None,
) -> dict:
    try:
        parsed_metadata = _batch_metadata_from_json(metadata)
        transcripts = []
        for index, file in enumerate(files):
            content, source_bytes, source_media_type = await _extract_upload(file)
            transcripts.append(
                {
                    "source_filename": file.filename or f"transcript_{index + 1}",
                    "content": content,
                    "source_bytes": source_bytes,
                    "source_media_type": source_media_type,
                    "metadata": parsed_metadata.get(file.filename or "", {}),
                }
            )
        batch = StudyWorkspaceStore(_local_data_root()).run_text_batch(
            study_id,
            skill_pack_version_id,
            transcripts,
            batch_id=batch_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Study artifact not found") from exc
    except (
        SchemaCompatibilityError,
        SourceBlobIntegrityError,
        StudyBatchOperationConflict,
        StudyBatchSnapshotConflict,
        StudySkillPackVersionConflict,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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


@app.post("/api/studies/{study_id}/backup")
def backup_study(study_id: str) -> dict:
    try:
        backup = ProjectArchiveStore(_local_data_root()).create_archive(study_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Study not found") from exc
    except ProjectArchiveConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="Project archive state conflicts with stored data",
        ) from exc
    except ProjectArchiveError as exc:
        raise HTTPException(
            status_code=400,
            detail="Project archive request is invalid",
        ) from exc
    return {
        "backup": {
            "study_id": backup.study_id,
            "archive_path": str(backup.archive_path),
            "archive_sha256": backup.archive_sha256,
            "member_count": backup.member_count,
            "created_at": backup.created_at,
        }
    }


@app.post("/api/studies/restore")
async def restore_study(file: Annotated[UploadFile, File()]) -> dict:
    archive_bytes = await file.read(MAX_ARCHIVE_FILE_BYTES + 1)
    if len(archive_bytes) > MAX_ARCHIVE_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Study archive is too large")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".nlpstudy.zip") as tmp:
        tmp.write(archive_bytes)
        archive_path = Path(tmp.name)
    try:
        restored = ProjectArchiveStore(_local_data_root()).restore_archive(
            archive_path
        )
    except FileExistsError as exc:
        raise HTTPException(
            status_code=409,
            detail="Project restore conflicts with stored data",
        ) from exc
    except ProjectArchiveConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="Project restore conflicts with stored data",
        ) from exc
    except ProjectArchiveError as exc:
        raise HTTPException(
            status_code=400,
            detail="Project restore request is invalid",
        ) from exc
    finally:
        archive_path.unlink(missing_ok=True)
    return {
        "restore": {
            "study_id": restored.study_id,
            "study_dir": str(restored.study_dir),
            "import_count": restored.import_count,
            "blob_count": restored.blob_count,
            "audit_event_count": restored.audit_event_count,
        }
    }


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
    project_source_id: Annotated[str, Form()] = "",
    parent_transcript_revision_id: Annotated[str, Form()] = "",
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

    content, source_bytes, source_media_type = await _extract_upload(file)

    run = _execute_or_400(
        content,
        parsed_config,
        source_filename=file.filename or "transcript",
        source_bytes=source_bytes,
        source_media_type=source_media_type,
        project_source_id=project_source_id,
        parent_transcript_revision_id=parent_transcript_revision_id,
    )
    try:
        stored = LocalRunStore(_local_data_root()).persist_run(
            run,
            source_bytes=source_bytes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
        project_source_id=request.project_source_id,
        parent_transcript_revision_id=request.parent_transcript_revision_id,
    )
    try:
        stored = LocalRunStore(_local_data_root()).persist_run(run)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _run_response(run, stored)


@app.get("/api/runs")
def list_runs() -> dict:
    return {"runs": LocalRunStore(_local_data_root()).list_runs()}


@app.get("/api/evidence/imports")
def list_evidence_imports() -> dict:
    return {"imports": EvidenceCatalog(_local_data_root()).list_imports()}


@app.get("/api/evidence/sources/{project_source_id}")
def get_evidence_source_history(project_source_id: str) -> dict:
    try:
        return EvidenceCatalog(_local_data_root()).source_history(project_source_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Evidence source not found") from exc


@app.get("/api/evidence/blobs/{source_blob_sha256}/verify")
def verify_evidence_source_blob(source_blob_sha256: str) -> dict:
    try:
        content = SourceBlobStore(_local_data_root()).read_verified(
            source_blob_sha256
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Source blob not found") from exc
    except SourceBlobIntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "source_blob_sha256": source_blob_sha256,
        "verified": True,
        "size_bytes": len(content),
    }


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


def _execute_or_400(
    content: str,
    config: StudyConfig,
    source_filename: str,
    *,
    source_bytes: bytes | None = None,
    source_media_type: str = "text/plain",
    project_source_id: str = "",
    parent_transcript_revision_id: str = "",
):
    try:
        return execute_analysis(
            content,
            config,
            source_filename,
            source_bytes=source_bytes,
            source_media_type=source_media_type,
            project_source_id=project_source_id,
            parent_transcript_revision_id=parent_transcript_revision_id,
        )
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


async def _extract_upload(file: UploadFile) -> tuple[str, bytes, str]:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".txt", ".docx"}:
        raise ValueError("Only DOCX and TXT uploads are supported")
    source_bytes = await file.read()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(source_bytes)
        tmp_path = Path(tmp.name)
    try:
        return (
            extract_transcript_text(tmp_path),
            source_bytes,
            file.content_type or "application/octet-stream",
        )
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
        "import_id": run.import_id,
        "project_source_id": run.project_source_id,
        "parent_transcript_revision_id": run.parent_transcript_revision_id,
        "workspace_id": run.workspace_id,
        "source_blob_sha256": run.source_blob_sha256,
        "source_media_type": run.source_media_type,
        "source_id": run.source_id,
        "transcript_sha256": run.transcript_sha256,
        "transcript_revision_id": run.transcript_revision_id,
        "evidence_set_id": stored.evidence_set_id,
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


def _agent_job_api_payload(store: AgentJobStore, job: AgentJob) -> dict:
    return {
        **agent_job_to_payload(job),
        "available_transitions": store.available_transitions(job.id),
    }


def _require_api_study(root: Path, study_id: str) -> None:
    try:
        StudyWorkspaceStore(root).load_study(study_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Study not found") from exc
    except StudyWorkspaceConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="Study storage is unavailable or invalid",
        ) from exc


def _require_review_api_study(root: Path, study_id: str) -> None:
    try:
        StudyWorkspaceStore(root).load_study(study_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=404,
            detail="Review dependency was not found",
        ) from exc
    except StudyWorkspaceConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="Review state conflicts with stored data",
        ) from exc


def _raise_study_segmentation_http_error(exc: Exception) -> NoReturn:
    if isinstance(exc, FileNotFoundError):
        raise HTTPException(
            status_code=404,
            detail="Segmentation run not found",
        ) from exc
    if isinstance(exc, _STUDY_SEGMENTATION_CONFLICT_ERRORS):
        raise HTTPException(
            status_code=409,
            detail="Segmentation state conflicts with stored data",
        ) from exc
    raise HTTPException(
        status_code=400,
        detail="Segmentation request is invalid",
    ) from exc


def _raise_coding_reference_http_error(exc: Exception) -> NoReturn:
    if isinstance(exc, CodingReferenceValidationError):
        raise HTTPException(
            status_code=400,
            detail="Coding reference request is invalid",
        ) from exc
    if isinstance(exc, CodingReferenceNotFoundError):
        raise HTTPException(
            status_code=404,
            detail="Coding reference dependency was not found",
        ) from exc
    raise HTTPException(
        status_code=409,
        detail="Coding reference state conflicts with stored data",
    ) from exc


def _raise_saved_query_http_error(exc: Exception) -> NoReturn:
    if isinstance(exc, SavedQueryValidationError):
        raise HTTPException(
            status_code=400,
            detail="Saved query request is invalid",
        ) from exc
    if isinstance(exc, SavedQueryNotFoundError):
        raise HTTPException(
            status_code=404,
            detail="Saved query dependency was not found",
        ) from exc
    raise HTTPException(
        status_code=409,
        detail="Saved query state conflicts with stored data",
    ) from exc


def _raise_note_http_error(exc: Exception) -> NoReturn:
    if isinstance(exc, NoteValidationError):
        raise HTTPException(
            status_code=400,
            detail="Note request is invalid",
        ) from exc
    if isinstance(exc, NoteNotFoundError):
        raise HTTPException(
            status_code=404,
            detail="Note dependency was not found",
        ) from exc
    raise HTTPException(
        status_code=409,
        detail="Note state conflicts with stored data",
    ) from exc


def _raise_review_http_error(exc: Exception) -> NoReturn:
    if isinstance(exc, ReviewValidationError):
        raise HTTPException(
            status_code=400,
            detail="Review request is invalid",
        ) from exc
    if isinstance(exc, ReviewNotFoundError):
        raise HTTPException(
            status_code=404,
            detail="Review dependency was not found",
        ) from exc
    raise HTTPException(
        status_code=409,
        detail="Review state conflicts with stored data",
    ) from exc


def _review_active_filter(value: str | None) -> bool | None:
    if value is None:
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    raise ReviewValidationError("active must be true or false")


def _review_page_limit(value: str) -> int:
    if (
        not value
        or not value.isascii()
        or not value.isdecimal()
        or value.startswith("0")
    ):
        raise ReviewValidationError("limit must be a canonical ASCII decimal")
    parsed = int(value)
    if not 1 <= parsed <= 50:
        raise ReviewValidationError("limit must be between 1 and 50")
    return parsed


def _saved_query_page_limit(value: str) -> int:
    if (
        not value
        or len(value) > 2
        or not value.isascii()
        or not value.isdecimal()
        or value.startswith("0")
    ):
        raise SavedQueryValidationError(
            "limit must be a canonical ASCII decimal"
        )
    parsed = int(value)
    if not 1 <= parsed <= 50:
        raise SavedQueryValidationError("limit must be between 1 and 50")
    return parsed


def _canonical_note_page_limit(value: object) -> int:
    if type(value) is int:
        parsed = value
    elif (
        isinstance(value, str)
        and value.isascii()
        and value.isdecimal()
        and (value == "0" or not value.startswith("0"))
    ):
        parsed = int(value)
    else:
        raise ValueError("limit must be a canonical ASCII decimal")
    if not 1 <= parsed <= 50:
        raise ValueError("limit must be between 1 and 50")
    return parsed


def _reject_repeated_query_parameters(
    request: Request,
    parameter_names: set[str],
) -> None:
    if any(
        len(request.query_params.getlist(parameter_name)) > 1
        for parameter_name in parameter_names
    ):
        raise HTTPException(
            status_code=422,
            detail="Query parameters must contain one scalar value",
        )


def _reject_repeated_review_query_parameters(
    request: Request,
    parameter_names: set[str],
) -> None:
    if any(
        len(request.query_params.getlist(parameter_name)) > 1
        for parameter_name in parameter_names
    ):
        raise HTTPException(
            status_code=422,
            detail="Request validation failed",
        )


def _coding_reference_payload(coding_reference) -> dict:
    return {
        "coding_reference_id": coding_reference.coding_reference_id,
        "project_id": coding_reference.project_id,
        "project_source_id": coding_reference.project_source_id,
        "transcript_revision_id": coding_reference.transcript_revision_id,
        "evidence_set_id": coding_reference.evidence_set_id,
        "target_kind": coding_reference.target_kind,
        "passage_id": coding_reference.passage_id,
        "cunit_id": coding_reference.cunit_id,
        "start_offset": coding_reference.start_offset,
        "end_offset": coding_reference.end_offset,
        "codebook_version_id": coding_reference.codebook_version_id,
        "code_id": coding_reference.code_id,
        "created_by": coding_reference.created_by,
        "created_at": coding_reference.created_at,
        "removed_by": coding_reference.removed_by,
        "removed_at": coding_reference.removed_at,
    }


def _saved_query_payload(saved_query) -> dict:
    filters = saved_query.definition.filters
    return {
        "saved_query_id": saved_query.saved_query_id,
        "project_id": saved_query.project_id,
        "title": saved_query.title,
        "definition": {
            "kind": saved_query.definition.kind,
            "version": saved_query.definition.version,
            "filters": {
                "project_source_id": filters.project_source_id,
                "codebook_version_id": filters.codebook_version_id,
                "code_id": filters.code_id,
                "created_by": filters.created_by,
                "include_removed": filters.include_removed,
            },
        },
        "created_by": saved_query.created_by,
        "created_at": saved_query.created_at,
    }


def _researcher_payload(researcher) -> dict:
    return {
        "project_id": researcher.project_id,
        "researcher_id": researcher.researcher_id,
        "display_name": researcher.display_name,
        "role": researcher.role,
        "active": researcher.active,
        "created_at": researcher.created_at,
        "updated_at": researcher.updated_at,
        "provenance_classification": researcher.provenance_classification,
        "provenance_actor_id": researcher.provenance_actor_id,
    }


def _agent_suggestion_payload(suggestion) -> dict:
    return {
        "agent_suggestion_id": suggestion.agent_suggestion_id,
        "project_id": suggestion.project_id,
        "origin_kind": suggestion.origin_kind,
        "origin_id": suggestion.origin_id,
        "origin_suggestion_key": suggestion.origin_suggestion_key,
        "project_source_id": suggestion.project_source_id,
        "transcript_revision_id": suggestion.transcript_revision_id,
        "evidence_set_id": suggestion.evidence_set_id,
        "target_kind": suggestion.target_kind,
        "passage_id": suggestion.passage_id,
        "cunit_id": suggestion.cunit_id,
        "start_offset": suggestion.start_offset,
        "end_offset": suggestion.end_offset,
        "codebook_version_id": suggestion.codebook_version_id,
        "code_id": suggestion.code_id,
        "created_by": suggestion.created_by,
        "created_at": suggestion.created_at,
    }


def _reviewer_decision_payload(decision) -> dict:
    return {
        "reviewer_decision_id": decision.reviewer_decision_id,
        "project_id": decision.project_id,
        "agent_suggestion_id": decision.agent_suggestion_id,
        "decision_number": decision.decision_number,
        "decision": decision.decision,
        "coding_reference_id": decision.coding_reference_id,
        "reviewed_by": decision.reviewed_by,
        "created_at": decision.created_at,
    }


def _agent_suggestion_snapshot_payload(snapshot) -> dict:
    return {
        "suggestion": _agent_suggestion_payload(snapshot.suggestion),
        "current_decision": (
            None
            if snapshot.current_decision is None
            else _reviewer_decision_payload(snapshot.current_decision)
        ),
        "review_status": snapshot.review_status,
    }


def _note_target_payload(target) -> dict:
    if target.kind == "study":
        return {"kind": "study"}
    if target.kind == "source":
        return {
            "kind": "source",
            "project_source_id": target.project_source_id,
        }
    if target.kind == "case":
        return {"kind": "case", "case_id": target.case_id}
    if target.kind == "code":
        return {
            "kind": "code",
            "codebook_version_id": target.codebook_version_id,
            "code_id": target.code_id,
        }
    payload = {
        "kind": "excerpt",
        "project_source_id": target.project_source_id,
        "transcript_revision_id": target.transcript_revision_id,
        "evidence_set_id": target.evidence_set_id,
        "excerpt_target_kind": target.excerpt_target_kind,
        "passage_id": target.passage_id,
        "start_offset": target.start_offset,
        "end_offset": target.end_offset,
    }
    if target.excerpt_target_kind == "cunit":
        payload["cunit_id"] = target.cunit_id
    return payload


def _note_record_payload(note) -> dict:
    return {
        "note_id": note.note_id,
        "note_kind": note.note_kind,
        "project_id": note.project_id,
        "target": _note_target_payload(note.target),
        "created_by": note.created_by,
        "created_at": note.created_at,
        "removed_by": note.removed_by,
        "removed_at": note.removed_at,
    }


def _note_revision_payload(revision) -> dict:
    return {
        "note_revision_id": revision.note_revision_id,
        "note_id": revision.note_id,
        "revision_number": revision.revision_number,
        "title": revision.title,
        "body": revision.body,
        "created_by": revision.created_by,
        "created_at": revision.created_at,
    }


def _note_snapshot_payload(snapshot) -> dict:
    return {
        "note": _note_record_payload(snapshot.note),
        "current_revision": _note_revision_payload(snapshot.current_revision),
    }


def _raise_case_http_error(exc: Exception) -> NoReturn:
    if isinstance(exc, FileNotFoundError):
        raise HTTPException(
            status_code=404,
            detail="Qualitative project data was not found",
        ) from exc
    if isinstance(exc, CaseNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, CaseValidationError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=409, detail=str(exc)) from exc


def _case_payload(case) -> dict:
    return {
        "case_id": case.case_id,
        "project_id": case.project_id,
        "case_kind": case.case_kind,
        "label": case.label,
        "description": case.description,
        "created_by": case.created_by,
        "updated_by": case.updated_by,
        "created_at": case.created_at,
        "updated_at": case.updated_at,
    }


def _attribute_definition_payload(definition) -> dict:
    return {
        "attribute_definition_id": definition.attribute_definition_id,
        "project_id": definition.project_id,
        "attribute_key": definition.attribute_key,
        "label": definition.label,
        "value_type": definition.value_type,
        "allowed_values": list(definition.allowed_values),
        "required": definition.required,
        "created_by": definition.created_by,
        "updated_by": definition.updated_by,
        "created_at": definition.created_at,
        "updated_at": definition.updated_at,
    }


def _case_attribute_value_payload(attribute_value) -> dict:
    return {
        "project_id": attribute_value.project_id,
        "case_id": attribute_value.case_id,
        "attribute_definition_id": attribute_value.attribute_definition_id,
        "attribute_key": attribute_value.attribute_key,
        "value_type": attribute_value.value_type,
        "value": attribute_value.value,
        "updated_by": attribute_value.updated_by,
        "created_at": attribute_value.created_at,
        "updated_at": attribute_value.updated_at,
    }


def _source_case_link_payload(source_link) -> dict:
    return {
        "project_id": source_link.project_id,
        "project_source_id": source_link.project_source_id,
        "case_id": source_link.case_id,
        "linked_by": source_link.linked_by,
        "created_at": source_link.created_at,
    }


def _case_snapshot_payload(snapshot) -> dict:
    return {
        "case": _case_payload(snapshot.case),
        "attribute_values": [
            _case_attribute_value_payload(attribute_value)
            for attribute_value in snapshot.attribute_values
        ],
        "project_source_ids": list(snapshot.project_source_ids),
    }


def _raise_codebook_http_error(exc: Exception) -> NoReturn:
    if isinstance(exc, (FileNotFoundError, CodebookNotFoundError)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (ValueError, CodebookValidationError)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=409, detail=str(exc)) from exc


def _codebook_payload(codebook) -> dict:
    return {
        "codebook_id": codebook.codebook_id,
        "project_id": codebook.project_id,
        "title": codebook.title,
        "description": codebook.description,
        "created_by": codebook.created_by,
        "updated_by": codebook.updated_by,
        "created_at": codebook.created_at,
        "updated_at": codebook.updated_at,
    }


def _codebook_version_payload(version) -> dict:
    return {
        "codebook_version_id": version.codebook_version_id,
        "project_id": version.project_id,
        "codebook_id": version.codebook_id,
        "version_number": version.version_number,
        "status": version.status,
        "based_on_version_id": version.based_on_version_id,
        "created_by": version.created_by,
        "created_at": version.created_at,
        "frozen_at": version.frozen_at,
    }


def _code_payload(code) -> dict:
    return {
        "code_id": code.code_id,
        "project_id": code.project_id,
        "codebook_version_id": code.codebook_version_id,
        "stable_code_key": code.stable_code_key,
        "parent_code_id": code.parent_code_id,
        "label": code.label,
        "definition": code.definition,
        "inclusion_criteria": code.inclusion_criteria,
        "exclusion_criteria": code.exclusion_criteria,
        "examples": list(code.examples),
        "notes": code.notes,
        "color": code.color,
        "sort_order": code.sort_order,
        "created_by": code.created_by,
        "created_at": code.created_at,
        "updated_at": code.updated_at,
    }


def _codebook_snapshot_payload(snapshot) -> dict:
    return {
        "codebook": _codebook_payload(snapshot.codebook),
        "version": _codebook_version_payload(snapshot.version),
        "codes": [_code_payload(code) for code in snapshot.codes],
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
    try:
        aggregate_payload = json.loads(
            aggregate_results_json.read_text(encoding="utf-8")
        )
        if (
            not isinstance(aggregate_payload, dict)
            or not isinstance(aggregate_payload.get("results"), list)
            or not isinstance(aggregate_payload.get("failures", []), list)
        ):
            raise TypeError("aggregate payload shape is invalid")
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        TypeError,
        UnicodeDecodeError,
    ) as exc:
        raise StudyBatchSnapshotConflict(
            "Completed study batch contains an invalid aggregate"
        ) from exc
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
