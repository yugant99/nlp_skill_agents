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


def test_validate_dynamic_skill_pack_endpoint() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/skill-packs/validate",
        json={
            "id": "care_study",
            "name": "Care Study",
            "version": "1.0.0",
            "metrics": ["concept_count_metrics", "cue_inventory_metrics"],
            "speaker_roles": {
                "caregiver": {"label": "Care Partner", "prefixes": ["CG"]},
                "participant": {"label": "Participant", "prefixes": ["P"]},
            },
            "concept_lexicons": {"pain": ["pain", "hurts"]},
            "nonverbal_cues": {"pause": ["pause"]},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "valid": True,
        "skill_pack": {
            "id": "care_study",
            "name": "Care Study",
            "version": "1.0.0",
            "metric_ids": ["concept_count_metrics", "cue_inventory_metrics"],
            "speaker_roles": {
                "caregiver": "Care Partner",
                "participant": "Participant",
            },
            "speaker_prefixes": {
                "caregiver": ["CG"],
                "participant": ["P"],
            },
            "disfluency_tokens": [],
            "concept_lexicons": {"pain": ["pain", "hurts"]},
            "nonverbal_cues": {"pause": ["pause"]},
        },
    }


def test_validate_dynamic_skill_pack_endpoint_returns_clear_errors() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/skill-packs/validate",
        json={
            "id": "bad",
            "name": "Bad",
            "version": "1.0.0",
            "metrics": ["not_registered"],
        },
    )

    assert response.status_code == 400
    assert "not_registered" in response.json()["detail"]


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


def test_list_runs_returns_recent_local_runs(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    for filename, participant in [("one.txt", "vr040"), ("two.txt", "vr041")]:
        client.post(
            "/api/runs",
            data={
                "config": json.dumps(
                    {
                        "participant_id": participant,
                        "selected_metrics": ["base_metrics"],
                    }
                )
            },
            files={
                "file": (
                    filename,
                    f"{participant}_c: Hello.\n{participant}_p: Hi.".encode(),
                    "text/plain",
                )
            },
        )

    response = client.get("/api/runs")

    assert response.status_code == 200
    assert [row["source_filename"] for row in response.json()["runs"]] == [
        "two.txt",
        "one.txt",
    ]


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


def test_create_run_accepts_custom_speaker_prefixes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/api/runs",
        data={
            "config": json.dumps(
                {
                    "participant_id": "dyad01",
                    "speaker_prefixes": {
                        "caregiver": "care_partner",
                        "participant": "participant",
                    },
                    "selected_metrics": ["base_metrics"],
                }
            )
        },
        files={
            "file": (
                "dyad01.txt",
                b"care_partner: Hello there.\nparticipant: Hello back.",
                "text/plain",
            )
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["turn_count"] == 2
    assert payload["diagnostics"]["turn_counts"] == {
        "caregiver": 1,
        "participant": 1,
    }
    assert payload["results"][0]["rows"][0]["clean_words"] == 2


def test_create_run_from_text_payload(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/api/runs/text",
        json={
            "source_filename": "pasted_transcript.txt",
            "content": "vr050_c: Hello.\nvr050_p: Um, hello back.",
            "config": {
                "participant_id": "vr050",
                "selected_metrics": ["base_metrics", "disfluency_metrics"],
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_filename"] == "pasted_transcript.txt"
    assert payload["turn_count"] == 2
    assert payload["results"][1]["rows"][-1]["disfluency_count"] == 1


def test_create_text_run_applies_embedded_dynamic_skill_pack(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    skill_pack = {
        "id": "care_study",
        "name": "Care Study",
        "version": "1.0.0",
        "metrics": ["concept_count_metrics", "cue_inventory_metrics"],
        "speaker_roles": {
            "caregiver": {"label": "Care Partner", "prefixes": ["CG"]},
            "participant": {"label": "Participant", "prefixes": ["P"]},
        },
        "disfluency_tokens": ["um"],
        "concept_lexicons": {"pain": ["pain", "hurt", "hurts"]},
        "nonverbal_cues": {"pause": ["pause"]},
    }

    response = client.post(
        "/api/runs/text",
        json={
            "source_filename": "care_study.txt",
            "content": "CG: Does it hurt? [pause]\nP: Um, the pain hurts.",
            "config": {"skill_pack": skill_pack},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["skill_pack"] == {
        "id": "care_study",
        "name": "Care Study",
        "version": "1.0.0",
    }
    assert payload["diagnostics"]["turn_counts"] == {
        "caregiver": 1,
        "participant": 1,
    }
    assert [result["metric_id"] for result in payload["results"]] == [
        "concept_count_metrics",
        "cue_inventory_metrics",
    ]
    assert payload["results"][0]["rows"][0]["match_count"] == 3
    results_json = tmp_path / "runs" / payload["run_id"] / "results.json"
    assert json.loads(results_json.read_text())["skill_pack"] == payload["skill_pack"]


def test_create_run_rejects_unknown_metric_with_400(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/api/runs/text",
        json={
            "source_filename": "bad_metric.txt",
            "content": "vr060_c: Hello.\nvr060_p: Hi.",
            "config": {
                "participant_id": "vr060",
                "selected_metrics": ["not_a_metric"],
            },
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown metric skill: not_a_metric"
