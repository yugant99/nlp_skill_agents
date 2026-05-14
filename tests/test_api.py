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
    assert payload["diagnostics"] == {
        "turn_counts": {"caregiver": 1, "participant": 1},
        "warnings": [],
    }
    assert payload["stored"]["results_json"].endswith("results.json")
    assert payload["exports"] == [
        {
            "metric_id": "base_metrics",
            "filename": "base_metrics.csv",
            "download_url": f"/api/runs/{payload['run_id']}/exports/base_metrics.csv",
        },
        {
            "metric_id": "disfluency_metrics",
            "filename": "disfluency_metrics.csv",
            "download_url": f"/api/runs/{payload['run_id']}/exports/disfluency_metrics.csv",
        },
    ]
    assert (tmp_path / "runs.sqlite3").exists()


def test_download_export_csv_for_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    create_response = client.post(
        "/api/runs",
        data={
            "config": json.dumps(
                {
                    "participant_id": "vr010",
                    "selected_metrics": ["base_metrics"],
                }
            )
        },
        files={
            "file": (
                "vr010.txt",
                b"vr010_c: Hello there.\nvr010_p: Hello.",
                "text/plain",
            )
        },
    )
    run_id = create_response.json()["run_id"]

    response = client.get(f"/api/runs/{run_id}/exports/base_metrics.csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=\"base_metrics.csv\"" in response.headers[
        "content-disposition"
    ]
    assert response.text.startswith("speaker,turns,clean_words")
    assert "caregiver,1,2" in response.text


def test_download_export_rejects_path_traversal(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.get("/api/runs/abc/exports/../runs.sqlite3")

    assert response.status_code == 404


def test_create_run_surfaces_diagnostic_warnings(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/api/runs",
        data={
            "config": json.dumps(
                {
                    "participant_id": "vr099",
                    "selected_metrics": ["base_metrics"],
                }
            )
        },
        files={
            "file": (
                "bad.txt",
                b"This transcript has no known speaker prefixes.",
                "text/plain",
            )
        },
    )

    assert response.status_code == 200
    assert response.json()["diagnostics"]["warnings"] == [
        {
            "code": "no_turns_found",
            "message": "No speaker turns were detected. Check participant ID and speaker prefixes.",
        }
    ]
