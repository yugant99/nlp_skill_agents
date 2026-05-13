import json

from fastapi.testclient import TestClient

from backend.app.main import app


def test_health_endpoint() -> None:
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "storage": "local"}


def test_default_skill_pack_endpoint() -> None:
    client = TestClient(app)

    response = client.get("/api/skill-packs/default")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "default_transcript_metrics"
    assert payload["metrics"] == [
        "base_metrics",
        "lexical_metrics",
        "disfluency_metrics",
    ]


def test_create_run_from_txt_upload(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/api/runs",
        data={
            "config": json.dumps(
                {
                    "participant_id": "vr009",
                    "selected_metrics": ["base_metrics", "disfluency_metrics"],
                    "disfluency_tokens": ["um"],
                }
            )
        },
        files={
            "file": (
                "vr009.txt",
                b"vr009_c: Um, hello there.\nvr009_p: Hello.",
                "text/plain",
            )
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_filename"] == "vr009.txt"
    assert [result["metric_id"] for result in payload["results"]] == [
        "base_metrics",
        "disfluency_metrics",
    ]
    assert payload["stored"]["results_json"].endswith("results.json")
    assert (tmp_path / "runs.sqlite3").exists()

