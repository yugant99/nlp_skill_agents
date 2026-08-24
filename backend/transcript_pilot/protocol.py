from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from backend.professor_demo.provider import SPECIALIST_SPECS, SpecialistId


PROTOCOL_VERSION = "research-transcript-v1"
PROMPT_VERSION = "four-luna-supervised-v1"
SCHEMA_VERSION = "strict-specialist-json-v1"
MERGE_VERSION = "deterministic-line-merge-v1"
PRODUCT_VERSION = "transcript-revision-pilot-v1"
CANONICALIZATION_VERSION = "crlf-normalize-strip-drop-blank-lines-v1"

MAX_TRANSCRIPT_BYTES = 1_000_000
MAX_TRANSCRIPT_LINES = 5_000
MAX_LINE_CHARACTERS = 500
CHUNK_MAX_LINES = 6
CHUNK_MAX_BYTES = 2_400

DEFAULT_AUTHORIZED_COST_USD = "0.50"
GLOBAL_COST_CEILING_USD = "5.00"

DataClassification = Literal["synthetic", "authorized-deidentified"]


SPECIALIST_INSTRUCTIONS: dict[SpecialistId, str] = {
    "speaker_turn": (
        "Classify only the explicit label immediately before the first content colon. "
        "Map Interviewer, I, INT, and Q (case-insensitive) to Interviewer. Map "
        "Participant, P, PAR, and A to Participant. Use Unknown for every other, "
        "missing, or ambiguous label. Do not infer identity from topic, order, names, "
        "or demographic clues. Return exactly one item per line."
    ),
    "timing_pause": (
        "Copy an explicit timestamp exactly; use unknown when none is written. Mark a "
        "pause as short only for [pause], (pause), [short pause], or (short pause), "
        "and as long only for [long pause] or (long pause), case-insensitively. Use none "
        "otherwise. Never infer a pause from punctuation, ellipses, fillers, hesitation, "
        "or sentence length. Return exactly one item per line."
    ),
    "repair_overlap": (
        "Remove only timestamp, speaker-label, pause, and nonverbal markup from each "
        "line. Preserve every spoken word, filler, repetition, false start, name, number, "
        "and uncertainty marker verbatim and in order. Do not improve grammar or style. "
        "Return exactly one item per line."
    ),
    "redaction_nonverbal": (
        "Identify direct personal identifiers and explicit nonverbal cues on each line. "
        "Each redaction source_text must be copied exactly from the spoken content. Do "
        "not redact ordinary times or durations. Do not treat pauses as nonverbal cues. "
        "Return exactly one item per line, using empty arrays when nothing applies."
    ),
}


@dataclass(frozen=True)
class TranscriptChunk:
    chunk_index: int
    start_line_index: int
    lines: tuple[str, ...]

    @property
    def end_line_index(self) -> int:
        return self.start_line_index + len(self.lines) - 1

    @property
    def sha256(self) -> str:
        payload = json.dumps(
            {
                "chunk_index": self.chunk_index,
                "start_line_index": self.start_line_index,
                "lines": self.lines,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_transcript_lines(transcript: str) -> list[str]:
    if not isinstance(transcript, str):
        raise ValueError("Transcript must be text")
    normalized = transcript.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise ValueError("Transcript must not be empty")
    if "\x00" in normalized:
        raise ValueError("Transcript contains an unsupported character")
    if len(normalized.encode("utf-8")) > MAX_TRANSCRIPT_BYTES:
        raise ValueError("Transcript exceeds the 1 MB pilot limit")
    lines = [line for line in normalized.split("\n") if line.strip()]
    if not lines or len(lines) > MAX_TRANSCRIPT_LINES:
        raise ValueError("Transcript must contain 1 to 5000 non-empty lines")
    if any(len(line) > MAX_LINE_CHARACTERS for line in lines):
        raise ValueError("Transcript contains a line longer than 500 characters")
    return lines


def chunk_transcript(transcript: str) -> list[TranscriptChunk]:
    lines = canonical_transcript_lines(transcript)
    chunks: list[TranscriptChunk] = []
    pending: list[str] = []
    pending_bytes = 0
    start_index = 0

    for line_index, line in enumerate(lines):
        line_bytes = len(line.encode("utf-8"))
        separator_bytes = 1 if pending else 0
        would_overflow = pending and (
            len(pending) >= CHUNK_MAX_LINES
            or pending_bytes + separator_bytes + line_bytes > CHUNK_MAX_BYTES
        )
        if would_overflow:
            chunks.append(
                TranscriptChunk(
                    chunk_index=len(chunks),
                    start_line_index=start_index,
                    lines=tuple(pending),
                )
            )
            pending = []
            pending_bytes = 0
            start_index = line_index
            separator_bytes = 0
        pending.append(line)
        pending_bytes += separator_bytes + line_bytes

    if pending:
        chunks.append(
            TranscriptChunk(
                chunk_index=len(chunks),
                start_line_index=start_index,
                lines=tuple(pending),
            )
        )
    return chunks


def protocol_fingerprint() -> str:
    schema_rows = {
        spec.specialist_id: spec.result_model.model_json_schema()
        for spec in SPECIALIST_SPECS
    }
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "merge_version": MERGE_VERSION,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "instructions": SPECIALIST_INSTRUCTIONS,
        "schemas": schema_rows,
        "max_transcript_bytes": MAX_TRANSCRIPT_BYTES,
        "max_transcript_lines": MAX_TRANSCRIPT_LINES,
        "max_line_characters": MAX_LINE_CHARACTERS,
        "chunk_max_lines": CHUNK_MAX_LINES,
        "chunk_max_bytes": CHUNK_MAX_BYTES,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
