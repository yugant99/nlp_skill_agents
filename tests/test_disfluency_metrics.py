from backend.analysis.metrics import calculate_disfluency_metrics
from backend.analysis.transcripts import StudyConfig, parse_transcript


def test_disfluency_metrics_count_tokens_by_speaker_with_examples() -> None:
    transcript = parse_transcript(
        """
        vr005_c: Um, I think uh this is nice.
        vr005_p: Well I, um, I remember it.
        """,
        StudyConfig(participant_id="vr005", disfluency_tokens=["um", "uh", "well"]),
        source_filename="vr005.txt",
    )

    result = calculate_disfluency_metrics(transcript)

    assert result.metric_id == "disfluency_metrics"
    assert result.rows == [
        {
            "speaker": "caregiver",
            "disfluency_count": 2,
            "total_words": 7,
            "disfluency_rate": 0.286,
            "examples": ["um", "uh"],
        },
        {
            "speaker": "participant",
            "disfluency_count": 2,
            "total_words": 6,
            "disfluency_rate": 0.333,
            "examples": ["well", "um"],
        },
        {
            "speaker": "total",
            "disfluency_count": 4,
            "total_words": 13,
            "disfluency_rate": 0.308,
            "examples": ["um", "uh", "well"],
        },
    ]


def test_disfluency_metrics_uses_default_inventory_when_config_is_empty() -> None:
    transcript = parse_transcript(
        "vr006_c: Hmm, that is good.\nvr006_p: okay.",
        StudyConfig(participant_id="vr006"),
        source_filename="vr006.txt",
    )

    result = calculate_disfluency_metrics(transcript)

    assert result.rows[0]["disfluency_count"] == 1
    assert result.rows[0]["examples"] == ["hmm"]

