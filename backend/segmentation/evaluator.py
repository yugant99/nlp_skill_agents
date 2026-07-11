from __future__ import annotations

import re

from backend.segmentation.models import (
    SegmentationEvaluation,
    SegmentationMetrics,
    SegmentationRuleFailure,
)


VALID_SPEAKERS = {"Av", "P", "AvN", "PN"}
_SPEAKER_LINE_PATTERN = re.compile(r"^(?P<speaker>AvN|PN|Av|P):\s+.+")
_ANY_LABEL_PATTERN = re.compile(r"^(?P<label>[A-Za-z][A-Za-z0-9]*):")
_TIME_MARKER_PATTERN = re.compile(r"^-\d+:\d{2}$")
_PAUSE_MARKER_PATTERN = re.compile(r"^;\s+:\d{2}$")


def evaluate_segmented_draft(
    draft_text: str,
    expected_rule_ids: list[str] | None = None,
    forbidden_tokens: list[str] | None = None,
) -> SegmentationEvaluation:
    lines = [line.strip() for line in draft_text.splitlines() if line.strip()]
    expected = set(expected_rule_ids or [])
    configured_rule_ids = set(expected)
    if any(forbidden_tokens or []):
        configured_rule_ids.add("official-source-guard")
    failures: list[SegmentationRuleFailure] = []
    metrics = _build_metrics(lines)

    if "speaker-markers" in expected:
        failures.extend(_speaker_marker_failures(lines))
    if "timestamp-markers" in expected and metrics.time_marker_count == 0:
        failures.append(
            SegmentationRuleFailure(
                rule_id="timestamp-markers",
                message="Segmented draft must include at least one -M:SS time marker.",
            )
        )
    if "pause-markers" in expected and metrics.pause_marker_count == 0:
        failures.append(
            SegmentationRuleFailure(
                rule_id="pause-markers",
                message="Expected at least one semicolon pause marker such as ; :02.",
            )
        )
    if "filled-pauses" in expected and metrics.special_notation_counts["filled_pauses"] == 0:
        failures.append(
            SegmentationRuleFailure(
                rule_id="filled-pauses",
                message="Filled pauses must be normalized to ([FP]).",
            )
        )
    if "overlap-markers" in expected and metrics.special_notation_counts["overlap_markers"] == 0:
        failures.append(
            SegmentationRuleFailure(
                rule_id="overlap-markers",
                message="Expected overlapping speech to be marked with angle brackets.",
            )
        )
    if (
        "abandoned-utterance" in expected
        and metrics.special_notation_counts["abandoned_utterances"] == 0
    ):
        failures.append(
            SegmentationRuleFailure(
                rule_id="abandoned-utterance",
                message="Expected an abandoned utterance marker using >.",
            )
        )
    if "redaction-comments" in expected:
        failures.extend(_redaction_failures(draft_text, metrics))
    if "omission-markers" in expected and metrics.special_notation_counts["omission_markers"] == 0:
        failures.append(
            SegmentationRuleFailure(
                rule_id="omission-markers",
                message="Expected omitted or partial-word material to be marked with *.",
            )
        )
    if (
        "communicative-nonverbal" in expected
        and metrics.special_notation_counts["communicative_nonverbal"] == 0
    ):
        failures.append(
            SegmentationRuleFailure(
                rule_id="communicative-nonverbal",
                message="Expected communicative nonverbal material on AvN or PN lines.",
            )
        )

    leaked_tokens = [
        token
        for token in (forbidden_tokens or [])
        if token and token.lower() in draft_text.lower()
    ]
    if leaked_tokens:
        failures.append(
            SegmentationRuleFailure(
                rule_id="official-source-guard",
                message=f"Draft includes forbidden source token(s): {', '.join(leaked_tokens)}.",
            )
        )

    failed_rule_ids = {failure.rule_id for failure in failures}
    return SegmentationEvaluation(
        configured_rule_count=len(configured_rule_ids),
        passed_rule_count=len(configured_rule_ids - failed_rule_ids),
        metrics=metrics,
        failures=failures,
    )


def _build_metrics(lines: list[str]) -> SegmentationMetrics:
    speaker_counts = {speaker: 0 for speaker in ["P", "Av", "PN", "AvN"]}
    utterance_count = 0
    time_marker_count = 0
    pause_marker_count = 0
    joined = "\n".join(lines)

    for line in lines:
        if _TIME_MARKER_PATTERN.match(line):
            time_marker_count += 1
        if _PAUSE_MARKER_PATTERN.match(line):
            pause_marker_count += 1
        match = _SPEAKER_LINE_PATTERN.match(line)
        if match:
            utterance_count += 1
            speaker_counts[match.group("speaker")] += 1

    return SegmentationMetrics(
        line_count=len(lines),
        utterance_count=utterance_count,
        time_marker_count=time_marker_count,
        pause_marker_count=pause_marker_count,
        speaker_counts={speaker: count for speaker, count in speaker_counts.items() if count},
        special_notation_counts={
            "redaction_comments": len(re.findall(r"\{redacted\}", joined, re.IGNORECASE)),
            "omission_markers": len(re.findall(r"\*", joined)),
            "filled_pauses": joined.count("([FP])"),
            "overlap_markers": joined.count("<"),
            "abandoned_utterances": len(re.findall(r">\s*(?:\n|$)", joined)),
            "communicative_nonverbal": sum(
                1 for line in lines if line.startswith(("AvN:", "PN:", "{AvN:", "{PN:"))
            ),
        },
    )


def _speaker_marker_failures(lines: list[str]) -> list[SegmentationRuleFailure]:
    failures: list[SegmentationRuleFailure] = []
    if not any(_SPEAKER_LINE_PATTERN.match(line) for line in lines):
        failures.append(
            SegmentationRuleFailure(
                rule_id="speaker-markers",
                message="Segmented draft must include speaker lines using Av, P, AvN, or PN.",
            )
        )
    for line_number, line in enumerate(lines, start=1):
        match = _ANY_LABEL_PATTERN.match(line)
        if match and match.group("label") not in VALID_SPEAKERS:
            failures.append(
                SegmentationRuleFailure(
                    rule_id="speaker-markers",
                    message=f"Unsupported speaker marker: {match.group('label')}.",
                    line_number=line_number,
                )
            )
    return failures


def _redaction_failures(
    draft_text: str,
    metrics: SegmentationMetrics,
) -> list[SegmentationRuleFailure]:
    failures: list[SegmentationRuleFailure] = []
    if "[redacted]" in draft_text.lower():
        failures.append(
            SegmentationRuleFailure(
                rule_id="redaction-comments",
                message="Redactions must use comment braces: {redacted}.",
            )
        )
    if metrics.special_notation_counts["redaction_comments"] == 0:
        failures.append(
            SegmentationRuleFailure(
                rule_id="redaction-comments",
                message="Expected at least one {redacted} comment.",
            )
        )
    return failures
