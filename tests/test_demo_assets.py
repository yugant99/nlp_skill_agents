import json
from pathlib import Path

from backend.storage.study_store import StudyWorkspaceStore


DEMO_DIR = Path(__file__).resolve().parents[1] / "demo_assets" / "healthcare_demo"


def test_healthcare_demo_assets_run_as_study_batch(tmp_path: Path) -> None:
    skill_pack = json.loads((DEMO_DIR / "skill_pack.json").read_text(encoding="utf-8"))
    transcripts = [
        {
            "source_filename": path.name,
            "content": path.read_text(encoding="utf-8"),
        }
        for path in sorted((DEMO_DIR / "transcripts").glob("*.txt"))
    ]

    assert len(transcripts) == 3
    assert skill_pack["metrics"] == [
        "base_metrics",
        "question_type_metrics",
        "care_plan_commitment_metrics",
        "concept_count_metrics",
    ]

    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study(
        {
            "name": "Healthcare Demo Study",
            "description": "Synthetic demo for professor walkthrough.",
        }
    )
    version = store.add_skill_pack_version(study.id, skill_pack)
    batch = store.run_text_batch(study.id, version.version_id, transcripts)

    assert batch.run_count == 3
    assert batch.failure_count == 0
    aggregate = json.loads(
        (batch.aggregate_dir / "aggregate_results.json").read_text(encoding="utf-8")
    )
    assert [result["metric_id"] for result in aggregate["results"]] == skill_pack["metrics"]
    assert (batch.aggregate_dir / "care_plan_commitment_metrics.csv").exists()
