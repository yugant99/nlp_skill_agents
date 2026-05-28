from __future__ import annotations

from dataclasses import dataclass


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
class ProfessorGradeRuleArea:
    area_id: str
    label: str
    status: str
    scientist_language: str


@dataclass(frozen=True)
class CUnitRulebookSummary:
    supported_rule_count: int
    demo_case_rule_count: int
    corpus_rule_count: int
    rule_definitions: list[CUnitRuleDefinition]
    professor_grade_areas: list[ProfessorGradeRuleArea]


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


PROFESSOR_GRADE_RULE_AREAS = [
    ProfessorGradeRuleArea(
        area_id="cunit-boundaries",
        label="C-unit boundary decisions",
        status="gap",
        scientist_language=(
            "Needs independent clause, dependent clause, coordination, and subordination "
            "judgment before claiming full C-unit expertise."
        ),
    ),
    ProfessorGradeRuleArea(
        area_id="maze-revision",
        label="Maze, revision, and reformulation handling",
        status="partial",
        scientist_language=(
            "Filled pauses and abandoned markers exist, but repetitions, false starts, "
            "reformulations, and exclusion policy need explicit evidence."
        ),
    ),
    ProfessorGradeRuleArea(
        area_id="ellipsis-minimal-response",
        label="Ellipsis and minimal responses",
        status="gap",
        scientist_language=(
            "Needs adjudication for elliptical answers and short responses that may or may "
            "not count as C-units in clinical discourse."
        ),
    ),
    ProfessorGradeRuleArea(
        area_id="unintelligible-partial-material",
        label="Unintelligible and partial material",
        status="gap",
        scientist_language=(
            "Needs explicit handling for unintelligible spans, partial words, and uncertain "
            "transcriber material."
        ),
    ),
    ProfessorGradeRuleArea(
        area_id="evidence-contract",
        label="Rule-level evidence contract",
        status="partial",
        scientist_language=(
            "Current failures route to specialists; next step is matched evidence lines, "
            "severity, rationale, and confidence per rule."
        ),
    ),
]


def build_cunit_rulebook_summary() -> CUnitRulebookSummary:
    return CUnitRulebookSummary(
        supported_rule_count=len(SUPPORTED_RULE_IDS),
        demo_case_rule_count=9,
        corpus_rule_count=10,
        rule_definitions=RULE_DEFINITIONS,
        professor_grade_areas=PROFESSOR_GRADE_RULE_AREAS,
    )
