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
    configured_rule_count: int
    passed_rule_count: int
    metrics: SegmentationMetrics
    failures: list[SegmentationRuleFailure]


@dataclass(frozen=True)
class CUnitBoundaryDecision:
    event_index: int
    speaker: str
    raw_text: str
    cleaned_text: str
    boundary_type: str
    decision: str
    cunit_count: int
    rationale: str
    confidence_status: str
    needs_human_review: bool
    excluded_maze: str = ""
    evidence_terms: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CUnitAdjudication:
    total_event_count: int
    participant_turn_count: int
    examiner_turn_count: int
    counted_cunit_count: int
    needs_review_count: int
    boundary_type_counts: dict[str, int]
    decisions: list[CUnitBoundaryDecision]
    validation_status: str = "not_domain_validated"
    evidence_scope: str = "deterministic_heuristics_and_synthetic_fixtures"
