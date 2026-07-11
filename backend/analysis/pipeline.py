from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from backend.evidence.identifiers import transcript_evidence_identity

from backend.analysis.metrics import (
    calculate_base_metrics,
    calculate_care_plan_commitment_metrics,
    calculate_concept_count_metrics,
    calculate_cue_inventory_metrics,
    calculate_disfluency_metrics,
    calculate_interaction_dynamics_metrics,
    calculate_lexical_metrics,
    calculate_question_type_metrics,
)
from backend.analysis.metric_plugins import (
    MetricPlugin,
    get_metric_plugin,
    metric_calculators,
    metric_plugin_catalog,
    register_metric_plugin,
)
from backend.analysis.metrics import MetricResult
from backend.analysis.transcripts import StudyConfig, Transcript, parse_transcript


DEFAULT_SELECTED_METRICS = [
    "base_metrics",
    "lexical_metrics",
    "disfluency_metrics",
]

def _register_builtin_metric_plugins() -> None:
    plugins = [
        MetricPlugin(
            id="base_metrics",
            label="Base Metrics",
            description="Turns, clean words, raw words, questions, and nonverbal counts.",
            category="Turn structure",
            output_schema={
                "speaker": "string",
                "turns": "integer",
                "clean_words": "integer",
                "raw_words": "integer",
                "sentences": "integer",
                "questions": "integer",
                "nonverbal_cues": "integer",
                "words_per_turn": "number",
            },
            calculate=calculate_base_metrics,
        ),
        MetricPlugin(
            id="lexical_metrics",
            label="Lexical Metrics",
            description="Token counts, unique tokens, type-token ratio, and lexical density.",
            category="Lexical profile",
            output_schema={
                "speaker": "string",
                "tokens": "integer",
                "unique_tokens": "integer",
                "type_token_ratio": "number",
                "lexical_density": "number",
            },
            calculate=calculate_lexical_metrics,
        ),
        MetricPlugin(
            id="disfluency_metrics",
            label="Disfluency Metrics",
            description="Configured disfluency counts, rates, and examples by speaker.",
            category="Speech markers",
            output_schema={
                "speaker": "string",
                "disfluency_count": "integer",
                "total_words": "integer",
                "disfluency_rate": "number",
                "examples": "string[]",
            },
            calculate=calculate_disfluency_metrics,
        ),
        MetricPlugin(
            id="concept_count_metrics",
            label="Concept Count Metrics",
            description="Researcher-defined concept lexicon counts and examples.",
            category="Research lexicon",
            output_schema={
                "concept": "string",
                "match_count": "integer",
                "turn_count": "integer",
                "speakers": "string",
                "rate_per_100_words": "number",
                "examples": "string[]",
            },
            calculate=calculate_concept_count_metrics,
        ),
        MetricPlugin(
            id="cue_inventory_metrics",
            label="Cue Inventory Metrics",
            description="Configured nonverbal cue counts and examples.",
            category="Nonverbal coding",
            output_schema={
                "cue": "string",
                "match_count": "integer",
                "turn_count": "integer",
                "speakers": "string",
                "examples": "string[]",
            },
            calculate=calculate_cue_inventory_metrics,
        ),
        MetricPlugin(
            id="interaction_dynamics_metrics",
            label="Interaction Dynamics Metrics",
            description="Turn-taking balance, question share, and longest-turn measures.",
            category="Conversation dynamics",
            output_schema={
                "speaker": "string",
                "turns": "integer",
                "word_share": "number",
                "question_turns": "integer",
                "avg_words_per_turn": "number",
                "longest_turn_words": "integer",
            },
            calculate=calculate_interaction_dynamics_metrics,
        ),
        MetricPlugin(
            id="care_plan_commitment_metrics",
            label="Care Plan Commitment Metrics",
            description="Caregiver future-action commitments for healthcare coordination.",
            category="Healthcare interaction",
            output_schema={
                "speaker": "string",
                "commitment_count": "integer",
                "turn_count": "integer",
                "commitment_rate": "number",
                "examples": "string[]",
            },
            calculate=calculate_care_plan_commitment_metrics,
        ),
        MetricPlugin(
            id="question_type_metrics",
            label="Question Type Metrics",
            description="Open and yes/no question patterns by speaker.",
            category="Question asking",
            output_schema={
                "speaker": "string",
                "turns": "integer",
                "question_turns": "integer",
                "open_question_turns": "integer",
                "yes_no_question_turns": "integer",
                "question_rate": "number",
                "examples": "string[]",
            },
            calculate=calculate_question_type_metrics,
        ),
    ]
    for plugin in plugins:
        register_metric_plugin(plugin, replace=True)


_register_builtin_metric_plugins()
METRIC_REGISTRY = metric_calculators()


@dataclass(frozen=True)
class AnalysisRun:
    run_id: str
    source_id: str
    source_sha256: str
    transcript_revision_id: str
    source_filename: str
    created_at: str
    transcript: Transcript
    results: list[MetricResult]


def execute_analysis(
    content: str,
    config: StudyConfig,
    source_filename: str,
) -> AnalysisRun:
    identity = transcript_evidence_identity(content)
    selected_metrics = config.selected_metrics or DEFAULT_SELECTED_METRICS
    resolved_config = StudyConfig(
        participant_id=config.participant_id,
        speaker_prefixes=dict(config.speaker_prefixes),
        speaker_labels=dict(config.speaker_labels),
        selected_metrics=list(selected_metrics),
        disfluency_tokens=list(config.disfluency_tokens),
        concept_lexicons={
            key: list(value) for key, value in config.concept_lexicons.items()
        },
        nonverbal_cues={key: list(value) for key, value in config.nonverbal_cues.items()},
        skill_pack_id=config.skill_pack_id,
        skill_pack_name=config.skill_pack_name,
        skill_pack_version=config.skill_pack_version,
    )
    transcript = parse_transcript(content, resolved_config, source_filename)
    results = []
    for metric_id in selected_metrics:
        results.append(get_metric_plugin(metric_id).calculate(transcript))
    return AnalysisRun(
        run_id=uuid4().hex,
        source_id=identity.source_id,
        source_sha256=identity.source_sha256,
        transcript_revision_id=identity.transcript_revision_id,
        source_filename=source_filename,
        created_at=datetime.now(UTC).isoformat(),
        transcript=transcript,
        results=results,
    )
