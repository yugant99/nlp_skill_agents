import csv
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from backend.analysis.pipeline import execute_analysis
from backend.analysis.transcripts import StudyConfig
from backend.storage.evidence_catalog import EvidenceCatalog
from backend.storage.evidence_target_registry import EvidenceTargetRegistry
from backend.storage.local_store import LocalRunStore
from backend.storage.sqlite_migrations import SchemaCompatibilityError
from backend.storage.source_blob_store import SourceBlobStore


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
    assert run.import_id.startswith("imp_")
    assert run.project_source_id.startswith("psrc_")
    assert run.parent_transcript_revision_id == ""
    assert run.workspace_id == "local-default"
    assert len(run.source_blob_sha256) == 64
    assert run.source_media_type == "text/plain"
    assert run.source_id.startswith("src_")
    assert len(run.transcript_sha256) == 64
    assert run.transcript_revision_id.startswith("trv_")
    assert run.evidence_set_id.startswith("evs_")
    assert all(turn.passage_id.startswith("psg_") for turn in run.transcript.turns)
    assert [result.metric_id for result in run.results] == [
        "base_metrics",
        "disfluency_metrics",
    ]
    assert run.results[1].rows[-1]["disfluency_count"] == 1


def test_local_store_rejects_turns_that_do_not_match_current_parser(
    tmp_path: Path,
) -> None:
    run = execute_analysis(
        "vr006_c: Canonical turn.\nvr006_p: Exact evidence.",
        StudyConfig(participant_id="vr006", selected_metrics=["base_metrics"]),
        source_filename="parser-contract.txt",
    )
    tampered = replace(
        run,
        transcript=replace(
            run.transcript,
            turns=[
                replace(run.transcript.turns[0], text="FORGED TURN"),
                *run.transcript.turns[1:],
            ],
        ),
    )

    with pytest.raises(ValueError, match="current parser"):
        LocalRunStore(tmp_path).persist_run(tampered)
    assert LocalRunStore(tmp_path).list_operations() == []


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
    assert result_payload["import_id"] == run.import_id
    assert result_payload["project_source_id"] == run.project_source_id
    assert result_payload["parent_transcript_revision_id"] == ""
    assert result_payload["workspace_id"] == "local-default"
    assert result_payload["source_blob_sha256"] == run.source_blob_sha256
    assert result_payload["source_media_type"] == "text/plain"
    assert result_payload["source_id"] == run.source_id
    assert result_payload["transcript_sha256"] == run.transcript_sha256
    assert result_payload["transcript_revision_id"] == run.transcript_revision_id
    assert result_payload["evidence_set_id"] == run.evidence_set_id
    assert stored.evidence_set_id == run.evidence_set_id
    assert SourceBlobStore(tmp_path).read_verified(run.source_blob_sha256) == (
        b"vr008_c: This is one sentence.\nvr008_p: Uh, yes."
    )
    assert [metric["metric_id"] for metric in result_payload["results"]] == [
        "base_metrics",
        "lexical_metrics",
        "disfluency_metrics",
    ]
    resolved = EvidenceTargetRegistry(tmp_path).resolve(
        run.workspace_id,
        run.project_source_id,
        run.transcript_revision_id,
        run.evidence_set_id,
        run.transcript.turns[0].passage_id,
    )
    assert resolved.import_id == run.import_id
    assert resolved.producer_kind == "analysis_turns"
    assert resolved.producer_version == 1
    assert resolved.target_kind == "passage"
    assert resolved.passage_ordinal == 0
    assert resolved.role == "caregiver"
    assert resolved.text == "This is one sentence."
    assert store.list_runs()[0]["evidence_set_id"] == run.evidence_set_id

    base_csv = stored.export_dir / "base_metrics.csv"
    with base_csv.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))
    assert rows[0]["speaker"] == "caregiver"
    assert rows[-1]["speaker"] == "total"

    with sqlite3.connect(tmp_path / "runs.sqlite3") as connection:
        db_rows = connection.execute(
            """
            select run_id, import_id, source_blob_sha256, source_media_type,
                   source_id, transcript_sha256, transcript_revision_id,
                   source_filename, metric_count
            from analysis_runs
            """
        ).fetchall()
    assert db_rows == [
        (
            stored.run_id,
            run.import_id,
            run.source_blob_sha256,
            run.source_media_type,
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
    assert listed[0]["import_id"] == run.import_id
    assert listed[0]["source_blob_sha256"] == run.source_blob_sha256
    assert listed[0]["source_media_type"] == run.source_media_type
    assert listed[0]["transcript_sha256"] == run.transcript_sha256
    assert listed[0]["transcript_revision_id"] == run.transcript_revision_id
    assert [migration["version"] for migration in store.migration_status()] == [
        1,
        2,
        3,
        4,
        5,
        6,
    ]
    with sqlite3.connect(tmp_path / "runs.sqlite3") as connection:
        assert connection.execute("pragma user_version").fetchone()[0] == 6
        assert connection.execute(
            """
            select name from sqlite_master
            where type = 'table' and name = 'analysis_operations'
            """
        ).fetchone() == ("analysis_operations",)


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


def test_local_store_journals_failure_and_exact_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run = execute_analysis(
        "vr032_c: Retry this.\nvr032_p: Okay.",
        StudyConfig(participant_id="vr032", selected_metrics=["base_metrics"]),
        source_filename="retry.txt",
    )
    store = LocalRunStore(tmp_path)
    original_record_import = EvidenceCatalog.record_import
    calls = 0

    def fail_once(catalog, record) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("private interview text must not enter the journal")
        original_record_import(catalog, record)

    monkeypatch.setattr(EvidenceCatalog, "record_import", fail_once)

    with pytest.raises(OSError, match="private interview"):
        store.persist_run(run)

    failed = LocalRunStore(tmp_path).list_operations()
    assert len(failed) == 1
    assert failed[0]["run_id"] == run.run_id
    assert failed[0]["status"] == "failed"
    assert failed[0]["stage"] == "source_blob_stored"
    assert failed[0]["attempt_count"] == 1
    assert failed[0]["last_error_type"] == "OSError"
    assert "private interview" not in json.dumps(failed[0])
    assert len(failed[0]["run_payload_sha256"]) == 64
    assert store.list_runs() == []
    assert EvidenceCatalog(tmp_path).list_imports() == []
    assert SourceBlobStore(tmp_path).read_verified(run.source_blob_sha256)

    stored = store.persist_run(run)

    completed = LocalRunStore(tmp_path).list_operations()[0]
    assert completed["status"] == "completed"
    assert completed["stage"] == "completed"
    assert completed["attempt_count"] == 2
    assert completed["last_error_type"] == ""
    assert completed["completed_at"]
    assert stored.results_json.exists()
    assert [item["run_id"] for item in store.list_runs()] == [run.run_id]
    assert [item["import_id"] for item in EvidenceCatalog(tmp_path).list_imports()] == [
        run.import_id
    ]


def test_local_store_retry_after_durable_evidence_registration_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = execute_analysis(
        "vr035_c: Register once.\nvr035_p: Retry safely.",
        StudyConfig(participant_id="vr035", selected_metrics=["base_metrics"]),
        source_filename="evidence-retry.txt",
    )
    store = LocalRunStore(tmp_path)
    original_register = EvidenceTargetRegistry.register_complete_set
    registration_calls = 0

    def fail_after_first_registration(self, prepared):
        nonlocal registration_calls
        snapshot = original_register(self, prepared)
        registration_calls += 1
        if registration_calls == 1:
            raise OSError("injected post-registration failure")
        return snapshot

    monkeypatch.setattr(
        EvidenceTargetRegistry,
        "register_complete_set",
        fail_after_first_registration,
    )

    with pytest.raises(OSError, match="post-registration"):
        store.persist_run(run)

    failed = store.list_operations()[0]
    snapshots = EvidenceTargetRegistry(tmp_path).workspace_snapshot(run.workspace_id)
    assert failed["status"] == "failed"
    assert failed["stage"] == "source_blob_stored"
    assert [snapshot.evidence_set_id for snapshot in snapshots] == [
        run.evidence_set_id
    ]

    stored = store.persist_run(run)

    completed = store.list_operations()[0]
    snapshots = EvidenceTargetRegistry(tmp_path).workspace_snapshot(run.workspace_id)
    assert stored.evidence_set_id == run.evidence_set_id
    assert completed["status"] == "completed"
    assert completed["attempt_count"] == 2
    assert registration_calls == 2
    assert [snapshot.evidence_set_id for snapshot in snapshots] == [
        run.evidence_set_id
    ]


def test_local_store_legacy_results_without_target_set_remain_readable(
    tmp_path: Path,
) -> None:
    run = execute_analysis(
        "vr036_c: Legacy.\nvr036_p: Read only.",
        StudyConfig(participant_id="vr036", selected_metrics=["base_metrics"]),
        source_filename="legacy-results.txt",
    )
    store = LocalRunStore(tmp_path)
    stored = store.persist_run(run)
    payload = json.loads(stored.results_json.read_text(encoding="utf-8"))
    payload.pop("evidence_set_id")
    stored.results_json.write_text(json.dumps(payload), encoding="utf-8")

    listed = store.list_runs()

    assert listed[0]["run_id"] == run.run_id
    assert listed[0]["evidence_set_id"] == ""


def test_local_store_rejects_mismatched_stored_evidence_set_id(
    tmp_path: Path,
) -> None:
    run = execute_analysis(
        "vr037_c: Exact.\nvr037_p: Ownership.",
        StudyConfig(participant_id="vr037", selected_metrics=["base_metrics"]),
        source_filename="mismatched-target.txt",
    )
    store = LocalRunStore(tmp_path)
    stored = store.persist_run(run)
    payload = json.loads(stored.results_json.read_text(encoding="utf-8"))
    payload["evidence_set_id"] = "evs_00000000000000000000000000000000"
    stored.results_json.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="ownership conflicts"):
        store.list_runs()


def test_local_store_rejects_forged_present_target_artifact_identity(
    tmp_path: Path,
) -> None:
    run = execute_analysis(
        "vr038_c: Strict.\nvr038_p: Reload.",
        StudyConfig(participant_id="vr038", selected_metrics=["base_metrics"]),
        source_filename="strict-target.txt",
    )
    store = LocalRunStore(tmp_path)
    stored = store.persist_run(run)
    payload = json.loads(stored.results_json.read_text(encoding="utf-8"))
    payload["workspace_id"] = "forged-workspace"
    payload["transcript_sha256"] = "0" * 64
    stored.results_json.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="identity conflicts"):
        store.list_runs()


def test_local_store_replays_exact_run_and_rejects_changed_payload(
    tmp_path: Path,
) -> None:
    run = execute_analysis(
        "vr033_c: Stable.\nvr033_p: Evidence.",
        StudyConfig(participant_id="vr033", selected_metrics=["base_metrics"]),
        source_filename="stable.txt",
    )
    store = LocalRunStore(tmp_path)

    store.persist_run(run)
    store.persist_run(run)

    completed = store.list_operations()[0]
    assert completed["status"] == "completed"
    assert completed["attempt_count"] == 2
    with pytest.raises(ValueError, match="identity conflicts"):
        store.persist_run(replace(run, source_filename="changed.txt"))
    assert store.list_operations()[0]["attempt_count"] == 2
    assert [item["run_id"] for item in store.list_runs()] == [run.run_id]


def test_local_store_rejects_pre_journal_run_conflict_before_side_effects(
    tmp_path: Path,
) -> None:
    run = execute_analysis(
        "vr034_c: Preserve this.\nvr034_p: Yes.",
        StudyConfig(participant_id="vr034", selected_metrics=["base_metrics"]),
        source_filename="preserve.txt",
    )
    store = LocalRunStore(tmp_path)
    assert store.list_runs() == []
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            """
            insert into analysis_runs (
              run_id, import_id, project_source_id,
              parent_transcript_revision_id, workspace_id,
              source_blob_sha256, source_media_type, source_id,
              transcript_sha256, transcript_revision_id,
              source_filename, created_at, metric_count
            ) values (?, 'imp_existing', 'psrc_existing', '', 'local-default',
                      ?, 'text/plain', 'src_existing', ?, 'trv_existing',
                      'existing.txt', ?, 1)
            """,
            (run.run_id, "a" * 64, "b" * 64, run.created_at),
        )

    with pytest.raises(ValueError, match="identity conflicts with stored run"):
        store.persist_run(run)

    assert store.list_operations() == []
    assert not (tmp_path / "runs" / run.run_id).exists()
    assert not SourceBlobStore(tmp_path).blob_path(run.source_blob_sha256).exists()
    assert EvidenceCatalog(tmp_path).list_imports() == []
    assert store.list_runs()[0]["source_filename"] == "existing.txt"


def test_local_store_rejects_newer_analysis_schema(tmp_path: Path) -> None:
    with sqlite3.connect(tmp_path / "runs.sqlite3") as connection:
        connection.execute("pragma user_version = 99")

    with pytest.raises(SchemaCompatibilityError, match="newer"):
        LocalRunStore(tmp_path).list_runs()
