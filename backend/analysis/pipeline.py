from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from backend.analysis.metrics import (
    MetricResult,
    calculate_base_metrics,
    calculate_concept_count_metrics,
    calculate_cue_inventory_metrics,
    calculate_disfluency_metrics,
    calculate_lexical_metrics,
)
from backend.analysis.transcripts import StudyConfig, Transcript, parse_transcript


DEFAULT_SELECTED_METRICS = [
    "base_metrics",
    "lexical_metrics",
    "disfluency_metrics",
]

METRIC_REGISTRY = {
    "base_metrics": calculate_base_metrics,
    "lexical_metrics": calculate_lexical_metrics,
    "disfluency_metrics": calculate_disfluency_metrics,
    "concept_count_metrics": calculate_concept_count_metrics,
    "cue_inventory_metrics": calculate_cue_inventory_metrics,
}


@dataclass(frozen=True)
class AnalysisRun:
    run_id: str
    source_filename: str
    created_at: str
    transcript: Transcript
    results: list[MetricResult]


def execute_analysis(
    content: str,
    config: StudyConfig,
    source_filename: str,
) -> AnalysisRun:
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
        if metric_id not in METRIC_REGISTRY:
            raise ValueError(f"Unknown metric skill: {metric_id}")
        results.append(METRIC_REGISTRY[metric_id](transcript))
    return AnalysisRun(
        run_id=uuid4().hex,
        source_filename=source_filename,
        created_at=datetime.now(UTC).isoformat(),
        transcript=transcript,
        results=results,
    )
