from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from backend.analysis.transcripts import Transcript, Turn


DEFAULT_DISFLUENCY_TOKENS = {
    "hm",
    "huh",
    "um",
    "uh",
    "eh",
    "mhm",
    "oh",
    "hmm",
    "umm",
    "uhh",
    "ehh",
    "ah",
    "mm",
    "em",
}


@dataclass(frozen=True)
class MetricResult:
    metric_id: str
    label: str
    rows: list[dict[str, Any]]


def calculate_base_metrics(transcript: Transcript) -> MetricResult:
    disfluency_tokens = set(transcript.config.disfluency_tokens) or DEFAULT_DISFLUENCY_TOKENS
    rows = [
        _base_row_for_role(transcript.turns, "caregiver", disfluency_tokens),
        _base_row_for_role(transcript.turns, "participant", disfluency_tokens),
    ]
    total_turns = sum(row["turns"] for row in rows)
    total_clean_words = sum(row["clean_words"] for row in rows)
    total = {
        "speaker": "total",
        "turns": total_turns,
        "clean_words": total_clean_words,
        "raw_words": sum(row["raw_words"] for row in rows),
        "sentences": sum(row["sentences"] for row in rows),
        "questions": sum(row["questions"] for row in rows),
        "nonverbal_cues": sum(row["nonverbal_cues"] for row in rows),
        "words_per_turn": round(total_clean_words / total_turns, 2)
        if total_turns
        else 0.0,
    }
    return MetricResult(
        metric_id="base_metrics",
        label="Base Metrics",
        rows=[*rows, total],
    )


def _base_row_for_role(
    turns: list[Turn],
    role: str,
    disfluency_tokens: set[str],
) -> dict[str, Any]:
    role_turns = [turn for turn in turns if turn.role == role]
    clean_words = sum(len(_word_tokens(_clean_text(turn.text), disfluency_tokens)) for turn in role_turns)
    raw_words = sum(len(_word_tokens(_remove_nonverbals(turn.text), set())) for turn in role_turns)
    turn_count = len(role_turns)
    return {
        "speaker": role,
        "turns": turn_count,
        "clean_words": clean_words,
        "raw_words": raw_words,
        "sentences": sum(_count_sentences(turn.text) for turn in role_turns),
        "questions": sum(turn.text.count("?") for turn in role_turns),
        "nonverbal_cues": sum(len(_extract_nonverbals(turn.text)) for turn in role_turns),
        "words_per_turn": round(clean_words / turn_count, 2) if turn_count else 0.0,
    }


def _clean_text(text: str) -> str:
    return _remove_nonverbals(text)


def _remove_nonverbals(text: str) -> str:
    return re.sub(r"\[[^\]]+\]", " ", text)


def _extract_nonverbals(text: str) -> list[str]:
    return re.findall(r"\[([^\]]+)\]", text)


def _word_tokens(text: str, disfluency_tokens: set[str]) -> list[str]:
    tokens = re.findall(r"[A-Za-z']+", text.lower())
    if not disfluency_tokens:
        return tokens
    normalized_disfluencies = {token.lower() for token in disfluency_tokens}
    return [token for token in tokens if token not in normalized_disfluencies]


def _count_sentences(text: str) -> int:
    clean_text = _remove_nonverbals(text).strip()
    if not clean_text:
        return 0
    return len([part for part in re.split(r"[.!?]+", clean_text) if part.strip()])

