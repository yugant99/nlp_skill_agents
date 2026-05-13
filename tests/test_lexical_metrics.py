from backend.analysis.metrics import calculate_lexical_metrics
from backend.analysis.transcripts import StudyConfig, parse_transcript


def test_lexical_metrics_calculate_type_token_ratio_by_speaker_and_total() -> None:
    transcript = parse_transcript(
        """
        vr003_c: Um, apple apple banana.
        vr003_p: banana carrot carrot. [pause]
        """,
        StudyConfig(participant_id="vr003"),
        source_filename="vr003.txt",
    )

    result = calculate_lexical_metrics(transcript)

    assert result.metric_id == "lexical_metrics"
    assert result.rows == [
        {
            "speaker": "caregiver",
            "tokens": 3,
            "unique_tokens": 2,
            "type_token_ratio": 0.667,
            "lexical_density": 1.0,
        },
        {
            "speaker": "participant",
            "tokens": 3,
            "unique_tokens": 2,
            "type_token_ratio": 0.667,
            "lexical_density": 1.0,
        },
        {
            "speaker": "total",
            "tokens": 6,
            "unique_tokens": 3,
            "type_token_ratio": 0.5,
            "lexical_density": 1.0,
        },
    ]


def test_lexical_metrics_reports_zeroes_for_empty_transcript() -> None:
    transcript = parse_transcript("", StudyConfig(participant_id="vr004"), "empty.txt")

    result = calculate_lexical_metrics(transcript)

    assert result.rows[-1] == {
        "speaker": "total",
        "tokens": 0,
        "unique_tokens": 0,
        "type_token_ratio": 0.0,
        "lexical_density": 0.0,
    }

