from fastapi.testclient import TestClient

from backend.app.main import app
from backend.professor_demo.service import ProfessorDemoService
from tests.test_professor_demo import FakeLunaClient, TRANSCRIPT


def test_professor_demo_api_run_accept_and_revert(tmp_path, monkeypatch) -> None:
    service = ProfessorDemoService(tmp_path, client=FakeLunaClient())
    monkeypatch.setattr("backend.professor_demo.api._service", lambda: service)
    client = TestClient(app)

    response = client.post(
        "/api/professor-demo/runs",
        json={"source": "synthetic-demo", "transcript": TRANSCRIPT},
    )
    assert response.status_code == 200
    run = response.json()
    assert run["status"] == "completed"
    assert run["receipt"]["attempted_call_count"] == 4
    assert run["revision_state"]["active_revision_number"] == 0

    accepted = client.post(
        f"/api/professor-demo/runs/{run['run_id']}/accept",
        json={"confirmation": "accept-generated-demo-revision"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["revision_state"]["active_revision_number"] == 1

    latest = client.get("/api/professor-demo/revisions/latest")
    assert latest.status_code == 200
    assert latest.json()["run_id"] == run["run_id"]

    reverted = client.post(
        f"/api/professor-demo/runs/{run['run_id']}/revert",
        json={"confirmation": "restore-original-demo-revision"},
    )
    assert reverted.status_code == 200
    assert reverted.json()["revision_state"]["active_revision_number"] == 0


def test_professor_demo_api_hides_validation_content() -> None:
    client = TestClient(app)
    sentinel = "DO_NOT_ECHO_PRIVATE_TRANSCRIPT"

    response = client.post(
        "/api/professor-demo/runs",
        json={
            "source": "synthetic-demo",
            "transcript": TRANSCRIPT,
            "unexpected": sentinel,
        },
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Request validation failed"}
    assert sentinel not in response.text
