from __future__ import annotations

import re

from backend.evidence.identifiers import (
    passage_evidence_id,
    transcript_evidence_identity,
)
from backend.segmentation.models import RawTranscriptEvent


_DESCRIPT_TURN_PATTERN = re.compile(
    r"^\s*\[(?P<hours>\d{2}):(?P<minutes>\d{2}):(?P<seconds>\d{2})\]\s*"
    r"(?P<speaker>[A-Za-z][A-Za-z0-9]*):\s*(?P<text>.+?)\s*$"
)


def extract_descript_events(
    content: str,
    source_filename: str = "",
) -> list[RawTranscriptEvent]:
    identity = transcript_evidence_identity(content)
    events: list[RawTranscriptEvent] = []
    for line in content.splitlines():
        match = _DESCRIPT_TURN_PATTERN.match(line)
        if not match:
            continue
        timestamp_seconds = (
            int(match.group("hours")) * 3600
            + int(match.group("minutes")) * 60
            + int(match.group("seconds"))
        )
        events.append(
            RawTranscriptEvent(
                timestamp_seconds=timestamp_seconds,
                speaker=match.group("speaker"),
                text=re.sub(r"\s+", " ", match.group("text")).strip(),
                source_filename=source_filename,
                passage_id=passage_evidence_id(
                    identity.transcript_revision_id,
                    len(events),
                ),
            )
        )
    return events
