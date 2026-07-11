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
    assert run.source_id.startswith("src_")
    assert len(run.transcript_sha256) == 64
    assert run.transcript_revision_id.startswith("trv_")
    assert all(turn.passage_id.startswith("psg_") for turn in run.transcript.turns)
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
    assert result_payload["source_id"] == run.source_id
    assert result_payload["transcript_sha256"] == run.transcript_sha256
    assert result_payload["transcript_revision_id"] == run.transcript_revision_id
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
            """
            select run_id, source_id, transcript_sha256, transcript_revision_id,
                   source_filename, metric_count
            from analysis_runs
            """
        ).fetchall()
    assert db_rows == [
        (
            stored.run_id,
            run.source_id,
            run.transcript_sha256,
            run.transcript_revision_id,
            "vr008.txt",
            3,
        )
    ]


def test_local_store_migrates_existing_run_metadata_schema(tmp_path: Path) -> None:
    with sqlite3.connect(tmp_path / "runs.sqlite3") as connection:
        connection.execute(
            """
            create table analysis_runs (
              run_id text primary key,
              source_filename text not null,
              created_at text not null,
              metric_count integer not null
            )
            """
        )

    run = execute_analysis(
        "vr009_c: Hello.\nvr009_p: Hi.",
        StudyConfig(participant_id="vr009", selected_metrics=["base_metrics"]),
        source_filename="vr009.txt",
    )
    store = LocalRunStore(tmp_path)

    store.persist_run(run)

    listed = store.list_runs()
    assert listed[0]["source_id"] == run.source_id
    assert listed[0]["transcript_sha256"] == run.transcript_sha256
    assert listed[0]["transcript_revision_id"] == run.transcript_revision_id


def test_local_store_lists_recent_runs_newest_first(tmp_path: Path) -> None:
    store = LocalRunStore(tmp_path)
    first = execute_analysis(
        "vr030_c: First.\nvr030_p: One.",
        StudyConfig(participant_id="vr030", selected_metrics=["base_metrics"]),
        source_filename="first.txt",
    )
    second = execute_analysis(
        "vr031_c: Second.\nvr031_p: Two.",
        StudyConfig(participant_id="vr031", selected_metrics=["base_metrics"]),
        source_filename="second.txt",
    )
    store.persist_run(first)
    store.persist_run(second)

    rows = store.list_runs()

    assert [row["source_filename"] for row in rows] == ["second.txt", "first.txt"]
    assert rows[0]["metric_count"] == 1
    assert rows[0]["results_json"].endswith("results.json")
