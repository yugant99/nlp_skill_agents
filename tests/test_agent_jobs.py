import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.extensions.agent_jobs import (
    AgentJobStore,
    create_metric_plugin_build_job,
    create_segmentation_rewrite_job,
)
from backend.extensions.plugin_requests import PluginRequestStore, create_plugin_request


def test_create_metric_plugin_build_job_from_request(tmp_path: Path) -> None:
    request = create_plugin_request(
        {
            "title": "Empathy Response Metric",
            "research_question": "Count caregiver empathy responses.",
            "requested_metric_id": "empathy_response_metric",
            "output_columns": ["speaker", "empathy_count", "examples"],
            "example_transcript": "CG: That sounds hard.\nP: Yes.",
            "expected_behavior": "Count one caregiver empathy response.",
        },
        store=PluginRequestStore(tmp_path),
    )

    job = create_metric_plugin_build_job(
        request,
        prompt_path=tmp_path
        / "plugin_requests"
        / request.id
        / "implementation_prompt.md",
        store=AgentJobStore(tmp_path),
    )

    assert job.id == "build_empathy_response_metric"
    assert job.job_type == "metric_plugin_build"
    assert job.status == "queued"
    assert job.source_request_id == "empathy_response_metric"
    assert job.branch_name == "codex/plugin-empathy-response-metric"
    assert job.runbook_path.endswith(
        "agent_jobs/build_empathy_response_metric/runbook.md"
    )
    assert "backend/analysis/metrics.py" in job.allowed_files
    assert ".venv/bin/pytest tests/test_metric_plugins.py -q" in job.verification_commands

    saved = json.loads((tmp_path / "agent_jobs" / f"{job.id}.json").read_text())
    assert saved["prompt_path"].endswith("implementation_prompt.md")
    assert saved["runbook_path"].endswith(
        "agent_jobs/build_empathy_response_metric/runbook.md"
    )

    runbook_path = tmp_path / "agent_jobs" / job.id / "runbook.md"
    runbook = runbook_path.read_text(encoding="utf-8")
    assert "# Agent Job Runbook: build_empathy_response_metric" in runbook
    assert "codex_internal_skills/metric-plugin-builder/SKILL.md" in runbook
    assert (
        "git worktree add ../nlp_skill_agents-build_empathy_response_metric "
        "-b codex/plugin-empathy-response-metric"
    ) in runbook
    assert str(tmp_path / "plugin_requests" / request.id / "implementation_prompt.md") in runbook
    assert "backend/analysis/metrics.py" in runbook
    assert ".venv/bin/pytest tests/test_metric_plugins.py -q" in runbook

    updated = AgentJobStore(tmp_path).update_status(job.id, "in_progress")

    assert updated.status == "in_progress"
    saved_after_update = json.loads(
        (tmp_path / "agent_jobs" / f"{job.id}.json").read_text()
    )
    assert saved_after_update["status"] == "in_progress"


def test_agent_job_store_rejects_unknown_status(tmp_path: Path) -> None:
    request = create_plugin_request(
        {
            "title": "Topic Shift Metric",
            "research_question": "Detect topic shifts.",
            "requested_metric_id": "topic_shift_metric",
            "output_columns": ["speaker", "topic_shift_count"],
            "example_transcript": "P: My knee hurts.\nP: Also, my sleep is bad.",
            "expected_behavior": "Count one topic shift.",
        },
        store=PluginRequestStore(tmp_path),
    )
    create_metric_plugin_build_job(
        request,
        prompt_path=tmp_path
        / "plugin_requests"
        / request.id
        / "implementation_prompt.md",
        store=AgentJobStore(tmp_path),
    )

    try:
        AgentJobStore(tmp_path).update_status("build_topic_shift_metric", "done-ish")
    except ValueError as exc:
        assert "Unsupported agent job status" in str(exc)
    else:
        raise AssertionError("Expected invalid status to raise")


def test_create_segmentation_rewrite_job_uses_html_runbook_and_evaluator_gate(
    tmp_path: Path,
) -> None:
    job = create_segmentation_rewrite_job(
        "pause_overlap_repair",
        store=AgentJobStore(tmp_path),
    )

    assert job.id == "rewrite_pause_overlap_repair"
    assert job.job_type == "segmentation_rewrite"
    assert job.source_request_id == "pause_overlap_repair"
    assert job.branch_name == "codex/segmentation-pause-overlap-repair"
    assert job.prompt_path.endswith(
        "agent_jobs/rewrite_pause_overlap_repair/rewrite_prompt.html"
    )
    assert job.runbook_path.endswith(
        "agent_jobs/rewrite_pause_overlap_repair/runbook.html"
    )
    assert "backend/segmentation/evaluator.py" in job.allowed_files
    assert ".venv/bin/pytest tests/test_segmentation_core.py -q" in (
        job.verification_commands
    )

    prompt = (
        tmp_path
        / "agent_jobs"
        / "rewrite_pause_overlap_repair"
        / "rewrite_prompt.html"
    ).read_text(encoding="utf-8")
    runbook = (
        tmp_path / "agent_jobs" / "rewrite_pause_overlap_repair" / "runbook.html"
    ).read_text(encoding="utf-8")
    assert "<!doctype html>" in prompt
    assert "<!doctype html>" in runbook
    assert "Synthetic case: pause_overlap_repair" in prompt
    assert "Run the deterministic evaluator" in runbook
    assert "official-source-guard" in runbook


def test_segmentation_rewrite_job_includes_failed_rules_and_specialists(
    tmp_path: Path,
) -> None:
    job = create_segmentation_rewrite_job(
        "run_123",
        failed_rule_ids=["pause-markers"],
        target_specialist_ids=["timing_pause"],
        store=AgentJobStore(tmp_path),
    )

    prompt = (tmp_path / "agent_jobs" / job.id / "rewrite_prompt.html").read_text(
        encoding="utf-8"
    )
    runbook = (tmp_path / "agent_jobs" / job.id / "runbook.html").read_text(
        encoding="utf-8"
    )

    assert "pause-markers" in prompt
    assert "timing_pause" in prompt
    assert "targeted segmentation rewrite" in runbook


def test_agent_job_api_creates_and_lists_build_jobs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    request_response = client.post(
        "/api/plugin-requests",
        json={
            "title": "Repair Sequence Metric",
            "research_question": "Detect repair sequences.",
            "requested_metric_id": "repair_sequence_metric",
            "output_columns": ["speaker", "repair_count", "examples"],
            "example_transcript": "P: I mean, no, yesterday.",
            "expected_behavior": "Count one repair sequence.",
        },
    )
    request_id = request_response.json()["request"]["id"]

    create_response = client.post(f"/api/plugin-requests/{request_id}/build-job")

    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["job"]["id"] == "build_repair_sequence_metric"
    assert payload["job"]["status"] == "queued"
    assert payload["job"]["runbook_path"].endswith(
        "agent_jobs/build_repair_sequence_metric/runbook.md"
    )
    assert payload["artifact_path"].endswith("agent_jobs/build_repair_sequence_metric.json")

    list_response = client.get("/api/agent-jobs")

    assert list_response.status_code == 200
    assert [job["id"] for job in list_response.json()["jobs"]] == [
        "build_repair_sequence_metric"
    ]

    update_response = client.patch(
        "/api/agent-jobs/build_repair_sequence_metric",
        json={"status": "verified"},
    )

    assert update_response.status_code == 200
    assert update_response.json()["job"]["status"] == "verified"
    assert client.get("/api/agent-jobs").json()["jobs"][0]["status"] == "verified"

    evidence_response = client.post(
        "/api/agent-jobs/build_repair_sequence_metric/evidence",
        json={
            "gate": "backend_tests",
            "command": ".venv/bin/pytest -q",
            "status": "passed",
            "summary": "70 passed",
        },
    )

    assert evidence_response.status_code == 200
    evidence = evidence_response.json()["evidence"]
    assert evidence["gate"] == "backend_tests"
    assert evidence["artifact_path"].endswith(
        "agent_jobs/build_repair_sequence_metric/evidence/backend_tests.json"
    )

    evidence_list_response = client.get(
        "/api/agent-jobs/build_repair_sequence_metric/evidence"
    )

    assert evidence_list_response.status_code == 200
    assert evidence_list_response.json()["evidence"][0]["summary"] == "70 passed"


def test_segmentation_rewrite_job_api_creates_agent_job(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/api/segmentation/cases/redaction_omission_nonverbal/rewrite-job"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["job"]["id"] == "rewrite_redaction_omission_nonverbal"
    assert payload["job"]["job_type"] == "segmentation_rewrite"
    assert payload["job"]["runbook_path"].endswith(
        "agent_jobs/rewrite_redaction_omission_nonverbal/runbook.html"
    )
    assert payload["artifact_path"].endswith(
        "agent_jobs/rewrite_redaction_omission_nonverbal.json"
    )
