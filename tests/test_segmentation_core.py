import json
from pathlib import Path

from backend.segmentation.descript import extract_descript_events
from backend.segmentation.evaluator import evaluate_segmented_draft
from backend.segmentation.synthetic import build_synthetic_case, list_synthetic_cases


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


def test_evaluator_scores_clean_synthetic_gold_and_exposes_machine_checks() -> None:
    case = build_synthetic_case("redaction_omission_nonverbal")

    evaluation = evaluate_segmented_draft(
        case.gold_text,
        expected_rule_ids=case.rule_ids,
        forbidden_tokens=case.official_source_guard_tokens,
    )

    assert evaluation.score == 100
    assert evaluation.failures == []
    assert evaluation.metrics.utterance_count == 6
    assert evaluation.metrics.time_marker_count == 2
    assert evaluation.metrics.pause_marker_count == 2
    assert evaluation.metrics.speaker_counts == {"P": 3, "Av": 2, "AvN": 1}
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
    assert evaluation.score < 100
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
    assert [packet.specialist_id for packet in run.rule_plan] == [
        "speaker_turn",
        "timing_pause",
        "repair_overlap",
    ]
    assert run.merged_draft
    assert run.evaluation is not None
    assert run.evaluation.score == 100
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


def test_segmentation_run_store_lists_runs_and_writes_exports(tmp_path: Path) -> None:
    from backend.segmentation.pipeline import SegmentationRunStore

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
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["run_id"] == run.run_id
    assert evidence["evaluation"]["score"] == 100


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

    assert loaded.corpus_run_id == corpus_run.corpus_run_id
    assert [item.corpus_run_id for item in listed] == [corpus_run.corpus_run_id]
