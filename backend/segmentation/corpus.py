from __future__ import annotations

import random

from backend.segmentation.models import SyntheticSegmentationCase
from backend.segmentation.synthetic import OFFICIAL_SOURCE_GUARD_TOKENS


def generate_synthetic_corpus(seed: int = 0) -> list[SyntheticSegmentationCase]:
    rng = random.Random(seed)
    scenario = rng.choice(["Market practice", "Window table", "Morning shelf"])
    object_name = rng.choice(["green cup", "small card", "blue bowl"])
    participant = rng.choice(["Rae", "Milo", "Tara"])
    return [
        SyntheticSegmentationCase(
            case_id="corpus_speaker_timing_pause",
            title=f"Synthetic scenario: {scenario}",
            descript_text=(
                f"[00:00:00] P: Good morning, {participant}.\n"
                f"[00:00:03] Av: Please touch the {object_name}."
            ),
            gold_text=(
                f"Synthetic scenario: {scenario}\n"
                "-0:00\n"
                f"P: Good morning, {participant}.\n"
                "; :03\n"
                f"Av: Please touch the {object_name}."
            ),
            rule_ids=["speaker-markers", "timestamp-markers", "pause-markers"],
            official_source_guard_tokens=OFFICIAL_SOURCE_GUARD_TOKENS,
        ),
        SyntheticSegmentationCase(
            case_id="corpus_repair_overlap",
            title="Synthetic scenario: Pantry repair",
            descript_text=(
                "[00:00:00] P: Uh, I want\n"
                "[00:00:02] Av: yes take the spoon."
            ),
            gold_text=(
                "Synthetic scenario: Pantry repair\n"
                "-0:00\n"
                "P: ([FP]) I want>\n"
                "Av: <yes take the spoon.>"
            ),
            rule_ids=[
                "speaker-markers",
                "timestamp-markers",
                "filled-pauses",
                "overlap-markers",
                "abandoned-utterance",
            ],
            official_source_guard_tokens=OFFICIAL_SOURCE_GUARD_TOKENS,
        ),
        SyntheticSegmentationCase(
            case_id="corpus_redaction_nonverbal",
            title="Synthetic scenario: Table cue",
            descript_text=(
                "[00:00:00] P: This is [redacted].\n"
                "[00:00:02] AvN: points to tray.\n"
                "[00:00:04] P: I wa want that."
            ),
            gold_text=(
                "Synthetic scenario: Table cue\n"
                "-0:00\n"
                "P: This is {redacted}.\n"
                "AvN: {AvN: points to tray}\n"
                "P: I wa* want that /*."
            ),
            rule_ids=[
                "speaker-markers",
                "timestamp-markers",
                "redaction-comments",
                "omission-markers",
                "communicative-nonverbal",
            ],
            official_source_guard_tokens=OFFICIAL_SOURCE_GUARD_TOKENS,
        ),
        SyntheticSegmentationCase(
            case_id="corpus_official_source_leakage_negative",
            title="Synthetic scenario: Leakage guard negative",
            descript_text="[00:00:00] P: Nala should not appear here.",
            gold_text="-0:00\nP: Nala should not appear here.",
            rule_ids=["speaker-markers", "timestamp-markers", "official-source-guard"],
            official_source_guard_tokens=OFFICIAL_SOURCE_GUARD_TOKENS,
        ),
    ]
