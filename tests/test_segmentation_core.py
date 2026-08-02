import json
from pathlib import Path

import pytest

from backend.segmentation.descript import extract_descript_events
from backend.segmentation.evaluator import evaluate_segmented_draft
from backend.segmentation.rulebook import (
    METHOD_AREAS,
    SUPPORTED_RULE_IDS,
    build_cunit_rulebook_summary,
)
from backend.segmentation.synthetic import build_synthetic_case, list_synthetic_cases


def test_semantic_cunit_adjudicator_classifies_boundary_decisions() -> None:
    from backend.segmentation.adjudicator import adjudicate_cunit_boundaries

    events = extract_descript_events(
        """
        [00:00:00] P: I picked up the cup and I moved it to the tray.
        [00:00:04] P: Because it was full.
        [00:00:06] P: Yes.
        [00:00:08] P: Uh, I wa want [unintelligible].
        [00:00:11] Av: What happened next?
        """,
        source_filename="semantic_fixture.txt",
    )

    adjudication = adjudicate_cunit_boundaries(events)

    assert adjudication.participant_turn_count == 4
    assert adjudication.examiner_turn_count == 1
    assert adjudication.counted_cunit_count == 3
    assert adjudication.needs_review_count == 2
    assert adjudication.validation_status == "not_domain_validated"
    assert adjudication.cunit_text_contract_version == 1
    assert adjudication.evidence_scope == (
        "deterministic_heuristics_and_synthetic_fixtures"
    )
    assert all(
        decision.confidence_status == "not_calibrated"
        for decision in adjudication.decisions
    )
    assert all(
        decision.passage_id.startswith("psg_")
        for decision in adjudication.decisions
    )
    assert adjudication.boundary_type_counts == {
        "coordination-split": 1,
        "dependent-clause-attachment": 1,
        "ellipsis-minimal-response": 1,
        "maze-revision-unintelligible": 1,
        "examiner-prompt": 1,
    }

    coordination = adjudication.decisions[0]
    assert coordination.boundary_type == "coordination-split"
    assert coordination.cunit_count == 2
    assert len(coordination.cunit_ids) == 2
    assert len(set(coordination.cunit_ids)) == 2
    assert coordination.cunit_texts == [
        "I picked up the cup",
        "and I moved it to the tray.",
    ]
    assert "coordinate clause" in coordination.rationale

    dependent = adjudication.decisions[1]
    assert dependent.boundary_type == "dependent-clause-attachment"
    assert dependent.cunit_count == 0
    assert dependent.cunit_ids == []
    assert dependent.cunit_texts == []
    assert dependent.needs_human_review is True

    minimal = adjudication.decisions[2]
    assert minimal.cunit_ids
    assert minimal.cunit_texts == ["Yes."]

    maze = adjudication.decisions[3]
    assert maze.boundary_type == "maze-revision-unintelligible"
    assert maze.excluded_maze == "Uh, wa"
    assert maze.needs_human_review is True

    assert all(
        decision.cunit_count
        == len(decision.cunit_ids)
        == len(decision.cunit_texts)
        for decision in adjudication.decisions
    )


def test_cunit_coordination_uses_first_exact_match_and_rejects_empty_unit() -> None:
    from backend.segmentation.adjudicator import adjudicate_cunit_boundaries

    events = extract_descript_events(
        """
        [00:00:00] P: I moved BUT She stayed and he left.
        [00:00:03] P: And I moved the cup.
        """,
        source_filename="coordination_fixture.txt",
    )

    adjudication = adjudicate_cunit_boundaries(events)

    first, leading = adjudication.decisions
    assert first.cunit_texts == [
        "I moved",
        "BUT She stayed and he left.",
    ]
    assert first.cunit_count == 2
    assert leading.boundary_type == "coordination-split"
    assert leading.decision == "needs-review"
    assert leading.cunit_count == 0
    assert leading.cunit_ids == []
    assert leading.cunit_texts == []
    assert leading.needs_human_review is True


def test_semantic_cunit_adjudicator_counts_nominal_subject_clauses() -> None:
    from backend.segmentation.adjudicator import adjudicate_cunit_boundaries

    events = extract_descript_events(
        """
        [00:00:00] P: Good morning, Mira.
        [00:00:02] P: The blue cup is beside the plate.
        [00:00:04] P: The water might spill.
        [00:00:06] P: It feels lighter.
        """,
        source_filename="nominal_subject_fixture.txt",
    )

    adjudication = adjudicate_cunit_boundaries(events)

    assert adjudication.counted_cunit_count == 4
    assert adjudication.needs_review_count == 0
    assert [decision.boundary_type for decision in adjudication.decisions] == [
        "formulaic-communicative-unit",
        "independent-clause",
        "independent-clause",
        "independent-clause",
    ]


def test_rulebook_limits_claims_to_synthetic_fixture_coverage() -> None:
    summary = build_cunit_rulebook_summary()

    assert SUPPORTED_RULE_IDS == [
        "speaker-markers",
        "timestamp-markers",
        "pause-markers",
        "filled-pauses",
        "overlap-markers",
        "abandoned-utterance",
        "redaction-comments",
        "omission-markers",
        "communicative-nonverbal",
        "official-source-guard",
    ]
    assert summary.implemented_rule_count == 10
    assert summary.tracked_fixture_rule_count == 9
    assert summary.generated_fixture_rule_count == 10
    assert summary.validation.status == "not_domain_validated"
    assert summary.validation.evidence_scope == (
        "tracked_and_generated_synthetic_fixtures"
    )
    assert "not accuracy" in summary.validation.claim_boundary
    assert any(
        "Agreement with expert human" in limitation
        for limitation in summary.validation.limitations
    )
    assert any(area.area_id == "cunit-boundaries" for area in METHOD_AREAS)
    assert any(
        area.status == "implemented-unvalidated"
        and "independent clause" in area.scientist_language.lower()
        for area in METHOD_AREAS
    )
    assert any(
        area.area_id == "ellipsis-minimal-response"
        and area.status == "implemented-unvalidated"
        for area in METHOD_AREAS
    )


def test_synthetic_case_contains_descript_input_gold_target_and_no_official_tokens() -> None:
    case = build_synthetic_case("pause_overlap_repair")

    assert case.case_id == "pause_overlap_repair"
    assert "[00:00:00]" in case.descript_text
    assert "P:" in case.descript_text
    assert "-0:00" in case.gold_text
    assert "; :02" in case.gold_text
    assert "([FP])" in case.gold_text
    assert "<" in case.gold_text
    assert ">" in case.gold_text
    assert case.forbidden_source_tokens == []

    combined = f"{case.descript_text}\n{case.gold_text}".lower()
    for token in ["nala", "james", "falcon", "lady bird", "call me"]:
        assert token not in combined


def test_list_synthetic_cases_returns_demo_ready_rule_coverage() -> None:
    cases = list_synthetic_cases()

    assert [case.case_id for case in cases] == [
        "pause_overlap_repair",
        "redaction_omission_nonverbal",
    ]
    assert all(case.gold_text.strip() for case in cases)
    assert {rule for case in cases for rule in case.rule_ids} >= {
        "speaker-markers",
        "timestamp-markers",
        "pause-markers",
        "filled-pauses",
        "overlap-markers",
        "abandoned-utterance",
        "redaction-comments",
        "omission-markers",
        "communicative-nonverbal",
    }


def test_synthetic_demo_transcripts_are_large_enough_for_scientist_demo() -> None:
    cases = list_synthetic_cases()

    for case in cases:
        descript_lines = [line for line in case.descript_text.splitlines() if line.strip()]
        gold_lines = [line for line in case.gold_text.splitlines() if line.strip()]

        assert len(descript_lines) >= 65, case.case_id
        assert len(gold_lines) >= 65, case.case_id


def test_default_synthetic_case_exercises_semantic_cunit_decisions() -> None:
    from backend.segmentation.adjudicator import adjudicate_cunit_boundaries

    case = build_synthetic_case("pause_overlap_repair")
    events = extract_descript_events(case.descript_text, source_filename="demo.txt")

    adjudication = adjudicate_cunit_boundaries(events)

    assert adjudication.boundary_type_counts["coordination-split"] >= 1
    assert adjudication.boundary_type_counts["dependent-clause-attachment"] >= 1
    assert adjudication.boundary_type_counts["ellipsis-minimal-response"] >= 1
    assert adjudication.boundary_type_counts["maze-revision-unintelligible"] >= 1


def test_extract_descript_events_reads_timestamped_speaker_turns() -> None:
    events = extract_descript_events(
        """
        [00:00:00] P: Good morning, Mira. I am [redacted].
        [00:00:04] Av: Uh, yes, start here.
        [00:00:09] P: We are going to get up now.
        """,
        source_filename="synthetic_descript.txt",
    )

    assert [(event.timestamp_seconds, event.speaker, event.text) for event in events] == [
        (0, "P", "Good morning, Mira. I am [redacted]."),
        (4, "Av", "Uh, yes, start here."),
        (9, "P", "We are going to get up now."),
    ]


def test_evaluator_counts_configured_synthetic_rule_checks() -> None:
    case = build_synthetic_case("redaction_omission_nonverbal")

    evaluation = evaluate_segmented_draft(
        case.gold_text,
        expected_rule_ids=case.rule_ids,
        forbidden_tokens=case.official_source_guard_tokens,
    )

    assert evaluation.configured_rule_count == len(set(case.rule_ids)) + 1
    assert evaluation.passed_rule_count == evaluation.configured_rule_count
    assert evaluation.failures == []
    assert evaluation.metrics.utterance_count >= 65
    assert evaluation.metrics.time_marker_count >= 2
    assert evaluation.metrics.pause_marker_count >= 2
    assert evaluation.metrics.speaker_counts["P"] >= 30
    assert evaluation.metrics.speaker_counts["Av"] >= 30
    assert evaluation.metrics.speaker_counts["AvN"] >= 1
    assert evaluation.metrics.special_notation_counts["redaction_comments"] == 1
    assert evaluation.metrics.special_notation_counts["omission_markers"] == 2
    assert evaluation.metrics.special_notation_counts["filled_pauses"] == 1


def test_evaluator_flags_rule_failures_and_official_source_leakage() -> None:
    bad_draft = """
    Synthetic scenario
    ; :02
    P: I want to see Nala [redacted]
    avatar: uh okay
    """

    evaluation = evaluate_segmented_draft(
        bad_draft,
        expected_rule_ids=[
            "timestamp-markers",
            "redaction-comments",
            "speaker-markers",
            "filled-pauses",
        ],
        forbidden_tokens=["nala", "james"],
    )

    failure_ids = {failure.rule_id for failure in evaluation.failures}
    assert evaluation.configured_rule_count == 5
    assert evaluation.passed_rule_count < evaluation.configured_rule_count
    assert failure_ids >= {
        "timestamp-markers",
        "redaction-comments",
        "speaker-markers",
        "filled-pauses",
        "official-source-guard",
    }


def test_synthetic_corpus_generates_stable_rule_cases_without_source_leakage() -> None:
    from backend.segmentation.corpus import generate_synthetic_corpus

    first = generate_synthetic_corpus(seed=7)
    second = generate_synthetic_corpus(seed=7)

    assert [case.case_id for case in first] == [case.case_id for case in second]
    assert {rule for case in first for rule in case.rule_ids} >= {
        "speaker-markers",
        "timestamp-markers",
        "pause-markers",
        "filled-pauses",
        "overlap-markers",
        "abandoned-utterance",
        "redaction-comments",
        "omission-markers",
        "communicative-nonverbal",
    }
    safe_cases = [
        case for case in first if "official-source-guard" not in case.rule_ids
    ]
    for case in safe_cases:
        combined = f"{case.descript_text}\n{case.gold_text}".lower()
        for token in case.official_source_guard_tokens:
            assert token.lower() not in combined


def test_rule_specialist_pipeline_plans_patches_merges_and_verifies(
    tmp_path: Path,
) -> None:
    from backend.segmentation.pipeline import SegmentationRunStore
    from backend.storage.evidence_target_registry import EvidenceTargetRegistry

    store = SegmentationRunStore(tmp_path)
    run = store.create_run(
        source_filename="session.txt",
        descript_text="[00:00:00] P: Good morning.\n[00:00:03] Av: Uh yes.",
        rule_ids=[
            "speaker-markers",
            "timestamp-markers",
            "pause-markers",
            "filled-pauses",
        ],
    )

    assert run.status == "verified"
    assert run.source == "researcher_provided"
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
    assert all(event.passage_id.startswith("psg_") for event in run.events)
    assert run.merged_draft.startswith("Researcher-provided transcript: session")
    assert [packet.specialist_id for packet in run.rule_plan] == [
        "speaker_turn",
        "timing_pause",
        "repair_overlap",
    ]
    assert run.merged_draft
    assert run.cunit_adjudication is not None
    assert run.cunit_adjudication.counted_cunit_count >= 1
    assert run.cunit_adjudication.decisions[0].rationale
    assert run.evaluation is not None
    assert run.evaluation.passed_rule_count == run.evaluation.configured_rule_count
    assert all(output.patches for output in run.specialist_outputs)
    for output in run.specialist_outputs:
        artifact_path = Path(str(output.evidence["artifact_path"]))
        assert artifact_path.exists()
        assert "Do not rewrite the full transcript" in artifact_path.read_text(
            encoding="utf-8"
        )
    assert (tmp_path / "segmentation_runs" / f"{run.run_id}.json").exists()

    loaded = store.load_run(run.run_id)

    assert loaded.run_id == run.run_id
    assert loaded.status == run.status
    assert loaded.evidence_set_id == run.evidence_set_id
    first_decision = run.cunit_adjudication.decisions[0]
    resolved = EvidenceTargetRegistry(tmp_path).resolve(
        run.workspace_id,
        run.project_source_id,
        run.transcript_revision_id,
        run.evidence_set_id,
        run.events[0].passage_id,
        first_decision.cunit_ids[0],
    )
    assert resolved.target_kind == "cunit"
    assert resolved.text == first_decision.cunit_texts[0]
    assert resolved.producer_kind == "cunit_segmentation"
    assert resolved.producer_version == 1
    assert resolved.producer_status == "verified"
    assert resolved.review_status == "not_domain_validated"

    repeated = store.create_run(
        source_filename="renamed-session.txt",
        descript_text="[00:00:00] P: Good morning.\n[00:00:03] Av: Uh yes.",
        rule_ids=[
            "speaker-markers",
            "timestamp-markers",
            "pause-markers",
            "filled-pauses",
        ],
    )
    assert repeated.run_id != run.run_id
    assert repeated.import_id != run.import_id
    assert repeated.project_source_id != run.project_source_id
    assert repeated.source_blob_sha256 == run.source_blob_sha256
    assert repeated.source_id == run.source_id
    assert repeated.transcript_sha256 == run.transcript_sha256
    assert repeated.transcript_revision_id == run.transcript_revision_id
    assert [event.passage_id for event in repeated.events] == [
        event.passage_id for event in run.events
    ]
    assert [
        decision.cunit_ids for decision in repeated.cunit_adjudication.decisions
    ] == [
        decision.cunit_ids for decision in run.cunit_adjudication.decisions
    ]


def test_segmentation_run_store_lists_runs_and_writes_exports(tmp_path: Path) -> None:
    from backend.segmentation.pipeline import SegmentationRunStore
    from backend.storage.source_blob_store import SourceBlobStore

    store = SegmentationRunStore(tmp_path)
    run = store.create_run(
        source_filename="session.txt",
        descript_text="[00:00:00] P: Good morning.\n[00:00:03] Av: Uh yes.",
        rule_ids=[
            "speaker-markers",
            "timestamp-markers",
            "pause-markers",
            "filled-pauses",
        ],
    )

    runs = store.list_runs()
    transcript_path = store.write_final_transcript(run.run_id)
    evidence_path = store.write_evidence_bundle(run.run_id)

    assert [item.run_id for item in runs] == [run.run_id]
    assert transcript_path.read_text(encoding="utf-8") == run.merged_draft
    assert SourceBlobStore(tmp_path).read_verified(run.source_blob_sha256) == (
        b"[00:00:00] P: Good morning.\n[00:00:03] Av: Uh yes."
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["run_id"] == run.run_id
    assert evidence["import_id"] == run.import_id
    assert evidence["source_blob_sha256"] == run.source_blob_sha256
    assert evidence["source_media_type"] == run.source_media_type
    assert evidence["source_id"] == run.source_id
    assert evidence["transcript_sha256"] == run.transcript_sha256
    assert evidence["transcript_revision_id"] == run.transcript_revision_id
    assert evidence["evidence_set_id"] == run.evidence_set_id
    assert evidence["cunit_adjudication"]["counted_cunit_count"] >= 1
    assert evidence["cunit_adjudication"]["decisions"][0]["boundary_type"]
    assert evidence["cunit_adjudication"]["decisions"][0]["passage_id"]
    assert evidence["cunit_adjudication"]["decisions"][0]["cunit_ids"]
    assert evidence["evaluation"]["passed_rule_count"] == evidence["evaluation"][
        "configured_rule_count"
    ]
    assert "score" not in evidence["evaluation"]
    assert "confidence" not in evidence["cunit_adjudication"]["decisions"][0]


def test_segmentation_store_records_and_validates_revision_lineage(
    tmp_path: Path,
) -> None:
    from backend.segmentation.pipeline import SegmentationRunStore
    from backend.storage.evidence_catalog import EvidenceCatalog

    store = SegmentationRunStore(tmp_path)
    first = store.create_run(
        source_filename="session.txt",
        descript_text="[00:00:00] P: I start here.",
        rule_ids=["speaker-markers"],
    )
    second = store.create_run(
        source_filename="session-revised.txt",
        descript_text="[00:00:00] P: I start over here.",
        rule_ids=["speaker-markers"],
        project_source_id=first.project_source_id,
        parent_transcript_revision_id=first.transcript_revision_id,
    )

    history = EvidenceCatalog(tmp_path).source_history(first.project_source_id)
    assert second.project_source_id == first.project_source_id
    assert second.parent_transcript_revision_id == first.transcript_revision_id
    assert [item["transcript_revision_id"] for item in history["revisions"]] == [
        first.transcript_revision_id,
        second.transcript_revision_id,
    ]

    with pytest.raises(ValueError, match="Parent revision does not belong"):
        store.create_run(
            source_filename="invalid.txt",
            descript_text="[00:00:00] P: This parent is invalid.",
            rule_ids=["speaker-markers"],
            project_source_id=first.project_source_id,
            parent_transcript_revision_id="trv_missing",
        )
    assert len(list((tmp_path / "segmentation_runs").glob("*.json"))) == 2


def test_segmentation_corpus_run_store_runs_generated_cases_and_summarizes_coverage(
    tmp_path: Path,
) -> None:
    from backend.segmentation.pipeline import SegmentationRunStore

    store = SegmentationRunStore(tmp_path)
    corpus_run = store.create_corpus_run(seed=11)

    assert corpus_run.status == "passed"
    assert corpus_run.total_case_count == 4
    assert corpus_run.regression_pass_count == 4
    assert corpus_run.regression_fail_count == 0
    assert set(corpus_run.rule_coverage) >= {
        "speaker-markers",
        "timestamp-markers",
        "pause-markers",
        "filled-pauses",
        "overlap-markers",
        "abandoned-utterance",
        "redaction-comments",
        "omission-markers",
        "communicative-nonverbal",
        "official-source-guard",
    }

    guard_result = next(
        result
        for result in corpus_run.results
        if result.case_id == "corpus_official_source_leakage_negative"
    )
    assert guard_result.expected_status == "failed"
    assert guard_result.status == "failed"
    assert guard_result.outcome == "passed"
    assert guard_result.failed_rule_ids == ["official-source-guard"]

    loaded = store.load_corpus_run(corpus_run.corpus_run_id)
    listed = store.list_corpus_runs()

    first_run = store.load_run(corpus_run.results[0].run_id)
    assert first_run.source == "synthetic"
    assert first_run.merged_draft.startswith("Synthetic run:")

    assert loaded.corpus_run_id == corpus_run.corpus_run_id
    assert [item.corpus_run_id for item in listed] == [corpus_run.corpus_run_id]


def test_segmentation_run_store_remerges_submitted_specialist_patches(
    tmp_path: Path,
) -> None:
    from backend.segmentation.pipeline import PatchOperation, SegmentationRunStore

    store = SegmentationRunStore(tmp_path)
    run = store.create_run(
        source_filename="session.txt",
        descript_text="[00:00:00] P: Good morning.\n[00:00:03] Av: Uh yes.",
        rule_ids=[
            "speaker-markers",
            "timestamp-markers",
            "pause-markers",
            "filled-pauses",
        ],
    )
    original_timing_patches = next(
        output.patches
        for output in run.specialist_outputs
        if output.specialist_id == "timing_pause"
    )

    updated = store.apply_specialist_patches(
        run.run_id,
        specialist_id="timing_pause",
        patches=[
            PatchOperation(
                operation="insert_before_event",
                event_index=0,
                text="-0:00",
                reason="submitted by timing/pause agent",
            )
        ],
    )

    assert updated.status == "needs_rewrite"
    assert updated.evidence_set_id == ""
    assert updated.source == "researcher_provided"
    assert "; :03" not in updated.merged_draft
    assert updated.failure_routes[0]["rule_id"] == "pause-markers"
    assert updated.failure_routes[0]["specialist_id"] == "timing_pause"
    timing_output = next(
        output
        for output in updated.specialist_outputs
        if output.specialist_id == "timing_pause"
    )
    assert timing_output.evidence["submitted_by"] == "specialist_agent"
    loaded = store.load_run(run.run_id)
    assert loaded.merged_draft == updated.merged_draft
    assert loaded.source == "researcher_provided"

    repaired = store.apply_specialist_patches(
        run.run_id,
        specialist_id="timing_pause",
        patches=original_timing_patches,
    )
    assert repaired.status == "verified"
    assert repaired.cunit_adjudication.cunit_text_contract_version == 1
    assert repaired.evidence_set_id == ""

    reverified = store.verify_run(run.run_id)
    assert reverified.evidence_set_id == run.evidence_set_id


def test_segmentation_run_store_defaults_legacy_payloads_to_synthetic(
    tmp_path: Path,
) -> None:
    from backend.segmentation.pipeline import (
        PatchOperation,
        SegmentationRunStore,
        _payload_sha256,
    )
    from backend.storage.segmentation_operation_store import SegmentationOperationStore

    store = SegmentationRunStore(tmp_path)
    run = store.create_run(
        source_filename="legacy.txt",
        descript_text="[00:00:00] P: Good morning.",
        rule_ids=["speaker-markers"],
    )
    run_path = tmp_path / "segmentation_runs" / f"{run.run_id}.json"
    payload = json.loads(run_path.read_text(encoding="utf-8"))
    payload.pop("source")
    payload.pop("import_id")
    payload.pop("project_source_id")
    payload.pop("parent_transcript_revision_id")
    payload.pop("workspace_id")
    payload.pop("source_blob_sha256")
    payload.pop("source_media_type")
    payload.pop("source_id")
    payload.pop("transcript_sha256")
    payload.pop("transcript_revision_id")
    payload.pop("evidence_set_id")
    for event in payload["events"]:
        event.pop("passage_id")
    payload["evaluation"]["score"] = 100
    payload["evaluation"].pop("configured_rule_count")
    payload["evaluation"].pop("passed_rule_count")
    payload["cunit_adjudication"].pop("validation_status")
    payload["cunit_adjudication"].pop("evidence_scope")
    payload["cunit_adjudication"].pop("cunit_text_contract_version")
    for decision in payload["cunit_adjudication"]["decisions"]:
        decision["confidence"] = 0.82
        decision.pop("confidence_status")
        decision.pop("passage_id")
        decision.pop("cunit_ids")
        decision.pop("cunit_texts")
    legacy_payload_sha256 = _payload_sha256(payload)
    run_path.write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "segmentation.sqlite3").unlink()

    loaded = store.load_run(run.run_id)
    assert loaded.source == "synthetic"
    assert loaded.import_id == f"imp_legacy_{run.run_id}"
    assert loaded.project_source_id == f"psrc_legacy_imp_legacy_{run.run_id}"
    assert loaded.parent_transcript_revision_id == ""
    assert loaded.workspace_id == "legacy"
    assert loaded.source_blob_sha256 == ""
    assert loaded.source_media_type == "unknown"
    assert loaded.source_id.startswith("src_")
    assert len(loaded.transcript_sha256) == 64
    assert loaded.transcript_revision_id.startswith("trv_")
    assert all(event.passage_id.startswith("psg_") for event in loaded.events)
    assert loaded.evaluation is not None
    assert loaded.evaluation.configured_rule_count == 1
    assert loaded.evaluation.passed_rule_count == 1
    assert loaded.cunit_adjudication.validation_status == "not_domain_validated"
    assert loaded.cunit_adjudication.cunit_text_contract_version == 0
    assert loaded.evidence_set_id == ""
    assert all(
        decision.confidence_status == "not_calibrated"
        for decision in loaded.cunit_adjudication.decisions
    )
    assert all(
        decision.passage_id.startswith("psg_")
        for decision in loaded.cunit_adjudication.decisions
    )
    assert all(
        decision.cunit_count
        == len(decision.cunit_ids)
        == len(decision.cunit_texts)
        for decision in loaded.cunit_adjudication.decisions
    )

    store.persist_run(
        loaded,
        operation_kind="rewrite",
        expected_previous_payload_sha256=legacy_payload_sha256,
    )
    rewritten = json.loads(run_path.read_text(encoding="utf-8"))
    operations = SegmentationOperationStore(tmp_path).list_operations()
    assert len(operations) == 1
    assert operations[0]["operation_kind"] == "rewrite"
    assert operations[0]["status"] == "completed"
    assert operations[0]["previous_payload_sha256"] != operations[0][
        "payload_sha256"
    ]
    assert "score" not in rewritten["evaluation"]
    assert "confidence" not in rewritten["cunit_adjudication"]["decisions"][0]
    assert rewritten["source_id"] == loaded.source_id
    assert rewritten["import_id"] == loaded.import_id
    assert rewritten["project_source_id"] == loaded.project_source_id
    assert rewritten["source_blob_sha256"] == ""
    assert rewritten["events"][0]["passage_id"]
    assert rewritten["cunit_adjudication"]["decisions"][0]["passage_id"]
    assert rewritten["cunit_adjudication"]["cunit_text_contract_version"] == 0
    assert rewritten["cunit_adjudication"]["decisions"][0]["cunit_texts"]
    assert rewritten["evidence_set_id"] == ""

    patched = store.apply_specialist_patches(
        run.run_id,
        specialist_id="speaker_turn",
        patches=[
            PatchOperation(
                operation="event_line",
                event_index=0,
                text="P: Legacy compatibility greeting.",
                reason="prove patch does not promote the text contract",
            )
        ],
    )
    assert patched.cunit_adjudication.cunit_text_contract_version == 0
    assert patched.evidence_set_id == ""

    verified = store.verify_run(run.run_id)
    assert verified.cunit_adjudication.cunit_text_contract_version == 1
    assert verified.evidence_set_id == ""


def test_segmentation_explicit_verify_registers_current_legacy_snapshot(
    tmp_path: Path,
) -> None:
    from backend.segmentation.pipeline import PatchOperation, SegmentationRunStore
    from backend.storage.evidence_target_registry import EvidenceTargetRegistry

    store = SegmentationRunStore(tmp_path)
    created = store.create_run(
        source_filename="pre-registry.txt",
        descript_text="[00:00:00] P: Good morning.",
        rule_ids=["speaker-markers"],
    )
    run_path = tmp_path / "segmentation_runs" / f"{created.run_id}.json"
    payload = json.loads(run_path.read_text(encoding="utf-8"))
    payload.pop("evidence_set_id")
    run_path.write_text(json.dumps(payload), encoding="utf-8")

    compatible = store.load_run(created.run_id)
    assert compatible.evidence_set_id == ""

    patched = store.apply_specialist_patches(
        created.run_id,
        specialist_id="speaker_turn",
        patches=[
            PatchOperation(
                operation="event_line",
                event_index=0,
                text="P: A current-lineage compatibility patch.",
                reason="compatibility loading must not register targets",
            )
        ],
    )
    assert patched.status == "verified"
    assert patched.cunit_adjudication.cunit_text_contract_version == 1
    assert patched.evidence_set_id == ""

    patched_again = store.apply_specialist_patches(
        created.run_id,
        specialist_id="speaker_turn",
        patches=[
            PatchOperation(
                operation="event_line",
                event_index=0,
                text="P: A second current-lineage compatibility patch.",
                reason="repeated patches must not create evidence provenance",
            )
        ],
    )
    assert patched_again.status == "verified"
    assert patched_again.evidence_set_id == ""

    verified = store.verify_run(created.run_id)
    assert verified.evidence_set_id == created.evidence_set_id
    resolved = EvidenceTargetRegistry(tmp_path).resolve(
        verified.workspace_id,
        verified.project_source_id,
        verified.transcript_revision_id,
        verified.evidence_set_id,
        verified.events[0].passage_id,
        verified.cunit_adjudication.decisions[0].cunit_ids[0],
    )
    assert resolved.text == verified.cunit_adjudication.decisions[0].cunit_texts[0]


def test_segmentation_rejects_misaligned_version_one_cunit_targets(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    from backend.segmentation.pipeline import (
        SegmentationRunStore,
        _segmentation_payload_sha256,
        segmentation_run_from_payload,
        segmentation_run_to_payload,
    )

    store = SegmentationRunStore(tmp_path)
    run = store.create_run(
        source_filename="target-contract.txt",
        descript_text="[00:00:00] P: Good morning.",
        rule_ids=["speaker-markers"],
    )
    decision = run.cunit_adjudication.decisions[0]
    malformed_adjudication = replace(
        run.cunit_adjudication,
        decisions=[replace(decision, cunit_texts=[])],
    )
    malformed_run = replace(run, cunit_adjudication=malformed_adjudication)

    with pytest.raises(ValueError, match="count, IDs, and canonical texts"):
        store.persist_run(
            malformed_run,
            operation_kind="rewrite",
            expected_previous_payload_sha256=_segmentation_payload_sha256(run),
        )

    payload = segmentation_run_to_payload(run)
    payload["cunit_adjudication"]["decisions"][0].pop("cunit_texts")
    with pytest.raises(ValueError, match="count, IDs, and canonical texts"):
        segmentation_run_from_payload(payload)

    invalid_id_payload = segmentation_run_to_payload(run)
    invalid_id_payload["evidence_set_id"] = "evs_invalid"
    with pytest.raises(ValueError, match="evidence_set_id is invalid"):
        segmentation_run_from_payload(invalid_id_payload)

    mismatched_id_payload = segmentation_run_to_payload(run)
    mismatched_id_payload["evidence_set_id"] = f"evs_{'0' * 32}"
    with pytest.raises(ValueError, match="does not match the current producer"):
        segmentation_run_from_payload(mismatched_id_payload)

    with pytest.raises(ValueError, match="does not match the current producer"):
        store.persist_run(
            replace(run, evidence_set_id=f"evs_{'0' * 32}"),
            operation_kind="rewrite",
            expected_previous_payload_sha256=_segmentation_payload_sha256(run),
        )

    assert store.load_run(run.run_id) == run
