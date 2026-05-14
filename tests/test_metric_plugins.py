from fastapi.testclient import TestClient

from backend.analysis.pipeline import execute_analysis, metric_plugin_catalog
from backend.analysis.skill_packs import parse_skill_pack
from backend.analysis.transcripts import StudyConfig
from backend.app.main import app


def test_metric_plugin_catalog_exposes_registered_plugins() -> None:
    catalog = metric_plugin_catalog()

    interaction = next(
        plugin for plugin in catalog if plugin["id"] == "interaction_dynamics_metrics"
    )

    assert interaction == {
        "id": "interaction_dynamics_metrics",
        "label": "Interaction Dynamics Metrics",
        "description": "Turn-taking balance, question share, and longest-turn measures.",
        "category": "Conversation dynamics",
        "output_schema": {
            "speaker": "string",
            "turns": "integer",
            "word_share": "number",
            "question_turns": "integer",
            "avg_words_per_turn": "number",
            "longest_turn_words": "integer",
        },
    }


def test_interaction_dynamics_plugin_runs_through_pipeline() -> None:
    run = execute_analysis(
        "CG: Did pain keep you awake?\n"
        "P: Um, pain kept me awake for two hours.\n"
        "CG: Did medication help?\n"
        "P: Medication helped.",
        StudyConfig(
            speaker_prefixes={"caregiver": ["CG"], "participant": ["P"]},
            selected_metrics=["interaction_dynamics_metrics"],
            disfluency_tokens=["um"],
        ),
        source_filename="interaction.txt",
    )

    assert run.results[0].metric_id == "interaction_dynamics_metrics"
    assert run.results[0].rows == [
        {
            "speaker": "caregiver",
            "turns": 2,
            "word_share": 0.471,
            "question_turns": 2,
            "avg_words_per_turn": 4.0,
            "longest_turn_words": 5,
        },
        {
            "speaker": "participant",
            "turns": 2,
            "word_share": 0.529,
            "question_turns": 0,
            "avg_words_per_turn": 4.5,
            "longest_turn_words": 7,
        },
        {
            "speaker": "total",
            "turns": 4,
            "word_share": 1.0,
            "question_turns": 2,
            "avg_words_per_turn": 4.25,
            "longest_turn_words": 7,
        },
    ]


def test_care_plan_commitment_plugin_counts_future_care_actions() -> None:
    run = execute_analysis(
        "CG: I will call the clinic tomorrow.\n"
        "P: That helps.\n"
        "CG: We can schedule your medication review next week.\n"
        "P: I will try to walk today.",
        StudyConfig(
            speaker_prefixes={"caregiver": ["CG"], "participant": ["P"]},
            selected_metrics=["care_plan_commitment_metrics"],
        ),
        source_filename="care-plan.txt",
    )

    assert run.results[0].metric_id == "care_plan_commitment_metrics"
    assert run.results[0].rows == [
        {
            "speaker": "caregiver",
            "commitment_count": 2,
            "turn_count": 2,
            "commitment_rate": 1.0,
            "examples": [
                "I will call the clinic tomorrow.",
                "We can schedule your medication review next week.",
            ],
        },
        {
            "speaker": "participant",
            "commitment_count": 0,
            "turn_count": 2,
            "commitment_rate": 0.0,
            "examples": [],
        },
        {
            "speaker": "total",
            "commitment_count": 2,
            "turn_count": 4,
            "commitment_rate": 0.5,
            "examples": [
                "I will call the clinic tomorrow.",
                "We can schedule your medication review next week.",
            ],
        },
    ]


def test_question_type_plugin_separates_open_and_yes_no_prompts() -> None:
    run = execute_analysis(
        "CG: How did pain affect sleep?\n"
        "P: It woke me up.\n"
        "CG: Did medication help?\n"
        "P: Can I take it at night?",
        StudyConfig(
            speaker_prefixes={"caregiver": ["CG"], "participant": ["P"]},
            selected_metrics=["question_type_metrics"],
        ),
        source_filename="questions.txt",
    )

    assert run.results[0].metric_id == "question_type_metrics"
    assert run.results[0].rows == [
        {
            "speaker": "caregiver",
            "turns": 2,
            "question_turns": 2,
            "open_question_turns": 1,
            "yes_no_question_turns": 1,
            "question_rate": 1.0,
            "examples": ["How did pain affect sleep?", "Did medication help?"],
        },
        {
            "speaker": "participant",
            "turns": 2,
            "question_turns": 1,
            "open_question_turns": 0,
            "yes_no_question_turns": 1,
            "question_rate": 0.5,
            "examples": ["Can I take it at night?"],
        },
        {
            "speaker": "total",
            "turns": 4,
            "question_turns": 3,
            "open_question_turns": 1,
            "yes_no_question_turns": 2,
            "question_rate": 0.75,
            "examples": [
                "How did pain affect sleep?",
                "Did medication help?",
                "Can I take it at night?",
            ],
        },
    ]


def test_skill_pack_validation_accepts_metric_plugins() -> None:
    pack = parse_skill_pack(
        {
            "id": "interaction_demo",
            "name": "Interaction Demo",
            "version": "0.1.0",
            "metrics": ["interaction_dynamics_metrics"],
            "speaker_roles": {
                "caregiver": {"label": "Caregiver", "prefixes": ["CG"]},
                "participant": {"label": "Participant", "prefixes": ["P"]},
            },
        }
    )

    assert [metric.id for metric in pack.metrics] == ["interaction_dynamics_metrics"]


def test_metric_plugin_catalog_endpoint() -> None:
    response = TestClient(app).get("/api/metric-plugins")

    assert response.status_code == 200
    plugin_ids = [plugin["id"] for plugin in response.json()["plugins"]]
    assert "interaction_dynamics_metrics" in plugin_ids
    assert "care_plan_commitment_metrics" in plugin_ids
    assert "question_type_metrics" in plugin_ids
