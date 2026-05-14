from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.extensions.plugin_requests import PluginRequest


AGENT_JOB_STATUSES = {"queued", "in_progress", "blocked", "verified", "merged"}


@dataclass(frozen=True)
class AgentJob:
    id: str
    job_type: str
    status: str
    source_request_id: str
    branch_name: str
    prompt_path: str
    runbook_path: str
    allowed_files: list[str]
    verification_commands: list[str]
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True)
class StoredAgentJob:
    job: AgentJob
    artifact_path: Path
    runbook_path: Path


@dataclass(frozen=True)
class AgentJobEvidence:
    job_id: str
    gate: str
    command: str
    status: str
    summary: str
    artifact_path: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


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
        runbook_path = Path(job.runbook_path)
        runbook_path.parent.mkdir(parents=True, exist_ok=True)
        runbook_path.write_text(build_agent_job_runbook(job), encoding="utf-8")
        return StoredAgentJob(
            job=job,
            artifact_path=artifact_path,
            runbook_path=runbook_path,
        )

    def list_jobs(self) -> list[AgentJob]:
        if not self.jobs_dir.exists():
            return []
        jobs = [
            agent_job_from_payload(json.loads(path.read_text(encoding="utf-8")))
            for path in self.jobs_dir.glob("*.json")
        ]
        return sorted(jobs, key=lambda job: job.created_at, reverse=True)

    def update_status(self, job_id: str, status: str) -> AgentJob:
        if status not in AGENT_JOB_STATUSES:
            raise ValueError(f"Unsupported agent job status: {status}")
        job_path = self.jobs_dir / f"{job_id}.json"
        if not job_path.exists():
            raise FileNotFoundError(job_id)
        job = agent_job_from_payload(json.loads(job_path.read_text(encoding="utf-8")))
        updated = replace(job, status=status)
        self.persist(updated)
        return updated

    def add_evidence(self, job_id: str, payload: dict[str, Any]) -> AgentJobEvidence:
        self._require_job(job_id)
        gate = _safe_gate_name(str(payload.get("gate") or "verification"))
        evidence_dir = self.jobs_dir / job_id / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = evidence_dir / f"{gate}.json"
        evidence = AgentJobEvidence(
            job_id=job_id,
            gate=gate,
            command=str(payload.get("command") or ""),
            status=str(payload.get("status") or "unknown"),
            summary=str(payload.get("summary") or ""),
            artifact_path=str(artifact_path),
        )
        artifact_path.write_text(
            json.dumps(asdict(evidence), indent=2),
            encoding="utf-8",
        )
        return evidence

    def list_evidence(self, job_id: str) -> list[AgentJobEvidence]:
        self._require_job(job_id)
        evidence_dir = self.jobs_dir / job_id / "evidence"
        if not evidence_dir.exists():
            return []
        evidence = [
            AgentJobEvidence(**json.loads(path.read_text(encoding="utf-8")))
            for path in evidence_dir.glob("*.json")
        ]
        return sorted(evidence, key=lambda item: item.created_at, reverse=True)

    def _require_job(self, job_id: str) -> None:
        if not (self.jobs_dir / f"{job_id}.json").exists():
            raise FileNotFoundError(job_id)


def create_metric_plugin_build_job(
    request: PluginRequest,
    prompt_path: Path | str,
    store: AgentJobStore | None = None,
) -> AgentJob:
    store = store or AgentJobStore()
    job_id = f"build_{request.id}"
    branch_name = f"codex/plugin-{request.id.replace('_', '-')}"
    job = AgentJob(
        id=job_id,
        job_type="metric_plugin_build",
        status="queued",
        source_request_id=request.id,
        branch_name=branch_name,
        prompt_path=str(prompt_path),
        runbook_path=str(store.jobs_dir / job_id / "runbook.md"),
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
    store.persist(job)
    return job


def agent_job_to_payload(job: AgentJob) -> dict[str, Any]:
    return asdict(job)


def agent_job_evidence_to_payload(evidence: AgentJobEvidence) -> dict[str, Any]:
    return asdict(evidence)


def build_agent_job_runbook(job: AgentJob) -> str:
    worktree_dir = f"../nlp_skill_agents-{job.id}"
    allowed_files = "\n".join(f"- `{path}`" for path in job.allowed_files)
    verification_commands = "\n".join(
        f"```bash\n{command}\n```" for command in job.verification_commands
    )
    return f"""# Agent Job Runbook: {job.id}

## Purpose
Build one queued NLP Skill Agents extension from the implementation prompt.

## Branch And Worktree
```bash
git worktree add {worktree_dir} -b {job.branch_name}
cd {worktree_dir}
```

## Prompt
Read the generated implementation prompt before editing:

```bash
cat {job.prompt_path}
```

## Internal Skill
Before implementation, read the project-local builder skill:

```bash
cat codex_internal_skills/metric-plugin-builder/SKILL.md
```

## Allowed Files
Keep the change scoped to these paths unless the prompt proves a wider edit is required:

{allowed_files}

## Verification Commands
Run every command before committing:

{verification_commands}

## Delivery
Commit the focused change, push `{job.branch_name}`, and open a PR against `master`.
"""


def agent_job_from_payload(payload: dict[str, Any]) -> AgentJob:
    return AgentJob(
        id=str(payload["id"]),
        job_type=str(payload["job_type"]),
        status=str(payload.get("status") or "queued"),
        source_request_id=str(payload["source_request_id"]),
        branch_name=str(payload["branch_name"]),
        prompt_path=str(payload["prompt_path"]),
        runbook_path=str(payload.get("runbook_path") or ""),
        allowed_files=[str(item) for item in payload.get("allowed_files", [])],
        verification_commands=[
            str(item) for item in payload.get("verification_commands", [])
        ],
        created_at=str(payload.get("created_at") or datetime.now(UTC).isoformat()),
    )


def _safe_gate_name(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in {"_", "-"} else "_"
        for character in value.strip().lower()
    ).strip("_")
    return normalized or "verification"
