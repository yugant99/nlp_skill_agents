from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from backend.analysis.skill_packs import parse_skill_pack


@dataclass(frozen=True)
class DraftedSkillPack:
    payload: dict[str, Any]
    warnings: list[str]


def draft_skill_pack_from_brief(brief: str, name: str | None = None) -> DraftedSkillPack:
    normalized_brief = brief.lower()
    pack_name = name or _default_name(normalized_brief)
    concepts = _select_concepts(normalized_brief)
    cues = _select_cues(normalized_brief)
    payload = {
        "id": _slugify(pack_name),
        "name": pack_name,
        "version": "0.1.0",
        "description": brief.strip(),
        "metrics": [
            "base_metrics",
            "lexical_metrics",
            "disfluency_metrics",
            "concept_count_metrics",
            "cue_inventory_metrics",
        ],
        "speaker_roles": _speaker_roles(normalized_brief),
        "disfluency_tokens": ["um", "uh", "hm", "hmm", "like"],
        "concept_lexicons": concepts,
        "nonverbal_cues": cues,
    }
    parse_skill_pack(payload)
    warnings = []
    if not concepts:
        warnings.append(
            "No concept keywords were recognized; add concept_lexicons before running."
        )
    return DraftedSkillPack(payload=payload, warnings=warnings)


def _default_name(brief: str) -> str:
    if "therapy" in brief or "therapist" in brief or "clinician" in brief:
        return "Therapy Conversation Draft"
    if "psychology" in brief or "interview" in brief:
        return "Psychology Interview Draft"
    if "caregiver" in brief or "care partner" in brief or "health" in brief:
        return "Caregiver Healthcare Draft"
    return "Research Transcript Draft"


def _speaker_roles(brief: str) -> dict[str, dict[str, list[str] | str]]:
    if "therapy" in brief or "therapist" in brief or "clinician" in brief:
        return {
            "clinician": {
                "label": "Clinician",
                "prefixes": ["T", "Therapist", "Clinician"],
            },
            "participant": {
                "label": "Participant",
                "prefixes": ["P", "Participant", "Client"],
            },
        }
    if "interview" in brief or "researcher" in brief or "psychology" in brief:
        return {
            "interviewer": {
                "label": "Interviewer",
                "prefixes": ["I", "Interviewer", "Researcher"],
            },
            "participant": {
                "label": "Participant",
                "prefixes": ["P", "Participant", "Client"],
            },
        }
    return {
        "caregiver": {
            "label": "Care Partner",
            "prefixes": ["CG", "Caregiver", "Interviewer"],
        },
        "participant": {
            "label": "Participant",
            "prefixes": ["P", "Participant", "Patient"],
        },
    }


def _select_concepts(brief: str) -> dict[str, list[str]]:
    selected = {
        concept: terms
        for concept, terms in _CONCEPT_LIBRARY.items()
        if _matches_any(brief, [concept, *terms])
    }
    if selected:
        return selected
    if "psychology" in brief or "interview" in brief:
        return {
            key: _CONCEPT_LIBRARY[key]
            for key in ("mood", "stress", "social_support", "coping")
        }
    if "therapy" in brief or "clinician" in brief:
        return {
            key: _CONCEPT_LIBRARY[key]
            for key in ("emotion", "risk", "goals", "relationships")
        }
    if "health" in brief or "caregiver" in brief or "patient" in brief:
        return {
            key: _CONCEPT_LIBRARY[key]
            for key in ("pain", "medication", "sleep", "mobility")
        }
    return {}


def _select_cues(brief: str) -> dict[str, list[str]]:
    selected = {
        cue: patterns
        for cue, patterns in _CUE_LIBRARY.items()
        if _matches_any(brief, [cue, *patterns])
    }
    if selected:
        return selected
    return {
        key: _CUE_LIBRARY[key]
        for key in ("pause", "laughter", "distress")
    }


def _matches_any(text: str, terms: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(term.lower())}\b", text) for term in terms)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "research_transcript_draft"


_CONCEPT_LIBRARY = {
    "pain": ["pain", "ache", "aches", "hurt", "hurts", "sore"],
    "medication": ["medication", "medicine", "pill", "pills", "dose", "prescription"],
    "sleep": ["sleep", "slept", "tired", "fatigue", "nap", "awake"],
    "mobility": ["mobility", "walk", "walking", "stand", "standing", "balance"],
    "memory": ["memory", "remember", "forgot", "forget", "recall"],
    "mood": ["mood", "happy", "sad", "angry", "upset", "calm", "irritable"],
    "anxiety": ["anxiety", "anxious", "worry", "worried", "panic", "nervous"],
    "stress": ["stress", "stressed", "pressure", "overwhelmed"],
    "social_support": ["social", "support", "family", "friend", "alone", "lonely"],
    "coping": ["cope", "coping", "manage", "managed", "strategy", "strategies"],
    "emotion": ["emotion", "feel", "feeling", "felt", "sad", "angry", "afraid"],
    "risk": ["risk", "harm", "unsafe", "danger", "hurt", "crisis"],
    "goals": ["goal", "goals", "change", "plan", "progress"],
    "relationships": ["relationship", "mother", "father", "partner", "friend", "family"],
}

_CUE_LIBRARY = {
    "pause": ["pause", "long pause", "silence"],
    "laughter": ["laughs", "laughing", "chuckles"],
    "distress": ["crying", "tearful", "sigh", "sighs"],
    "cough": ["cough", "coughs"],
    "gesture": ["gesture", "points", "nods", "shakes head"],
}
