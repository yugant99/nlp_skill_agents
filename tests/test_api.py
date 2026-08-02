import json
import sqlite3
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.qualitative.coding_references import CodingReferenceConflictError
from backend.segmentation.pipeline import SegmentationRunStore
from backend.storage.evidence_catalog import EvidenceCatalog, EvidenceImportRecord
from backend.storage.segmentation_operation_store import SegmentationOperationStore
from backend.storage.source_blob_store import (
    SourceBlobIntegrityError,
    SourceBlobStore,
)
from backend.storage.study_store import StudyWorkspaceStore
from backend.storage.study_batch_operation_store import StudyBatchOperationStore


def _bootstrap_qualitative_project(
    client: TestClient,
    *,
    name: str,
    researcher_id: str = "res_api_researcher",
    researcher_name: str = "API Researcher",
) -> tuple[str, str]:
    study_response = client.post("/api/studies", json={"name": name})
    assert study_response.status_code == 200
    study_id = study_response.json()["study"]["id"]
    bootstrap_response = client.put(
        f"/api/studies/{study_id}/qualitative/project",
        json={
            "researcher_id": researcher_id,
            "researcher_name": researcher_name,
        },
    )
    assert bootstrap_response.status_code == 200
    return study_id, researcher_id


def _record_api_project_source(
    root: Path,
    *,
    project_source_id: str,
    workspace_id: str,
    suffix: str,
) -> None:
    EvidenceCatalog(root).record_import(
        EvidenceImportRecord(
            import_id=f"imp_{suffix}",
            run_id=f"run_{suffix}",
            pipeline="api-test",
            source_id=f"src_{suffix}",
            source_filename=f"{suffix}.txt",
            source_media_type="text/plain",
            source_blob_sha256="a" * 64,
            transcript_revision_id=f"trv_{suffix}",
            transcript_sha256="b" * 64,
            imported_at="2026-08-01T12:00:00+00:00",
            project_source_id=project_source_id,
            workspace_id=workspace_id,
        )
    )


def _bootstrap_coding_reference_api(
    client: TestClient,
    *,
    name: str,
) -> dict[str, object]:
    study_id, researcher_id = _bootstrap_qualitative_project(
        client,
        name=name,
    )
    codebooks_url = f"/api/studies/{study_id}/qualitative/codebooks"
    codebook_response = client.post(
        codebooks_url,
        json={
            "researcher_id": researcher_id,
            "title": "Coding reference codebook",
        },
    )
    assert codebook_response.status_code == 200
    codebook = codebook_response.json()["codebook"]
    version_response = client.post(
        f"{codebooks_url}/{codebook['codebook_id']}/versions",
        json={"researcher_id": researcher_id, "based_on_version_id": None},
    )
    assert version_response.status_code == 200
    version = version_response.json()["version"]
    codes_url = (
        f"{codebooks_url}/{codebook['codebook_id']}/versions/"
        f"{version['codebook_version_id']}/codes"
    )
    code_response = client.post(
        codes_url,
        json={
            "researcher_id": researcher_id,
            "stable_code_key": "arrival",
            "label": "Arrival",
        },
    )
    assert code_response.status_code == 200
    code = code_response.json()["code"]
    freeze_response = client.post(
        f"{codebooks_url}/{codebook['codebook_id']}/versions/"
        f"{version['codebook_version_id']}/freeze",
        json={"researcher_id": researcher_id},
    )
    assert freeze_response.status_code == 200

    segmentation_response = client.post(
        f"/api/studies/{study_id}/segmentation/runs",
        json={
            "source_filename": "coding-reference.txt",
            "descript_text": "[00:00:00] P: I came and I stayed.",
            "rule_ids": ["speaker-markers"],
        },
    )
    assert segmentation_response.status_code == 200
    run = segmentation_response.json()["run"]
    assert run["status"] == "verified"
    assert run["evidence_set_id"].startswith("evs_")
    assert run["cunit_adjudication"]["cunit_text_contract_version"] == 1
    decision = run["cunit_adjudication"]["decisions"][0]
    assert decision["cunit_ids"]
    assert decision["cunit_texts"][0]

    return {
        "study_id": study_id,
        "researcher_id": researcher_id,
        "codebook_id": codebook["codebook_id"],
        "codebook_version_id": version["codebook_version_id"],
        "code_id": code["code_id"],
        "codes_url": codes_url,
        "run": run,
    }


def test_health_endpoint() -> None:
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "storage": "local"}


def test_storage_schema_status_reports_applied_migrations(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.get("/api/storage/schema-status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["compatible"] is True
    assert payload["databases"]["analysis_runs"]["current_version"] == 6
    assert [
        migration["name"]
        for migration in payload["databases"]["analysis_runs"]["migrations"]
    ] == [
        "create-base-analysis-runs",
        "add-evidence-identity",
        "add-source-import-identity",
        "add-project-source-lineage",
        "index-analysis-run-history",
        "create-analysis-operation-journal",
    ]
    assert payload["databases"]["evidence_catalog"]["current_version"] == 4
    assert [
        migration["name"]
        for migration in payload["databases"]["evidence_catalog"]["migrations"]
    ] == [
        "create-import-catalog",
        "add-project-source-lineage",
        "index-workspace-history",
        "add-canonical-evidence-targets",
    ]
    assert payload["databases"]["segmentation_operations"]["current_version"] == 1
    assert [
        migration["name"]
        for migration in payload["databases"]["segmentation_operations"][
            "migrations"
        ]
    ] == ["create-segmentation-operations"]


def test_storage_schema_status_rejects_newer_database(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    with sqlite3.connect(tmp_path / "evidence.sqlite3") as connection:
        connection.execute("pragma user_version = 99")
    client = TestClient(app)

    response = client.get("/api/storage/schema-status")

    assert response.status_code == 409
    assert "newer than supported version 4" in response.json()["detail"]


def test_storage_schema_status_rejects_newer_segmentation_database(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    with sqlite3.connect(tmp_path / "segmentation.sqlite3") as connection:
        connection.execute("pragma user_version = 99")
    client = TestClient(app)

    response = client.get("/api/storage/schema-status")

    assert response.status_code == 409
    assert "segmentation operations schema version 99" in response.json()["detail"]


def test_qualitative_schema_status_reports_per_study_contract(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    study = client.post("/api/studies", json={"name": "Qualitative Contract"})
    study_id = study.json()["study"]["id"]

    response = client.get(f"/api/studies/{study_id}/qualitative/schema-status")

    assert response.status_code == 200
    assert response.json() == {
        "compatible": True,
        "project_id": study_id,
        "current_version": 2,
        "migrations": [
            {
                "version": 1,
                "name": "create-qualitative-core-contract",
                "applied_at": response.json()["migrations"][0]["applied_at"],
            },
            {
                "version": 2,
                "name": "add-coding-reference-contract",
                "applied_at": response.json()["migrations"][1]["applied_at"],
            },
        ],
    }
    assert (
        tmp_path / "studies" / study_id / "qualitative.sqlite3"
    ).is_file()


def test_qualitative_schema_status_rejects_missing_or_newer_project(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    missing = client.get("/api/studies/missing/qualitative/schema-status")
    study = client.post("/api/studies", json={"name": "Future Qualitative"})
    study_id = study.json()["study"]["id"]
    database_path = tmp_path / "studies" / study_id / "qualitative.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("pragma user_version = 99")

    newer = client.get(f"/api/studies/{study_id}/qualitative/schema-status")

    tampered_study = client.post(
        "/api/studies",
        json={"name": "Tampered Qualitative"},
    ).json()["study"]["id"]
    tampered_path = (
        tmp_path / "studies" / tampered_study / "qualitative.sqlite3"
    )
    assert client.get(
        f"/api/studies/{tampered_study}/qualitative/schema-status"
    ).status_code == 200
    with sqlite3.connect(tampered_path) as connection:
        connection.execute("drop trigger prevent_frozen_code_update")
    tampered = client.get(
        f"/api/studies/{tampered_study}/qualitative/schema-status"
    )

    assert missing.status_code == 404
    assert newer.status_code == 409
    assert "newer than supported version 2" in newer.json()["detail"]
    assert tampered.status_code == 409
    assert tampered.json()["detail"] == "Qualitative database is invalid"


def test_qualitative_project_bootstrap_is_fresh_and_idempotent(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    request = {
        "researcher_id": "res_bootstrap_api",
        "researcher_name": "  Bootstrap Researcher  ",
    }

    missing = client.put(
        "/api/studies/missing/qualitative/project",
        json=request,
    )
    malformed = client.put(
        "/api/studies/missing/qualitative/project",
        json={"researcher_id": "res_bootstrap_api"},
    )
    study = client.post("/api/studies", json={"name": "Bootstrap API"})
    study_id = study.json()["study"]["id"]
    first = client.put(
        f"/api/studies/{study_id}/qualitative/project",
        json=request,
    )
    repeated = client.put(
        f"/api/studies/{study_id}/qualitative/project",
        json=request,
    )
    conflict = client.put(
        f"/api/studies/{study_id}/qualitative/project",
        json={**request, "researcher_name": "Different Researcher"},
    )
    different_actor = client.put(
        f"/api/studies/{study_id}/qualitative/project",
        json={
            "researcher_id": "res_second_bootstrap",
            "researcher_name": "Second Researcher",
        },
    )

    assert missing.status_code == 404
    assert malformed.status_code == 422
    assert first.status_code == 200
    assert first.json() == {
        "project": {
            "project_id": study_id,
            "researcher": {
                "researcher_id": "res_bootstrap_api",
                "display_name": "Bootstrap Researcher",
                "role": "researcher",
                "active": True,
            },
        }
    }
    assert repeated.status_code == 200
    assert repeated.json() == first.json()
    assert conflict.status_code == 409
    assert different_actor.status_code == 409
    with sqlite3.connect(
        tmp_path / "studies" / study_id / "qualitative.sqlite3"
    ) as connection:
        assert connection.execute("select count(*) from researchers").fetchone() == (
            1,
        )
        assert connection.execute(
            """
            select count(*) from qualitative_audit_events
            where event_type = 'qualitative.project.initialized'
            """
        ).fetchone() == (1,)


def test_codebook_api_happy_flow_hierarchy_freeze_and_derivation(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    study_id, researcher_id = _bootstrap_qualitative_project(
        client,
        name="Codebook Happy API",
    )
    base = f"/api/studies/{study_id}/qualitative/codebooks"

    created = client.post(
        base,
        json={
            "researcher_id": researcher_id,
            "title": " Interview themes ",
            "description": "Portable thematic codebook",
        },
    )
    assert created.status_code == 200
    codebook = created.json()["codebook"]
    codebook_id = codebook["codebook_id"]
    assert codebook["title"] == "Interview themes"
    assert client.get(base).json()["codebooks"] == [codebook]

    draft = client.post(
        f"{base}/{codebook_id}/versions",
        json={"researcher_id": researcher_id, "based_on_version_id": None},
    )
    assert draft.status_code == 200
    version_id = draft.json()["version"]["codebook_version_id"]
    assert draft.json()["version"]["version_number"] == 1
    assert draft.json()["version"]["status"] == "draft"
    assert draft.json()["codes"] == []

    codes_url = f"{base}/{codebook_id}/versions/{version_id}/codes"
    root = client.post(
        codes_url,
        json={
            "researcher_id": researcher_id,
            "stable_code_key": "support",
            "label": "Support",
            "sort_order": 0,
        },
    )
    assert root.status_code == 200
    root_code = root.json()["code"]
    child = client.post(
        codes_url,
        json={
            "researcher_id": researcher_id,
            "stable_code_key": "peer_support",
            "label": "Peer support",
            "parent_code_id": root_code["code_id"],
            "definition": "Help provided by peers",
            "examples": ["A friend called me"],
            "sort_order": 0,
        },
    )
    assert child.status_code == 200
    child_code = child.json()["code"]
    updated = client.put(
        f"{codes_url}/{child_code['code_id']}",
        json={
            "researcher_id": researcher_id,
            "label": "Peer support revised",
            "parent_code_id": root_code["code_id"],
            "definition": "Revised peer help",
            "inclusion_criteria": "Named peer assistance",
            "exclusion_criteria": "Professional assistance",
            "examples": ["A friend checked in"],
            "notes": "Review during coding",
            "color": "#336699",
            "sort_order": 1,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["code"]["stable_code_key"] == "peer_support"
    assert updated.json()["code"]["label"] == "Peer support revised"

    read_url = f"{base}/{codebook_id}/versions/{version_id}"
    read = client.get(read_url)
    assert read.status_code == 200
    assert [code["stable_code_key"] for code in read.json()["codes"]] == [
        "support",
        "peer_support",
    ]

    frozen = client.post(
        f"{read_url}/freeze",
        json={"researcher_id": researcher_id},
    )
    repeated = client.post(
        f"{read_url}/freeze",
        json={"researcher_id": researcher_id},
    )
    rejected = client.put(
        f"{codes_url}/{child_code['code_id']}",
        json={
            "researcher_id": researcher_id,
            "label": "Rejected frozen change",
            "parent_code_id": root_code["code_id"],
        },
    )
    assert frozen.status_code == 200
    assert frozen.json()["version"]["status"] == "frozen"
    assert repeated.status_code == 200
    assert repeated.json() == frozen.json()
    assert rejected.status_code == 409

    derived = client.post(
        f"{base}/{codebook_id}/versions",
        json={
            "researcher_id": researcher_id,
            "based_on_version_id": version_id,
        },
    )
    assert derived.status_code == 200
    assert derived.json()["version"]["version_number"] == 2
    assert derived.json()["version"]["status"] == "draft"
    assert [code["stable_code_key"] for code in derived.json()["codes"]] == [
        "support",
        "peer_support",
    ]
    assert {
        code["code_id"] for code in derived.json()["codes"]
    }.isdisjoint({root_code["code_id"], child_code["code_id"]})
    derived_by_key = {
        code["stable_code_key"]: code for code in derived.json()["codes"]
    }
    assert (
        derived_by_key["peer_support"]["parent_code_id"]
        == derived_by_key["support"]["code_id"]
    )


def test_codebook_api_maps_structural_domain_missing_and_conflict_errors(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    study_id = client.post(
        "/api/studies",
        json={"name": "Codebook Errors API"},
    ).json()["study"]["id"]
    base = f"/api/studies/{study_id}/qualitative/codebooks"

    uninitialized_list = client.get(base)
    missing_body = client.post(base, json={"title": "No actor"})
    missing_researcher = client.post(
        base,
        json={"researcher_id": "res_unknown", "title": "No bootstrap"},
    )
    assert uninitialized_list.status_code == 404
    assert missing_body.status_code == 422
    assert missing_researcher.status_code == 404

    bootstrap = client.put(
        f"/api/studies/{study_id}/qualitative/project",
        json={
            "researcher_id": "res_error_api",
            "researcher_name": "Error Researcher",
        },
    )
    assert bootstrap.status_code == 200
    blank = client.post(
        base,
        json={"researcher_id": "res_error_api", "title": "   "},
    )
    missing_codebook = client.post(
        f"{base}/cbk_missing/versions",
        json={"researcher_id": "res_error_api", "based_on_version_id": None},
    )
    assert blank.status_code == 400
    assert missing_codebook.status_code == 404

    created = client.post(
        base,
        json={"researcher_id": "res_error_api", "title": "Errors"},
    ).json()["codebook"]
    draft = client.post(
        f"{base}/{created['codebook_id']}/versions",
        json={"researcher_id": "res_error_api", "based_on_version_id": None},
    ).json()
    version_id = draft["version"]["codebook_version_id"]
    freeze_empty = client.post(
        f"{base}/{created['codebook_id']}/versions/{version_id}/freeze",
        json={"researcher_id": "res_error_api"},
    )
    codes_url = f"{base}/{created['codebook_id']}/versions/{version_id}/codes"
    first = client.post(
        codes_url,
        json={
            "researcher_id": "res_error_api",
            "stable_code_key": "duplicate",
            "label": "First",
        },
    )
    duplicate = client.post(
        codes_url,
        json={
            "researcher_id": "res_error_api",
            "stable_code_key": "duplicate",
            "label": "Second",
        },
    )
    assert freeze_empty.status_code == 400
    assert first.status_code == 200
    assert duplicate.status_code == 409


@pytest.mark.parametrize("invalid_sort_order", [True, "1", 1.0])
def test_codebook_api_rejects_coercive_sort_order_types(
    tmp_path,
    monkeypatch,
    invalid_sort_order: object,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    study_id, researcher_id = _bootstrap_qualitative_project(
        client,
        name="Codebook Strict Sort Order API",
    )
    base = f"/api/studies/{study_id}/qualitative/codebooks"
    codebook = client.post(
        base,
        json={"researcher_id": researcher_id, "title": "Strict ordering"},
    ).json()["codebook"]
    draft = client.post(
        f"{base}/{codebook['codebook_id']}/versions",
        json={"researcher_id": researcher_id, "based_on_version_id": None},
    ).json()["version"]
    codes_url = (
        f"{base}/{codebook['codebook_id']}/versions/"
        f"{draft['codebook_version_id']}/codes"
    )

    rejected_create = client.post(
        codes_url,
        json={
            "researcher_id": researcher_id,
            "stable_code_key": "rejected",
            "label": "Rejected",
            "sort_order": invalid_sort_order,
        },
    )
    accepted = client.post(
        codes_url,
        json={
            "researcher_id": researcher_id,
            "stable_code_key": "accepted",
            "label": "Accepted",
        },
    )
    assert accepted.status_code == 200
    rejected_update = client.put(
        f"{codes_url}/{accepted.json()['code']['code_id']}",
        json={
            "researcher_id": researcher_id,
            "label": "Rejected update",
            "sort_order": invalid_sort_order,
        },
    )

    assert rejected_create.status_code == 422
    assert rejected_update.status_code == 422


def test_codebook_api_exports_imports_and_rejects_invalid_or_newer_documents(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    study_id, researcher_id = _bootstrap_qualitative_project(
        client,
        name="Codebook Import API",
    )
    base = f"/api/studies/{study_id}/qualitative/codebooks"
    codebook = client.post(
        base,
        json={
            "researcher_id": researcher_id,
            "title": "Portable",
            "description": "Round trip",
        },
    ).json()["codebook"]
    draft = client.post(
        f"{base}/{codebook['codebook_id']}/versions",
        json={"researcher_id": researcher_id, "based_on_version_id": None},
    ).json()
    version_id = draft["version"]["codebook_version_id"]
    add = client.post(
        f"{base}/{codebook['codebook_id']}/versions/{version_id}/codes",
        json={
            "researcher_id": researcher_id,
            "stable_code_key": "portable_code",
            "label": "Portable code",
            "definition": "Survives export and import",
            "examples": ["Example text"],
        },
    )
    assert add.status_code == 200

    export_url = (
        f"{base}/{codebook['codebook_id']}/versions/{version_id}/export"
    )
    exported = client.get(export_url)
    assert exported.status_code == 200
    document = exported.json()
    assert document["format"] == "nlp-skill-agents.codebook-version"
    assert document["format_version"] == 1
    assert document["codes"][0]["stable_code_key"] == "portable_code"

    imported = client.post(
        f"{base}/import",
        json={"researcher_id": researcher_id, "document": document},
    )
    assert imported.status_code == 200
    imported_payload = imported.json()
    assert imported_payload["version"]["version_number"] == 1
    assert imported_payload["version"]["status"] == "draft"
    assert imported_payload["codebook"]["codebook_id"] != codebook["codebook_id"]
    assert imported_payload["codes"][0]["stable_code_key"] == "portable_code"
    assert imported_payload["codes"][0]["code_id"] != add.json()["code"]["code_id"]

    invalid_document = {**document, "format_version": 99}
    invalid = client.post(
        f"{base}/import",
        json={"researcher_id": researcher_id, "document": invalid_document},
    )
    malformed_json = client.post(
        f"{base}/import",
        content="{not-json",
        headers={"content-type": "application/json"},
    )
    assert invalid.status_code == 400
    assert malformed_json.status_code == 422
    assert len(client.get(base).json()["codebooks"]) == 2

    future_study = client.post(
        "/api/studies",
        json={"name": "Future Codebook API"},
    ).json()["study"]["id"]
    future_database = (
        tmp_path / "studies" / future_study / "qualitative.sqlite3"
    )
    with sqlite3.connect(future_database) as connection:
        connection.execute("pragma user_version = 99")
    newer = client.put(
        f"/api/studies/{future_study}/qualitative/project",
        json={
            "researcher_id": "res_future_api",
            "researcher_name": "Future Researcher",
        },
    )
    assert newer.status_code == 409


def test_coding_reference_api_lifecycle_exact_envelopes_and_strict_queries(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    fixture = _bootstrap_coding_reference_api(
        client,
        name="Coding Reference Lifecycle API",
    )
    study_id = fixture["study_id"]
    researcher_id = fixture["researcher_id"]
    run = fixture["run"]
    assert isinstance(study_id, str)
    assert isinstance(researcher_id, str)
    assert isinstance(run, dict)
    base = f"/api/studies/{study_id}/qualitative/coding-references"
    event = run["events"][0]
    decision = run["cunit_adjudication"]["decisions"][0]
    passage_request = {
        "researcher_id": researcher_id,
        "project_source_id": run["project_source_id"],
        "transcript_revision_id": run["transcript_revision_id"],
        "evidence_set_id": run["evidence_set_id"],
        "target_kind": "passage",
        "passage_id": event["passage_id"],
        "cunit_id": "",
        "start_offset": 0,
        "end_offset": 1,
        "codebook_version_id": fixture["codebook_version_id"],
        "code_id": fixture["code_id"],
    }

    created = client.post(base, json=passage_request)
    repeated = client.post(base, json=passage_request)

    assert created.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json() == created.json()
    passage_reference = created.json()["coding_reference"]
    assert set(passage_reference) == {
        "coding_reference_id",
        "project_id",
        "project_source_id",
        "transcript_revision_id",
        "evidence_set_id",
        "target_kind",
        "passage_id",
        "cunit_id",
        "start_offset",
        "end_offset",
        "codebook_version_id",
        "code_id",
        "created_by",
        "created_at",
        "removed_by",
        "removed_at",
    }
    assert passage_reference["project_id"] == study_id
    assert passage_reference["removed_by"] is None
    assert passage_reference["removed_at"] is None
    assert "I came" not in created.text
    assert "Arrival" not in created.text

    cunit_request = {
        **passage_request,
        "target_kind": "cunit",
        "cunit_id": decision["cunit_ids"][0],
    }
    cunit_created = client.post(base, json=cunit_request)
    assert cunit_created.status_code == 200
    cunit_reference = cunit_created.json()["coding_reference"]
    assert cunit_reference["coding_reference_id"] != passage_reference[
        "coding_reference_id"
    ]

    fetched = client.get(
        f"{base}/{passage_reference['coding_reference_id']}"
    )
    filtered = client.get(
        base,
        params={
            "project_source_id": run["project_source_id"],
            "codebook_version_id": fixture["codebook_version_id"],
            "code_id": fixture["code_id"],
            "created_by": researcher_id,
        },
    )
    assert fetched.status_code == 200
    assert fetched.json() == created.json()
    assert filtered.status_code == 200
    assert filtered.json()["coding_references"] == [
        passage_reference,
        cunit_reference,
    ]

    removed = client.request(
        "DELETE",
        f"{base}/{passage_reference['coding_reference_id']}",
        json={"researcher_id": researcher_id},
    )
    removed_again = client.request(
        "DELETE",
        f"{base}/{passage_reference['coding_reference_id']}",
        json={"researcher_id": researcher_id},
    )
    assert removed.status_code == 200
    assert removed_again.status_code == 200
    assert removed_again.json() == removed.json()
    removed_reference = removed.json()["coding_reference"]
    assert removed_reference["removed_by"] == researcher_id
    assert removed_reference["removed_at"] is not None
    assert client.get(base).json()["coding_references"] == [cunit_reference]
    included = client.get(base, params={"include_removed": "true"})
    assert included.status_code == 200
    assert included.json()["coding_references"] == [
        removed_reference,
        cunit_reference,
    ]

    for invalid_value in ("1", "yes", "TRUE"):
        rejected = client.get(
            base,
            params={"include_removed": invalid_value},
        )
        assert rejected.status_code == 422
    assert client.get(
        f"{base}?include_removed=true&include_removed=false"
    ).status_code == 422
    assert client.get(
        f"{base}?code_id={fixture['code_id']}&code_id={fixture['code_id']}"
    ).status_code == 422
    assert client.get(
        base,
        params={"project_source_id[eq]": run["project_source_id"]},
    ).status_code == 422


@pytest.mark.parametrize("invalid_offset", [True, "0", 0.0])
def test_coding_reference_api_rejects_coercive_offset_types(
    tmp_path,
    monkeypatch,
    invalid_offset: object,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    fixture = _bootstrap_coding_reference_api(
        client,
        name=f"Coding Reference Strict Offset {invalid_offset!r}",
    )
    study_id = fixture["study_id"]
    run = fixture["run"]
    assert isinstance(study_id, str)
    assert isinstance(run, dict)
    response = client.post(
        f"/api/studies/{study_id}/qualitative/coding-references",
        json={
            "researcher_id": fixture["researcher_id"],
            "project_source_id": run["project_source_id"],
            "transcript_revision_id": run["transcript_revision_id"],
            "evidence_set_id": run["evidence_set_id"],
            "target_kind": "passage",
            "passage_id": run["events"][0]["passage_id"],
            "cunit_id": "",
            "start_offset": invalid_offset,
            "end_offset": 1,
            "codebook_version_id": fixture["codebook_version_id"],
            "code_id": fixture["code_id"],
        },
    )

    assert response.status_code == 422


def test_coding_reference_api_maps_domain_errors_without_private_details(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    fixture = _bootstrap_coding_reference_api(
        client,
        name="Coding Reference Error API",
    )
    study_id = fixture["study_id"]
    researcher_id = fixture["researcher_id"]
    run = fixture["run"]
    assert isinstance(study_id, str)
    assert isinstance(researcher_id, str)
    assert isinstance(run, dict)
    base = f"/api/studies/{study_id}/qualitative/coding-references"
    decision = run["cunit_adjudication"]["decisions"][0]
    request = {
        "researcher_id": researcher_id,
        "project_source_id": run["project_source_id"],
        "transcript_revision_id": run["transcript_revision_id"],
        "evidence_set_id": run["evidence_set_id"],
        "target_kind": "passage",
        "passage_id": run["events"][0]["passage_id"],
        "cunit_id": "",
        "start_offset": 0,
        "end_offset": 1,
        "codebook_version_id": fixture["codebook_version_id"],
        "code_id": fixture["code_id"],
    }

    invalid_requests = [
        {**request, "start_offset": -1},
        {**request, "start_offset": 1, "end_offset": 1},
        {**request, "end_offset": 10_000},
        {**request, "target_kind": "unknown"},
        {**request, "cunit_id": decision["cunit_ids"][0]},
        {**request, "target_kind": "cunit", "cunit_id": ""},
    ]
    for invalid_request in invalid_requests:
        invalid = client.post(base, json=invalid_request)
        assert invalid.status_code == 400
        assert invalid.json() == {
            "detail": "Coding reference request is invalid"
        }

    missing_actor = client.post(
        base,
        json={**request, "researcher_id": "res_missing"},
    )
    missing_target = client.post(
        base,
        json={**request, "evidence_set_id": "evs_" + "0" * 32},
    )
    missing_study = client.get(
        "/api/studies/not-real/qualitative/coding-references"
    )
    assert missing_actor.status_code == 404
    assert missing_target.status_code == 404
    assert missing_study.status_code == 404
    for response in (missing_actor, missing_target):
        assert response.json() == {
            "detail": "Coding reference dependency was not found"
        }
    assert missing_study.json() == {"detail": "Study not found"}

    private_study_detail = "PRIVATE-STUDY /private/study.json"
    corrupt_study_id = client.post(
        "/api/studies",
        json={"name": "Corrupt Coding Reference Study"},
    ).json()["study"]["id"]
    (
        tmp_path / "studies" / corrupt_study_id / "study.json"
    ).write_text(private_study_detail, encoding="utf-8")
    corrupt_base = (
        f"/api/studies/{corrupt_study_id}/qualitative/coding-references"
    )
    corrupt_responses = [
        client.get(corrupt_base),
        client.post(corrupt_base, json=request),
        client.get(f"{corrupt_base}/{'cdr_' + '0' * 32}"),
        client.request(
            "DELETE",
            f"{corrupt_base}/{'cdr_' + '0' * 32}",
            json={"researcher_id": researcher_id},
        ),
    ]
    for corrupt_study in corrupt_responses:
        assert corrupt_study.status_code == 409
        assert corrupt_study.json() == {
            "detail": "Study storage is unavailable or invalid"
        }
        assert private_study_detail not in corrupt_study.text

    codebooks_url = f"/api/studies/{study_id}/qualitative/codebooks"
    draft = client.post(
        f"{codebooks_url}/{fixture['codebook_id']}/versions",
        json={
            "researcher_id": researcher_id,
            "based_on_version_id": fixture["codebook_version_id"],
        },
    )
    assert draft.status_code == 200
    draft_version = draft.json()["version"]
    draft_conflict = client.post(
        base,
        json={
            **request,
            "codebook_version_id": draft_version["codebook_version_id"],
            "code_id": draft.json()["codes"][0]["code_id"],
        },
    )
    assert draft_conflict.status_code == 409
    assert draft_conflict.json() == {
        "detail": "Coding reference state conflicts with stored data"
    }

    malformed_sentinel = "PRIVATE-MALFORMED /private/malformed/path"
    malformed = client.post(
        base,
        content="{not-json " + malformed_sentinel,
        headers={"content-type": "application/json"},
    )
    extra_sentinel = "PRIVATE-EXTRA /private/extra/path"
    extra_field = client.post(
        base,
        json={**request, "content": extra_sentinel},
    )
    assert malformed.status_code == 422
    assert extra_field.status_code == 422
    assert malformed.json() == {"detail": "Request validation failed"}
    assert extra_field.json() == {"detail": "Request validation failed"}
    assert malformed_sentinel not in malformed.text
    assert extra_sentinel not in extra_field.text

    query_sentinel = "PRIVATE-QUERY /private/query/path"
    invalid_query = client.get(
        base,
        params={"project_source_id[eq]": query_sentinel},
    )
    assert invalid_query.status_code == 422
    assert invalid_query.json() == {"detail": "Request validation failed"}
    assert query_sentinel not in invalid_query.text

    private_detail = "PRIVATE-CONTENT /private/evidence/path sha256-deadbeef"

    def reject_list(self, **kwargs):
        raise CodingReferenceConflictError(private_detail)

    monkeypatch.setattr(
        "backend.app.main.CodingReferenceService.list_references",
        reject_list,
    )
    private_conflict = client.get(base)
    assert private_conflict.status_code == 409
    assert private_conflict.json() == {
        "detail": "Coding reference state conflicts with stored data"
    }
    assert private_detail not in private_conflict.text


def test_case_attribute_api_happy_flow_and_exact_envelopes(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    study_id, researcher_id = _bootstrap_qualitative_project(
        client,
        name="Case Attribute Happy API",
    )
    cases_url = f"/api/studies/{study_id}/qualitative/cases"

    created_by_kind = {}
    for case_kind, label in [
        ("timepoint", "Week 1"),
        ("participant", " P1 "),
        ("session", "Session 1"),
        ("condition", "Control"),
        ("dyad", "Dyad 1"),
    ]:
        response = client.post(
            cases_url,
            json={
                "researcher_id": researcher_id,
                "case_kind": case_kind,
                "label": label,
            },
        )
        assert response.status_code == 200
        case = response.json()["case"]
        assert set(case) == {
            "case_id",
            "project_id",
            "case_kind",
            "label",
            "description",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        }
        created_by_kind[case_kind] = case

    listed = client.get(cases_url)
    assert listed.status_code == 200
    assert [case["case_kind"] for case in listed.json()["cases"]] == [
        "condition",
        "dyad",
        "participant",
        "session",
        "timepoint",
    ]

    participant = created_by_kind["participant"]
    updated = client.put(
        f"{cases_url}/{participant['case_id']}",
        json={
            "researcher_id": researcher_id,
            "case_kind": "participant",
            "label": "P1 revised",
            "description": "Primary participant",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["case"]["case_id"] == participant["case_id"]
    assert updated.json()["case"]["label"] == "P1 revised"

    definitions_url = (
        f"/api/studies/{study_id}/qualitative/attribute-definitions"
    )
    definition_response = client.post(
        definitions_url,
        json={
            "researcher_id": researcher_id,
            "attribute_key": "study_arm",
            "label": "Study arm",
            "value_type": "categorical",
            "allowed_values": ["control", "intervention"],
            "required": True,
        },
    )
    assert definition_response.status_code == 200
    definition = definition_response.json()["attribute_definition"]
    assert set(definition) == {
        "attribute_definition_id",
        "project_id",
        "attribute_key",
        "label",
        "value_type",
        "allowed_values",
        "required",
        "created_by",
        "updated_by",
        "created_at",
        "updated_at",
    }
    assert definition["allowed_values"] == ["control", "intervention"]
    assert definition["required"] is True
    assert client.get(definitions_url).json()["attribute_definitions"] == [
        definition
    ]

    attribute_url = (
        f"{cases_url}/{participant['case_id']}/attributes/"
        f"{definition['attribute_definition_id']}"
    )
    set_value = client.put(
        attribute_url,
        json={"researcher_id": researcher_id, "value": "control"},
    )
    assert set_value.status_code == 200
    attribute_value = set_value.json()["attribute_value"]
    assert set(attribute_value) == {
        "project_id",
        "case_id",
        "attribute_definition_id",
        "attribute_key",
        "value_type",
        "value",
        "updated_by",
        "created_at",
        "updated_at",
    }
    assert attribute_value["value"] == "control"

    source_id = "psrc/case-api?revision=1"
    _record_api_project_source(
        tmp_path,
        project_source_id=source_id,
        workspace_id=study_id,
        suffix="case_api_source",
    )
    sources_url = f"{cases_url}/{participant['case_id']}/sources"
    source_request = {
        "researcher_id": researcher_id,
        "project_source_id": source_id,
    }
    linked = client.put(sources_url, json=source_request)
    retried = client.put(sources_url, json=source_request)
    assert linked.status_code == 200
    assert retried.status_code == 200
    assert retried.json() == linked.json()
    assert set(linked.json()["source_link"]) == {
        "project_id",
        "project_source_id",
        "case_id",
        "linked_by",
        "created_at",
    }

    snapshot = client.get(f"{cases_url}/{participant['case_id']}")
    assert snapshot.status_code == 200
    assert set(snapshot.json()) == {
        "case",
        "attribute_values",
        "project_source_ids",
    }
    assert snapshot.json()["attribute_values"] == [attribute_value]
    assert snapshot.json()["project_source_ids"] == [source_id]

    replaced = client.put(
        attribute_url,
        json={"researcher_id": researcher_id, "value": "intervention"},
    )
    padded_attribute_url = (
        f"{cases_url}/%20{participant['case_id']}%20/attributes/"
        f"%20{definition['attribute_definition_id']}%20"
    )
    padded_sources_url = (
        f"{cases_url}/%20{participant['case_id']}%20/sources"
    )
    cleared = client.request(
        "DELETE",
        padded_attribute_url,
        json={"researcher_id": researcher_id},
    )
    unlinked = client.request("DELETE", padded_sources_url, json=source_request)
    assert replaced.status_code == 200
    assert replaced.json()["attribute_value"]["value"] == "intervention"
    assert cleared.status_code == 200
    assert cleared.json() == {
        "cleared": {
            "case_id": participant["case_id"],
            "attribute_definition_id": definition["attribute_definition_id"],
        }
    }
    assert unlinked.status_code == 200
    assert unlinked.json() == {
        "unlinked": {
            "case_id": participant["case_id"],
            "project_source_id": source_id,
        }
    }


def test_case_attribute_api_structural_validation_precedes_service() -> None:
    client = TestClient(app)
    base = "/api/studies/missing/qualitative"

    responses = [
        client.post(
            f"{base}/cases",
            json={"case_kind": "participant", "label": "P1"},
        ),
        client.post(
            f"{base}/attribute-definitions",
            json={
                "researcher_id": "res_missing",
                "attribute_key": "arm",
                "label": "Arm",
                "value_type": "categorical",
                "allowed_values": ["control"],
                "required": 1,
            },
        ),
        client.post(
            f"{base}/attribute-definitions",
            json={
                "researcher_id": "res_missing",
                "attribute_key": "arm",
                "label": "Arm",
                "value_type": "categorical",
                "allowed_values": ["control"],
                "required": "true",
            },
        ),
        client.post(
            f"{base}/attribute-definitions",
            json={
                "researcher_id": "res_missing",
                "attribute_key": "arm",
                "label": "Arm",
                "value_type": "categorical",
                "allowed_values": {"choice": "control"},
            },
        ),
        client.put(
            f"{base}/cases/cas_missing/attributes/atr_missing",
            json={"researcher_id": "res_missing"},
        ),
        client.put(
            f"{base}/cases/cas_missing/sources",
            json={"researcher_id": "res_missing"},
        ),
        client.request(
            "DELETE",
            f"{base}/cases/cas_missing/sources",
            json={"researcher_id": "res_missing", "project_source_id": 1},
        ),
        client.post(
            f"{base}/cases",
            content="{not-json",
            headers={"content-type": "application/json"},
        ),
    ]

    assert [response.status_code for response in responses] == [422] * len(
        responses
    )


def test_case_attribute_api_maps_domain_missing_and_conflict_errors(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    study_id = client.post(
        "/api/studies",
        json={"name": "Case Attribute Error API"},
    ).json()["study"]["id"]
    cases_url = f"/api/studies/{study_id}/qualitative/cases"

    assert client.get(cases_url).status_code == 404
    study_id, researcher_id = _bootstrap_qualitative_project(
        client,
        name="Case Attribute Domain API",
    )
    cases_url = f"/api/studies/{study_id}/qualitative/cases"
    invalid_kind = client.post(
        cases_url,
        json={
            "researcher_id": researcher_id,
            "case_kind": "unknown",
            "label": "Invalid",
        },
    )
    missing_researcher = client.post(
        cases_url,
        json={
            "researcher_id": "res_unknown",
            "case_kind": "participant",
            "label": "Unknown actor",
        },
    )
    assert invalid_kind.status_code == 400
    assert missing_researcher.status_code == 404

    case = client.post(
        cases_url,
        json={
            "researcher_id": researcher_id,
            "case_kind": "participant",
            "label": "P1",
        },
    ).json()["case"]
    database_path = tmp_path / "studies" / study_id / "qualitative.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executemany(
            """
            insert into researchers (
              researcher_id, project_id, display_name, role,
              active, created_at, updated_at
            ) values (?, ?, ?, 'researcher', ?, ?, ?)
            """,
            [
                (
                    "res_other_api",
                    study_id,
                    "Other API Researcher",
                    1,
                    "2026-08-01T12:00:00+00:00",
                    "2026-08-01T12:00:00+00:00",
                ),
                (
                    "res_inactive_api",
                    study_id,
                    "Inactive API Researcher",
                    0,
                    "2026-08-01T12:00:00+00:00",
                    "2026-08-01T12:00:00+00:00",
                ),
            ],
        )
    inactive_researcher = client.post(
        cases_url,
        json={
            "researcher_id": "res_inactive_api",
            "case_kind": "participant",
            "label": "Inactive actor",
        },
    )
    assert inactive_researcher.status_code == 409

    definitions_url = (
        f"/api/studies/{study_id}/qualitative/attribute-definitions"
    )
    definition_request = {
        "researcher_id": researcher_id,
        "attribute_key": "score",
        "label": "Score",
        "value_type": "number",
    }
    invalid_definition_type = client.post(
        definitions_url,
        json={
            **definition_request,
            "attribute_key": "unknown_type",
            "value_type": "unknown",
        },
    )
    definition = client.post(
        definitions_url,
        json=definition_request,
    ).json()["attribute_definition"]
    duplicate = client.post(definitions_url, json=definition_request)
    attribute_url = (
        f"{cases_url}/{case['case_id']}/attributes/"
        f"{definition['attribute_definition_id']}"
    )
    wrong_values = [
        client.put(
            attribute_url,
            json={"researcher_id": researcher_id, "value": value},
        )
        for value in (True, None, [], {})
    ]
    missing_definition = client.put(
        f"{cases_url}/{case['case_id']}/attributes/atr_missing",
        json={"researcher_id": researcher_id, "value": 1},
    )
    missing_value = client.request(
        "DELETE",
        attribute_url,
        json={"researcher_id": researcher_id},
    )
    assert invalid_definition_type.status_code == 400
    assert duplicate.status_code == 409
    assert [response.status_code for response in wrong_values] == [400] * 4
    assert missing_definition.status_code == 404
    assert missing_value.status_code == 404
    assert client.get(f"{cases_url}/cas_missing").status_code == 404

    sources_url = f"{cases_url}/{case['case_id']}/sources"
    missing_source = client.put(
        sources_url,
        json={
            "researcher_id": researcher_id,
            "project_source_id": "psrc_missing",
        },
    )
    normalized_source_id = client.put(
        sources_url,
        json={
            "researcher_id": researcher_id,
            "project_source_id": " psrc_missing ",
        },
    )
    _record_api_project_source(
        tmp_path,
        project_source_id="psrc_foreign",
        workspace_id="different-workspace",
        suffix="foreign_case_api_source",
    )
    wrong_workspace = client.put(
        sources_url,
        json={
            "researcher_id": researcher_id,
            "project_source_id": "psrc_foreign",
        },
    )
    _record_api_project_source(
        tmp_path,
        project_source_id="psrc_actor_conflict",
        workspace_id=study_id,
        suffix="actor_conflict_case_api_source",
    )
    actor_link = client.put(
        sources_url,
        json={
            "researcher_id": researcher_id,
            "project_source_id": "psrc_actor_conflict",
        },
    )
    different_actor = client.put(
        sources_url,
        json={
            "researcher_id": "res_other_api",
            "project_source_id": "psrc_actor_conflict",
        },
    )
    missing_link = client.request(
        "DELETE",
        sources_url,
        json={
            "researcher_id": researcher_id,
            "project_source_id": "psrc_missing",
        },
    )
    assert missing_source.status_code == 404
    assert normalized_source_id.status_code == 400
    assert missing_link.status_code == 404
    assert wrong_workspace.status_code == 409
    assert actor_link.status_code == 200
    assert different_actor.status_code == 409

    future_study = client.post(
        "/api/studies",
        json={"name": "Future Case Attribute API"},
    ).json()["study"]["id"]
    future_database = (
        tmp_path / "studies" / future_study / "qualitative.sqlite3"
    )
    with sqlite3.connect(future_database) as connection:
        connection.execute("pragma user_version = 99")
    newer = client.get(
        f"/api/studies/{future_study}/qualitative/cases"
    )
    assert newer.status_code == 409


def test_case_attribute_api_contains_raw_missing_file_details(
    tmp_path,
    monkeypatch,
) -> None:
    private_path = str(tmp_path / "private-study" / "qualitative.sqlite3")

    def raise_private_missing_file(*_args, **_kwargs):
        raise FileNotFoundError(private_path)

    monkeypatch.setattr(
        "backend.app.main.CaseService.list_cases",
        raise_private_missing_file,
    )
    response = TestClient(app).get(
        "/api/studies/study-private/qualitative/cases"
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Qualitative project data was not found"
    }
    assert private_path not in response.text


def test_analysis_operations_endpoint_reports_completed_and_incomplete(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    created = client.post(
        "/api/runs/text",
        json={
            "source_filename": "journal.txt",
            "content": "vr001_c: Journal this.\nvr001_p: Okay.",
            "config": {
                "participant_id": "vr001",
                "selected_metrics": ["base_metrics"],
            },
        },
    )

    response = client.get("/api/storage/analysis-operations")
    incomplete = client.get(
        "/api/storage/analysis-operations",
        params={"incomplete_only": "true"},
    )

    assert created.status_code == 200
    assert response.status_code == 200
    operations = response.json()["operations"]
    assert len(operations) == 1
    assert operations[0]["run_id"] == created.json()["run_id"]
    assert operations[0]["status"] == "completed"
    assert operations[0]["stage"] == "completed"
    assert operations[0]["last_error_type"] == ""
    assert "content" not in operations[0]
    assert incomplete.status_code == 200
    assert incomplete.json() == {"operations": []}


def test_segmentation_operations_endpoint_reports_completed_and_incomplete(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    created = client.post(
        "/api/segmentation/runs",
        json={
            "source_filename": "PRIVATE-FILENAME-NEVER-JOURNAL.txt",
            "descript_text": "[00:00:00] P: PRIVATE-CONTENT-NEVER-JOURNAL.",
            "rule_ids": ["speaker-markers"],
        },
    )
    pending_id = SegmentationOperationStore(tmp_path).begin(
        run_id="pending_run",
        import_id="pending_import",
        operation_kind="create",
        previous_payload_sha256="",
        payload_sha256="a" * 64,
    )

    response = client.get("/api/storage/segmentation-operations")
    incomplete = client.get(
        "/api/storage/segmentation-operations",
        params={"incomplete_only": "true"},
    )
    limited = client.get(
        "/api/storage/segmentation-operations",
        params={"limit": 0},
    )
    oversized = client.get(
        "/api/storage/segmentation-operations",
        params={"limit": 999},
    )

    assert created.status_code == 200
    assert response.status_code == 200
    operations = response.json()["operations"]
    completed = next(
        operation
        for operation in operations
        if operation["run_id"] == created.json()["run"]["run_id"]
    )
    assert completed["operation_kind"] == "create"
    assert completed["status"] == "completed"
    assert completed["stage"] == "completed"
    assert completed["last_error_type"] == ""
    assert "descript_text" not in completed
    assert "source_filename" not in completed
    assert incomplete.status_code == 200
    assert [
        operation["operation_id"]
        for operation in incomplete.json()["operations"]
    ] == [pending_id]
    assert len(limited.json()["operations"]) == 1
    assert len(oversized.json()["operations"]) == 2
    assert "PRIVATE-FILENAME-NEVER-JOURNAL" not in response.text
    assert "PRIVATE-CONTENT-NEVER-JOURNAL" not in response.text
    with sqlite3.connect(tmp_path / "segmentation.sqlite3") as connection:
        journal_values = str(
            connection.execute("select * from segmentation_operations").fetchall()
        )
    assert "PRIVATE-FILENAME-NEVER-JOURNAL" not in journal_values
    assert "PRIVATE-CONTENT-NEVER-JOURNAL" not in journal_values


def test_segmentation_mutations_report_active_operation_conflict(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    created = client.post(
        "/api/segmentation/runs",
        json={
            "source_filename": "conflict.txt",
            "descript_text": "[00:00:00] P: Keep this version.",
            "rule_ids": ["speaker-markers"],
        },
    )
    run_id = created.json()["run"]["run_id"]
    operation_store = SegmentationOperationStore(tmp_path)
    create_operation = operation_store.list_operations()[0]
    operation_store.begin(
        run_id=run_id,
        import_id=created.json()["run"]["import_id"],
        operation_kind="patch",
        previous_payload_sha256=create_operation["payload_sha256"],
        payload_sha256="b" * 64,
    )

    verified = client.post(f"/api/segmentation/runs/{run_id}/verify")
    patched = client.post(
        f"/api/segmentation/runs/{run_id}/specialists/speaker_turn/patches",
        json={"patches": []},
    )

    assert created.status_code == 200
    assert verified.status_code == 409
    assert patched.status_code == 409
    assert "already running" in verified.json()["detail"]
    assert "already running" in patched.json()["detail"]


def test_segmentation_create_routes_report_source_integrity_conflict(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))

    def reject_blob(self, content, expected_sha256):
        raise SourceBlobIntegrityError("Stored source blob failed verification")

    monkeypatch.setattr(SourceBlobStore, "store", reject_blob)
    client = TestClient(app)

    created = client.post(
        "/api/segmentation/runs",
        json={
            "source_filename": "conflict.txt",
            "descript_text": "[00:00:00] P: Preserve integrity.",
            "rule_ids": ["speaker-markers"],
        },
    )
    uploaded = client.post(
        "/api/segmentation/runs/files",
        data={"rule_ids": '["speaker-markers"]'},
        files={
            "file": (
                "conflict.txt",
                b"[00:00:00] P: Preserve integrity.",
                "text/plain",
            )
        },
    )
    corpus = client.post("/api/segmentation/corpus-runs", json={"seed": 0})

    for response in (created, uploaded, corpus):
        assert response.status_code == 409
        assert "failed verification" in response.json()["detail"]


def test_segmentation_operations_endpoint_rejects_newer_schema(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    with sqlite3.connect(tmp_path / "segmentation.sqlite3") as connection:
        connection.execute("pragma user_version = 99")
    client = TestClient(app)

    response = client.get("/api/storage/segmentation-operations")
    mutation = client.post(
        "/api/segmentation/runs",
        json={
            "source_filename": "future.txt",
            "descript_text": "[00:00:00] P: Future schema.",
            "rule_ids": ["speaker-markers"],
        },
    )

    assert response.status_code == 409
    assert "newer than supported version 1" in response.json()["detail"]
    assert mutation.status_code == 409
    assert "newer than supported version 1" in mutation.json()["detail"]


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
    assert payload["import_id"].startswith("imp_")
    assert payload["project_source_id"].startswith("psrc_")
    assert payload["parent_transcript_revision_id"] == ""
    assert payload["workspace_id"] == "local-default"
    assert payload["source_blob_sha256"] == sha256(
        b"vr009_c: Um, hello there.\nvr009_p: Hello."
    ).hexdigest()
    assert payload["source_media_type"] == "text/plain"
    assert payload["source_id"].startswith("src_")
    assert len(payload["transcript_sha256"]) == 64
    assert payload["transcript_revision_id"].startswith("trv_")
    assert payload["evidence_set_id"].startswith("evs_")
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
    assert (tmp_path / "evidence.sqlite3").exists()

    imports_response = client.get("/api/evidence/imports")
    assert imports_response.status_code == 200
    assert imports_response.json()["imports"][0]["import_id"] == payload["import_id"]
    blob_response = client.get(
        f"/api/evidence/blobs/{payload['source_blob_sha256']}/verify"
    )
    assert blob_response.status_code == 200
    assert blob_response.json() == {
        "source_blob_sha256": payload["source_blob_sha256"],
        "verified": True,
        "size_bytes": len(b"vr009_c: Um, hello there.\nvr009_p: Hello."),
    }


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


def test_text_run_api_records_and_validates_revision_lineage(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    config = {"participant_id": "vr051", "selected_metrics": ["base_metrics"]}

    first_response = client.post(
        "/api/runs/text",
        json={
            "source_filename": "session.txt",
            "content": "vr051_c: First prompt.\nvr051_p: First response.",
            "config": config,
        },
    )
    first = first_response.json()
    second_response = client.post(
        "/api/runs/text",
        json={
            "source_filename": "session-revised.txt",
            "content": "vr051_c: Revised prompt.\nvr051_p: Revised response.",
            "config": config,
            "project_source_id": first["project_source_id"],
            "parent_transcript_revision_id": first["transcript_revision_id"],
        },
    )
    second = second_response.json()

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second["project_source_id"] == first["project_source_id"]
    assert (
        second["parent_transcript_revision_id"]
        == first["transcript_revision_id"]
    )
    assert second["transcript_revision_id"] != first["transcript_revision_id"]

    history_response = client.get(
        f"/api/evidence/sources/{first['project_source_id']}"
    )
    history = history_response.json()
    assert history_response.status_code == 200
    assert [item["transcript_revision_id"] for item in history["revisions"]] == [
        first["transcript_revision_id"],
        second["transcript_revision_id"],
    ]

    invalid_response = client.post(
        "/api/runs/text",
        json={
            "source_filename": "invalid-revision.txt",
            "content": "vr051_c: Invalid.\nvr051_p: Invalid.",
            "config": config,
            "project_source_id": first["project_source_id"],
            "parent_transcript_revision_id": "trv_missing",
        },
    )
    assert invalid_response.status_code == 400
    assert "Parent revision does not belong" in invalid_response.json()["detail"]

    rootless_response = client.post(
        "/api/runs/text",
        json={
            "source_filename": "rootless-revision.txt",
            "content": "vr051_c: Rootless.\nvr051_p: Rootless.",
            "config": config,
            "project_source_id": first["project_source_id"],
        },
    )
    assert rootless_response.status_code == 400
    assert "existing source requires a parent" in rootless_response.json()["detail"]
    assert len(client.get("/api/evidence/imports").json()["imports"]) == 2
    assert len(list((tmp_path / "runs").glob("*/results.json"))) == 2


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


def test_study_api_rejects_duplicate_identity_without_overwrite(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    original = client.post(
        "/api/studies",
        json={"name": "Same Study", "description": "keep me"},
    )
    duplicate = client.post(
        "/api/studies",
        json={"name": "Same Study", "description": "replace me"},
    )

    assert original.status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Study already exists"
    studies = client.get("/api/studies").json()["studies"]
    assert len(studies) == 1
    assert studies[0]["description"] == "keep me"


def test_study_batch_api_retries_exact_request_and_reports_conflicts(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Retry API Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "retry_api_pack",
            "name": "Retry API Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    client = TestClient(app)
    batch_id = "batch_20260729050505_aaaabbbb"
    request_payload = {
        "batch_id": batch_id,
        "skill_pack_version_id": version.version_id,
        "transcripts": [
            {
                "source_filename": "retry.txt",
                "content": "P1_c: Hello.\nP1_p: Hi.",
                "metadata": {"participant_id": "P1"},
            }
        ],
    }

    created = client.post(
        f"/api/studies/{study.id}/batches/text",
        json=request_payload,
    )
    replayed = client.post(
        f"/api/studies/{study.id}/batches/text",
        json=request_payload,
    )
    changed = client.post(
        f"/api/studies/{study.id}/batches/text",
        json={
            **request_payload,
            "transcripts": [
                {
                    **request_payload["transcripts"][0],
                    "content": "P1_c: Changed.\nP1_p: Changed.",
                }
            ],
        },
    )

    assert created.status_code == 200
    assert replayed.status_code == 200
    assert created.json()["batch"]["batch_id"] == batch_id
    assert replayed.json()["batch"] == created.json()["batch"]
    assert changed.status_code == 409
    assert "identity conflicts" in changed.json()["detail"]
    operation = StudyBatchOperationStore(tmp_path, study.id).get_operation(batch_id)
    assert operation["attempt_count"] == 1
    assert len(store.list_batches(study.id)) == 1
    assert len(
        [
            event
            for event in store.audit_log.list_events(limit=None)
            if event["event_type"] == "batch.completed"
        ]
    ) == 1

    aggregate_path = (
        tmp_path
        / "studies"
        / study.id
        / "batches"
        / batch_id
        / "aggregate_results.json"
    )
    aggregate_path.write_text("{}", encoding="utf-8")
    tampered = client.post(
        f"/api/studies/{study.id}/batches/text",
        json=request_payload,
    )
    assert tampered.status_code == 409
    assert "Completed study batch" in tampered.json()["detail"]


def test_study_skill_pack_version_api_is_idempotent_and_rejects_mutation(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    study_id = client.post(
        "/api/studies",
        json={"name": "Immutable Pack API Study"},
    ).json()["study"]["id"]
    payload = {
        "id": "immutable_api_pack",
        "name": "Immutable API Pack",
        "version": "1.0.0",
        "metrics": ["base_metrics"],
    }

    created = client.post(
        f"/api/studies/{study_id}/skill-pack-versions",
        json=payload,
    )
    repeated = client.post(
        f"/api/studies/{study_id}/skill-pack-versions",
        json=dict(payload),
    )
    conflicting = client.post(
        f"/api/studies/{study_id}/skill-pack-versions",
        json={**payload, "name": "Mutated API Pack"},
    )

    assert created.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json() == created.json()
    assert conflicting.status_code == 409
    assert "already exists with different content" in conflicting.json()["detail"]


def test_study_skill_pack_version_api_rejects_empty_derived_identifier(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    study_id = client.post(
        "/api/studies",
        json={"name": "Invalid Pack Identifier API Study"},
    ).json()["study"]["id"]

    response = client.post(
        f"/api/studies/{study_id}/skill-pack-versions",
        json={
            "id": "---",
            "name": "Invalid Identifier Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )

    assert response.status_code == 400
    assert "normalized identifier text" in response.json()["detail"]
    assert not (tmp_path / "studies" / study_id / "skill_packs").exists()


def test_study_batch_operation_api_is_bounded_and_content_safe(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Operation API Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "operation_api_pack",
            "name": "Operation API Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    store.run_text_batch(
        study.id,
        version.version_id,
        [
            {
                "source_filename": "PRIVATE-FILENAME-NEVER-JOURNAL.txt",
                "content": "PRIVATE-CONTENT-NEVER-JOURNAL",
                "metadata": {"note": "PRIVATE-METADATA-NEVER-JOURNAL"},
            }
        ],
        batch_id="batch_20260729060606_11112222",
    )
    StudyBatchOperationStore(tmp_path, study.id).begin(
        batch_id="batch_20260729060607_33334444",
        skill_pack_version_id=version.version_id,
        skill_pack_sha256="a" * 64,
        request_sha256="b" * 64,
        item_count=0,
        created_at="2026-07-29T06:06:07+00:00",
    )
    client = TestClient(app)

    response = client.get(f"/api/studies/{study.id}/batch-operations")
    incomplete = client.get(
        f"/api/studies/{study.id}/batch-operations",
        params={"incomplete_only": "true"},
    )
    limited = client.get(
        f"/api/studies/{study.id}/batch-operations",
        params={"limit": 0},
    )
    schema = client.get(
        f"/api/studies/{study.id}/batch-operations/schema-status"
    )

    assert response.status_code == 200
    assert len(response.json()["operations"]) == 2
    assert [
        operation["status"] for operation in incomplete.json()["operations"]
    ] == ["running"]
    assert len(limited.json()["operations"]) == 1
    assert "PRIVATE-FILENAME-NEVER-JOURNAL" not in response.text
    assert "PRIVATE-CONTENT-NEVER-JOURNAL" not in response.text
    assert "PRIVATE-METADATA-NEVER-JOURNAL" not in response.text
    assert schema.status_code == 200
    assert schema.json()["compatible"] is True
    assert schema.json()["study_id"] == study.id
    assert schema.json()["current_version"] == 3
    assert [item["name"] for item in schema.json()["migrations"]] == [
        "create-study-batch-operations",
        "add-study-batch-aggregate-hash",
        "repair-study-batch-aggregate-hashes",
    ]

    missing = client.get(
        "/api/studies/missing/batch-operations/schema-status"
    )
    invalid_list = client.get("/api/studies/INVALID/batch-operations")
    invalid_schema = client.get(
        "/api/studies/INVALID/batch-operations/schema-status"
    )
    with sqlite3.connect(
        tmp_path / "studies" / study.id / "batch_operations.sqlite3"
    ) as connection:
        connection.execute("pragma user_version = 99")
    newer = client.get(
        f"/api/studies/{study.id}/batch-operations/schema-status"
    )
    newer_batch_list = client.get(f"/api/studies/{study.id}/batches")
    newer_batch = client.get(
        f"/api/studies/{study.id}/batches/batch_20260729060606_11112222"
    )
    newer_backup = client.post(f"/api/studies/{study.id}/backup")
    assert missing.status_code == 404
    assert invalid_list.status_code == 400
    assert invalid_schema.status_code == 400
    assert "normalized study identifier" in invalid_list.json()["detail"]
    assert "normalized study identifier" in invalid_schema.json()["detail"]
    assert newer.status_code == 409
    assert newer_batch_list.status_code == 409
    assert newer_batch.status_code == 409
    assert "newer than supported version 3" in newer.json()["detail"]
    assert newer_backup.status_code == 409


def test_study_file_batch_api_accepts_explicit_retry_identity(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "File Retry API Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "file_retry_api_pack",
            "name": "File Retry API Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    client = TestClient(app)
    batch_id = "batch_20260729070707_55556666"

    response = client.post(
        f"/api/studies/{study.id}/batches/files",
        data={
            "skill_pack_version_id": version.version_id,
            "batch_id": batch_id,
        },
        files={"files": ("session.txt", b"P1_c: Hello.\nP1_p: Hi.", "text/plain")},
    )

    assert response.status_code == 200
    assert response.json()["batch"]["batch_id"] == batch_id
    assert StudyBatchOperationStore(tmp_path, study.id).get_operation(batch_id)[
        "status"
    ] == "completed"


def test_study_batch_api_declares_and_enforces_retry_identity_pattern(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    text_response = client.post(
        "/api/studies/any-study/batches/text",
        json={
            "skill_pack_version_id": "pack-1_0_0",
            "batch_id": "ordinary-client-key",
            "transcripts": [
                {"source_filename": "session.txt", "content": "CG: Hello."}
            ],
        },
    )
    file_response = client.post(
        "/api/studies/any-study/batches/files",
        data={
            "skill_pack_version_id": "pack-1_0_0",
            "batch_id": "ordinary-client-key",
        },
        files={"files": ("session.txt", b"CG: Hello.", "text/plain")},
    )
    openapi = client.get("/openapi.json").json()
    text_pattern = openapi["components"]["schemas"]["StudyTextBatchRequest"][
        "properties"
    ]["batch_id"]["anyOf"][0]["pattern"]
    multipart_schema_name = next(
        name
        for name in openapi["components"]["schemas"]
        if name.startswith("Body_create_study_file_batch")
    )
    file_pattern = openapi["components"]["schemas"][multipart_schema_name][
        "properties"
    ]["batch_id"]["anyOf"][0]["pattern"]

    assert text_response.status_code == 422
    assert file_response.status_code == 422
    assert text_pattern == r"^batch_[0-9]{14}_[0-9a-f]{8}$"
    assert file_pattern == text_pattern


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
    docx_bytes = docx_buffer.getvalue()

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
                    docx_bytes,
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
    imports = client.get("/api/evidence/imports").json()["imports"]
    docx_import = next(
        item for item in imports if item["source_filename"] == "P2_lab_week2.docx"
    )
    assert docx_import["source_blob_sha256"] == sha256(docx_bytes).hexdigest()
    verify_response = client.get(
        f"/api/evidence/blobs/{docx_import['source_blob_sha256']}/verify"
    )
    assert verify_response.json()["size_bytes"] == len(docx_bytes)


def test_study_backup_and_restore_api_round_trips_project(tmp_path, monkeypatch) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(source_root))
    client = TestClient(app)
    study_response = client.post("/api/studies", json={"name": "Backup API Study"})
    study_id = study_response.json()["study"]["id"]
    version_response = client.post(
        f"/api/studies/{study_id}/skill-pack-versions",
        json={
            "id": "backup_api_pack",
            "name": "Backup API Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    client.post(
        f"/api/studies/{study_id}/batches/text",
        json={
            "skill_pack_version_id": version_response.json()["version"][
                "version_id"
            ],
            "transcripts": [
                {
                    "source_filename": "session.txt",
                    "content": "P1_c: One.\nP1_p: Two.",
                }
            ],
        },
    )

    backup_response = client.post(f"/api/studies/{study_id}/backup")
    backup = backup_response.json()["backup"]
    archive_bytes = Path(backup["archive_path"]).read_bytes()

    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(restore_root))
    restore_response = client.post(
        "/api/studies/restore",
        files={"file": ("backup.nlpstudy.zip", archive_bytes, "application/zip")},
    )

    assert backup_response.status_code == 200
    assert len(backup["archive_sha256"]) == 64
    assert restore_response.status_code == 200
    assert restore_response.json()["restore"]["study_id"] == study_id
    assert restore_response.json()["restore"]["audit_event_count"] == 3
    assert client.get("/api/studies").json()["studies"][0]["id"] == study_id
    restored_import = client.get("/api/evidence/imports").json()["imports"][0]
    assert client.get(
        f"/api/evidence/blobs/{restored_import['source_blob_sha256']}/verify"
    ).status_code == 200
    conflict_response = client.post(
        "/api/studies/restore",
        files={"file": ("backup.nlpstudy.zip", archive_bytes, "application/zip")},
    )
    assert conflict_response.status_code == 409


def test_study_backup_api_reports_running_batch_conflict(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Backup Conflict Study"})
    StudyBatchOperationStore(tmp_path, study.id).begin(
        batch_id="batch_20260729090909_9999aaaa",
        skill_pack_version_id="backup_pack-1_0_0",
        skill_pack_sha256="a" * 64,
        request_sha256="b" * 64,
        item_count=0,
        created_at="2026-07-29T09:09:09+00:00",
    )
    client = TestClient(app)

    response = client.post(f"/api/studies/{study.id}/backup")

    assert response.status_code == 409
    assert "running batch" in response.json()["detail"]
    assert not list((tmp_path / "backups").glob("*.nlpstudy.zip"))


@pytest.mark.parametrize("blob_state", ["missing", "corrupt"])
def test_study_backup_api_reports_evidence_blob_integrity_conflict(
    tmp_path,
    monkeypatch,
    blob_state,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": f"{blob_state.title()} Blob Backup Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": f"{blob_state}_blob_backup_pack",
            "name": f"{blob_state.title()} Blob Backup Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    batch = store.run_text_batch(
        study.id,
        version.version_id,
        [{"source_filename": "session.txt", "content": "CG: One.\nP: Two."}],
    )
    item = StudyBatchOperationStore(tmp_path, study.id).list_items(batch.batch_id)[0]
    blob_path = SourceBlobStore(tmp_path).blob_path(item["source_blob_sha256"])
    if blob_state == "missing":
        blob_path.unlink()
    else:
        blob_path.write_bytes(b"corrupt source blob")

    response = TestClient(app).post(f"/api/studies/{study.id}/backup")

    assert response.status_code == 409
    assert "source blob" in response.json()["detail"]
    assert not list((tmp_path / "backups").glob("*.nlpstudy.zip"))


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
            "participant_count": 8,
            "conditions": ["home", "lab", "clinic"],
            "week_count": 3,
            "custom_fields": ["site", "arm"],
        },
    )
    read_response = client.get(f"/api/studies/{study_id}/schema")

    assert update_response.status_code == 200
    schema = update_response.json()["schema"]
    assert schema["study_id"] == study_id
    assert schema["participants"] == [
        "P1",
        "P2",
        "P3",
        "P4",
        "P5",
        "P6",
        "P7",
        "P8",
    ]
    assert schema["conditions"] == ["home", "lab", "clinic"]
    assert schema["weeks"] == ["week_1", "week_2", "week_3"]
    assert schema["custom_fields"] == ["site", "arm"]
    assert read_response.status_code == 200
    assert read_response.json()["schema"] == schema

    oversized_response = client.put(
        f"/api/studies/{study_id}/schema",
        json={"participant_count": 10_001},
    )
    assert oversized_response.status_code == 422


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


def test_pre_journal_study_batch_api_lists_loads_and_drills_down(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Legacy API Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "legacy_api_pack",
            "name": "Legacy API Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    batch = store.run_text_batch(
        study.id,
        version.version_id,
        [
            {
                "source_filename": "legacy.txt",
                "metadata": {"participant_id": "P1"},
                "content": "CG: Legacy prompt.\nP: Legacy response.",
            }
        ],
    )
    study_id = study.id
    batch_id = batch.batch_id
    study_dir = tmp_path / "studies" / study_id
    batch_dir = batch.aggregate_dir
    aggregate_payload = json.loads(
        (batch_dir / "aggregate_results.json").read_text(encoding="utf-8")
    )
    run_path = next((batch_dir / "runs").glob("*.json"))
    run_payload = json.loads(run_path.read_text(encoding="utf-8"))
    run_id = run_payload["run_id"]
    created_at = run_payload["created_at"]
    for field_name in (
        "import_id",
        "project_source_id",
        "parent_transcript_revision_id",
        "workspace_id",
        "source_blob_sha256",
        "source_media_type",
        "source_id",
        "transcript_sha256",
        "transcript_revision_id",
        "evidence_set_id",
    ):
        run_payload.pop(field_name)
    run_path.write_text(json.dumps(run_payload), encoding="utf-8")
    journal_path = study_dir / "batch_operations.sqlite3"
    journal_path.unlink()
    assert not journal_path.exists()
    client = TestClient(app)

    list_response = client.get(f"/api/studies/{study_id}/batches")
    detail_response = client.get(f"/api/studies/{study_id}/batches/{batch_id}")
    runs_response = client.get(f"/api/studies/{study_id}/batches/{batch_id}/runs")
    run_response = client.get(
        f"/api/studies/{study_id}/batches/{batch_id}/runs/{run_id}"
    )

    assert list_response.status_code == 200
    assert [batch["batch_id"] for batch in list_response.json()["batches"]] == [
        batch_id
    ]
    assert detail_response.status_code == 200
    assert detail_response.json()["batch"]["batch_id"] == batch_id
    assert detail_response.json()["results"] == aggregate_payload["results"]
    assert runs_response.status_code == 200
    assert runs_response.json()["runs"] == [
        {
            "run_id": run_id,
            "import_id": "",
            "project_source_id": "",
            "parent_transcript_revision_id": "",
            "workspace_id": "",
            "source_blob_sha256": "",
            "source_media_type": "",
            "source_id": "",
            "transcript_sha256": "",
            "transcript_revision_id": "",
            "evidence_set_id": "",
            "source_filename": "legacy.txt",
            "metadata": {"participant_id": "P1"},
            "created_at": created_at,
            "turn_count": run_payload["turn_count"],
            "metric_ids": ["base_metrics"],
        }
    ]
    assert run_response.status_code == 200
    assert run_response.json()["run"] == run_payload


def test_incomplete_journal_batches_are_hidden_and_direct_reads_conflict(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Incomplete Batch Visibility Study"})
    journal = StudyBatchOperationStore(tmp_path, study.id)
    batch_ids = {
        "running": "batch_20260728020202_aaaabbbb",
        "failed": "batch_20260728020203_ccccdddd",
    }
    for status, batch_id in batch_ids.items():
        journal.begin(
            batch_id=batch_id,
            skill_pack_version_id="incomplete_pack-1_0_0",
            skill_pack_sha256="a" * 64,
            request_sha256=("b" if status == "running" else "c") * 64,
            item_count=0,
            created_at="2026-07-28T02:02:02+00:00",
        )
        if status == "failed":
            journal.fail(batch_id, error_type="RuntimeError")
        batch_dir = tmp_path / "studies" / study.id / "batches" / batch_id
        batch_dir.mkdir(parents=True)
        (batch_dir / "batch.json").write_text(
            json.dumps(
                {
                    "study_id": study.id,
                    "batch_id": batch_id,
                    "skill_pack_version_id": "incomplete_pack-1_0_0",
                    "run_count": 0,
                    "failure_count": 0,
                    "aggregate_dir": str(batch_dir),
                    "created_at": "2026-07-28T02:02:02+00:00",
                }
            ),
            encoding="utf-8",
        )
    client = TestClient(app)

    list_response = client.get(f"/api/studies/{study.id}/batches")

    assert list_response.status_code == 200
    assert list_response.json()["batches"] == []
    for batch_id in batch_ids.values():
        responses = [
            client.get(f"/api/studies/{study.id}/batches/{batch_id}"),
            client.get(f"/api/studies/{study.id}/batches/{batch_id}/runs"),
            client.get(
                f"/api/studies/{study.id}/batches/{batch_id}/runs/blocked_run"
            ),
        ]
        assert [response.status_code for response in responses] == [409, 409, 409]
        assert all(
            "has not reached the completed boundary" in response.json()["detail"]
            for response in responses
        )


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
    assert list_response.json()["runs"][0]["source_id"] == loaded["source_id"]
    assert (
        list_response.json()["runs"][0]["transcript_revision_id"]
        == loaded["transcript_revision_id"]
    )
    assert {
        key: value
        for key, value in loaded["turns"][0].items()
        if key != "passage_id"
    } == {
        "turn_index": 0,
        "role": "caregiver",
        "speaker_label": "Caregiver",
        "raw_prefix": "P1_c",
        "text": "Hello?",
    }
    assert loaded["turns"][0]["passage_id"].startswith("psg_")
    assert loaded["results"][0]["metric_id"] == "base_metrics"


def test_completed_study_batch_reader_apis_report_missing_manifest_conflict(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Missing Manifest API Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "missing_manifest_api_pack",
            "name": "Missing Manifest API Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    batch = store.run_text_batch(
        study.id,
        version.version_id,
        [{"source_filename": "one.txt", "content": "CG: Hello.\nP: Hi."}],
    )
    run_id = store.list_batch_runs(study.id, batch.batch_id)[0]["run_id"]
    (batch.aggregate_dir / "batch.json").unlink()
    client = TestClient(app)

    responses = [
        client.get(f"/api/studies/{study.id}/batches"),
        client.get(f"/api/studies/{study.id}/batches/{batch.batch_id}"),
        client.get(f"/api/studies/{study.id}/batches/{batch.batch_id}/runs"),
        client.get(
            f"/api/studies/{study.id}/batches/{batch.batch_id}/runs/{run_id}"
        ),
    ]

    assert [response.status_code for response in responses] == [409, 409, 409, 409]
    assert all(
        "missing its manifest" in response.json()["detail"]
        for response in responses
    )


def test_completed_study_batch_reader_apis_report_corrupt_blob_conflict(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Corrupt Blob API Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "corrupt_blob_api_pack",
            "name": "Corrupt Blob API Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    batch = store.run_text_batch(
        study.id,
        version.version_id,
        [{"source_filename": "one.txt", "content": "CG: Hello.\nP: Hi."}],
    )
    item = StudyBatchOperationStore(tmp_path, study.id).list_items(
        batch.batch_id
    )[0]
    SourceBlobStore(tmp_path).blob_path(item["source_blob_sha256"]).write_bytes(
        b"corrupt"
    )
    client = TestClient(app)

    responses = [
        client.get(f"/api/studies/{study.id}/batches/{batch.batch_id}"),
        client.get(f"/api/studies/{study.id}/batches/{batch.batch_id}/runs"),
        client.get(
            f"/api/studies/{study.id}/batches/{batch.batch_id}/runs/{item['run_id']}"
        ),
    ]

    assert [response.status_code for response in responses] == [409, 409, 409]
    assert all(
        "source blob conflicts" in response.json()["detail"]
        for response in responses
    )


@pytest.mark.parametrize("dependency_kind", ["blob", "audit", "evidence"])
def test_completed_study_batch_reader_apis_report_dependency_io_conflict(
    tmp_path,
    monkeypatch,
    dependency_kind: str,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Dependency IO API Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "dependency_io_api_pack",
            "name": "Dependency IO API Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    batch = store.run_text_batch(
        study.id,
        version.version_id,
        [{"source_filename": "one.txt", "content": "CG: Hello.\nP: Hi."}],
    )
    item = StudyBatchOperationStore(tmp_path, study.id).list_items(
        batch.batch_id
    )[0]
    if dependency_kind == "blob":
        dependency_path = SourceBlobStore(tmp_path).blob_path(
            item["source_blob_sha256"]
        )
    elif dependency_kind == "audit":
        dependency_path = tmp_path / "audit" / "events.jsonl"
    else:
        dependency_path = tmp_path / "evidence.sqlite3"
    dependency_path.unlink()
    dependency_path.mkdir()
    client = TestClient(app)

    responses = [
        client.get(f"/api/studies/{study.id}/batches/{batch.batch_id}"),
        client.get(f"/api/studies/{study.id}/batches/{batch.batch_id}/runs"),
        client.get(
            f"/api/studies/{study.id}/batches/{batch.batch_id}/runs/{item['run_id']}"
        ),
    ]

    assert [response.status_code for response in responses] == [409, 409, 409]
    assert all(
        "completed study batch" in response.json()["detail"].lower()
        for response in responses
    )


@pytest.mark.parametrize(
    "journal_state",
    ["directory", "corrupt", "symlink", "missing-table"],
)
def test_study_batch_apis_report_invalid_journal_conflict(
    tmp_path,
    monkeypatch,
    journal_state: str,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Invalid Journal API Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "invalid_journal_api_pack",
            "name": "Invalid Journal API Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    batch = store.run_text_batch(
        study.id,
        version.version_id,
        [{"source_filename": "one.txt", "content": "CG: Hello.\nP: Hi."}],
    )
    run_id = store.list_batch_runs(study.id, batch.batch_id)[0]["run_id"]
    journal_path = tmp_path / "studies" / study.id / "batch_operations.sqlite3"
    if journal_state == "missing-table":
        with sqlite3.connect(journal_path) as connection:
            connection.execute("drop table study_batch_operation_items")
    else:
        journal_path.unlink()
    if journal_state == "directory":
        journal_path.mkdir()
    elif journal_state == "corrupt":
        journal_path.write_bytes(b"not a sqlite database")
    elif journal_state == "symlink":
        target = tmp_path / "journal-target.sqlite3"
        target.write_bytes(b"not a sqlite database")
        journal_path.symlink_to(target)
    client = TestClient(app)

    responses = [
        client.get(f"/api/studies/{study.id}/batch-operations/schema-status"),
        client.get(f"/api/studies/{study.id}/batch-operations"),
        client.get(f"/api/studies/{study.id}/batches"),
        client.get(f"/api/studies/{study.id}/batches/{batch.batch_id}"),
        client.get(f"/api/studies/{study.id}/batches/{batch.batch_id}/runs"),
        client.get(
            f"/api/studies/{study.id}/batches/{batch.batch_id}/runs/{run_id}"
        ),
    ]

    assert [response.status_code for response in responses] == [409] * 6
    assert all(
        "journal is invalid" in response.json()["detail"].lower()
        for response in responses
    )


def test_study_batch_apis_reject_hash_aligned_incomplete_current_evidence(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Incomplete Current Evidence API Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "incomplete_current_evidence_api_pack",
            "name": "Incomplete Current Evidence API Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    batch_id = "batch_20260731151515_aabbccdd"
    transcript = {
        "source_filename": "one.txt",
        "content": "CG: Hello.\nP: Hi.",
        "metadata": {},
        "project_source_id": "",
        "parent_transcript_revision_id": "",
    }
    batch = store.run_text_batch(
        study.id,
        version.version_id,
        [transcript],
        batch_id=batch_id,
    )
    journal = StudyBatchOperationStore(tmp_path, study.id)
    item = journal.list_items(batch_id)[0]
    run_path = batch.aggregate_dir / "runs" / f"{item['run_id']}.json"
    run_payload = json.loads(run_path.read_text(encoding="utf-8"))
    run_payload.pop("workspace_id")
    run_path.write_text(json.dumps(run_payload), encoding="utf-8")
    aligned_hash = sha256(
        json.dumps(
            run_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    with sqlite3.connect(journal.db_path) as connection:
        connection.execute(
            """
            update study_batch_operation_items
            set run_payload_sha256 = ?
            where batch_id = ? and item_index = ?
            """,
            (aligned_hash, batch_id, item["item_index"]),
        )
    client = TestClient(app)

    responses = [
        client.get(f"/api/studies/{study.id}/batches/{batch_id}"),
        client.get(f"/api/studies/{study.id}/batches/{batch_id}/runs"),
        client.get(
            f"/api/studies/{study.id}/batches/{batch_id}/runs/{item['run_id']}"
        ),
        client.post(
            f"/api/studies/{study.id}/batches/text",
            json={
                "skill_pack_version_id": version.version_id,
                "batch_id": batch_id,
                "transcripts": [transcript],
            },
        ),
    ]

    assert [response.status_code for response in responses] == [409] * 4
    assert all(
        "completed study batch" in response.json()["detail"].lower()
        for response in responses
    )


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
    from backend.llm import openrouter

    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(openrouter, "_DOTENV_LOADED", True)
    client = TestClient(app)

    response = client.get("/api/deployment-profile/secure-offline")

    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert response.json()["checks"][1]["id"] == "network_llm_disabled"


def test_segmentation_api_lists_and_returns_synthetic_cases() -> None:
    client = TestClient(app)

    list_response = client.get("/api/segmentation/cases")
    case_response = client.get("/api/segmentation/cases/pause_overlap_repair")

    assert list_response.status_code == 200
    cases = list_response.json()["cases"]
    assert [case["case_id"] for case in cases] == [
        "pause_overlap_repair",
        "redaction_omission_nonverbal",
    ]
    assert cases[0]["source"] == "synthetic"
    assert cases[0]["forbidden_source_tokens"] == []

    assert case_response.status_code == 200
    payload = case_response.json()["case"]
    assert payload["case_id"] == "pause_overlap_repair"
    assert "[00:00:00]" in payload["descript_text"]
    assert "([FP])" in payload["gold_text"]


def test_segmentation_api_evaluates_draft_against_synthetic_rules() -> None:
    client = TestClient(app)
    case = client.get("/api/segmentation/cases/redaction_omission_nonverbal").json()[
        "case"
    ]

    good_response = client.post(
        "/api/segmentation/evaluate",
        json={
            "case_id": "redaction_omission_nonverbal",
            "draft_text": case["gold_text"],
        },
    )
    bad_response = client.post(
        "/api/segmentation/evaluate",
        json={
            "case_id": "redaction_omission_nonverbal",
            "draft_text": "P: I saw Nala [redacted]",
        },
    )

    assert good_response.status_code == 200
    good_evaluation = good_response.json()["evaluation"]
    assert good_evaluation["passed_rule_count"] == good_evaluation[
        "configured_rule_count"
    ]
    assert "score" not in good_evaluation
    assert good_evaluation["failures"] == []

    assert bad_response.status_code == 200
    failures = {
        failure["rule_id"]
        for failure in bad_response.json()["evaluation"]["failures"]
    }
    assert failures >= {"redaction-comments", "official-source-guard"}


def test_segmentation_api_rejects_unknown_synthetic_case() -> None:
    client = TestClient(app)

    response = client.get("/api/segmentation/cases/not-real")
    evaluate_response = client.post(
        "/api/segmentation/evaluate",
        json={"case_id": "not-real", "draft_text": "P: Hello."},
    )

    assert response.status_code == 404
    assert evaluate_response.status_code == 404


def test_study_segmentation_api_scopes_runs_and_guards_mutations(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    local_run = client.post(
        "/api/segmentation/runs",
        json={
            "source_filename": "local.txt",
            "descript_text": "[00:00:00] P: Local-only evidence.",
            "rule_ids": ["speaker-markers"],
        },
    )
    owned_study = client.post(
        "/api/studies",
        json={"name": "Owned Segmentation API"},
    ).json()["study"]["id"]
    foreign_study = client.post(
        "/api/studies",
        json={"name": "Foreign Segmentation API"},
    ).json()["study"]["id"]
    owned = client.post(
        f"/api/studies/{owned_study}/segmentation/runs",
        json={
            "source_filename": "owned.txt",
            "descript_text": "[00:00:00] P: Study-owned evidence.",
            "rule_ids": ["speaker-markers"],
            "workspace_id": foreign_study,
        },
    )
    foreign = client.post(
        f"/api/studies/{foreign_study}/segmentation/runs",
        json={
            "source_filename": "foreign.txt",
            "descript_text": "[00:00:00] P: Foreign evidence.",
            "rule_ids": ["speaker-markers"],
        },
    )

    assert local_run.status_code == 200
    assert owned.status_code == 200
    assert foreign.status_code == 200
    owned_run = owned.json()["run"]
    foreign_run = foreign.json()["run"]
    assert owned_run["workspace_id"] == owned_study
    assert foreign_run["workspace_id"] == foreign_study
    assert owned_run["evidence_set_id"].startswith("evs_")
    assert owned_run["cunit_adjudication"]["cunit_text_contract_version"] == 1

    owned_list = client.get(
        f"/api/studies/{owned_study}/segmentation/runs"
    )
    foreign_list = client.get(
        f"/api/studies/{foreign_study}/segmentation/runs"
    )
    assert owned_list.status_code == 200
    assert foreign_list.status_code == 200
    assert [run["run_id"] for run in owned_list.json()["runs"]] == [
        owned_run["run_id"]
    ]
    assert [run["run_id"] for run in foreign_list.json()["runs"]] == [
        foreign_run["run_id"]
    ]
    assert local_run.json()["run"]["run_id"] not in {
        run["run_id"] for run in owned_list.json()["runs"]
    }

    owned_url = (
        f"/api/studies/{owned_study}/segmentation/runs/"
        f"{owned_run['run_id']}"
    )
    fetched = client.get(owned_url)
    verified = client.post(f"{owned_url}/verify")
    patched = client.post(
        f"{owned_url}/specialists/speaker_turn/patches",
        json={"patches": []},
    )
    assert fetched.status_code == 200
    assert verified.status_code == 200
    assert patched.status_code == 200
    assert fetched.json()["run"]["workspace_id"] == owned_study
    assert verified.json()["run"]["workspace_id"] == owned_study
    assert patched.json()["run"]["workspace_id"] == owned_study

    snapshot_path = (
        tmp_path / "segmentation_runs" / f"{owned_run['run_id']}.json"
    )
    snapshot_before = snapshot_path.read_bytes()
    operations_before = SegmentationOperationStore(tmp_path).list_operations()
    wrong_base = (
        f"/api/studies/{foreign_study}/segmentation/runs/"
        f"{owned_run['run_id']}"
    )
    wrong_responses = [
        client.get(wrong_base),
        client.post(f"{wrong_base}/verify"),
        client.post(
            f"{wrong_base}/specialists/speaker_turn/patches",
            json={"patches": []},
        ),
    ]
    for response in wrong_responses:
        assert response.status_code == 409
        assert response.json() == {
            "detail": "Segmentation state conflicts with stored data"
        }
    assert snapshot_path.read_bytes() == snapshot_before
    assert SegmentationOperationStore(tmp_path).list_operations() == (
        operations_before
    )

    validation_sentinel = "PRIVATE-PATCH /private/patch/path"
    coercive_patch = client.post(
        f"{owned_url}/specialists/speaker_turn/patches",
        json={
            "patches": [
                {
                    "operation": "replace_event_text",
                    "event_index": True,
                    "text": validation_sentinel,
                }
            ]
        },
    )
    missing_run = client.get(
        f"/api/studies/{owned_study}/segmentation/runs/{'0' * 32}"
    )
    run_count_before = len(
        SegmentationRunStore(tmp_path).list_runs()
    )
    missing_study = client.post(
        "/api/studies/not-a-study/segmentation/runs",
        json={
            "source_filename": "missing.txt",
            "descript_text": "[00:00:00] P: Must not be persisted.",
            "rule_ids": ["speaker-markers"],
        },
    )
    assert coercive_patch.status_code == 422
    assert coercive_patch.json() == {"detail": "Request validation failed"}
    assert validation_sentinel not in coercive_patch.text
    assert missing_run.status_code == 404
    assert missing_run.json() == {"detail": "Segmentation run not found"}
    assert missing_study.status_code == 404
    assert missing_study.json() == {"detail": "Study not found"}
    assert len(SegmentationRunStore(tmp_path).list_runs()) == run_count_before
    assert snapshot_path.read_bytes() == snapshot_before
    assert SegmentationOperationStore(tmp_path).list_operations() == (
        operations_before
    )

    private_detail = "PRIVATE-STUDY-CONTENT /private/study/path"
    study_record_path = tmp_path / "studies" / foreign_study / "study.json"
    study_record_path.write_text(private_detail, encoding="utf-8")
    corrupt_study = client.get(
        f"/api/studies/{foreign_study}/segmentation/runs"
    )
    assert corrupt_study.status_code == 409
    assert corrupt_study.json() == {
        "detail": "Study storage is unavailable or invalid"
    }
    assert private_detail not in corrupt_study.text


def test_segmentation_run_api_creates_fetches_and_verifies_rule_specialist_run(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/api/segmentation/runs",
        json={
            "source_filename": "session.txt",
            "descript_text": "[00:00:00] P: Good morning.\n[00:00:03] Av: Uh yes.",
            "rule_ids": [
                "speaker-markers",
                "timestamp-markers",
                "pause-markers",
                "filled-pauses",
            ],
        },
    )

    assert response.status_code == 200
    run = response.json()["run"]
    assert run["source"] == "researcher_provided"
    assert run["import_id"].startswith("imp_")
    assert run["project_source_id"].startswith("psrc_")
    assert run["parent_transcript_revision_id"] == ""
    assert run["workspace_id"] == "local-default"
    assert len(run["source_blob_sha256"]) == 64
    assert run["source_media_type"] == "text/plain"
    assert run["source_id"].startswith("src_")
    assert len(run["transcript_sha256"]) == 64
    assert run["transcript_revision_id"].startswith("trv_")
    assert run["events"][0]["passage_id"].startswith("psg_")
    assert run["cunit_adjudication"]["decisions"][0]["cunit_ids"]
    assert run["merged_draft"].startswith(
        "Researcher-provided transcript: session"
    )
    assert run["status"] == "verified"
    assert run["rule_plan"][0]["specialist_id"] == "speaker_turn"
    assert run["specialist_outputs"][0]["patches"]
    assert run["specialist_outputs"][0]["evidence"]["artifact_path"].endswith(
        "specialists/speaker_turn.html"
    )

    fetch_response = client.get(f"/api/segmentation/runs/{run['run_id']}")

    assert fetch_response.status_code == 200
    assert fetch_response.json()["run"]["run_id"] == run["run_id"]
    assert fetch_response.json()["run"]["source"] == "researcher_provided"

    verify_response = client.post(f"/api/segmentation/runs/{run['run_id']}/verify")

    assert verify_response.status_code == 200
    assert verify_response.json()["run"]["run_id"] == run["run_id"]
    assert verify_response.json()["run"]["source"] == "researcher_provided"
    evaluation = verify_response.json()["run"]["evaluation"]
    assert evaluation["passed_rule_count"] == evaluation["configured_rule_count"]


def test_segmentation_run_api_accepts_uploaded_txt_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    source_bytes = b"[00:00:00] P: Good morning.\n[00:00:03] Av: Uh yes.\n"
    response = client.post(
        "/api/segmentation/runs/files",
        data={
            "rule_ids": json.dumps(
                [
                    "speaker-markers",
                    "timestamp-markers",
                    "pause-markers",
                    "filled-pauses",
                ]
            )
        },
        files={
            "file": (
                "descript_export.txt",
                source_bytes,
                "text/plain",
            )
        },
    )

    assert response.status_code == 200
    run = response.json()["run"]
    assert run["source_filename"] == "descript_export.txt"
    assert run["source"] == "researcher_provided"
    assert run["source_blob_sha256"] == sha256(source_bytes).hexdigest()
    assert run["source_media_type"] == "text/plain"
    assert run["merged_draft"].startswith(
        "Researcher-provided transcript: descript_export"
    )
    assert run["status"] == "verified"
    assert run["events"][0]["source_filename"] == "descript_export.txt"
    blob_response = client.get(
        f"/api/evidence/blobs/{run['source_blob_sha256']}/verify"
    )
    assert blob_response.status_code == 200
    assert blob_response.json()["size_bytes"] == len(source_bytes)


def test_segmentation_run_file_api_rejects_non_txt_upload(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/api/segmentation/runs/files",
        data={"rule_ids": json.dumps(["speaker-markers"])},
        files={
            "file": (
                "descript_export.docx",
                b"not really a docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Only TXT segmentation uploads are supported"


def test_segmentation_run_api_rejects_invalid_input(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    empty_response = client.post(
        "/api/segmentation/runs",
        json={
            "source_filename": "empty.txt",
            "descript_text": "   ",
            "rule_ids": ["speaker-markers"],
        },
    )
    unknown_rule_response = client.post(
        "/api/segmentation/runs",
        json={
            "source_filename": "session.txt",
            "descript_text": "[00:00:00] P: Good morning.",
            "rule_ids": ["not-a-rule"],
        },
    )
    unknown_source_response = client.post(
        "/api/segmentation/runs",
        json={
            "source_filename": "session.txt",
            "descript_text": "[00:00:00] P: Good morning.",
            "rule_ids": ["speaker-markers"],
            "source": "external",
        },
    )

    assert empty_response.status_code == 400
    assert "descript_text" in empty_response.json()["detail"]
    assert unknown_rule_response.status_code == 400
    assert "not-a-rule" in unknown_rule_response.json()["detail"]
    assert unknown_source_response.status_code == 422


def test_segmentation_rulebook_api_exposes_coverage_and_limits() -> None:
    client = TestClient(app)

    response = client.get("/api/segmentation/rulebook")

    assert response.status_code == 200
    payload = response.json()["rulebook"]
    assert payload["implemented_rule_count"] == 10
    assert payload["tracked_fixture_rule_count"] == 9
    assert payload["generated_fixture_rule_count"] == 10
    assert payload["validation"]["status"] == "not_domain_validated"
    assert "not accuracy" in payload["validation"]["claim_boundary"]
    assert payload["rule_definitions"][0]["rule_id"] == "speaker-markers"
    assert any(
        area["area_id"] == "cunit-boundaries"
        and area["status"] == "implemented-unvalidated"
        for area in payload["method_areas"]
    )


def test_segmentation_run_rewrite_job_uses_failed_rule_routing(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    create_response = client.post(
        "/api/segmentation/runs",
        json={
            "source_filename": "needs_rewrite.txt",
            "descript_text": "[00:00:00] P: Good morning.",
            "rule_ids": ["speaker-markers", "overlap-markers"],
        },
    )
    run = create_response.json()["run"]

    assert run["status"] == "needs_rewrite"
    assert run["cunit_adjudication"]["counted_cunit_count"] == 1
    assert run["cunit_adjudication"]["decisions"][0]["boundary_type"]
    assert run["failure_routes"] == [
        {
            "rule_id": "overlap-markers",
            "specialist_id": "repair_overlap",
            "message": "Expected overlapping speech to be marked with angle brackets.",
        }
    ]

    rewrite_response = client.post(
        f"/api/segmentation/runs/{run['run_id']}/rewrite-job"
    )

    assert rewrite_response.status_code == 200
    payload = rewrite_response.json()
    assert payload["job"]["id"] == f"rewrite_{run['run_id']}"
    assert payload["job"]["source_request_id"] == run["run_id"]
    prompt_path = tmp_path / "agent_jobs" / payload["job"]["id"] / "rewrite_prompt.html"
    prompt = prompt_path.read_text(encoding="utf-8")
    assert "overlap-markers" in prompt
    assert "repair_overlap" in prompt


def test_segmentation_run_api_lists_runs_and_downloads_exports(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    create_response = client.post(
        "/api/segmentation/runs",
        json={
            "source_filename": "export_me.txt",
            "descript_text": "[00:00:00] P: Good morning.\n[00:00:03] Av: Uh yes.",
            "rule_ids": [
                "speaker-markers",
                "timestamp-markers",
                "pause-markers",
                "filled-pauses",
            ],
        },
    )
    run = create_response.json()["run"]

    list_response = client.get("/api/segmentation/runs")
    transcript_response = client.get(
        f"/api/segmentation/runs/{run['run_id']}/exports/final_transcript.txt"
    )
    evidence_response = client.get(
        f"/api/segmentation/runs/{run['run_id']}/exports/evidence.json"
    )
    specialist_response = client.get(
        f"/api/segmentation/runs/{run['run_id']}/specialists/speaker_turn.html"
    )

    assert list_response.status_code == 200
    assert list_response.json()["runs"][0]["run_id"] == run["run_id"]
    assert transcript_response.status_code == 200
    assert transcript_response.text.startswith(
        "Researcher-provided transcript: export_me"
    )
    assert "P: Good morning." in transcript_response.text
    assert evidence_response.status_code == 200
    assert evidence_response.json()["source"] == "researcher_provided"
    evaluation = evidence_response.json()["evaluation"]
    assert evaluation["passed_rule_count"] == evaluation["configured_rule_count"]
    assert "score" not in evaluation
    assert specialist_response.status_code == 200
    assert "Do not rewrite the full transcript" in specialist_response.text


def test_segmentation_corpus_run_api_creates_and_lists_regression_batch(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    create_response = client.post(
        "/api/segmentation/corpus-runs",
        json={"seed": 19},
    )
    list_response = client.get("/api/segmentation/corpus-runs")

    assert create_response.status_code == 200
    corpus_run = create_response.json()["corpus_run"]
    assert corpus_run["status"] == "passed"
    assert corpus_run["seed"] == 19
    assert corpus_run["total_case_count"] == 4
    assert corpus_run["regression_fail_count"] == 0
    synthetic_run = client.get(
        f"/api/segmentation/runs/{corpus_run['results'][0]['run_id']}"
    ).json()["run"]
    assert synthetic_run["source"] == "synthetic"
    assert synthetic_run["merged_draft"].startswith("Synthetic run:")
    assert any(
        result["expected_status"] == "failed"
        and result["failed_rule_ids"] == ["official-source-guard"]
        for result in corpus_run["results"]
    )
    assert list_response.status_code == 200
    assert list_response.json()["corpus_runs"][0]["corpus_run_id"] == corpus_run[
        "corpus_run_id"
    ]


def test_segmentation_run_analysis_api_uses_verified_merged_transcript(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    create_response = client.post(
        "/api/segmentation/runs",
        json={
            "source_filename": "analysis_me.txt",
            "descript_text": "[00:00:00] P: Good morning.\n[00:00:03] Av: Uh yes.",
            "rule_ids": [
                "speaker-markers",
                "timestamp-markers",
                "pause-markers",
                "filled-pauses",
            ],
        },
    )
    run = create_response.json()["run"]

    analysis_response = client.post(
        f"/api/segmentation/runs/{run['run_id']}/analysis",
        json={},
    )

    assert analysis_response.status_code == 200
    payload = analysis_response.json()
    assert payload["source_filename"] == "analysis_me_segmented.txt"
    assert payload["turn_count"] == 2
    assert [result["metric_id"] for result in payload["results"]] == [
        "base_metrics",
        "lexical_metrics",
        "disfluency_metrics",
    ]
    assert payload["results"][0]["rows"][1]["speaker"] == "participant"
    assert (tmp_path / "runs" / payload["run_id"] / "results.json").exists()


def test_segmentation_run_analysis_api_honors_metric_config(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    create_response = client.post(
        "/api/segmentation/runs",
        json={
            "source_filename": "configured_analysis.txt",
            "descript_text": "[00:00:00] P: Good morning.\n[00:00:03] Av: Uh yes.",
            "rule_ids": [
                "speaker-markers",
                "timestamp-markers",
                "pause-markers",
                "filled-pauses",
            ],
        },
    )
    run = create_response.json()["run"]

    analysis_response = client.post(
        f"/api/segmentation/runs/{run['run_id']}/analysis",
        json={
            "config": {
                "selected_metrics": ["base_metrics"],
                "disfluency_tokens": ["yes"],
            }
        },
    )

    assert analysis_response.status_code == 200
    payload = analysis_response.json()
    assert [result["metric_id"] for result in payload["results"]] == ["base_metrics"]
    assert payload["results"][0]["rows"][2]["turns"] == 2


def test_segmentation_run_analysis_api_rejects_unverified_runs(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    create_response = client.post(
        "/api/segmentation/runs",
        json={
            "source_filename": "leak.txt",
            "descript_text": "[00:00:00] P: Nala should not appear here.",
            "rule_ids": [
                "speaker-markers",
                "timestamp-markers",
                "official-source-guard",
            ],
        },
    )
    run = create_response.json()["run"]

    analysis_response = client.post(
        f"/api/segmentation/runs/{run['run_id']}/analysis",
        json={},
    )

    assert analysis_response.status_code == 400
    assert analysis_response.json()["detail"] == (
        "Segmentation run must be verified before analysis"
    )


def test_segmentation_run_api_accepts_specialist_patch_submission(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    create_response = client.post(
        "/api/segmentation/runs",
        json={
            "source_filename": "patch_me.txt",
            "descript_text": "[00:00:00] P: Good morning.\n[00:00:03] Av: Uh yes.",
            "rule_ids": [
                "speaker-markers",
                "timestamp-markers",
                "pause-markers",
                "filled-pauses",
            ],
        },
    )
    run = create_response.json()["run"]

    patch_response = client.post(
        f"/api/segmentation/runs/{run['run_id']}/specialists/timing_pause/patches",
        json={
            "patches": [
                {
                    "operation": "insert_before_event",
                    "event_index": 0,
                    "text": "-0:00",
                    "reason": "submitted by timing/pause agent",
                }
            ]
        },
    )

    assert patch_response.status_code == 200
    updated = patch_response.json()["run"]
    assert updated["status"] == "needs_rewrite"
    assert updated["failure_routes"][0]["specialist_id"] == "timing_pause"
    assert "; :03" not in updated["merged_draft"]


def test_segmentation_run_api_rejects_invalid_specialist_patch_submission(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    create_response = client.post(
        "/api/segmentation/runs",
        json={
            "source_filename": "patch_me.txt",
            "descript_text": "[00:00:00] P: Good morning.",
            "rule_ids": ["speaker-markers", "timestamp-markers"],
        },
    )
    run = create_response.json()["run"]

    patch_response = client.post(
        f"/api/segmentation/runs/{run['run_id']}/specialists/timing_pause/patches",
        json={
            "patches": [
                {
                    "operation": "insert_before_event",
                    "event_index": 99,
                    "text": "-0:00",
                    "reason": "bad event index",
                }
            ]
        },
    )

    assert patch_response.status_code == 400
    assert patch_response.json()["detail"] == "Patch event_index out of range"
