from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RawTranscriptEvent:
    timestamp_seconds: int
    speaker: str
    text: str
    source_filename: str = ""


@dataclass(frozen=True)
class SyntheticSegmentationCase:
    case_id: str
    title: str
    descript_text: str
    gold_text: str
    rule_ids: list[str]
    official_source_guard_tokens: list[str]
    forbidden_source_tokens: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SegmentationMetrics:
    line_count: int
    utterance_count: int
    time_marker_count: int
    pause_marker_count: int
    speaker_counts: dict[str, int]
    special_notation_counts: dict[str, int]


@dataclass(frozen=True)
class SegmentationRuleFailure:
    rule_id: str
    message: str
    line_number: int | None = None


@dataclass(frozen=True)
class SegmentationEvaluation:
    score: int
    metrics: SegmentationMetrics
    failures: list[SegmentationRuleFailure]

