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
    runbook_path: str
    allowed_files: list[str]
    verification_commands: list[str]
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True)
class StoredAgentJob:
    job: AgentJob
    artifact_path: Path
    runbook_path: Path


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
