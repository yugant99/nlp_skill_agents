from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.extensions.plugin_requests import PluginRequest


@dataclass(frozen=True)
class AgentJob:
    id: str
    job_type: str
    status: str
    source_request_id: str
    branch_name: str
    prompt_path: str
    allowed_files: list[str]
    verification_commands: list[str]
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True)
class StoredAgentJob:
    job: AgentJob
    artifact_path: Path


class AgentJobStore:
    def __init__(self, root: Path | str = "local_data") -> None:
        self.root = Path(root)
        self.jobs_dir = self.root / "agent_jobs"

    def persist(self, job: AgentJob) -> StoredAgentJob:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = self.jobs_dir / f"{job.id}.json"
        artifact_path.write_text(
            json.dumps(agent_job_to_payload(job), indent=2),
            encoding="utf-8",
        )
        return StoredAgentJob(job=job, artifact_path=artifact_path)

    def list_jobs(self) -> list[AgentJob]:
        if not self.jobs_dir.exists():
            return []
        jobs = [
            agent_job_from_payload(json.loads(path.read_text(encoding="utf-8")))
            for path in self.jobs_dir.glob("*.json")
        ]
        return sorted(jobs, key=lambda job: job.created_at, reverse=True)


def create_metric_plugin_build_job(
    request: PluginRequest,
    prompt_path: Path | str,
    store: AgentJobStore | None = None,
) -> AgentJob:
    branch_name = f"codex/plugin-{request.id.replace('_', '-')}"
    job = AgentJob(
        id=f"build_{request.id}",
        job_type="metric_plugin_build",
        status="queued",
        source_request_id=request.id,
        branch_name=branch_name,
        prompt_path=str(prompt_path),
        allowed_files=[
            "backend/analysis/metrics.py",
            "backend/analysis/pipeline.py",
            "tests/test_metric_plugins.py",
            "study_skill_packs/",
            "checkpoints/",
        ],
        verification_commands=[
            ".venv/bin/pytest tests/test_metric_plugins.py -q",
            ".venv/bin/pytest -q",
            "cd frontend && npm run lint && npm run build",
        ],
    )
    (store or AgentJobStore()).persist(job)
    return job


def agent_job_to_payload(job: AgentJob) -> dict[str, Any]:
    return asdict(job)


def agent_job_from_payload(payload: dict[str, Any]) -> AgentJob:
    return AgentJob(
        id=str(payload["id"]),
        job_type=str(payload["job_type"]),
        status=str(payload.get("status") or "queued"),
        source_request_id=str(payload["source_request_id"]),
        branch_name=str(payload["branch_name"]),
        prompt_path=str(payload["prompt_path"]),
        allowed_files=[str(item) for item in payload.get("allowed_files", [])],
        verification_commands=[
            str(item) for item in payload.get("verification_commands", [])
        ],
        created_at=str(payload.get("created_at") or datetime.now(UTC).isoformat()),
    )
