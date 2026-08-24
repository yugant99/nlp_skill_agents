from __future__ import annotations

import re
from dataclasses import dataclass

from backend.transcript_pilot.protocol import DataClassification


@dataclass(frozen=True)
class PrivacyFinding:
    category: str
    line_index: int
    preview: str


_DIRECT_IDENTIFIER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "email",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    ),
    (
        "phone",
        re.compile(r"(?<!\d)(?:\+?1[ .-]?)?(?:\(?\d{3}\)?[ .-]?)\d{3}[ .-]?\d{4}(?!\d)"),
    ),
    (
        "government-id",
        re.compile(r"(?<!\d)(?:\d{3}[ -]?\d{3}[ -]?\d{3}|\d{3}[ -]?\d{2}[ -]?\d{4})(?!\d)"),
    ),
    (
        "ip-address",
        re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    ),
)


def scan_direct_identifiers(transcript: str) -> list[PrivacyFinding]:
    findings: list[PrivacyFinding] = []
    for line_index, line in enumerate(transcript.splitlines()):
        for category, pattern in _DIRECT_IDENTIFIER_PATTERNS:
            for match in pattern.finditer(line):
                findings.append(
                    PrivacyFinding(
                        category=category,
                        line_index=line_index,
                        preview=_masked_preview(match.group(0)),
                    )
                )
    return findings


def enforce_egress_boundary(
    *,
    transcript: str,
    classification: DataClassification,
    contains_direct_identifiers: bool,
    remote_egress_authorized: bool,
    authorization_basis: str,
) -> list[PrivacyFinding]:
    if not remote_egress_authorized:
        raise ValueError("Remote transcript egress was not authorized")
    if contains_direct_identifiers:
        raise ValueError("Transcripts containing direct identifiers are not supported")
    if len(authorization_basis.strip()) < 12:
        raise ValueError("Provide a brief authorization or de-identification basis")
    findings = scan_direct_identifiers(transcript)
    if classification == "authorized-deidentified" and findings:
        categories = ", ".join(sorted({finding.category for finding in findings}))
        raise ValueError(
            "The local privacy screen found possible direct identifiers: " + categories
        )
    return findings


def _masked_preview(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * min(8, len(value) - 4)}{value[-2:]}"
