from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.extensions.plugin_requests import PluginRequest


AGENT_JOB_STATUSES = {"queued", "in_progress", "blocked", "verified", "merged"}
AGENT_JOB_EVIDENCE_STATUSES = {"passed", "failed"}
AGENT_JOB_TRANSITIONS = {
    "queued": {"in_progress", "blocked"},
    "in_progress": {"blocked", "verified"},
    "blocked": {"in_progress"},
    "verified": {"merged"},
    "merged": set(),
}


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
        runbook = (
            build_agent_job_runbook_html(job)
            if runbook_path.suffix == ".html"
            else build_agent_job_runbook(job)
        )
        runbook_path.write_text(runbook, encoding="utf-8")
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
        job = self._load_job(job_id)
        if status == job.status:
            return job
        if status not in AGENT_JOB_TRANSITIONS[job.status]:
            raise ValueError(
                f"Agent job cannot transition from {job.status} to {status}"
            )
        evidence = self.list_evidence(job_id)
        if status == "verified":
            missing_commands = _missing_verification_commands(job, evidence)
            if missing_commands:
                raise ValueError(
                    "Agent job is missing passed evidence for: "
                    + "; ".join(missing_commands)
                )
        if status == "merged" and not _has_passed_merge_evidence(evidence):
            raise ValueError("Agent job requires passed merge evidence")
        updated = replace(job, status=status)
        self.persist(updated)
        return updated

    def available_transitions(self, job_id: str) -> list[str]:
        job = self._load_job(job_id)
        candidates = set(AGENT_JOB_TRANSITIONS[job.status])
        evidence = self.list_evidence(job_id)
        if "verified" in candidates and _missing_verification_commands(job, evidence):
            candidates.remove("verified")
        if "merged" in candidates and not _has_passed_merge_evidence(evidence):
            candidates.remove("merged")
        return [
            status
            for status in ["in_progress", "blocked", "verified", "merged"]
            if status in candidates
        ]

    def add_evidence(self, job_id: str, payload: dict[str, Any]) -> AgentJobEvidence:
        self._require_job(job_id)
        gate = _safe_gate_name(str(payload.get("gate") or "verification"))
        status = str(payload.get("status") or "").strip().lower()
        if status not in AGENT_JOB_EVIDENCE_STATUSES:
            raise ValueError(f"Unsupported agent job evidence status: {status}")
        evidence_dir = self.jobs_dir / job_id / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = evidence_dir / f"{gate}.json"
        evidence = AgentJobEvidence(
            job_id=job_id,
            gate=gate,
            command=str(payload.get("command") or ""),
            status=status,
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

    def _load_job(self, job_id: str) -> AgentJob:
        job_path = self.jobs_dir / f"{job_id}.json"
        if not job_path.exists():
            raise FileNotFoundError(job_id)
        return agent_job_from_payload(
            json.loads(job_path.read_text(encoding="utf-8"))
        )


def _missing_verification_commands(
    job: AgentJob,
    evidence: list[AgentJobEvidence],
) -> list[str]:
    passed_commands = {
        item.command.strip()
        for item in evidence
        if item.status == "passed" and item.command.strip()
    }
    return [
        command
        for command in job.verification_commands
        if command not in passed_commands
    ]


def _has_passed_merge_evidence(evidence: list[AgentJobEvidence]) -> bool:
    return any(
        item.gate == "merge"
        and item.status == "passed"
        and bool(item.command.strip())
        for item in evidence
    )


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


def create_segmentation_rewrite_job(
    case_id: str,
    *,
    failed_rule_ids: list[str] | None = None,
    target_specialist_ids: list[str] | None = None,
    store: AgentJobStore | None = None,
) -> AgentJob:
    store = store or AgentJobStore()
    safe_case_id = _safe_gate_name(case_id)
    job_id = f"rewrite_{safe_case_id}"
    job_dir = store.jobs_dir / job_id
    prompt_path = job_dir / "rewrite_prompt.html"
    job_dir.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        build_segmentation_rewrite_prompt_html(
            safe_case_id,
            failed_rule_ids=failed_rule_ids or [],
            target_specialist_ids=target_specialist_ids or [],
        ),
        encoding="utf-8",
    )
    job = AgentJob(
        id=job_id,
        job_type="segmentation_rewrite",
        status="queued",
        source_request_id=safe_case_id,
        branch_name=f"codex/segmentation-{safe_case_id.replace('_', '-')}",
        prompt_path=str(prompt_path),
        runbook_path=str(job_dir / "runbook.html"),
        allowed_files=[
            "backend/segmentation/",
            "backend/segmentation/evaluator.py",
            "backend/segmentation/synthetic.py",
            "backend/app/main.py",
            "frontend/src/",
            "tests/test_segmentation_core.py",
            "tests/test_api.py",
            "tests/test_agent_jobs.py",
            "checkpoints/",
        ],
        verification_commands=[
            ".venv/bin/pytest tests/test_segmentation_core.py -q",
            ".venv/bin/pytest tests/test_api.py -k segmentation -q",
            ".venv/bin/pytest tests/test_agent_jobs.py -k segmentation -q",
            "cd frontend && npm run build",
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


def build_agent_job_runbook_html(job: AgentJob) -> str:
    allowed_files = "".join(f"<li><code>{path}</code></li>" for path in job.allowed_files)
    verification_commands = "".join(
        f"<li><code>{command}</code></li>" for command in job.verification_commands
    )
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Agent Job Runbook: {job.id}</title>
    <style>
      body {{
        margin: 0;
        background: #f7f4ea;
        color: #24312f;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      main {{
        max-width: 920px;
        margin: 0 auto;
        padding: 36px 24px;
      }}
      section {{
        border: 1px solid #d9d4c5;
        border-radius: 8px;
        background: #fffdf8;
        margin-top: 16px;
        padding: 16px;
      }}
      h1 {{
        margin: 0 0 8px;
        font-size: 30px;
      }}
      h2 {{
        margin: 0 0 10px;
        font-size: 17px;
      }}
      code {{
        border: 1px solid #d9d4c5;
        border-radius: 4px;
        background: #f0eee4;
        padding: 1px 5px;
      }}
      li {{
        margin: 6px 0;
      }}
    </style>
  </head>
  <body>
    <main>
      <h1>Agent Job Runbook: {job.id}</h1>
      <p>Branch: <code>{job.branch_name}</code></p>
      <section>
        <h2>Purpose</h2>
        <p>Run a targeted segmentation rewrite agent for synthetic case or run <code>{job.source_request_id}</code>, then verify the candidate with machine-checkable evaluator artifacts.</p>
      </section>
      <section>
        <h2>Prompt</h2>
        <p>Open <code>{job.prompt_path}</code> before editing. Do not use official transcript text or official expected outputs.</p>
      </section>
      <section>
        <h2>Allowed Files</h2>
        <ul>{allowed_files}</ul>
      </section>
      <section>
        <h2>Evaluator Gate</h2>
        <p>Run the deterministic evaluator and treat <code>official-source-guard</code> failures as blocking.</p>
        <ul>{verification_commands}</ul>
      </section>
    </main>
  </body>
</html>
"""


def build_segmentation_rewrite_prompt_html(
    case_id: str,
    *,
    failed_rule_ids: list[str] | None = None,
    target_specialist_ids: list[str] | None = None,
) -> str:
    failed_rules = "".join(
        f"<li><code>{rule_id}</code></li>" for rule_id in (failed_rule_ids or [])
    )
    target_specialists = "".join(
        f"<li><code>{specialist_id}</code></li>"
        for specialist_id in (target_specialist_ids or [])
    )
    routing_section = ""
    if failed_rules or target_specialists:
        routing_section = f"""
      <h2>Targeted Repair Scope</h2>
      <p>Only patch the failed rule scope. Do not rewrite the whole transcript.</p>
      <h3>Failed Rules</h3>
      <ul>{failed_rules or "<li><code>none</code></li>"}</ul>
      <h3>Target Specialists</h3>
      <ul>{target_specialists or "<li><code>verification</code></li>"}</ul>
"""
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Segmentation Rewrite Prompt</title>
  </head>
  <body>
    <main>
      <h1>Synthetic case: {case_id}</h1>
      <p>Rewrite only synthetic segmentation drafts for this case. Use rule-scoped edits, return the revised draft plus evaluator evidence, and never introduce official transcript names, titles, copied lines, or paraphrased official source content.</p>
      <ul>
        <li>Start from <code>GET /api/segmentation/cases/{case_id}</code>.</li>
        <li>Evaluate with <code>POST /api/segmentation/evaluate</code>.</li>
        <li>Blocking guard: <code>official-source-guard</code>.</li>
      </ul>
      {routing_section}
    </main>
  </body>
</html>
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
