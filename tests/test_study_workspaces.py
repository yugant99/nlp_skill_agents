import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.storage.evidence_catalog import EvidenceCatalog
from backend.storage.study_batch_operation_store import (
    StudyBatchOperationConflict,
    StudyBatchOperationStore,
)
from backend.storage.study_store import (
    StudyBatchSnapshotConflict,
    StudySkillPackVersionConflict,
    StudyWorkspaceStore,
)


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


def test_study_schema_is_saved_and_attached_to_batch_outputs(tmp_path: Path) -> None:
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Schema Study"})

    schema = store.save_study_schema(
        study.id,
        {
            "participant_count": 8,
            "conditions": "home, lab, clinic",
            "week_count": 3,
            "custom_fields": ["site", "arm"],
        },
    )
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "schema_pack",
            "name": "Schema Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    batch = store.run_text_batch(
        study.id,
        version.version_id,
        [
            {
                "source_filename": "P1_home_week1.txt",
                "metadata": {
                    "participant_id": "P1",
                    "condition": "home",
                    "week": "week_1",
                    "site": "north",
                },
                "content": "P1_c: Hello?\nP1_p: Hi.",
            }
        ],
    )

    schema_path = tmp_path / "studies" / study.id / "study_schema.json"
    assert schema_path.exists()
    assert schema.participants == [
        "P1",
        "P2",
        "P3",
        "P4",
        "P5",
        "P6",
        "P7",
        "P8",
    ]
    assert schema.conditions == ["home", "lab", "clinic"]
    assert schema.weeks == ["week_1", "week_2", "week_3"]
    assert schema.custom_fields == ["site", "arm"]

    aggregate_payload = json.loads(
        (batch.aggregate_dir / "aggregate_results.json").read_text(encoding="utf-8")
    )
    assert aggregate_payload["study_schema"]["participant_count"] == 8
    assert aggregate_payload["study_schema"]["conditions"] == ["home", "lab", "clinic"]
    assert aggregate_payload["study_schema"]["weeks"] == ["week_1", "week_2", "week_3"]
    assert aggregate_payload["study_schema"]["custom_fields"] == ["site", "arm"]


def test_study_workspace_lists_and_loads_batch_history(tmp_path: Path) -> None:
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "History Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "history_pack",
            "name": "History Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    first_batch = store.run_text_batch(
        study.id,
        version.version_id,
        [{"source_filename": "one.txt", "content": "CG: Hello.\nP: Hi."}],
    )
    second_batch = store.run_text_batch(
        study.id,
        version.version_id,
        [{"source_filename": "two.txt", "content": "CG: Again?\nP: Yes."}],
    )

    batches = store.list_batches(study.id)
    loaded = store.load_batch(study.id, first_batch.batch_id)

    assert [batch.batch_id for batch in batches] == [
        second_batch.batch_id,
        first_batch.batch_id,
    ]
    assert batches[0].run_count == 1
    assert batches[0].failure_count == 0
    assert loaded.batch_id == first_batch.batch_id
    assert (loaded.aggregate_dir / "aggregate_results.json").exists()


def test_study_workspace_lists_and_loads_batch_run_drilldown(tmp_path: Path) -> None:
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Run Drilldown Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "run_drilldown_pack",
            "name": "Run Drilldown Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    batch = store.run_text_batch(
        study.id,
        version.version_id,
        [
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
    )

    run_summaries = store.list_batch_runs(study.id, batch.batch_id)
    loaded_run = store.load_batch_run(study.id, batch.batch_id, run_summaries[0]["run_id"])

    assert [run["source_filename"] for run in run_summaries] == [
        "P1_home_week1.txt",
        "P2_lab_week1.txt",
    ]
    assert run_summaries[0]["metadata"]["participant_id"] == "P1"
    assert run_summaries[0]["turn_count"] == 2
    assert run_summaries[0]["import_id"].startswith("imp_")
    assert run_summaries[0]["project_source_id"].startswith("psrc_")
    assert run_summaries[0]["parent_transcript_revision_id"] == ""
    assert run_summaries[0]["workspace_id"] == study.id
    assert len(run_summaries[0]["source_blob_sha256"]) == 64
    assert run_summaries[0]["source_media_type"] == "text/plain"
    assert run_summaries[0]["source_id"] == loaded_run["source_id"]
    assert (
        run_summaries[0]["transcript_sha256"]
        == loaded_run["transcript_sha256"]
    )
    assert (
        run_summaries[0]["transcript_revision_id"]
        == loaded_run["transcript_revision_id"]
    )
    assert loaded_run["source_filename"] == "P1_home_week1.txt"
    assert [
        {key: value for key, value in turn.items() if key != "passage_id"}
        for turn in loaded_run["turns"]
    ] == [
        {
            "turn_index": 0,
            "role": "caregiver",
            "speaker_label": "Caregiver",
            "raw_prefix": "P1_c",
            "text": "Hello?",
        },
        {
            "turn_index": 1,
            "role": "participant",
            "speaker_label": "Participant",
            "raw_prefix": "P1_p",
            "text": "Hi.",
        },
    ]
    assert all(turn["passage_id"].startswith("psg_") for turn in loaded_run["turns"])
    assert len({turn["passage_id"] for turn in loaded_run["turns"]}) == 2
    assert loaded_run["results"][0]["metric_id"] == "base_metrics"


def test_study_workspace_lists_legacy_batch_runs_without_identity_fields(
    tmp_path: Path,
) -> None:
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Legacy Batch Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "legacy_batch_pack",
            "name": "Legacy Batch Pack",
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
                "content": "P1_c: Hello.\nP1_p: Hi.",
            }
        ],
    )
    run_path = next((batch.aggregate_dir / "runs").glob("*.json"))
    payload = json.loads(run_path.read_text(encoding="utf-8"))
    for field in (
        "import_id",
        "project_source_id",
        "parent_transcript_revision_id",
        "workspace_id",
        "source_blob_sha256",
        "source_media_type",
        "source_id",
        "transcript_sha256",
        "transcript_revision_id",
    ):
        payload.pop(field)
    run_path.write_text(json.dumps(payload), encoding="utf-8")

    summary = store.list_batch_runs(study.id, batch.batch_id)[0]

    assert summary["import_id"] == ""
    assert summary["source_blob_sha256"] == ""
    assert summary["source_media_type"] == ""
    assert summary["source_id"] == ""
    assert summary["project_source_id"] == ""
    assert summary["parent_transcript_revision_id"] == ""
    assert summary["workspace_id"] == ""
    assert summary["transcript_sha256"] == ""
    assert summary["transcript_revision_id"] == ""


def test_study_workspace_keeps_revision_lineage_inside_the_study(
    tmp_path: Path,
) -> None:
    from backend.storage.evidence_catalog import EvidenceCatalog
    from backend.storage.source_blob_store import SourceBlobStore

    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Revision Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "revision_pack",
            "name": "Revision Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    first_batch = store.run_text_batch(
        study.id,
        version.version_id,
        [{"source_filename": "session.txt", "content": "P1_c: One.\nP1_p: Two."}],
    )
    first = store.list_batch_runs(study.id, first_batch.batch_id)[0]
    second_batch = store.run_text_batch(
        study.id,
        version.version_id,
        [
            {
                "source_filename": "session-revised.txt",
                "content": "P1_c: Revised one.\nP1_p: Revised two.",
                "project_source_id": first["project_source_id"],
                "parent_transcript_revision_id": first[
                    "transcript_revision_id"
                ],
            }
        ],
    )
    second = store.list_batch_runs(study.id, second_batch.batch_id)[0]

    history = EvidenceCatalog(tmp_path).source_history(first["project_source_id"])
    assert first["workspace_id"] == study.id
    assert second["workspace_id"] == study.id
    assert second["project_source_id"] == first["project_source_id"]
    assert history["source"]["workspace_id"] == study.id
    assert SourceBlobStore(tmp_path).read_verified(first["source_blob_sha256"]) == (
        b"P1_c: One.\nP1_p: Two."
    )
    assert history["revisions"][1]["parent_transcript_revision_id"] == first[
        "transcript_revision_id"
    ]


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


def test_study_batch_journal_completes_with_reserved_item_identity(
    tmp_path: Path,
) -> None:
    store, study_id, version_id, transcripts = _journal_batch_fixture(tmp_path)

    batch = store.run_text_batch(study_id, version_id, transcripts)

    journal = StudyBatchOperationStore(tmp_path, study_id)
    operation = journal.get_operation(batch.batch_id)
    item = journal.list_items(batch.batch_id)[0]
    run = store.list_batch_runs(study_id, batch.batch_id)[0]
    manifest = json.loads(
        (batch.aggregate_dir / "batch.json").read_text(encoding="utf-8")
    )
    assert operation["status"] == "completed"
    assert operation["stage"] == "completed"
    assert operation["attempt_count"] == 1
    assert operation["item_count"] == 1
    assert len(operation["aggregate_payload_sha256"]) == 64
    assert operation["completed_at"]
    assert item["stage"] == "completed"
    assert item["run_id"] == run["run_id"]
    assert item["import_id"] == run["import_id"]
    assert item["project_source_id"] == run["project_source_id"]
    assert item["source_blob_sha256"] == run["source_blob_sha256"]
    assert item["transcript_revision_id"] == run["transcript_revision_id"]
    assert manifest["aggregate_dir"] == (
        f"studies/{study_id}/batches/{batch.batch_id}"
    )
    assert store.load_batch(study_id, batch.batch_id).aggregate_dir == (
        batch.aggregate_dir
    )


def test_study_batch_exact_retry_reuses_side_effect_identities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, study_id, version_id, transcripts = _journal_batch_fixture(tmp_path)
    batch_id = "batch_20260729010101_deadbeef"
    original_record_import = EvidenceCatalog.record_import
    call_count = 0

    def fail_after_first_import(self, record):
        nonlocal call_count
        original_record_import(self, record)
        call_count += 1
        if call_count == 1:
            raise OSError("injected persistence failure")

    monkeypatch.setattr(EvidenceCatalog, "record_import", fail_after_first_import)
    with pytest.raises(OSError, match="injected persistence failure"):
        store.run_text_batch(
            study_id,
            version_id,
            transcripts,
            batch_id=batch_id,
        )

    journal = StudyBatchOperationStore(tmp_path, study_id)
    failed_operation = journal.get_operation(batch_id)
    reserved_before_retry = journal.list_items(batch_id)[0]
    assert failed_operation["status"] == "failed"
    assert failed_operation["last_error_type"] == "OSError"
    assert reserved_before_retry["stage"] == "source_blob_stored"

    batch = store.run_text_batch(
        study_id,
        version_id,
        transcripts,
        batch_id=batch_id,
    )

    completed_operation = journal.get_operation(batch_id)
    reserved_after_retry = journal.list_items(batch_id)[0]
    imports = EvidenceCatalog(tmp_path).list_imports()
    batch_events = [
        event
        for event in store.audit_log.list_events(limit=None)
        if event["event_type"] == "batch.completed"
        and event["metadata"]["batch_id"] == batch_id
    ]
    assert batch.batch_id == batch_id
    assert completed_operation["status"] == "completed"
    assert completed_operation["attempt_count"] == 2
    assert reserved_after_retry["stage"] == "completed"
    assert {
        key: reserved_after_retry[key]
        for key in ("run_id", "import_id", "project_source_id", "created_at")
    } == {
        key: reserved_before_retry[key]
        for key in ("run_id", "import_id", "project_source_id", "created_at")
    }
    assert [item["import_id"] for item in imports] == [
        reserved_before_retry["import_id"]
    ]
    assert len(batch_events) == 1


def test_study_batch_retry_deduplicates_audit_written_before_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, study_id, version_id, transcripts = _journal_batch_fixture(tmp_path)
    batch_id = "batch_20260729020202_cafebabe"
    original_import_events = store.audit_log.import_events
    call_count = 0

    def fail_after_first_audit(events):
        nonlocal call_count
        imported = original_import_events(events)
        call_count += 1
        if call_count == 1:
            raise OSError("injected post-audit failure")
        return imported

    monkeypatch.setattr(store.audit_log, "import_events", fail_after_first_audit)
    with pytest.raises(OSError, match="injected post-audit failure"):
        store.run_text_batch(
            study_id,
            version_id,
            transcripts,
            batch_id=batch_id,
        )

    journal = StudyBatchOperationStore(tmp_path, study_id)
    assert journal.get_operation(batch_id)["stage"] == "batch_manifest_written"
    store.run_text_batch(
        study_id,
        version_id,
        transcripts,
        batch_id=batch_id,
    )

    batch_events = [
        event
        for event in store.audit_log.list_events(limit=None)
        if event["event_type"] == "batch.completed"
        and event["metadata"]["batch_id"] == batch_id
    ]
    assert journal.get_operation(batch_id)["status"] == "completed"
    assert len(batch_events) == 1


def test_study_batch_completed_retry_is_a_noop_and_changed_request_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, study_id, version_id, transcripts = _journal_batch_fixture(tmp_path)
    batch_id = "batch_20260729030303_0badf00d"
    first = store.run_text_batch(
        study_id,
        version_id,
        transcripts,
        batch_id=batch_id,
    )
    journal = StudyBatchOperationStore(tmp_path, study_id)
    original_operation = journal.get_operation(batch_id)

    def unexpected_analysis(*args, **kwargs):
        raise AssertionError("completed retries must not execute analysis")

    monkeypatch.setattr(
        "backend.storage.study_store.execute_analysis",
        unexpected_analysis,
    )
    replayed = store.run_text_batch(
        study_id,
        version_id,
        transcripts,
        batch_id=batch_id,
    )
    with pytest.raises(StudyBatchOperationConflict, match="identity conflicts"):
        store.run_text_batch(
            study_id,
            version_id,
            [{**transcripts[0], "content": "P1_c: Changed.\nP1_p: Changed."}],
            batch_id=batch_id,
        )

    batch_events = [
        event
        for event in store.audit_log.list_events(limit=None)
        if event["event_type"] == "batch.completed"
        and event["metadata"]["batch_id"] == batch_id
    ]
    assert replayed == first
    assert journal.get_operation(batch_id)["attempt_count"] == (
        original_operation["attempt_count"]
    )
    assert len(batch_events) == 1


def test_study_batch_replay_survives_identical_schema_save(
    tmp_path: Path,
) -> None:
    store, study_id, version_id, transcripts = _journal_batch_fixture(tmp_path)
    schema_payload = {
        "participant_count": 2,
        "conditions": ["home", "lab"],
        "week_count": 2,
        "custom_fields": ["site"],
    }
    original_schema = store.save_study_schema(study_id, schema_payload)
    batch_id = "batch_20260729034343_abcddcba"
    original_batch = store.run_text_batch(
        study_id,
        version_id,
        transcripts,
        batch_id=batch_id,
    )

    unchanged_schema = store.save_study_schema(study_id, schema_payload)
    replayed_batch = store.run_text_batch(
        study_id,
        version_id,
        transcripts,
        batch_id=batch_id,
    )

    schema_events = [
        event
        for event in store.audit_log.list_events(limit=None)
        if event["event_type"] == "study.schema.updated"
    ]
    assert unchanged_schema == original_schema
    assert replayed_batch == original_batch
    assert len(schema_events) == 1
    store.save_study_schema(
        study_id,
        {**schema_payload, "participant_count": 3},
    )
    with pytest.raises(StudyBatchOperationConflict, match="identity conflicts"):
        store.run_text_batch(
            study_id,
            version_id,
            transcripts,
            batch_id=batch_id,
        )


def test_study_skill_pack_versions_are_immutable_and_idempotent(
    tmp_path: Path,
) -> None:
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Immutable Pack Study"})
    payload = {
        "id": "immutable_pack",
        "name": "Immutable Pack",
        "version": "1.0.0",
        "metrics": ["base_metrics"],
    }
    original = store.add_skill_pack_version(study.id, payload)
    batch_id = "batch_20260729035353_1122aabb"
    transcripts = [{"source_filename": "session.txt", "content": "CG: Hello."}]
    original_batch = store.run_text_batch(
        study.id,
        original.version_id,
        transcripts,
        batch_id=batch_id,
    )

    identical = store.add_skill_pack_version(study.id, dict(payload))
    with pytest.raises(
        StudySkillPackVersionConflict,
        match="already exists with different content",
    ):
        store.add_skill_pack_version(
            study.id,
            {**payload, "name": "Mutated Pack"},
        )
    replayed_batch = store.run_text_batch(
        study.id,
        original.version_id,
        transcripts,
        batch_id=batch_id,
    )

    version_events = [
        event
        for event in store.audit_log.list_events(limit=None)
        if event["event_type"] == "skill_pack.versioned"
    ]
    assert identical == original
    assert replayed_batch == original_batch
    assert len(version_events) == 1


def test_study_batch_completed_retry_verifies_persisted_outputs(
    tmp_path: Path,
) -> None:
    store, study_id, version_id, transcripts = _journal_batch_fixture(tmp_path)
    batch_id = "batch_20260729035303_abcd1234"
    batch = store.run_text_batch(
        study_id,
        version_id,
        transcripts,
        batch_id=batch_id,
    )
    aggregate_path = batch.aggregate_dir / "aggregate_results.json"
    aggregate_payload = json.loads(aggregate_path.read_text(encoding="utf-8"))
    aggregate_payload["run_count"] = 99
    aggregate_path.write_text(json.dumps(aggregate_payload), encoding="utf-8")

    with pytest.raises(
        StudyBatchSnapshotConflict,
        match="aggregate conflicts",
    ):
        store.run_text_batch(
            study_id,
            version_id,
            transcripts,
            batch_id=batch_id,
        )


@pytest.mark.parametrize("tampered_field", ["failures", "study_schema"])
def test_study_batch_completed_retry_rejects_self_referential_aggregate_tampering(
    tmp_path: Path,
    tampered_field: str,
) -> None:
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Aggregate Integrity Study"})
    store.save_study_schema(
        study.id,
        {"participant_count": 2, "conditions": ["home"], "week_count": 1},
    )
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "aggregate_integrity_pack",
            "name": "Aggregate Integrity Pack",
            "version": "1.0.0",
            "metrics": ["missing_metric_for_integrity_test"],
        },
        validate=False,
    )
    transcripts = [{"source_filename": "rejected.txt", "content": "CG: Hello."}]
    batch_id = "batch_20260729035803_aaaacccc"
    batch = store.run_text_batch(
        study.id,
        version.version_id,
        transcripts,
        batch_id=batch_id,
    )
    aggregate_path = batch.aggregate_dir / "aggregate_results.json"
    aggregate_payload = json.loads(aggregate_path.read_text(encoding="utf-8"))
    if tampered_field == "failures":
        aggregate_payload["failures"][0] = {
            "source_filename": "forged.txt",
            "error": "forged analytical failure",
        }
    else:
        aggregate_payload["study_schema"]["conditions"] = ["forged-condition"]
    aggregate_path.write_text(json.dumps(aggregate_payload), encoding="utf-8")

    with pytest.raises(
        StudyBatchSnapshotConflict,
        match="aggregate conflicts with its journal",
    ):
        store.run_text_batch(
            study.id,
            version.version_id,
            transcripts,
            batch_id=batch_id,
        )


@pytest.mark.parametrize("corrupt_audit", ["{not-json\n", "42\n"])
def test_study_batch_completed_retry_rejects_malformed_audit_log(
    tmp_path: Path,
    corrupt_audit: str,
) -> None:
    store, study_id, version_id, transcripts = _journal_batch_fixture(tmp_path)
    batch_id = "batch_20260729036303_dcba4321"
    store.run_text_batch(
        study_id,
        version_id,
        transcripts,
        batch_id=batch_id,
    )
    store.audit_log.events_path.write_text(corrupt_audit, encoding="utf-8")

    with pytest.raises(StudyBatchSnapshotConflict, match="audit log is invalid"):
        store.run_text_batch(
            study_id,
            version_id,
            transcripts,
            batch_id=batch_id,
        )


def test_study_batch_retry_rejects_conflicting_existing_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, study_id, version_id, transcripts = _journal_batch_fixture(tmp_path)
    batch_id = "batch_20260729040404_1234abcd"
    original_record_aggregate = StudyBatchOperationStore.record_aggregate_written

    def fail_after_aggregate(
        self,
        current_batch_id,
        *,
        aggregate_payload_sha256,
    ):
        raise OSError("injected post-aggregate failure")

    monkeypatch.setattr(
        StudyBatchOperationStore,
        "record_aggregate_written",
        fail_after_aggregate,
    )
    with pytest.raises(OSError, match="injected post-aggregate failure"):
        store.run_text_batch(
            study_id,
            version_id,
            transcripts,
            batch_id=batch_id,
        )
    monkeypatch.setattr(
        StudyBatchOperationStore,
        "record_aggregate_written",
        original_record_aggregate,
    )
    aggregate_path = (
        tmp_path
        / "studies"
        / study_id
        / "batches"
        / batch_id
        / "aggregate_results.json"
    )
    aggregate_path.write_text("tampered snapshot", encoding="utf-8")

    with pytest.raises(StudyBatchSnapshotConflict, match="aggregate_results.json"):
        store.run_text_batch(
            study_id,
            version_id,
            transcripts,
            batch_id=batch_id,
        )

    assert aggregate_path.read_text(encoding="utf-8") == "tampered snapshot"
    operation = StudyBatchOperationStore(tmp_path, study_id).get_operation(batch_id)
    assert operation["status"] == "failed"
    assert operation["last_error_type"] == "StudyBatchSnapshotConflict"


def test_study_batch_retry_normalizes_metadata_key_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, study_id, version_id, transcripts = _journal_batch_fixture(tmp_path)
    batch_id = "batch_20260729041414_5678abcd"
    original_advance_item = StudyBatchOperationStore.advance_item
    failed = False

    def fail_after_snapshot(self, current_batch_id, item_index, stage):
        nonlocal failed
        original_advance_item(self, current_batch_id, item_index, stage)
        if stage == "snapshot_written" and not failed:
            failed = True
            raise OSError("injected post-snapshot failure")

    first_request = [
        {
            **transcripts[0],
            "metadata": {"week": "week_1", "participant_id": "P1"},
        }
    ]
    reordered_request = [
        {
            **transcripts[0],
            "metadata": {"participant_id": "P1", "week": "week_1"},
        }
    ]
    monkeypatch.setattr(
        StudyBatchOperationStore,
        "advance_item",
        fail_after_snapshot,
    )
    with pytest.raises(OSError, match="post-snapshot failure"):
        store.run_text_batch(
            study_id,
            version_id,
            first_request,
            batch_id=batch_id,
        )
    monkeypatch.setattr(
        StudyBatchOperationStore,
        "advance_item",
        original_advance_item,
    )

    batch = store.run_text_batch(
        study_id,
        version_id,
        reordered_request,
        batch_id=batch_id,
    )

    assert batch.batch_id == batch_id
    operation = StudyBatchOperationStore(tmp_path, study_id).get_operation(batch_id)
    assert operation["status"] == "completed"
    assert operation["attempt_count"] == 2


def test_study_batch_refuses_unjournaled_existing_artifacts_before_side_effects(
    tmp_path: Path,
) -> None:
    store, study_id, version_id, transcripts = _journal_batch_fixture(tmp_path)
    batch_id = "batch_20260729042424_8765dcba"
    batch = store.run_text_batch(
        study_id,
        version_id,
        transcripts,
        batch_id=batch_id,
    )
    journal = StudyBatchOperationStore(tmp_path, study_id)
    journal.db_path.unlink()
    run_paths_before = set((batch.aggregate_dir / "runs").glob("*.json"))
    import_ids_before = {
        record.import_id
        for record in EvidenceCatalog(tmp_path).workspace_import_records(study_id)
    }

    with pytest.raises(
        StudyBatchSnapshotConflict,
        match="artifacts exist without a journal",
    ):
        store.run_text_batch(
            study_id,
            version_id,
            transcripts,
            batch_id=batch_id,
        )

    assert set((batch.aggregate_dir / "runs").glob("*.json")) == run_paths_before
    assert {
        record.import_id
        for record in EvidenceCatalog(tmp_path).workspace_import_records(study_id)
    } == import_ids_before
    with pytest.raises(FileNotFoundError):
        StudyBatchOperationStore(tmp_path, study_id).get_operation(batch_id)


def test_study_batch_journal_does_not_store_source_or_error_content(
    tmp_path: Path,
) -> None:
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Journal Privacy Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "journal_privacy_pack",
            "name": "Journal Privacy Pack",
            "version": "1.0.0",
            "metrics": ["secret_error_detail_metric"],
        },
        validate=False,
    )
    batch = store.run_text_batch(
        study.id,
        version.version_id,
        [
            {
                "source_filename": "secret_patient_filename.txt",
                "content": "secret transcript sentence only for privacy proof",
                "metadata": {"private_note": "secret metadata value"},
            }
        ],
    )

    database_text = StudyBatchOperationStore(
        tmp_path,
        study.id,
    ).db_path.read_bytes().decode("utf-8", errors="ignore")
    assert batch.failure_count == 1
    assert "secret_patient_filename" not in database_text
    assert "secret transcript sentence" not in database_text
    assert "secret metadata value" not in database_text
    assert "secret_error_detail_metric" not in database_text


def test_study_batch_hard_interruption_remains_visible_and_blocked(
    tmp_path: Path,
) -> None:
    store, study_id, version_id, transcripts = _journal_batch_fixture(tmp_path)
    batch_id = "batch_20260729101010_bbbbcccc"
    script = """
import os
import sys
from backend.storage.source_blob_store import SourceBlobStore
from backend.storage.study_store import StudyWorkspaceStore

original_store = SourceBlobStore.store

def stop_after_blob(self, content, expected_sha256):
    original_store(self, content, expected_sha256)
    os._exit(29)

SourceBlobStore.store = stop_after_blob
StudyWorkspaceStore(sys.argv[1]).run_text_batch(
    sys.argv[2],
    sys.argv[3],
    [{
        "source_filename": "session.txt",
        "content": "P1_c: Hello.\\nP1_p: Hi.",
        "metadata": {"participant_id": "P1"},
    }],
    batch_id=sys.argv[4],
)
"""

    process = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(tmp_path),
            study_id,
            version_id,
            batch_id,
        ],
        cwd=Path(__file__).parents[1],
        check=False,
    )

    journal = StudyBatchOperationStore(tmp_path, study_id)
    operation = journal.get_operation(batch_id)
    item = journal.list_items(batch_id)[0]
    assert process.returncode == 29
    assert operation["status"] == "running"
    assert operation["stage"] == "prepared"
    assert item["stage"] == "analysis_completed"
    assert (
        tmp_path
        / "source_blobs"
        / "sha256"
        / item["source_blob_sha256"][:2]
        / f"{item['source_blob_sha256']}.blob"
    ).is_file()
    assert not list(
        (
            tmp_path / "studies" / study_id / "batches" / batch_id / "runs"
        ).glob("*.json")
    )
    with pytest.raises(StudyBatchOperationConflict, match="already running"):
        store.run_text_batch(
            study_id,
            version_id,
            transcripts,
            batch_id=batch_id,
        )


def _journal_batch_fixture(
    root: Path,
) -> tuple[StudyWorkspaceStore, str, str, list[dict[str, object]]]:
    store = StudyWorkspaceStore(root)
    study = store.create_study({"name": "Journal Integration Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "journal_integration_pack",
            "name": "Journal Integration Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    return (
        store,
        study.id,
        version.version_id,
        [
            {
                "source_filename": "session.txt",
                "content": "P1_c: Hello.\nP1_p: Hi.",
                "metadata": {"participant_id": "P1"},
            }
        ],
    )
