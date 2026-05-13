from backend.analysis.metrics import calculate_base_metrics
from backend.analysis.transcripts import StudyConfig, parse_transcript


def test_base_metrics_count_turns_words_questions_sentences_and_nonverbals() -> None:
    transcript = parse_transcript(
        """
        vr001_c: Um, look at this picture. [laughter] Do you like it?
        vr001_p: I like the mountain.
        vr001_c: Great. What color is the sky?
        """,
        StudyConfig(participant_id="vr001"),
        source_filename="vr001.txt",
    )

    result = calculate_base_metrics(transcript)

    assert result.metric_id == "base_metrics"
    assert result.rows == [
        {
            "speaker": "caregiver",
            "turns": 2,
            "clean_words": 14,
            "raw_words": 15,
            "sentences": 4,
            "questions": 2,
            "nonverbal_cues": 1,
            "words_per_turn": 7.0,
        },
        {
            "speaker": "participant",
            "turns": 1,
            "clean_words": 4,
            "raw_words": 4,
            "sentences": 1,
            "questions": 0,
            "nonverbal_cues": 0,
            "words_per_turn": 4.0,
        },
        {
            "speaker": "total",
            "turns": 3,
            "clean_words": 18,
            "raw_words": 19,
            "sentences": 5,
            "questions": 2,
            "nonverbal_cues": 1,
            "words_per_turn": 6.0,
        },
    ]


def test_base_metrics_uses_custom_disfluency_tokens_from_config() -> None:
    transcript = parse_transcript(
        "vr002_c: like this is nice.\nvr002_p: yes like very nice.",
        StudyConfig(participant_id="vr002", disfluency_tokens=["like"]),
        source_filename="vr002.txt",
    )

    result = calculate_base_metrics(transcript)

    caregiver = result.rows[0]
    participant = result.rows[1]
    assert caregiver["raw_words"] == 4
    assert caregiver["clean_words"] == 3
    assert participant["raw_words"] == 4
    assert participant["clean_words"] == 3
