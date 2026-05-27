from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.segmentation.corpus import generate_synthetic_corpus
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


@dataclass(frozen=True)
class SegmentationCorpusCaseResult:
    case_id: str
    title: str
    run_id: str
    status: str
    expected_status: str
    outcome: str
    score: int
    rule_ids: list[str]
    failed_rule_ids: list[str]


@dataclass(frozen=True)
class SegmentationCorpusRun:
    corpus_run_id: str
    seed: int
    status: str
    total_case_count: int
    regression_pass_count: int
    regression_fail_count: int
    rule_coverage: list[str]
    results: list[SegmentationCorpusCaseResult]
    source: str = "synthetic"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class SegmentationRunStore:
    def __init__(self, root: Path | str = "local_data") -> None:
        self.root = Path(root)
        self.runs_dir = self.root / "segmentation_runs"
        self.corpus_runs_dir = self.root / "segmentation_corpus_runs"

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

        run_id = uuid4().hex
        rule_plan = plan_rule_work(normalized_rule_ids)
        specialist_outputs = [
            build_specialist_output(packet, events)
            for packet in rule_plan
        ]
        specialist_outputs = self._write_specialist_artifacts(
            run_id,
            source_filename,
            events,
            specialist_outputs,
        )
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
            run_id=run_id,
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

    def create_corpus_run(self, *, seed: int = 0) -> SegmentationCorpusRun:
        cases = generate_synthetic_corpus(seed=seed)
        results: list[SegmentationCorpusCaseResult] = []
        rule_coverage: set[str] = set()
        for case in cases:
            rule_coverage.update(case.rule_ids)
            run = self.create_run(
                source_filename=f"{case.case_id}.txt",
                descript_text=case.descript_text,
                rule_ids=case.rule_ids,
            )
            expected_status = _expected_status_for_case(case)
            failed_rule_ids = (
                [failure.rule_id for failure in run.evaluation.failures]
                if run.evaluation
                else []
            )
            outcome = "passed" if run.status == expected_status else "failed"
            results.append(
                SegmentationCorpusCaseResult(
                    case_id=case.case_id,
                    title=case.title,
                    run_id=run.run_id,
                    status=run.status,
                    expected_status=expected_status,
                    outcome=outcome,
                    score=run.evaluation.score if run.evaluation else 0,
                    rule_ids=case.rule_ids,
                    failed_rule_ids=failed_rule_ids,
                )
            )

        regression_pass_count = sum(1 for result in results if result.outcome == "passed")
        regression_fail_count = len(results) - regression_pass_count
        corpus_run = SegmentationCorpusRun(
            corpus_run_id=uuid4().hex,
            seed=seed,
            status="passed" if regression_fail_count == 0 else "failed",
            total_case_count=len(results),
            regression_pass_count=regression_pass_count,
            regression_fail_count=regression_fail_count,
            rule_coverage=sorted(rule_coverage),
            results=results,
        )
        self.persist_corpus_run(corpus_run)
        return corpus_run

    def apply_specialist_patches(
        self,
        run_id: str,
        *,
        specialist_id: str,
        patches: list[PatchOperation],
    ) -> SegmentationRun:
        run = self.load_run(run_id)
        packet = next(
            (
                packet
                for packet in run.rule_plan
                if packet.specialist_id == specialist_id
            ),
            None,
        )
        if packet is None:
            raise ValueError("Specialist is not assigned to this run")
        _validate_submitted_patches(patches, event_count=len(run.events))
        updated_output = SpecialistOutput(
            specialist_id=specialist_id,
            rule_ids=packet.rule_ids,
            patches=patches,
            evidence={
                "source_event_indexes": sorted(
                    {patch.event_index for patch in patches}
                ),
                "patch_count": len(patches),
                "submitted_by": "specialist_agent",
            },
        )
        specialist_outputs = [
            updated_output if output.specialist_id == specialist_id else output
            for output in run.specialist_outputs
        ]
        specialist_outputs = self._write_specialist_artifacts(
            run.run_id,
            run.source_filename,
            run.events,
            specialist_outputs,
        )
        merged_draft, merge_evidence = merge_specialist_outputs(
            run.source_filename,
            run.events,
            specialist_outputs,
        )
        evaluation = evaluate_segmented_draft(
            merged_draft,
            expected_rule_ids=run.rule_ids,
            forbidden_tokens=OFFICIAL_SOURCE_GUARD_TOKENS,
        )
        updated_run = SegmentationRun(
            run_id=run.run_id,
            source_filename=run.source_filename,
            descript_text=run.descript_text,
            events=run.events,
            rule_ids=run.rule_ids,
            rule_plan=run.rule_plan,
            specialist_outputs=specialist_outputs,
            merged_draft=merged_draft,
            merge_evidence=merge_evidence,
            evaluation=evaluation,
            status=_status_from_evaluation(evaluation, merge_evidence),
            failure_routes=route_failures(evaluation.failures),
            source=run.source,
            created_at=run.created_at,
        )
        self.persist_run(updated_run)
        return updated_run

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

    def persist_corpus_run(self, corpus_run: SegmentationCorpusRun) -> None:
        self.corpus_runs_dir.mkdir(parents=True, exist_ok=True)
        (self.corpus_runs_dir / f"{corpus_run.corpus_run_id}.json").write_text(
            json.dumps(segmentation_corpus_run_to_payload(corpus_run), indent=2),
            encoding="utf-8",
        )

    def load_run(self, run_id: str) -> SegmentationRun:
        run_path = self.runs_dir / f"{run_id}.json"
        if not run_path.exists():
            raise FileNotFoundError(run_id)
        return segmentation_run_from_payload(
            json.loads(run_path.read_text(encoding="utf-8"))
        )

    def load_corpus_run(self, corpus_run_id: str) -> SegmentationCorpusRun:
        corpus_run_path = self.corpus_runs_dir / f"{corpus_run_id}.json"
        if not corpus_run_path.exists():
            raise FileNotFoundError(corpus_run_id)
        return segmentation_corpus_run_from_payload(
            json.loads(corpus_run_path.read_text(encoding="utf-8"))
        )

    def list_runs(self) -> list[SegmentationRun]:
        if not self.runs_dir.exists():
            return []
        runs = [
            segmentation_run_from_payload(json.loads(path.read_text(encoding="utf-8")))
            for path in self.runs_dir.glob("*.json")
        ]
        return sorted(runs, key=lambda run: run.created_at, reverse=True)

    def list_corpus_runs(self) -> list[SegmentationCorpusRun]:
        if not self.corpus_runs_dir.exists():
            return []
        corpus_runs = [
            segmentation_corpus_run_from_payload(
                json.loads(path.read_text(encoding="utf-8"))
            )
            for path in self.corpus_runs_dir.glob("*.json")
        ]
        return sorted(corpus_runs, key=lambda run: run.created_at, reverse=True)

    def write_final_transcript(self, run_id: str) -> Path:
        run = self.load_run(run_id)
        export_dir = self.runs_dir / run_id / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = export_dir / "final_transcript.txt"
        transcript_path.write_text(run.merged_draft, encoding="utf-8")
        return transcript_path

    def write_evidence_bundle(self, run_id: str) -> Path:
        run = self.load_run(run_id)
        export_dir = self.runs_dir / run_id / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = export_dir / "evidence.json"
        evidence_path.write_text(
            json.dumps(segmentation_run_to_payload(run), indent=2),
            encoding="utf-8",
        )
        return evidence_path

    def _write_specialist_artifacts(
        self,
        run_id: str,
        source_filename: str,
        events: list[RawTranscriptEvent],
        outputs: list[SpecialistOutput],
    ) -> list[SpecialistOutput]:
        specialist_dir = self.runs_dir / run_id / "specialists"
        specialist_dir.mkdir(parents=True, exist_ok=True)
        return [
            SpecialistOutput(
                specialist_id=output.specialist_id,
                rule_ids=output.rule_ids,
                patches=output.patches,
                evidence={
                    **output.evidence,
                    "artifact_path": str(
                        _write_specialist_artifact(
                            specialist_dir,
                            source_filename,
                            events,
                            output,
                        )
                    ),
                },
            )
            for output in outputs
        ]


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


def _write_specialist_artifact(
    specialist_dir: Path,
    source_filename: str,
    events: list[RawTranscriptEvent],
    output: SpecialistOutput,
) -> Path:
    artifact_path = specialist_dir / f"{output.specialist_id}.html"
    rule_items = "".join(f"<li><code>{rule_id}</code></li>" for rule_id in output.rule_ids)
    patch_items = "".join(
        "<li>"
        f"<code>{patch.operation}</code> event <code>{patch.event_index}</code>: "
        f"<code>{patch.text}</code> - {patch.reason}"
        "</li>"
        for patch in output.patches
    )
    event_items = "".join(
        "<li>"
        f"<code>{index}</code> [{event.timestamp_seconds}s] "
        f"<code>{event.speaker}</code>: {event.text}"
        "</li>"
        for index, event in enumerate(events)
    )
    artifact_path.write_text(
        f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Segmentation Specialist Packet: {output.specialist_id}</title>
  </head>
  <body>
    <main>
      <h1>{output.specialist_id}</h1>
      <p>Source file: <code>{source_filename}</code></p>
      <p>Do not rewrite the full transcript. Return only rule-scoped patches for this specialist.</p>
      <h2>Rules</h2>
      <ul>{rule_items}</ul>
      <h2>Source Events</h2>
      <ul>{event_items}</ul>
      <h2>Deterministic Patch Stub</h2>
      <ul>{patch_items}</ul>
    </main>
  </body>
</html>
""",
        encoding="utf-8",
    )
    return artifact_path


def segmentation_run_to_payload(run: SegmentationRun) -> dict[str, Any]:
    return asdict(run)


def segmentation_corpus_run_to_payload(
    corpus_run: SegmentationCorpusRun,
) -> dict[str, Any]:
    return asdict(corpus_run)


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


def segmentation_corpus_run_from_payload(
    payload: dict[str, Any],
) -> SegmentationCorpusRun:
    return SegmentationCorpusRun(
        corpus_run_id=str(payload["corpus_run_id"]),
        seed=int(payload.get("seed", 0)),
        status=str(payload.get("status") or "failed"),
        total_case_count=int(payload.get("total_case_count", 0)),
        regression_pass_count=int(payload.get("regression_pass_count", 0)),
        regression_fail_count=int(payload.get("regression_fail_count", 0)),
        rule_coverage=[
            str(rule_id) for rule_id in payload.get("rule_coverage", [])
        ],
        results=[
            SegmentationCorpusCaseResult(
                case_id=str(result["case_id"]),
                title=str(result.get("title") or ""),
                run_id=str(result["run_id"]),
                status=str(result.get("status") or "failed"),
                expected_status=str(result.get("expected_status") or "verified"),
                outcome=str(result.get("outcome") or "failed"),
                score=int(result.get("score", 0)),
                rule_ids=[
                    str(rule_id) for rule_id in result.get("rule_ids", [])
                ],
                failed_rule_ids=[
                    str(rule_id)
                    for rule_id in result.get("failed_rule_ids", [])
                ],
            )
            for result in payload.get("results", [])
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


def _validate_submitted_patches(
    patches: list[PatchOperation],
    *,
    event_count: int,
) -> None:
    allowed_operations = {"insert_before_event", "event_line"}
    for patch in patches:
        if patch.operation not in allowed_operations:
            raise ValueError("Unsupported patch operation")
        if patch.event_index < 0 or patch.event_index >= event_count:
            raise ValueError("Patch event_index out of range")
        if not patch.text.strip():
            raise ValueError("Patch text must be non-empty")


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


def _expected_status_for_case(case) -> str:
    combined = f"{case.descript_text}\n{case.gold_text}".lower()
    has_guard_leak = any(
        token.lower() in combined
        for token in case.official_source_guard_tokens
    )
    if "official-source-guard" in case.rule_ids and has_guard_leak:
        return "failed"
    return "verified"


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
