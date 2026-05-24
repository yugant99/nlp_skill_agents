from __future__ import annotations

from backend.segmentation.models import SyntheticSegmentationCase


OFFICIAL_SOURCE_GUARD_TOKENS = [
    "call me",
    "falcon",
    "james",
    "lady bird",
    "nala",
]


def list_synthetic_cases() -> list[SyntheticSegmentationCase]:
    return [
        build_synthetic_case("pause_overlap_repair"),
        build_synthetic_case("redaction_omission_nonverbal"),
    ]


def build_synthetic_case(case_id: str) -> SyntheticSegmentationCase:
    cases = {
        "pause_overlap_repair": _pause_overlap_repair_case,
        "redaction_omission_nonverbal": _redaction_omission_nonverbal_case,
    }
    try:
        return cases[case_id]()
    except KeyError as exc:
        raise ValueError(f"Unknown synthetic segmentation case: {case_id}") from exc


def _pause_overlap_repair_case() -> SyntheticSegmentationCase:
    descript_text = """[00:00:00] P: Good morning, Mira.
[00:00:03] P: Uh, I mean, we can start.
[00:00:05] Av: Yes, begin here.
[00:00:07] P: I want
[00:00:08] Av: And then we lift the cup."""
    gold_text = """Synthetic scenario: Sunrise kitchen
-0:00
P: Good morning, Mira.
P: ([FP]) I mean, we can start.
; :02
Av: <yes, begin here>
P: I want>
-0:08
Av: And then we lift the cup."""
    return SyntheticSegmentationCase(
        case_id="pause_overlap_repair",
        title="Synthetic scenario: Sunrise kitchen",
        descript_text=descript_text,
        gold_text=gold_text,
        rule_ids=[
            "speaker-markers",
            "timestamp-markers",
            "pause-markers",
            "filled-pauses",
            "overlap-markers",
            "abandoned-utterance",
        ],
        official_source_guard_tokens=OFFICIAL_SOURCE_GUARD_TOKENS,
    )


def _redaction_omission_nonverbal_case() -> SyntheticSegmentationCase:
    descript_text = """[00:00:00] P: This is [redacted].
[00:00:02] Av: Uh, look at the blue cup.
[00:00:04] P: I want the flower.
[00:00:05] AvN: points to shelf.
[00:00:07] Av: Yes, choose one.
[00:00:10] P: Okay."""
    gold_text = """Synthetic scenario: Garden practice
-0:00
P: This is {redacted}.
; :01
Av: ([FP]) look at the blue cup.
P: I wa* want the flower /*.
AvN: {AvN: points to shelf}
-0:07
Av: Yes, choose one.
; :03
P: Okay."""
    return SyntheticSegmentationCase(
        case_id="redaction_omission_nonverbal",
        title="Synthetic scenario: Garden practice",
        descript_text=descript_text,
        gold_text=gold_text,
        rule_ids=[
            "speaker-markers",
            "timestamp-markers",
            "pause-markers",
            "filled-pauses",
            "redaction-comments",
            "omission-markers",
            "communicative-nonverbal",
        ],
        official_source_guard_tokens=OFFICIAL_SOURCE_GUARD_TOKENS,
    )

