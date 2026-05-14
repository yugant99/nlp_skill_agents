from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from backend.llm.openrouter import DEFAULT_OPENROUTER_MODEL, complete_json
from backend.analysis.skill_packs import parse_skill_pack


@dataclass(frozen=True)
class DraftedSkillPack:
    payload: dict[str, Any]
    warnings: list[str]


@dataclass(frozen=True)
class RefinedSkillPack:
    payload: dict[str, Any]
    applied_changes: list[str]
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


def draft_skill_pack_with_openrouter(
    brief: str,
    name: str | None = None,
    model: str | None = None,
) -> DraftedSkillPack:
    payload = complete_json(
        system_prompt=_skill_authoring_system_prompt(),
        user_prompt=_draft_user_prompt(brief, name),
        model=model or DEFAULT_OPENROUTER_MODEL,
    )
    if name:
        payload["name"] = str(payload.get("name") or name)
    payload = _normalize_llm_skill_pack_payload(payload)
    parse_skill_pack(payload)
    return DraftedSkillPack(payload=payload, warnings=[])


def refine_skill_pack(payload: dict[str, Any], instruction: str) -> RefinedSkillPack:
    parse_skill_pack(payload)
    refined = _deep_copy_payload(payload)
    normalized_instruction = instruction.lower()
    concepts = dict(refined.get("concept_lexicons", {}))
    cues = dict(refined.get("nonverbal_cues", {}))
    applied_changes: list[str] = []

    if (
        "split" in normalized_instruction
        and "pain" in normalized_instruction
        and "pain" in concepts
    ):
        concepts.pop("pain")
        concepts["acute_pain"] = ["acute", "sharp", "sudden", "hurt", "hurts"]
        concepts["chronic_pain"] = [
            "chronic",
            "ongoing",
            "persistent",
            "ache",
            "aches",
            "sore",
        ]
        applied_changes.append("split pain into acute_pain and chronic_pain")

    for concept, terms in _CONCEPT_LIBRARY.items():
        remove_requested = _requests_remove(normalized_instruction, concept)
        if remove_requested and concept in concepts:
            concepts.pop(concept)
            applied_changes.append(f"removed concept {concept}")
        if (
            not remove_requested
            and _requests_add(normalized_instruction, concept)
            and concept not in concepts
        ):
            concepts[concept] = terms
            applied_changes.append(f"added concept {concept}")

    for cue, patterns in _CUE_LIBRARY.items():
        remove_requested = _requests_remove(normalized_instruction, cue)
        if remove_requested and cue in cues:
            cues.pop(cue)
            applied_changes.append(f"removed cue {cue}")
        if (
            not remove_requested
            and _requests_add(normalized_instruction, cue)
            and cue not in cues
        ):
            cues[cue] = patterns
            applied_changes.append(f"added cue {cue}")

    refined["concept_lexicons"] = concepts
    refined["nonverbal_cues"] = cues
    parse_skill_pack(refined)
    warnings = []
    if not applied_changes:
        warnings.append(
            "No supported refinement was detected; edit the skill pack JSON/YAML directly."
        )
    return RefinedSkillPack(
        payload=refined,
        applied_changes=applied_changes,
        warnings=warnings,
    )


def refine_skill_pack_with_openrouter(
    payload: dict[str, Any],
    instruction: str,
    model: str | None = None,
) -> RefinedSkillPack:
    parse_skill_pack(payload)
    response = complete_json(
        system_prompt=_skill_authoring_system_prompt(),
        user_prompt=_refine_user_prompt(payload, instruction),
        model=model or DEFAULT_OPENROUTER_MODEL,
    )
    refined_payload = response.get("payload", response)
    if not isinstance(refined_payload, dict):
        raise ValueError("OpenRouter refinement response must include an object payload")
    refined_payload = _normalize_llm_skill_pack_payload(refined_payload)
    parse_skill_pack(refined_payload)
    applied_changes = response.get("applied_changes", [])
    warnings = response.get("warnings", [])
    return RefinedSkillPack(
        payload=refined_payload,
        applied_changes=applied_changes if isinstance(applied_changes, list) else [],
        warnings=warnings if isinstance(warnings, list) else [],
    )


def _skill_authoring_system_prompt() -> str:
    return (
        "You draft and refine schema-valid NLP Skill Agents study skill packs. "
        "Return only valid JSON. Do not include markdown. Do not request or include "
        "transcript content. The application must never send transcript content to "
        "the model by default. A skill pack object must contain id, name, version, "
        "description, metrics, speaker_roles, disfluency_tokens, concept_lexicons, "
        "and nonverbal_cues. Metrics must only use base_metrics, lexical_metrics, "
        "disfluency_metrics, concept_count_metrics, and cue_inventory_metrics. "
        "speaker_roles values must include label and prefixes. concept_lexicons and "
        "nonverbal_cues must be objects whose values are string arrays."
    )


def _normalize_llm_skill_pack_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _deep_copy_payload(payload)
    name = str(normalized.get("name") or "Research Transcript Draft")
    brief_hint = " ".join(
        [
            name,
            str(normalized.get("description") or ""),
        ]
    ).lower()
    normalized["id"] = _slugify(name)
    normalized["name"] = name
    normalized["version"] = str(normalized.get("version") or "0.1.0")
    normalized["description"] = str(normalized.get("description") or "")
    normalized["metrics"] = _normalize_metric_ids(normalized.get("metrics"))
    normalized["speaker_roles"] = _normalize_speaker_roles(
        normalized.get("speaker_roles"),
        fallback_hint=brief_hint,
    )
    normalized["disfluency_tokens"] = _normalize_string_list(
        normalized.get("disfluency_tokens"),
        fallback=["um", "uh", "hm", "hmm", "like"],
    )
    normalized["concept_lexicons"] = _normalize_string_list_dict(
        normalized.get("concept_lexicons")
    )
    normalized["nonverbal_cues"] = _normalize_string_list_dict(
        normalized.get("nonverbal_cues")
    )
    return normalized


def _normalize_speaker_roles(value: Any, fallback_hint: str) -> dict[str, Any]:
    if isinstance(value, dict):
        roles: dict[str, Any] = {}
        for raw_role, definition in value.items():
            role = _slugify(str(raw_role))
            if not role:
                continue
            if isinstance(definition, str) and definition.strip():
                roles[role] = definition.strip()
                continue
            if not isinstance(definition, dict):
                continue
            label = str(definition.get("label") or role.replace("_", " ").title())
            prefixes = _normalize_string_list(definition.get("prefixes"))
            roles[role] = {"label": label}
            if prefixes:
                roles[role]["prefixes"] = prefixes
        if roles:
            return roles
    return _speaker_roles(fallback_hint)


def _normalize_metric_ids(value: Any) -> list[str]:
    allowed = {
        "base_metrics",
        "lexical_metrics",
        "disfluency_metrics",
        "concept_count_metrics",
        "cue_inventory_metrics",
    }
    if isinstance(value, dict):
        candidates = list(value.keys())
    elif isinstance(value, list):
        candidates = [
            item.get("id") if isinstance(item, dict) else item
            for item in value
        ]
    else:
        candidates = []
    metrics = [item for item in candidates if isinstance(item, str) and item in allowed]
    if metrics:
        return metrics
    return [
        "base_metrics",
        "lexical_metrics",
        "disfluency_metrics",
        "concept_count_metrics",
        "cue_inventory_metrics",
    ]


def _normalize_string_list(value: Any, fallback: list[str] | None = None) -> list[str]:
    if isinstance(value, list):
        normalized = [str(item).strip() for item in value if str(item).strip()]
        if normalized:
            return normalized
    return list(fallback or [])


def _normalize_string_list_dict(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    normalized = {}
    for key, items in value.items():
        normalized_key = _slugify(str(key))
        normalized_items = _normalize_string_list(items)
        if normalized_key and normalized_items:
            normalized[normalized_key] = normalized_items
    return normalized


def _draft_user_prompt(brief: str, name: str | None) -> str:
    return (
        "Draft one study skill pack from this researcher brief.\n"
        f"Preferred name: {name or 'infer a concise study name'}\n"
        f"Brief: {brief}\n"
        "Return the skill pack object itself."
    )


def _refine_user_prompt(payload: dict[str, Any], instruction: str) -> str:
    return (
        "Refine the current skill pack according to the instruction.\n"
        "Return an object with keys payload, applied_changes, and warnings.\n"
        f"Current skill pack JSON: {payload}\n"
        f"Instruction: {instruction}"
    )


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


def _requests_add(instruction: str, key: str) -> bool:
    readable_key = key.replace("_", " ")
    return (
        re.search(rf"\b(add|include|track)\b.{{0,48}}\b{re.escape(readable_key)}\b", instruction)
        is not None
        or re.search(rf"\b(add|include|track)\b.{{0,48}}\b{re.escape(key)}\b", instruction)
        is not None
    )


def _requests_remove(instruction: str, key: str) -> bool:
    readable_key = key.replace("_", " ")
    return (
        re.search(
            rf"\b(remove|drop|ignore|exclude)\b.{{0,48}}\b{re.escape(readable_key)}\b",
            instruction,
        )
        is not None
        or re.search(
            rf"\b(remove|drop|ignore|exclude)\b.{{0,48}}\b{re.escape(key)}\b",
            instruction,
        )
        is not None
    )


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "research_transcript_draft"


def _deep_copy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    copied: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            copied[key] = _deep_copy_payload(value)
        elif isinstance(value, list):
            copied[key] = [dict(item) if isinstance(item, dict) else item for item in value]
        else:
            copied[key] = value
    return copied


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
