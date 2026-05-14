import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.extensions.plugin_requests import (
    PluginRequestStore,
    create_plugin_request,
)


def test_create_plugin_request_normalizes_and_persists_artifact(tmp_path: Path) -> None:
    store = PluginRequestStore(tmp_path)

    request = create_plugin_request(
        {
            "title": "Empathy Response Metric",
            "research_question": "Count participant turns after caregiver empathy responses.",
            "requested_metric_id": "Empathy Response Metric",
            "output_columns": "speaker, empathy_count, examples",
            "example_transcript": "CG: That sounds hard.\nP: It was difficult.",
            "expected_behavior": "Count the caregiver turn as an empathy response.",
        },
        store=store,
    )

    assert request.id == "empathy_response_metric"
    assert request.status == "draft"
    assert request.requested_metric_id == "empathy_response_metric"
    assert request.output_columns == ["speaker", "empathy_count", "examples"]
    assert request.examples[0].transcript == "CG: That sounds hard.\nP: It was difficult."
    assert (
        request.examples[0].expected_behavior
        == "Count the caregiver turn as an empathy response."
    )

    saved = json.loads((tmp_path / "plugin_requests" / "empathy_response_metric.json").read_text())
    assert saved["id"] == "empathy_response_metric"
    assert saved["title"] == "Empathy Response Metric"

    prompt_path = (
        tmp_path
        / "plugin_requests"
        / "empathy_response_metric"
        / "implementation_prompt.md"
    )
    prompt = prompt_path.read_text(encoding="utf-8")
    assert "# Metric Plugin Request: Empathy Response Metric" in prompt
    assert "Branch: `codex/plugin-empathy-response-metric`" in prompt
    assert "- `speaker`" in prompt
    assert "- `empathy_count`" in prompt
    assert "CG: That sounds hard." in prompt
    assert ".venv/bin/pytest tests/test_metric_plugins.py -q" in prompt


def test_create_plugin_request_rejects_missing_research_question(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="research_question"):
        create_plugin_request(
            {
                "title": "Bad Metric",
                "research_question": "",
                "requested_metric_id": "bad_metric",
                "output_columns": ["speaker"],
            },
            store=PluginRequestStore(tmp_path),
        )


def test_plugin_request_api_creates_and_lists_local_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    create_response = client.post(
        "/api/plugin-requests",
        json={
            "title": "Repair Sequence Metric",
            "research_question": "Detect repair sequences after unclear participant turns.",
            "requested_metric_id": "repair_sequence_metric",
            "output_columns": ["speaker", "repair_count", "examples"],
            "example_transcript": "P: I mean, no, the medicine was yesterday.",
            "expected_behavior": "Count self-correction as one repair sequence.",
        },
    )

    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["request"]["id"] == "repair_sequence_metric"
    assert payload["request"]["status"] == "draft"
    assert payload["artifact_path"].endswith(
        "plugin_requests/repair_sequence_metric.json"
    )
    assert payload["implementation_prompt_path"].endswith(
        "plugin_requests/repair_sequence_metric/implementation_prompt.md"
    )

    list_response = client.get("/api/plugin-requests")

    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()["requests"]] == [
        "repair_sequence_metric"
    ]
