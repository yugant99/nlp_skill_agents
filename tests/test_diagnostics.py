from backend.analysis.diagnostics import analyze_transcript_quality
from backend.analysis.transcripts import StudyConfig, parse_transcript


def test_diagnostics_warn_when_no_turns_are_found() -> None:
    transcript = parse_transcript(
        "This transcript has no speaker prefixes.",
        StudyConfig(participant_id="vr020"),
        "bad.txt",
    )

    diagnostics = analyze_transcript_quality(transcript)

    assert diagnostics.turn_counts == {"caregiver": 0, "participant": 0}
    assert diagnostics.warnings == [
        {
            "code": "no_turns_found",
            "message": "No speaker turns were detected. Check participant ID and speaker prefixes.",
        }
    ]


def test_diagnostics_report_turn_counts_and_missing_roles() -> None:
    transcript = parse_transcript(
        "vr021_c: Hello.\nvr021_c: Are you there?",
        StudyConfig(participant_id="vr021"),
        "one_speaker.txt",
    )

    diagnostics = analyze_transcript_quality(transcript)

    assert diagnostics.turn_counts == {"caregiver": 2, "participant": 0}
    assert diagnostics.warnings == [
        {
            "code": "missing_role_turns",
            "message": "No turns detected for participant.",
        }
    ]

