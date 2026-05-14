from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeAlias

from docx import Document


SpeakerPrefixes: TypeAlias = dict[str, str | list[str]]


@dataclass(frozen=True)
class Turn:
    turn_index: int
    role: str
    speaker_label: str
    raw_prefix: str
    text: str


@dataclass(frozen=True)
class StudyConfig:
    participant_id: str = ""
    speaker_prefixes: SpeakerPrefixes = field(default_factory=dict)
    speaker_labels: dict[str, str] = field(default_factory=dict)
    selected_metrics: list[str] = field(default_factory=list)
    disfluency_tokens: list[str] = field(default_factory=list)
    concept_lexicons: dict[str, list[str]] = field(default_factory=dict)
    nonverbal_cues: dict[str, list[str]] = field(default_factory=dict)
    skill_pack_id: str = ""
    skill_pack_name: str = ""
    skill_pack_version: str = ""


@dataclass(frozen=True)
class Transcript:
    source_filename: str
    config: StudyConfig
    turns: list[Turn]


DEFAULT_SPEAKER_LABELS = {
    "caregiver": "Caregiver",
    "participant": "Participant",
}


def extract_transcript_text(path: Path | str) -> str:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".txt":
        return source.read_text(encoding="utf-8").strip()
    if suffix == ".docx":
        doc = Document(source)
        return "\n".join(
            paragraph.text.strip()
            for paragraph in doc.paragraphs
            if paragraph.text.strip()
        )
    raise ValueError(f"Unsupported transcript format: {source.suffix}")


def parse_transcript(
    content: str,
    config: StudyConfig,
    source_filename: str,
) -> Transcript:
    resolved = _resolve_config(content, config)
    prefix_to_role = {}
    prefixes = []
    for role, role_prefixes in resolved.speaker_prefixes.items():
        normalized_prefixes = _prefix_list(role_prefixes)
        for prefix in normalized_prefixes:
            prefix_to_role[prefix.lower()] = role
            prefixes.append(re.escape(prefix))
    if not prefixes:
        return Transcript(source_filename=source_filename, config=resolved, turns=[])

    pattern = re.compile(rf"({'|'.join(prefixes)}):", re.IGNORECASE)
    parts = pattern.split(content)
    turns: list[Turn] = []

    for index in range(1, len(parts), 2):
        if index + 1 >= len(parts):
            continue
        raw_prefix = parts[index].strip()
        text = re.sub(r"\s+", " ", parts[index + 1]).strip()
        if not text:
            continue
        role = prefix_to_role.get(raw_prefix.lower(), "unknown")
        turns.append(
            Turn(
                turn_index=len(turns),
                role=role,
                speaker_label=resolved.speaker_labels.get(role, role.title()),
                raw_prefix=raw_prefix,
                text=text,
            )
        )

    return Transcript(source_filename=source_filename, config=resolved, turns=turns)


def _resolve_config(content: str, config: StudyConfig) -> StudyConfig:
    participant_id = config.participant_id or _infer_participant_id(content)
    speaker_prefixes = dict(config.speaker_prefixes)
    if participant_id and not speaker_prefixes:
        speaker_prefixes = {
            "caregiver": f"{participant_id}_c",
            "participant": f"{participant_id}_p",
        }
    speaker_labels = {**DEFAULT_SPEAKER_LABELS, **config.speaker_labels}
    return StudyConfig(
        participant_id=participant_id,
        speaker_prefixes=speaker_prefixes,
        speaker_labels=speaker_labels,
        selected_metrics=list(config.selected_metrics),
        disfluency_tokens=list(config.disfluency_tokens),
        concept_lexicons={key: list(value) for key, value in config.concept_lexicons.items()},
        nonverbal_cues={key: list(value) for key, value in config.nonverbal_cues.items()},
        skill_pack_id=config.skill_pack_id,
        skill_pack_name=config.skill_pack_name,
        skill_pack_version=config.skill_pack_version,
    )


def _infer_participant_id(content: str) -> str:
    match = re.search(r"\b(vr(?:x)?\d+)_\w+:", content, re.IGNORECASE)
    return match.group(1).lower() if match else ""


def _prefix_list(value: str | list[str]) -> list[str]:
    if isinstance(value, str):
        return [value]
    return list(value)
