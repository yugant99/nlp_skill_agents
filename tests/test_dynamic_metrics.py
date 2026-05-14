from backend.analysis.pipeline import execute_analysis
from backend.analysis.transcripts import StudyConfig


def test_dynamic_concept_metrics_count_researcher_defined_lexicons() -> None:
    run = execute_analysis(
        "CG: Does your back hurt today? [pause]\n"
        "P: Um, the pain hurts when I walk. [laughs]\n"
        "CG: Did you take the pill?",
        StudyConfig(
            speaker_prefixes={
                "caregiver": ["CG"],
                "participant": ["P"],
            },
            selected_metrics=["concept_count_metrics"],
            concept_lexicons={
                "pain": ["pain", "hurt", "hurts"],
                "medication": ["pill", "dose"],
            },
            disfluency_tokens=["um"],
        ),
        source_filename="dynamic.txt",
    )

    result = run.results[0]

    assert result.metric_id == "concept_count_metrics"
    assert result.rows == [
        {
            "concept": "pain",
            "match_count": 3,
            "turn_count": 2,
            "speakers": "caregiver, participant",
            "rate_per_100_words": 18.75,
            "examples": ["hurt", "pain", "hurts"],
        },
        {
            "concept": "medication",
            "match_count": 1,
            "turn_count": 1,
            "speakers": "caregiver",
            "rate_per_100_words": 6.25,
            "examples": ["pill"],
        },
    ]


def test_dynamic_cue_inventory_counts_researcher_defined_nonverbals() -> None:
    run = execute_analysis(
        "CG: Tell me about breakfast. [long pause]\n"
        "P: I ate cereal. [laughs]\n"
        "CG: Okay. [silence]",
        StudyConfig(
            speaker_prefixes={
                "caregiver": ["CG"],
                "participant": ["P"],
            },
            selected_metrics=["cue_inventory_metrics"],
            nonverbal_cues={
                "pause": ["pause", "long pause", "silence"],
                "laughter": ["laughs", "laughing", "chuckles"],
                "gesture": ["points"],
            },
        ),
        source_filename="dynamic.txt",
    )

    result = run.results[0]

    assert result.metric_id == "cue_inventory_metrics"
    assert result.rows == [
        {
            "cue": "pause",
            "match_count": 2,
            "turn_count": 2,
            "speakers": "caregiver",
            "examples": ["long pause", "silence"],
        },
        {
            "cue": "laughter",
            "match_count": 1,
            "turn_count": 1,
            "speakers": "participant",
            "examples": ["laughs"],
        },
        {
            "cue": "gesture",
            "match_count": 0,
            "turn_count": 0,
            "speakers": "",
            "examples": [],
        },
    ]
