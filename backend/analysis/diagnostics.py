from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.analysis.transcripts import Transcript


@dataclass(frozen=True)
class TranscriptDiagnostics:
    turn_counts: dict[str, int]
    warnings: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_counts": self.turn_counts,
            "warnings": self.warnings,
        }


def analyze_transcript_quality(transcript: Transcript) -> TranscriptDiagnostics:
    roles = list(transcript.config.speaker_prefixes.keys()) or ["caregiver", "participant"]
    turn_counts = {
        role: sum(1 for turn in transcript.turns if turn.role == role)
        for role in roles
    }
    warnings: list[dict[str, str]] = []

    if not transcript.turns:
        warnings.append(
            {
                "code": "no_turns_found",
                "message": "No speaker turns were detected. Check participant ID and speaker prefixes.",
            }
        )
    else:
        for role, count in turn_counts.items():
            if count == 0:
                warnings.append(
                    {
                        "code": "missing_role_turns",
                        "message": f"No turns detected for {role}.",
                    }
                )

    return TranscriptDiagnostics(turn_counts=turn_counts, warnings=warnings)

