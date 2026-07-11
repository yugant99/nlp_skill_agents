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
