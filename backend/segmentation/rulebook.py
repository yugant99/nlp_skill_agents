from __future__ import annotations

from dataclasses import dataclass

from backend.segmentation.corpus import generate_synthetic_corpus
from backend.segmentation.models import SyntheticSegmentationCase
from backend.segmentation.synthetic import list_synthetic_cases


SUPPORTED_RULE_IDS = [
    "speaker-markers",
    "timestamp-markers",
    "pause-markers",
    "filled-pauses",
    "overlap-markers",
    "abandoned-utterance",
    "redaction-comments",
    "omission-markers",
    "communicative-nonverbal",
    "official-source-guard",
]


@dataclass(frozen=True)
class CUnitRuleDefinition:
    rule_id: str
    label: str
    specialist_id: str
    deterministic_check: str
    current_depth: str
    scientist_language: str


@dataclass(frozen=True)
class CUnitMethodArea:
    area_id: str
    label: str
    status: str
    scientist_language: str


@dataclass(frozen=True)
class SegmentationValidationProfile:
    status: str
    evidence_scope: str
    claim_boundary: str
    limitations: list[str]


@dataclass(frozen=True)
class CUnitRulebookSummary:
    implemented_rule_count: int
    tracked_fixture_rule_count: int
    generated_fixture_rule_count: int
    validation: SegmentationValidationProfile
    rule_definitions: list[CUnitRuleDefinition]
    method_areas: list[CUnitMethodArea]


RULE_DEFINITIONS = [
    CUnitRuleDefinition(
        rule_id="speaker-markers",
        label="Speaker markers",
        specialist_id="speaker_turn",
        deterministic_check="Validates that speaker lines use P, Av, PN, or AvN.",
        current_depth="surface",
        scientist_language="Checks transcript speaker coding, not conversational-role interpretation.",
    ),
    CUnitRuleDefinition(
        rule_id="timestamp-markers",
        label="Timestamp markers",
        specialist_id="timing_pause",
        deterministic_check="Requires at least one -M:SS marker.",
        current_depth="surface",
        scientist_language="Confirms time markers exist; does not yet audit every boundary placement.",
    ),
    CUnitRuleDefinition(
        rule_id="pause-markers",
        label="Pause markers",
        specialist_id="timing_pause",
        deterministic_check="Requires semicolon pause notation such as ; :02.",
        current_depth="surface",
        scientist_language="Recognizes pause notation but does not yet enforce a full pause-threshold policy.",
    ),
    CUnitRuleDefinition(
        rule_id="filled-pauses",
        label="Filled pauses",
        specialist_id="repair_overlap",
        deterministic_check="Checks for normalized ([FP]) markers.",
        current_depth="surface",
        scientist_language="Normalizes filled pauses, but does not yet classify all maze material.",
    ),
    CUnitRuleDefinition(
        rule_id="overlap-markers",
        label="Overlap markers",
        specialist_id="repair_overlap",
        deterministic_check="Checks for overlap notation.",
        current_depth="surface",
        scientist_language="Shows overlap handling; does not yet prove real acoustic overlap alignment.",
    ),
    CUnitRuleDefinition(
        rule_id="abandoned-utterance",
        label="Abandoned utterances",
        specialist_id="repair_overlap",
        deterministic_check="Checks for line-final abandoned utterance markers.",
        current_depth="surface",
        scientist_language="Flags abandoned material markers, not full revision-chain interpretation.",
    ),
    CUnitRuleDefinition(
        rule_id="redaction-comments",
        label="Redaction comments",
        specialist_id="redaction_nonverbal",
        deterministic_check="Requires brace comments such as {redacted} and rejects [redacted].",
        current_depth="deterministic",
        scientist_language="Protects privacy notation and keeps official transcript leakage out of the demo.",
    ),
    CUnitRuleDefinition(
        rule_id="omission-markers",
        label="Omission markers",
        specialist_id="redaction_nonverbal",
        deterministic_check="Requires omission or partial-word asterisk notation.",
        current_depth="surface",
        scientist_language="Recognizes omission notation but does not yet distinguish all partial-word cases.",
    ),
    CUnitRuleDefinition(
        rule_id="communicative-nonverbal",
        label="Communicative nonverbal",
        specialist_id="redaction_nonverbal",
        deterministic_check="Requires PN or AvN nonverbal speaker lines.",
        current_depth="surface",
        scientist_language="Captures nonverbal coding, not full communicative intent adjudication.",
    ),
    CUnitRuleDefinition(
        rule_id="official-source-guard",
        label="Official source guard",
        specialist_id="source_guard",
        deterministic_check="Fails runs that contain forbidden official-source tokens.",
        current_depth="deterministic",
        scientist_language="Prevents the demo from copying or leaking official transcript content.",
    ),
]


METHOD_AREAS = [
    CUnitMethodArea(
        area_id="cunit-boundaries",
        label="C-unit boundary decisions",
        status="implemented-unvalidated",
        scientist_language=(
            "Adjudicates independent clause counts, dependent clause attachment, "
            "coordination splits, and subordination review flags with rationale."
        ),
    ),
    CUnitMethodArea(
        area_id="maze-revision",
        label="Maze, revision, and reformulation handling",
        status="implemented-unvalidated",
        scientist_language=(
            "Excludes filled pauses, false starts, partial-word repetitions, "
            "revisions, and unintelligible spans from counted C-units with evidence."
        ),
    ),
    CUnitMethodArea(
        area_id="ellipsis-minimal-response",
        label="Ellipsis and minimal responses",
        status="implemented-unvalidated",
        scientist_language=(
            "Classifies short elliptical responses separately so minimal clinical "
            "answers count only when they carry communicative content."
        ),
    ),
    CUnitMethodArea(
        area_id="unintelligible-partial-material",
        label="Unintelligible and partial material",
        status="implemented-unvalidated",
        scientist_language=(
            "Detects unintelligible spans, bracketed uncertainty, and partial-word "
            "material, then routes those turns for human review."
        ),
    ),
    CUnitMethodArea(
        area_id="evidence-contract",
        label="Rule-level evidence contract",
        status="partial-unvalidated",
        scientist_language=(
            "Current failures route to specialists with rationale and evidence terms; "
            "agreement with expert human decisions has not been measured."
        ),
    ),
]


VALIDATION_PROFILE = SegmentationValidationProfile(
    status="not_domain_validated",
    evidence_scope="tracked_and_generated_synthetic_fixtures",
    claim_boundary=(
        "Configured deterministic rules are exercised by synthetic fixtures; "
        "these counts are not accuracy, reliability, or validity estimates."
    ),
    limitations=[
        "No representative psychology-study transcript sample has been evaluated.",
        "Agreement with expert human C-unit decisions has not been measured.",
        "Sensitivity, specificity, reliability, and calibrated confidence are unknown.",
        "Every automated C-unit decision remains a researcher-reviewable proposal.",
    ],
)


def build_cunit_rulebook_summary() -> CUnitRulebookSummary:
    tracked_fixture_rule_count = _covered_rule_count(list_synthetic_cases())
    generated_fixture_rule_count = _covered_rule_count(
        generate_synthetic_corpus(seed=0)
    )
    return CUnitRulebookSummary(
        implemented_rule_count=len(SUPPORTED_RULE_IDS),
        tracked_fixture_rule_count=tracked_fixture_rule_count,
        generated_fixture_rule_count=generated_fixture_rule_count,
        validation=VALIDATION_PROFILE,
        rule_definitions=RULE_DEFINITIONS,
        method_areas=METHOD_AREAS,
    )


def _covered_rule_count(cases: list[SyntheticSegmentationCase]) -> int:
    return len({rule_id for case in cases for rule_id in case.rule_ids})
