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
                "content": "CG: How did walking feel?\nP: It hurt.",
            },
            {
                "source_filename": "two.txt",
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

    with (batch.aggregate_dir / "question_type_metrics.csv").open(
        newline="",
        encoding="utf-8",
    ) as csv_file:
        rows = list(csv.DictReader(csv_file))
    assert rows[0]["source_filename"] == "one.txt"
    assert rows[0]["speaker"] == "caregiver"
    assert rows[0]["open_question_turns"] == "1"
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
