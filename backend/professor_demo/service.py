from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from backend.professor_demo.provider import (
    COST_CEILING_USD,
    ENDPOINT_TAG,
    MODEL_ID,
    PROVIDER_NAME,
    SPECIALIST_SPECS,
    LunaDemoClient,
    LunaProviderError,
    PreflightReceipt,
    ProviderCallReceipt,
    RedactionNonverbalResult,
    RepairOverlapResult,
    SpeakerTurnResult,
    SpecialistId,
    SpecialistResult,
    TimingPauseResult,
)
from backend.storage.atomic import atomic_write_text
from backend.storage.workspace_lock import workspace_mutation_lock


MAX_TRANSCRIPT_BYTES = 4_000
MAX_TRANSCRIPT_LINES = 12
RUN_ID_PREFIX = "demo_"

SAMPLE_TRANSCRIPT = """[00:00] Q: Could you walk me through the delay?
[00:05] A: Um, I got there at nine—no, nine fifteen. [door closes]
[00:12] Q: What happened next?
[00:15] A: My name is Maya Patel, and I... I waited about forty minutes. (long pause)"""


class ProfessorDemoError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


class DemoModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SpecialistRun(DemoModel):
    specialist_id: SpecialistId
    label: str
    status: Literal["valid", "error"]
    schema_valid: bool
    output: dict[str, Any] | None
    receipt: ProviderCallReceipt
    error_code: str | None
    error_message: str | None


class DemoUsageReceipt(DemoModel):
    model: str
    provider: str
    endpoint: str
    attempted_call_count: int
    completed_call_count: int
    valid_result_count: int
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
    known_cost_subtotal_usd: str
    total_cost_usd: str | None
    accounting_complete: bool
    currency: Literal["USD"]
    preflight: PreflightReceipt


class TranscriptRevision(DemoModel):
    revision_number: Literal[0, 1]
    revision_id: str
    transcript: str
    sha256: str
    created_at: str


class RevisionState(DemoModel):
    active_revision_number: Literal[0, 1]
    original: TranscriptRevision
    candidate: TranscriptRevision | None
    accepted_at: str | None
    reverted_at: str | None


class ProfessorDemoRun(DemoModel):
    run_id: str
    status: Literal["completed", "failed"]
    source: Literal["synthetic-demo"]
    created_at: str
    original_transcript: str
    merged_transcript: str | None
    merged_line_count: int
    specialists: list[SpecialistRun]
    receipt: DemoUsageReceipt
    revision_state: RevisionState
    failure_code: str | None
    failure_message: str | None


class ProfessorDemoStore:
    def __init__(self, local_data_root: Path | str) -> None:
        self.local_data_root = Path(local_data_root)
        self.demo_root = self.local_data_root / "professor_demo"
        self.runs_dir = self.demo_root / "runs"
        self.latest_path = self.demo_root / "latest.json"

    def save(self, run: ProfessorDemoRun) -> None:
        with workspace_mutation_lock(self.local_data_root):
            self._write_run(run)

    def load(self, run_id: str) -> ProfessorDemoRun:
        path = self._run_path(run_id)
        if not path.is_file():
            raise ProfessorDemoError("run_not_found", "Professor demo run was not found")
        try:
            return ProfessorDemoRun.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ProfessorDemoError(
                "run_unreadable",
                "Professor demo run could not be read",
            ) from exc

    def accept(self, run_id: str) -> ProfessorDemoRun:
        with workspace_mutation_lock(self.local_data_root):
            run = self.load(run_id)
            if run.status != "completed" or run.revision_state.candidate is None:
                raise ProfessorDemoError(
                    "run_not_acceptable",
                    "Only a completed four-specialist run can be accepted",
                )
            if run.revision_state.active_revision_number == 1:
                return run
            now = _utc_now()
            revision_state = run.revision_state.model_copy(
                update={
                    "active_revision_number": 1,
                    "accepted_at": run.revision_state.accepted_at or now,
                    "reverted_at": None,
                }
            )
            accepted = run.model_copy(update={"revision_state": revision_state})
            _assert_original_preserved(run, accepted)
            self._write_run(accepted)
            atomic_write_text(
                self.latest_path,
                json.dumps({"run_id": run_id}, indent=2) + "\n",
            )
            return accepted

    def revert(self, run_id: str) -> ProfessorDemoRun:
        with workspace_mutation_lock(self.local_data_root):
            run = self.load(run_id)
            if run.revision_state.candidate is None:
                raise ProfessorDemoError(
                    "revision_not_found",
                    "This run has no generated demo revision",
                )
            if run.revision_state.active_revision_number == 0:
                return run
            revision_state = run.revision_state.model_copy(
                update={
                    "active_revision_number": 0,
                    "reverted_at": _utc_now(),
                }
            )
            reverted = run.model_copy(update={"revision_state": revision_state})
            _assert_original_preserved(run, reverted)
            self._write_run(reverted)
            return reverted

    def latest(self) -> ProfessorDemoRun:
        if not self.latest_path.is_file():
            raise ProfessorDemoError(
                "revision_not_found",
                "No accepted professor demo revision exists",
            )
        try:
            payload = json.loads(self.latest_path.read_text(encoding="utf-8"))
            run_id = payload["run_id"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ProfessorDemoError(
                "revision_unreadable",
                "The latest professor demo revision could not be read",
            ) from exc
        if not isinstance(run_id, str):
            raise ProfessorDemoError(
                "revision_unreadable",
                "The latest professor demo revision could not be read",
            )
        return self.load(run_id)

    def _write_run(self, run: ProfessorDemoRun) -> None:
        atomic_write_text(
            self._run_path(run.run_id),
            json.dumps(run.model_dump(mode="json"), indent=2, ensure_ascii=False)
            + "\n",
        )

    def _run_path(self, run_id: str) -> Path:
        if not _valid_run_id(run_id):
            raise ProfessorDemoError("run_not_found", "Professor demo run was not found")
        return self.runs_dir / f"{run_id}.json"


class ProfessorDemoService:
    def __init__(
        self,
        local_data_root: Path | str,
        *,
        client: LunaDemoClient | None = None,
    ) -> None:
        self.store = ProfessorDemoStore(local_data_root)
        self.client = client or LunaDemoClient()

    def run(self, transcript: str) -> ProfessorDemoRun:
        original, transcript_lines = validate_synthetic_transcript(transcript)
        try:
            preflight = self.client.preflight(transcript_lines)
        except LunaProviderError as exc:
            raise ProfessorDemoError(exc.code, exc.public_message) from exc

        run_id = f"{RUN_ID_PREFIX}{uuid4().hex}"
        created_at = _utc_now()
        specialist_runs: list[SpecialistRun] = []
        results: dict[SpecialistId, SpecialistResult] = {}

        for spec in SPECIALIST_SPECS:
            call_receipt: ProviderCallReceipt | None = None
            try:
                result, call_receipt = self.client.call_specialist(
                    spec,
                    transcript_lines,
                )
                _validate_line_coverage(result, len(transcript_lines))
                results[spec.specialist_id] = result
                specialist_runs.append(
                    SpecialistRun(
                        specialist_id=spec.specialist_id,
                        label=spec.label,
                        status="valid",
                        schema_valid=True,
                        output=result.model_dump(mode="json"),
                        receipt=call_receipt,
                        error_code=None,
                        error_message=None,
                    )
                )
            except LunaProviderError as exc:
                specialist_runs.append(
                    SpecialistRun(
                        specialist_id=spec.specialist_id,
                        label=spec.label,
                        status="error",
                        schema_valid=False,
                        output=None,
                        receipt=exc.receipt or _unknown_call_receipt(),
                        error_code=exc.code,
                        error_message=exc.public_message,
                    )
                )
            except ValueError:
                specialist_runs.append(
                    SpecialistRun(
                        specialist_id=spec.specialist_id,
                        label=spec.label,
                        status="error",
                        schema_valid=False,
                        output=None,
                        receipt=call_receipt or _unknown_call_receipt(),
                        error_code="specialist_result_invalid",
                        error_message=f"{spec.label} failed local validation",
                    )
                )

        receipt = _build_usage_receipt(specialist_runs, preflight)
        merged_transcript: str | None = None
        failure_code: str | None = None
        failure_message: str | None = None
        if len(results) == len(SPECIALIST_SPECS) and receipt.accounting_complete:
            try:
                merged_transcript = merge_specialist_results(
                    transcript_lines,
                    results,
                )
            except ValueError:
                failure_code = "local_merge_failed"
                failure_message = "Validated results could not be combined safely"
        elif len(results) != len(SPECIALIST_SPECS):
            failure_code = "specialist_run_failed"
            failure_message = "One or more Luna specialists did not return a strict result"
        else:
            failure_code = "usage_accounting_incomplete"
            failure_message = "Native provider usage accounting was incomplete"

        if (
            receipt.total_cost_usd is not None
            and Decimal(receipt.total_cost_usd) >= COST_CEILING_USD
        ):
            merged_transcript = None
            failure_code = "actual_cost_ceiling_exceeded"
            failure_message = "The completed run exceeded the demo cost ceiling"

        status: Literal["completed", "failed"] = (
            "completed" if merged_transcript is not None else "failed"
        )
        original_revision = TranscriptRevision(
            revision_number=0,
            revision_id=f"{run_id}:0",
            transcript=original,
            sha256=_sha256(original),
            created_at=created_at,
        )
        candidate_revision = (
            TranscriptRevision(
                revision_number=1,
                revision_id=f"{run_id}:1",
                transcript=merged_transcript,
                sha256=_sha256(merged_transcript),
                created_at=created_at,
            )
            if merged_transcript is not None
            else None
        )
        run = ProfessorDemoRun(
            run_id=run_id,
            status=status,
            source="synthetic-demo",
            created_at=created_at,
            original_transcript=original,
            merged_transcript=merged_transcript,
            merged_line_count=(len(transcript_lines) if merged_transcript else 0),
            specialists=specialist_runs,
            receipt=receipt,
            revision_state=RevisionState(
                active_revision_number=0,
                original=original_revision,
                candidate=candidate_revision,
                accepted_at=None,
                reverted_at=None,
            ),
            failure_code=failure_code,
            failure_message=failure_message,
        )
        self.store.save(run)
        return run


def validate_synthetic_transcript(transcript: str) -> tuple[str, list[str]]:
    if not isinstance(transcript, str):
        raise ProfessorDemoError(
            "transcript_invalid",
            "Synthetic transcript must be text",
        )
    original = transcript.strip()
    if not original or len(original.encode("utf-8")) > MAX_TRANSCRIPT_BYTES:
        raise ProfessorDemoError(
            "transcript_invalid",
            "Synthetic transcript must contain 1 to 4000 UTF-8 bytes",
        )
    if "\x00" in original:
        raise ProfessorDemoError(
            "transcript_invalid",
            "Synthetic transcript contains an unsupported character",
        )
    transcript_lines = [line.strip() for line in original.splitlines() if line.strip()]
    if not 2 <= len(transcript_lines) <= MAX_TRANSCRIPT_LINES:
        raise ProfessorDemoError(
            "transcript_invalid",
            "Synthetic transcript must contain 2 to 12 non-empty lines",
        )
    if any(len(line) > 500 for line in transcript_lines):
        raise ProfessorDemoError(
            "transcript_invalid",
            "A synthetic transcript line is too long",
        )
    return original, transcript_lines


def merge_specialist_results(
    transcript_lines: list[str],
    results: dict[SpecialistId, SpecialistResult],
) -> str:
    if set(results) != {spec.specialist_id for spec in SPECIALIST_SPECS}:
        raise ValueError("exactly four specialist results are required")

    speaker = cast(SpeakerTurnResult, results["speaker_turn"])
    timing = cast(TimingPauseResult, results["timing_pause"])
    repair = cast(RepairOverlapResult, results["repair_overlap"])
    redaction = cast(RedactionNonverbalResult, results["redaction_nonverbal"])
    _validate_line_coverage(speaker, len(transcript_lines))
    _validate_line_coverage(timing, len(transcript_lines))
    _validate_line_coverage(repair, len(transcript_lines))
    _validate_line_coverage(redaction, len(transcript_lines))

    speaker_by_line = {item.line_index: item for item in speaker.lines}
    timing_by_line = {item.line_index: item for item in timing.lines}
    repair_by_line = {item.line_index: item for item in repair.lines}
    redaction_by_line = {item.line_index: item for item in redaction.lines}
    merged_lines: list[str] = []

    for line_index in range(len(transcript_lines)):
        speaker_line = speaker_by_line[line_index]
        timing_line = timing_by_line[line_index]
        repair_line = repair_by_line[line_index]
        redaction_line = redaction_by_line[line_index]
        text = repair_line.cleaned_text

        spans = sorted(
            redaction_line.redactions,
            key=lambda item: (-len(item.source_text), item.source_text.casefold()),
        )
        if len({item.source_text for item in spans}) != len(spans):
            raise ValueError("duplicate redaction source")
        for span in spans:
            if span.source_text not in text:
                raise ValueError("redaction source is absent from cleaned text")
            text = text.replace(span.source_text, span.replacement)

        timestamp = "--:--" if timing_line.timestamp == "unknown" else timing_line.timestamp
        annotations: list[str] = []
        cues = sorted(
            {
                cue
                for raw_cue in redaction_line.nonverbal_cues
                if (cue := _normalize_nonverbal_cue(raw_cue)) is not None
            },
            key=str.casefold,
        )
        if cues:
            annotations.append(f"[nonverbal: {'; '.join(cues)}]")
        if timing_line.pause != "none":
            annotations.append(f"[pause: {timing_line.pause}]")
        annotation_text = f" {' '.join(annotations)}" if annotations else ""
        merged_lines.append(
            f"[{timestamp}] {speaker_line.speaker.upper()}: {text}{annotation_text}"
        )

    return "\n".join(merged_lines)


def _validate_line_coverage(result: SpecialistResult, line_count: int) -> None:
    lines = result.lines
    indexes = [item.line_index for item in lines]
    if len(indexes) != line_count or set(indexes) != set(range(line_count)):
        raise ValueError("specialist result must cover each line exactly once")


def _normalize_nonverbal_cue(value: str) -> str | None:
    cue = " ".join(value.strip().split())
    matching_wrappers = {("[", "]"), ("(", ")"), ("{", "}")}
    while len(cue) >= 2 and (cue[0], cue[-1]) in matching_wrappers:
        cue = " ".join(cue[1:-1].strip().split())
    if not cue or re.search(r"\bpause\b", cue, flags=re.IGNORECASE):
        return None
    return cue


def _build_usage_receipt(
    specialists: list[SpecialistRun],
    preflight: PreflightReceipt,
) -> DemoUsageReceipt:
    call_receipts = [specialist.receipt for specialist in specialists]
    accounting_complete = (
        len(call_receipts) == len(SPECIALIST_SPECS)
        and all(receipt.accounting_complete for receipt in call_receipts)
    )
    costs = [
        Decimal(receipt.cost_usd)
        for receipt in call_receipts
        if receipt.cost_usd is not None
    ]
    known_subtotal = sum(costs, Decimal("0"))
    total_cost = known_subtotal if accounting_complete else None
    return DemoUsageReceipt(
        model=MODEL_ID,
        provider=PROVIDER_NAME,
        endpoint=ENDPOINT_TAG,
        attempted_call_count=len(specialists),
        completed_call_count=sum(
            receipt.generation_id is not None and receipt.finish_reason == "stop"
            for receipt in call_receipts
        ),
        valid_result_count=sum(item.schema_valid for item in specialists),
        prompt_tokens=_sum_if_complete(call_receipts, "prompt_tokens"),
        completion_tokens=_sum_if_complete(call_receipts, "completion_tokens"),
        reasoning_tokens=_sum_if_complete(call_receipts, "reasoning_tokens"),
        total_tokens=_sum_if_complete(call_receipts, "total_tokens"),
        known_cost_subtotal_usd=_decimal_text(known_subtotal),
        total_cost_usd=(_decimal_text(total_cost) if total_cost is not None else None),
        accounting_complete=accounting_complete,
        currency="USD",
        preflight=preflight,
    )


def _sum_if_complete(
    receipts: list[ProviderCallReceipt],
    field_name: Literal[
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "total_tokens",
    ],
) -> int | None:
    values = [getattr(receipt, field_name) for receipt in receipts]
    if any(value is None for value in values):
        return None
    return sum(cast(int, value) for value in values)


def _unknown_call_receipt() -> ProviderCallReceipt:
    return ProviderCallReceipt(
        model_requested=MODEL_ID,
        endpoint_requested=ENDPOINT_TAG,
        generation_id=None,
        model_returned=None,
        provider_returned=None,
        router_attempt_count=None,
        cache_hit=None,
        finish_reason=None,
        prompt_tokens=None,
        completion_tokens=None,
        reasoning_tokens=None,
        total_tokens=None,
        cost_usd=None,
        accounting_complete=False,
        latency_ms=0,
    )


def _assert_original_preserved(
    before: ProfessorDemoRun,
    after: ProfessorDemoRun,
) -> None:
    if (
        before.revision_state.original != after.revision_state.original
        or before.original_transcript != after.original_transcript
    ):
        raise ProfessorDemoError(
            "original_revision_changed",
            "The original demo transcript changed unexpectedly",
        )


def _valid_run_id(run_id: str) -> bool:
    if not run_id.startswith(RUN_ID_PREFIX):
        return False
    suffix = run_id.removeprefix(RUN_ID_PREFIX)
    return len(suffix) == 32 and all(character in "0123456789abcdef" for character in suffix)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")
