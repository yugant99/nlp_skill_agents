import csv
import json
from pathlib import Path

from backend.storage.study_store import StudyWorkspaceStore


def test_study_workspace_runs_text_batch_with_aggregate_exports(tmp_path: Path) -> None:
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study(
        {
            "name": "Mobility Care Study",
            "description": "Caregiver participant mobility transcripts.",
        }
    )
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "mobility_care_pack",
            "name": "Mobility Care Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics", "question_type_metrics"],
            "speaker_roles": {
                "caregiver": {"label": "Caregiver", "prefixes": ["CG"]},
                "participant": {"label": "Participant", "prefixes": ["P"]},
            },
        },
    )

    batch = store.run_text_batch(
        study.id,
        version.version_id,
        [
            {
                "source_filename": "one.txt",
                "metadata": {
                    "participant_id": "P1",
                    "condition": "home",
                    "week": "week_1",
                },
                "content": "CG: How did walking feel?\nP: It hurt.",
            },
            {
                "source_filename": "two.txt",
                "metadata": {
                    "participant_id": "P2",
                    "condition": "lab",
                    "week": "week_1",
                    "site": "clinic_a",
                },
                "content": "CG: Did medication help?\nP: Yes.",
            },
        ],
    )

    assert study.id == "mobility-care-study"
    assert version.version_id == "mobility_care_pack-1_0_0"
    assert batch.study_id == "mobility-care-study"
    assert batch.run_count == 2
    assert batch.failure_count == 0
    assert batch.aggregate_dir.exists()
    assert (batch.aggregate_dir / "aggregate_results.json").exists()
    assert (batch.aggregate_dir / "base_metrics.csv").exists()
    assert (batch.aggregate_dir / "question_type_metrics.csv").exists()

    aggregate_payload = json.loads(
        (batch.aggregate_dir / "aggregate_results.json").read_text(encoding="utf-8")
    )
    assert aggregate_payload["study_id"] == "mobility-care-study"
    assert aggregate_payload["skill_pack_version_id"] == "mobility_care_pack-1_0_0"
    assert [result["metric_id"] for result in aggregate_payload["results"]] == [
        "base_metrics",
        "question_type_metrics",
    ]
    assert aggregate_payload["results"][0]["rows"][0]["source_filename"] == "one.txt"
    assert aggregate_payload["results"][0]["rows"][0]["participant_id"] == "P1"
    assert aggregate_payload["results"][0]["rows"][0]["condition"] == "home"
    assert aggregate_payload["results"][0]["rows"][0]["week"] == "week_1"
    assert aggregate_payload["results"][0]["rows"][3]["participant_id"] == "P2"
    assert aggregate_payload["results"][0]["rows"][3]["site"] == "clinic_a"

    run_payload = json.loads(next((batch.aggregate_dir / "runs").glob("*.json")).read_text())
    assert run_payload["metadata"]["participant_id"] in {"P1", "P2"}

    with (batch.aggregate_dir / "question_type_metrics.csv").open(
        newline="",
        encoding="utf-8",
    ) as csv_file:
        rows = list(csv.DictReader(csv_file))
        assert csv_file.name.endswith("question_type_metrics.csv")
    assert rows[0].keys() >= {
        "participant_id",
        "condition",
        "week",
        "source_filename",
        "run_id",
    }
    assert rows[0]["participant_id"] == "P1"
    assert rows[0]["condition"] == "home"
    assert rows[0]["week"] == "week_1"
    assert rows[0]["source_filename"] == "one.txt"
    assert rows[0]["speaker"] == "caregiver"
    assert rows[0]["open_question_turns"] == "1"
    assert rows[3]["participant_id"] == "P2"
    assert rows[3]["condition"] == "lab"
    assert rows[3]["site"] == "clinic_a"
    assert rows[3]["source_filename"] == "two.txt"
    assert rows[3]["yes_no_question_turns"] == "1"


def test_study_workspace_records_batch_failures_without_stopping(tmp_path: Path) -> None:
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Failure Isolation Study"})
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
        [
            {"source_filename": "bad.txt", "content": "CG: Hello."},
            {"source_filename": "also_bad.txt", "content": "CG: Hello again."},
        ],
    )

    assert batch.run_count == 0
    assert batch.failure_count == 2
    payload = json.loads(
        (batch.aggregate_dir / "aggregate_results.json").read_text(encoding="utf-8")
    )
    assert [failure["source_filename"] for failure in payload["failures"]] == [
        "bad.txt",
        "also_bad.txt",
    ]


def test_batch_participant_metadata_can_drive_default_prefix_parsing(
    tmp_path: Path,
) -> None:
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Participant Prefix Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "participant_prefix_pack",
            "name": "Participant Prefix Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )

    batch = store.run_text_batch(
        study.id,
        version.version_id,
        [
            {
                "source_filename": "p1_week1.txt",
                "metadata": {"participant_id": "P1", "condition": "home", "week": "week_1"},
                "content": "P1_c: How did walking feel?\nP1_p: It hurt.",
            }
        ],
    )

    aggregate_payload = json.loads(
        (batch.aggregate_dir / "aggregate_results.json").read_text(encoding="utf-8")
    )

    caregiver_row = aggregate_payload["results"][0]["rows"][0]
    participant_row = aggregate_payload["results"][0]["rows"][1]
    assert caregiver_row["speaker"] == "caregiver"
    assert caregiver_row["turns"] == 1
    assert participant_row["speaker"] == "participant"
    assert participant_row["turns"] == 1


def test_study_workspace_exports_reproducibility_bundle(tmp_path: Path) -> None:
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Bundle Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "bundle_pack",
            "name": "Bundle Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    store.run_text_batch(
        study.id,
        version.version_id,
        [{"source_filename": "one.txt", "content": "CG: Hello.\nP: Hi."}],
    )

    bundle = store.export_study_bundle(study.id)

    assert bundle.study_id == "bundle-study"
    assert bundle.manifest_path.exists()
    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
    assert manifest["study"]["id"] == "bundle-study"
    assert manifest["bundle_id"].startswith("bundle-study-")
    assert manifest["files"]
    assert all(file_record["sha256"] for file_record in manifest["files"])
    assert "studies/bundle-study/study.json" in [
        file_record["relative_path"] for file_record in manifest["files"]
    ]


def test_study_workspace_writes_audit_events(tmp_path: Path) -> None:
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Audit Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "audit_pack",
            "name": "Audit Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    batch = store.run_text_batch(
        study.id,
        version.version_id,
        [{"source_filename": "one.txt", "content": "CG: Hello."}],
    )
    bundle = store.export_study_bundle(study.id)

    events = store.audit_log.list_events()

    assert [event["event_type"] for event in events] == [
        "study.created",
        "skill_pack.versioned",
        "batch.completed",
        "bundle.exported",
    ]
    assert events[0]["subject_id"] == "audit-study"
    assert events[2]["metadata"]["batch_id"] == batch.batch_id
    assert events[3]["metadata"]["bundle_id"] == bundle.bundle_id
