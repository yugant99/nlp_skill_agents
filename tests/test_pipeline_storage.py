import csv
import json
import sqlite3
from pathlib import Path

from backend.analysis.pipeline import execute_analysis
from backend.analysis.transcripts import StudyConfig
from backend.storage.local_store import LocalRunStore


def test_execute_analysis_runs_selected_metric_skills() -> None:
    run = execute_analysis(
        "vr007_c: Um, hello there.\nvr007_p: Hello again.",
        StudyConfig(
            participant_id="vr007",
            selected_metrics=["base_metrics", "disfluency_metrics"],
            disfluency_tokens=["um"],
        ),
        source_filename="vr007.txt",
    )

    assert run.source_filename == "vr007.txt"
    assert [result.metric_id for result in run.results] == [
        "base_metrics",
        "disfluency_metrics",
    ]
    assert run.results[1].rows[-1]["disfluency_count"] == 1


def test_local_store_persists_json_csv_and_sqlite_metadata(tmp_path: Path) -> None:
    run = execute_analysis(
        "vr008_c: This is one sentence.\nvr008_p: Uh, yes.",
        StudyConfig(
            participant_id="vr008",
            selected_metrics=["base_metrics", "lexical_metrics", "disfluency_metrics"],
        ),
        source_filename="vr008.txt",
    )
    store = LocalRunStore(tmp_path)

    stored = store.persist_run(run)

    assert stored.run_dir.exists()
    result_payload = json.loads(stored.results_json.read_text(encoding="utf-8"))
    assert result_payload["source_filename"] == "vr008.txt"
    assert [metric["metric_id"] for metric in result_payload["results"]] == [
        "base_metrics",
        "lexical_metrics",
        "disfluency_metrics",
    ]

    base_csv = stored.export_dir / "base_metrics.csv"
    with base_csv.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))
    assert rows[0]["speaker"] == "caregiver"
    assert rows[-1]["speaker"] == "total"

    with sqlite3.connect(tmp_path / "runs.sqlite3") as connection:
        db_rows = connection.execute(
            "select run_id, source_filename, metric_count from analysis_runs"
        ).fetchall()
    assert db_rows == [(stored.run_id, "vr008.txt", 3)]

