from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.segmentation.descript import extract_descript_events
from backend.segmentation.evaluator import evaluate_segmented_draft
from backend.segmentation.models import (
    RawTranscriptEvent,
    SegmentationEvaluation,
    SegmentationMetrics,
    SegmentationRuleFailure,
)
from backend.segmentation.synthetic import OFFICIAL_SOURCE_GUARD_TOKENS


RULE_TO_SPECIALIST = {
    "speaker-markers": "speaker_turn",
    "timestamp-markers": "timing_pause",
    "pause-markers": "timing_pause",
    "filled-pauses": "repair_overlap",
    "overlap-markers": "repair_overlap",
    "abandoned-utterance": "repair_overlap",
    "redaction-comments": "redaction_nonverbal",
    "omission-markers": "redaction_nonverbal",
    "communicative-nonverbal": "redaction_nonverbal",
    "official-source-guard": "source_guard",
}


@dataclass(frozen=True)
class RuleWorkPacket:
    specialist_id: str
    rule_ids: list[str]


@dataclass(frozen=True)
class PatchOperation:
    operation: str
    event_index: int
    text: str
    reason: str


@dataclass(frozen=True)
class SpecialistOutput:
    specialist_id: str
    rule_ids: list[str]
    patches: list[PatchOperation]
    evidence: dict[str, Any]


@dataclass(frozen=True)
class MergeEvidence:
    applied_patch_count: int
    conflicts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SegmentationRun:
    run_id: str
    source_filename: str
    descript_text: str
    events: list[RawTranscriptEvent]
    rule_ids: list[str]
    rule_plan: list[RuleWorkPacket]
    specialist_outputs: list[SpecialistOutput]
    merged_draft: str
    merge_evidence: MergeEvidence
    evaluation: SegmentationEvaluation | None
    status: str
    failure_routes: list[dict[str, str]]
    source: str = "synthetic"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class SegmentationRunStore:
    def __init__(self, root: Path | str = "local_data") -> None:
        self.root = Path(root)
        self.runs_dir = self.root / "segmentation_runs"

    def create_run(
        self,
        *,
        source_filename: str,
        descript_text: str,
        rule_ids: list[str],
    ) -> SegmentationRun:
        if not descript_text.strip():
            raise ValueError("descript_text must be non-empty")
        normalized_rule_ids = _normalize_rule_ids(rule_ids)
        events = extract_descript_events(descript_text, source_filename)
        if not events:
            raise ValueError("No timestamped Descript speaker events were found")

        rule_plan = plan_rule_work(normalized_rule_ids)
        specialist_outputs = [
            build_specialist_output(packet, events)
            for packet in rule_plan
        ]
        merged_draft, merge_evidence = merge_specialist_outputs(
            source_filename,
            events,
            specialist_outputs,
        )
        evaluation = evaluate_segmented_draft(
            merged_draft,
            expected_rule_ids=normalized_rule_ids,
            forbidden_tokens=OFFICIAL_SOURCE_GUARD_TOKENS,
        )
        failure_routes = route_failures(evaluation.failures)
        status = _status_from_evaluation(evaluation, merge_evidence)
        run = SegmentationRun(
            run_id=uuid4().hex,
            source_filename=source_filename,
            descript_text=descript_text,
            events=events,
            rule_ids=normalized_rule_ids,
            rule_plan=rule_plan,
            specialist_outputs=specialist_outputs,
            merged_draft=merged_draft,
            merge_evidence=merge_evidence,
            evaluation=evaluation,
            status=status,
            failure_routes=failure_routes,
        )
        self.persist_run(run)
        return run

    def verify_run(self, run_id: str) -> SegmentationRun:
        run = self.load_run(run_id)
        evaluation = evaluate_segmented_draft(
            run.merged_draft,
            expected_rule_ids=run.rule_ids,
            forbidden_tokens=OFFICIAL_SOURCE_GUARD_TOKENS,
        )
        updated = SegmentationRun(
            **{
                **asdict(run),
                "events": run.events,
                "rule_plan": run.rule_plan,
                "specialist_outputs": run.specialist_outputs,
                "merge_evidence": run.merge_evidence,
                "evaluation": evaluation,
                "status": _status_from_evaluation(evaluation, run.merge_evidence),
                "failure_routes": route_failures(evaluation.failures),
            }
        )
        self.persist_run(updated)
        return updated

    def persist_run(self, run: SegmentationRun) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        (self.runs_dir / f"{run.run_id}.json").write_text(
            json.dumps(segmentation_run_to_payload(run), indent=2),
            encoding="utf-8",
        )

    def load_run(self, run_id: str) -> SegmentationRun:
        run_path = self.runs_dir / f"{run_id}.json"
        if not run_path.exists():
            raise FileNotFoundError(run_id)
        return segmentation_run_from_payload(
            json.loads(run_path.read_text(encoding="utf-8"))
        )


def plan_rule_work(rule_ids: list[str]) -> list[RuleWorkPacket]:
    packets: dict[str, list[str]] = {}
    for rule_id in rule_ids:
        specialist_id = RULE_TO_SPECIALIST[rule_id]
        if specialist_id == "source_guard":
            continue
        packets.setdefault(specialist_id, []).append(rule_id)
    return [
        RuleWorkPacket(specialist_id=specialist_id, rule_ids=packet_rule_ids)
        for specialist_id, packet_rule_ids in packets.items()
    ]


def build_specialist_output(
    packet: RuleWorkPacket,
    events: list[RawTranscriptEvent],
) -> SpecialistOutput:
    patches: list[PatchOperation] = []
    if packet.specialist_id == "speaker_turn":
        patches.extend(_speaker_turn_patches(events))
    if packet.specialist_id == "timing_pause":
        patches.extend(_timing_pause_patches(events))
    if packet.specialist_id == "repair_overlap":
        patches.extend(_repair_overlap_patches(events, packet.rule_ids))
    if packet.specialist_id == "redaction_nonverbal":
        patches.extend(_redaction_nonverbal_patches(events, packet.rule_ids))
    return SpecialistOutput(
        specialist_id=packet.specialist_id,
        rule_ids=packet.rule_ids,
        patches=patches,
        evidence={
            "source_event_indexes": sorted({patch.event_index for patch in patches}),
            "patch_count": len(patches),
        },
    )


def merge_specialist_outputs(
    source_filename: str,
    events: list[RawTranscriptEvent],
    outputs: list[SpecialistOutput],
) -> tuple[str, MergeEvidence]:
    patches = [patch for output in outputs for patch in output.patches]
    lines = [f"Synthetic run: {Path(source_filename).stem}"]
    conflicts: list[str] = []
    applied = 0
    for event_index, event in enumerate(events):
        before = [
            patch for patch in patches
            if patch.event_index == event_index and patch.operation == "insert_before_event"
        ]
        for patch in sorted(before, key=lambda item: item.text):
            lines.append(patch.text)
            applied += 1

        event_line_patches = [
            patch for patch in patches
            if patch.event_index == event_index and patch.operation == "event_line"
        ]
        if len({patch.text for patch in event_line_patches}) > 2:
            conflicts.append(f"event {event_index} has conflicting event_line patches")
        if event_line_patches:
            lines.append(event_line_patches[-1].text)
            applied += 1
        else:
            lines.append(f"{event.speaker}: {event.text}")

    return "\n".join(lines), MergeEvidence(
        applied_patch_count=applied,
        conflicts=conflicts,
    )


def route_failures(
    failures: list[SegmentationRuleFailure],
) -> list[dict[str, str]]:
    routes = []
    for failure in failures:
        routes.append(
            {
                "rule_id": failure.rule_id,
                "specialist_id": RULE_TO_SPECIALIST.get(
                    failure.rule_id,
                    "verification",
                ),
                "message": failure.message,
            }
        )
    return routes


def segmentation_run_to_payload(run: SegmentationRun) -> dict[str, Any]:
    return asdict(run)


def segmentation_run_from_payload(payload: dict[str, Any]) -> SegmentationRun:
    return SegmentationRun(
        run_id=str(payload["run_id"]),
        source_filename=str(payload["source_filename"]),
        descript_text=str(payload["descript_text"]),
        events=[
            RawTranscriptEvent(
                timestamp_seconds=int(event["timestamp_seconds"]),
                speaker=str(event["speaker"]),
                text=str(event["text"]),
                source_filename=str(event.get("source_filename") or ""),
            )
            for event in payload.get("events", [])
        ],
        rule_ids=[str(rule_id) for rule_id in payload.get("rule_ids", [])],
        rule_plan=[
            RuleWorkPacket(
                specialist_id=str(packet["specialist_id"]),
                rule_ids=[str(rule_id) for rule_id in packet.get("rule_ids", [])],
            )
            for packet in payload.get("rule_plan", [])
        ],
        specialist_outputs=[
            SpecialistOutput(
                specialist_id=str(output["specialist_id"]),
                rule_ids=[str(rule_id) for rule_id in output.get("rule_ids", [])],
                patches=[
                    PatchOperation(
                        operation=str(patch["operation"]),
                        event_index=int(patch["event_index"]),
                        text=str(patch["text"]),
                        reason=str(patch["reason"]),
                    )
                    for patch in output.get("patches", [])
                ],
                evidence=dict(output.get("evidence", {})),
            )
            for output in payload.get("specialist_outputs", [])
        ],
        merged_draft=str(payload.get("merged_draft") or ""),
        merge_evidence=MergeEvidence(
            applied_patch_count=int(
                payload.get("merge_evidence", {}).get("applied_patch_count", 0)
            ),
            conflicts=[
                str(conflict)
                for conflict in payload.get("merge_evidence", {}).get("conflicts", [])
            ],
        ),
        evaluation=_evaluation_from_payload(payload.get("evaluation")),
        status=str(payload.get("status") or "created"),
        failure_routes=[
            {
                "rule_id": str(route.get("rule_id") or ""),
                "specialist_id": str(route.get("specialist_id") or ""),
                "message": str(route.get("message") or ""),
            }
            for route in payload.get("failure_routes", [])
        ],
        source=str(payload.get("source") or "synthetic"),
        created_at=str(payload.get("created_at") or datetime.now(UTC).isoformat()),
    )


def _normalize_rule_ids(rule_ids: list[str]) -> list[str]:
    normalized = [rule_id for rule_id in rule_ids if rule_id]
    unknown = [rule_id for rule_id in normalized if rule_id not in RULE_TO_SPECIALIST]
    if unknown:
        raise ValueError(f"Unsupported segmentation rule id(s): {', '.join(unknown)}")
    return normalized or [
        "speaker-markers",
        "timestamp-markers",
        "pause-markers",
    ]


def _speaker_turn_patches(events: list[RawTranscriptEvent]) -> list[PatchOperation]:
    return [
        PatchOperation(
            operation="event_line",
            event_index=index,
            text=f"{event.speaker}: {event.text}",
            reason="Create normalized speaker turn line",
        )
        for index, event in enumerate(events)
    ]


def _timing_pause_patches(events: list[RawTranscriptEvent]) -> list[PatchOperation]:
    patches = [
        PatchOperation(
            operation="insert_before_event",
            event_index=0,
            text=_time_marker(events[0].timestamp_seconds),
            reason="Start timestamp marker required",
        )
    ]
    for index in range(1, len(events)):
        gap = events[index].timestamp_seconds - events[index - 1].timestamp_seconds
        if gap > 0:
            patches.append(
                PatchOperation(
                    operation="insert_before_event",
                    event_index=index,
                    text=f"; :{gap:02d}",
                    reason="Elapsed gap requires pause marker",
                )
            )
    return patches


def _repair_overlap_patches(
    events: list[RawTranscriptEvent],
    rule_ids: list[str],
) -> list[PatchOperation]:
    patches: list[PatchOperation] = []
    for index, event in enumerate(events):
        text = event.text
        if "filled-pauses" in rule_ids:
            text = re.sub(r"\b(uh|um)\b,?\s*", "([FP]) ", text, flags=re.IGNORECASE)
        if "abandoned-utterance" in rule_ids and index == 0 and not text.endswith(">"):
            text = f"{text.rstrip('.')}>"
        if "overlap-markers" in rule_ids and index == 1:
            text = f"<{text}>"
        if text != event.text:
            patches.append(
                PatchOperation(
                    operation="event_line",
                    event_index=index,
                    text=f"{event.speaker}: {text}",
                    reason="Apply repair, overlap, or filled-pause notation",
                )
            )
    return patches


def _redaction_nonverbal_patches(
    events: list[RawTranscriptEvent],
    rule_ids: list[str],
) -> list[PatchOperation]:
    patches: list[PatchOperation] = []
    for index, event in enumerate(events):
        text = event.text
        if "redaction-comments" in rule_ids:
            text = text.replace("[redacted]", "{redacted}")
        if "omission-markers" in rule_ids:
            text = re.sub(r"\bwa want\b", "wa* want /*", text, flags=re.IGNORECASE)
        if "communicative-nonverbal" in rule_ids and event.speaker in {"AvN", "PN"}:
            text = f"{{{event.speaker}: {text}}}"
        if text != event.text:
            patches.append(
                PatchOperation(
                    operation="event_line",
                    event_index=index,
                    text=f"{event.speaker}: {text}",
                    reason="Apply redaction, omission, or nonverbal notation",
                )
            )
    return patches


def _time_marker(timestamp_seconds: int) -> str:
    minutes, seconds = divmod(timestamp_seconds, 60)
    return f"-{minutes}:{seconds:02d}"


def _status_from_evaluation(
    evaluation: SegmentationEvaluation,
    merge_evidence: MergeEvidence,
) -> str:
    if merge_evidence.conflicts:
        return "failed"
    if any(failure.rule_id == "official-source-guard" for failure in evaluation.failures):
        return "failed"
    return "verified" if not evaluation.failures else "needs_rewrite"


def _evaluation_from_payload(payload: dict[str, Any] | None) -> SegmentationEvaluation | None:
    if not payload:
        return None
    metrics_payload = payload["metrics"]
    return SegmentationEvaluation(
        score=int(payload["score"]),
        metrics=SegmentationMetrics(
            line_count=int(metrics_payload["line_count"]),
            utterance_count=int(metrics_payload["utterance_count"]),
            time_marker_count=int(metrics_payload["time_marker_count"]),
            pause_marker_count=int(metrics_payload["pause_marker_count"]),
            speaker_counts={
                str(key): int(value)
                for key, value in metrics_payload.get("speaker_counts", {}).items()
            },
            special_notation_counts={
                str(key): int(value)
                for key, value in metrics_payload.get("special_notation_counts", {}).items()
            },
        ),
        failures=[
            SegmentationRuleFailure(
                rule_id=str(failure["rule_id"]),
                message=str(failure["message"]),
                line_number=failure.get("line_number"),
            )
            for failure in payload.get("failures", [])
        ],
    )
