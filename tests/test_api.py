import json
from io import BytesIO

from docx import Document
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.storage.study_store import StudyWorkspaceStore


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


def test_validate_skill_pack_text_endpoint_accepts_yaml() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/skill-packs/validate-text",
        json={
            "filename": "study.yaml",
            "content": """
id: yaml_pack
name: YAML Pack
version: 1.0.0
metrics:
  - concept_count_metrics
concept_lexicons:
  pain:
    - pain
    - hurts
nonverbal_cues:
  pause:
    - pause
""".strip(),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["skill_pack"]["id"] == "yaml_pack"
    assert payload["payload"]["metrics"] == ["concept_count_metrics"]
    assert payload["payload"]["concept_lexicons"] == {"pain": ["pain", "hurts"]}


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


def test_study_workspace_batch_api_creates_aggregate_outputs(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    study_response = client.post(
        "/api/studies",
        json={
            "name": "Question Study",
            "description": "Prompting style across transcripts.",
        },
    )

    assert study_response.status_code == 200
    study_id = study_response.json()["study"]["id"]

    pack_response = client.post(
        f"/api/studies/{study_id}/skill-pack-versions",
        json={
            "id": "question_pack",
            "name": "Question Pack",
            "version": "1.0.0",
            "metrics": ["question_type_metrics"],
            "speaker_roles": {
                "caregiver": {"label": "Caregiver", "prefixes": ["CG"]},
                "participant": {"label": "Participant", "prefixes": ["P"]},
            },
        },
    )

    assert pack_response.status_code == 200
    version_id = pack_response.json()["version"]["version_id"]

    batch_response = client.post(
        f"/api/studies/{study_id}/batches/text",
        json={
            "skill_pack_version_id": version_id,
            "transcripts": [
                {
                    "source_filename": "one.txt",
                    "metadata": {
                        "participant_id": "P1",
                        "condition": "home",
                        "week": "week_1",
                    },
                    "content": "CG: How are you?\nP: Fine.",
                },
                {
                    "source_filename": "two.txt",
                    "metadata": {
                        "participant_id": "P2",
                        "condition": "lab",
                        "week": "week_1",
                    },
                    "content": "CG: Did sleep improve?\nP: Yes.",
                },
            ],
        },
    )

    assert batch_response.status_code == 200
    payload = batch_response.json()
    assert payload["batch"]["run_count"] == 2
    assert payload["batch"]["failure_count"] == 0
    assert payload["aggregate_results_json"].endswith("aggregate_results.json")
    assert payload["results"][0]["metric_id"] == "question_type_metrics"
    assert payload["results"][0]["rows"][0]["participant_id"] == "P1"
    assert payload["results"][0]["rows"][0]["condition"] == "home"
    assert payload["results"][0]["rows"][0]["week"] == "week_1"
    assert payload["results"][0]["rows"][3]["participant_id"] == "P2"
    assert payload["results"][0]["rows"][3]["condition"] == "lab"
    assert payload["exports"] == [
        {
            "metric_id": "question_type_metrics",
            "filename": "question_type_metrics.csv",
            "path": f"{payload['batch']['aggregate_dir']}/question_type_metrics.csv",
        }
    ]

    list_response = client.get("/api/studies")
    assert list_response.status_code == 200
    assert list_response.json()["studies"][0]["id"] == "question-study"

    bundle_response = client.post(f"/api/studies/{study_id}/bundle")

    assert bundle_response.status_code == 200
    bundle_payload = bundle_response.json()["bundle"]
    assert bundle_payload["study_id"] == "question-study"
    assert bundle_payload["manifest_path"].endswith("manifest.json")

    audit_response = client.get("/api/audit-events")

    assert audit_response.status_code == 200
    event_types = [event["event_type"] for event in audit_response.json()["events"]]
    assert event_types[-4:] == [
        "study.created",
        "skill_pack.versioned",
        "batch.completed",
        "bundle.exported",
    ]


def test_study_workspace_file_batch_api_accepts_txt_and_docx(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    study_response = client.post(
        "/api/studies",
        json={"name": "Multipart Study"},
    )
    study_id = study_response.json()["study"]["id"]
    pack_response = client.post(
        f"/api/studies/{study_id}/skill-pack-versions",
        json={
            "id": "multipart_pack",
            "name": "Multipart Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    version_id = pack_response.json()["version"]["version_id"]

    docx_buffer = BytesIO()
    doc = Document()
    doc.add_paragraph("P2_c: Did balance improve?")
    doc.add_paragraph("P2_p: It improved a little.")
    doc.save(docx_buffer)
    docx_buffer.seek(0)

    response = client.post(
        f"/api/studies/{study_id}/batches/files",
        data={
            "skill_pack_version_id": version_id,
            "metadata": json.dumps(
                {
                    "P1_home_week1.txt": {
                        "participant_id": "P1",
                        "condition": "home",
                        "week": "week_1",
                    },
                    "P2_lab_week2.docx": {
                        "participant_id": "P2",
                        "condition": "lab",
                        "week": "week_2",
                    },
                }
            ),
        },
        files=[
            (
                "files",
                (
                    "P1_home_week1.txt",
                    b"P1_c: How did walking feel?\nP1_p: It felt steady.",
                    "text/plain",
                ),
            ),
            (
                "files",
                (
                    "P2_lab_week2.docx",
                    docx_buffer.getvalue(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ),
            ),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["batch"]["run_count"] == 2
    assert payload["batch"]["failure_count"] == 0
    rows = payload["results"][0]["rows"]
    assert rows[0]["participant_id"] == "P1"
    assert rows[0]["condition"] == "home"
    assert rows[0]["week"] == "week_1"
    assert rows[0]["turns"] == 1
    assert rows[3]["participant_id"] == "P2"
    assert rows[3]["condition"] == "lab"
    assert rows[3]["week"] == "week_2"
    assert rows[3]["turns"] == 1


def test_study_schema_api_persists_casebook_design(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    study_response = client.post(
        "/api/studies",
        json={"name": "Schema API Study"},
    )
    study_id = study_response.json()["study"]["id"]

    update_response = client.put(
        f"/api/studies/{study_id}/schema",
        json={
            "participant_count": 4,
            "conditions": ["home", "lab", "clinic"],
            "week_count": 3,
            "custom_fields": ["site", "arm"],
        },
    )
    read_response = client.get(f"/api/studies/{study_id}/schema")

    assert update_response.status_code == 200
    schema = update_response.json()["schema"]
    assert schema["study_id"] == study_id
    assert schema["participants"] == ["P1", "P2", "P3", "P4"]
    assert schema["conditions"] == ["home", "lab", "clinic"]
    assert schema["weeks"] == ["week_1", "week_2", "week_3"]
    assert schema["custom_fields"] == ["site", "arm"]
    assert read_response.status_code == 200
    assert read_response.json()["schema"] == schema


def test_study_batch_history_api_lists_and_loads_results(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    study_response = client.post("/api/studies", json={"name": "History API Study"})
    study_id = study_response.json()["study"]["id"]
    pack_response = client.post(
        f"/api/studies/{study_id}/skill-pack-versions",
        json={
            "id": "history_api_pack",
            "name": "History API Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    version_id = pack_response.json()["version"]["version_id"]
    first_response = client.post(
        f"/api/studies/{study_id}/batches/text",
        json={
            "skill_pack_version_id": version_id,
            "transcripts": [{"source_filename": "one.txt", "content": "CG: Hello.\nP: Hi."}],
        },
    )
    second_response = client.post(
        f"/api/studies/{study_id}/batches/text",
        json={
            "skill_pack_version_id": version_id,
            "transcripts": [{"source_filename": "two.txt", "content": "CG: Again?\nP: Yes."}],
        },
    )

    list_response = client.get(f"/api/studies/{study_id}/batches")
    loaded_response = client.get(
        f"/api/studies/{study_id}/batches/{first_response.json()['batch']['batch_id']}"
    )

    assert list_response.status_code == 200
    batch_ids = [batch["batch_id"] for batch in list_response.json()["batches"]]
    assert batch_ids == [
        second_response.json()["batch"]["batch_id"],
        first_response.json()["batch"]["batch_id"],
    ]
    assert loaded_response.status_code == 200
    loaded = loaded_response.json()
    assert loaded["batch"]["batch_id"] == first_response.json()["batch"]["batch_id"]
    assert loaded["results"][0]["metric_id"] == "base_metrics"
    assert loaded["results"][0]["rows"][0]["source_filename"] == "one.txt"


def test_study_batch_run_drilldown_api_lists_and_loads_one_run(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    study_response = client.post("/api/studies", json={"name": "Run Drilldown API Study"})
    study_id = study_response.json()["study"]["id"]
    pack_response = client.post(
        f"/api/studies/{study_id}/skill-pack-versions",
        json={
            "id": "run_drilldown_api_pack",
            "name": "Run Drilldown API Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    batch_response = client.post(
        f"/api/studies/{study_id}/batches/text",
        json={
            "skill_pack_version_id": pack_response.json()["version"]["version_id"],
            "transcripts": [
                {
                    "source_filename": "P1_home_week1.txt",
                    "metadata": {"participant_id": "P1", "condition": "home", "week": "week_1"},
                    "content": "P1_c: Hello?\nP1_p: Hi.",
                },
                {
                    "source_filename": "P2_lab_week1.txt",
                    "metadata": {"participant_id": "P2", "condition": "lab", "week": "week_1"},
                    "content": "P2_c: Again?\nP2_p: Yes.",
                },
            ],
        },
    )
    batch_id = batch_response.json()["batch"]["batch_id"]

    list_response = client.get(f"/api/studies/{study_id}/batches/{batch_id}/runs")
    run_id = list_response.json()["runs"][0]["run_id"]
    loaded_response = client.get(
        f"/api/studies/{study_id}/batches/{batch_id}/runs/{run_id}"
    )

    assert list_response.status_code == 200
    assert [run["source_filename"] for run in list_response.json()["runs"]] == [
        "P1_home_week1.txt",
        "P2_lab_week1.txt",
    ]
    assert list_response.json()["runs"][0]["metadata"]["participant_id"] == "P1"
    assert loaded_response.status_code == 200
    loaded = loaded_response.json()["run"]
    assert loaded["source_filename"] == "P1_home_week1.txt"
    assert loaded["turns"][0] == {
        "turn_index": 0,
        "role": "caregiver",
        "speaker_label": "Caregiver",
        "raw_prefix": "P1_c",
        "text": "Hello?",
    }
    assert loaded["results"][0]["metric_id"] == "base_metrics"


def test_study_batch_api_includes_failed_file_details(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Failure API Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "bad_metric_pack",
            "name": "Bad Metric Pack",
            "version": "1.0.0",
            "metrics": ["not_registered"],
        },
        validate=False,
    )
    batch = store.run_text_batch(
        study.id,
        version.version_id,
        [{"source_filename": "bad.txt", "content": "CG: Hello."}],
    )
    client = TestClient(app)

    response = client.get(f"/api/studies/{study.id}/batches/{batch.batch_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["batch"]["failure_count"] == 1
    assert payload["failures"] == [
        {
            "source_filename": "bad.txt",
            "error": "Skill pack references unknown metric id(s): not_registered",
        }
    ]


def test_library_approval_api_records_entries_and_audit(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/api/library/skill-packs",
        json={
            "payload": {
                "id": "approved_pack",
                "name": "Approved Pack",
                "version": "1.0.0",
                "metrics": ["base_metrics"],
            },
            "reviewer": "professor",
            "notes": "Ready for reuse.",
        },
    )

    assert response.status_code == 200
    assert response.json()["entry"]["id"] == "approved_pack"

    list_response = client.get("/api/library")

    assert list_response.status_code == 200
    assert list_response.json()["entries"][0]["entry_type"] == "skill_pack"

    audit_response = client.get("/api/audit-events")
    assert audit_response.json()["events"][-1]["event_type"] == (
        "library.skill_pack.approved"
    )


def test_deployment_profile_endpoint_reports_secure_offline_status(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client = TestClient(app)

    response = client.get("/api/deployment-profile/secure-offline")

    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert response.json()["checks"][1]["id"] == "network_llm_disabled"
