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


def calculate_lexical_metrics(transcript: Transcript) -> MetricResult:
    disfluency_tokens = set(transcript.config.disfluency_tokens) or DEFAULT_DISFLUENCY_TOKENS
    rows = [
        _lexical_row_for_role(transcript.turns, "caregiver", disfluency_tokens),
        _lexical_row_for_role(transcript.turns, "participant", disfluency_tokens),
    ]
    total_tokens = _tokens_for_turns(transcript.turns, disfluency_tokens)
    return MetricResult(
        metric_id="lexical_metrics",
        label="Lexical Metrics",
        rows=[
            *rows,
            _lexical_row_from_tokens("total", total_tokens),
        ],
    )


def calculate_disfluency_metrics(transcript: Transcript) -> MetricResult:
    disfluency_tokens = set(transcript.config.disfluency_tokens) or DEFAULT_DISFLUENCY_TOKENS
    rows = [
        _disfluency_row_for_role(transcript.turns, "caregiver", disfluency_tokens),
        _disfluency_row_for_role(transcript.turns, "participant", disfluency_tokens),
    ]
    total_words = sum(row["total_words"] for row in rows)
    total_count = sum(row["disfluency_count"] for row in rows)
    return MetricResult(
        metric_id="disfluency_metrics",
        label="Disfluency Metrics",
        rows=[
            *rows,
            {
                "speaker": "total",
                "disfluency_count": total_count,
                "total_words": total_words,
                "disfluency_rate": round(total_count / total_words, 3)
                if total_words
                else 0.0,
                "examples": _unique_examples(
                    example for row in rows for example in row["examples"]
                ),
            },
        ],
    )


def calculate_concept_count_metrics(transcript: Transcript) -> MetricResult:
    disfluency_tokens = set(transcript.config.disfluency_tokens) or DEFAULT_DISFLUENCY_TOKENS
    total_words = len(_tokens_for_turns(transcript.turns, disfluency_tokens))
    rows = []
    for concept, terms in transcript.config.concept_lexicons.items():
        normalized_terms = {term.lower() for term in terms}
        matches: list[str] = []
        turn_indexes: set[int] = set()
        speakers: list[str] = []
        for turn in transcript.turns:
            turn_tokens = _word_tokens(_remove_nonverbals(turn.text), disfluency_tokens)
            turn_matches = [token for token in turn_tokens if token in normalized_terms]
            if not turn_matches:
                continue
            matches.extend(turn_matches)
            turn_indexes.add(turn.turn_index)
            speakers.append(turn.role)
        rows.append(
            {
                "concept": concept,
                "match_count": len(matches),
                "turn_count": len(turn_indexes),
                "speakers": ", ".join(_unique_examples(speakers)),
                "rate_per_100_words": round((len(matches) / total_words) * 100, 2)
                if total_words
                else 0.0,
                "examples": _unique_examples(matches),
            }
        )
    return MetricResult(
        metric_id="concept_count_metrics",
        label="Concept Count Metrics",
        rows=rows,
    )


def calculate_cue_inventory_metrics(transcript: Transcript) -> MetricResult:
    rows = []
    for cue, patterns in transcript.config.nonverbal_cues.items():
        normalized_patterns = {pattern.lower() for pattern in patterns}
        matches: list[str] = []
        turn_indexes: set[int] = set()
        speakers: list[str] = []
        for turn in transcript.turns:
            turn_matches = [
                cue_text.lower()
                for cue_text in _extract_nonverbals(turn.text)
                if cue_text.lower() in normalized_patterns
            ]
            if not turn_matches:
                continue
            matches.extend(turn_matches)
            turn_indexes.add(turn.turn_index)
            speakers.append(turn.role)
        rows.append(
            {
                "cue": cue,
                "match_count": len(matches),
                "turn_count": len(turn_indexes),
                "speakers": ", ".join(_unique_examples(speakers)),
                "examples": _unique_examples(matches),
            }
        )
    return MetricResult(
        metric_id="cue_inventory_metrics",
        label="Cue Inventory Metrics",
        rows=rows,
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


def _lexical_row_for_role(
    turns: list[Turn],
    role: str,
    disfluency_tokens: set[str],
) -> dict[str, Any]:
    role_turns = [turn for turn in turns if turn.role == role]
    return _lexical_row_from_tokens(role, _tokens_for_turns(role_turns, disfluency_tokens))


def _disfluency_row_for_role(
    turns: list[Turn],
    role: str,
    disfluency_tokens: set[str],
) -> dict[str, Any]:
    role_turns = [turn for turn in turns if turn.role == role]
    all_tokens: list[str] = []
    matches: list[str] = []
    normalized_disfluencies = {token.lower() for token in disfluency_tokens}
    for turn in role_turns:
        tokens = _word_tokens(_remove_nonverbals(turn.text), set())
        all_tokens.extend(tokens)
        matches.extend(token for token in tokens if token in normalized_disfluencies)
    total_words = len(all_tokens)
    return {
        "speaker": role,
        "disfluency_count": len(matches),
        "total_words": total_words,
        "disfluency_rate": round(len(matches) / total_words, 3)
        if total_words
        else 0.0,
        "examples": _unique_examples(matches),
    }


def _lexical_row_from_tokens(speaker: str, tokens: list[str]) -> dict[str, Any]:
    token_count = len(tokens)
    unique_count = len(set(tokens))
    lexical_tokens = [token for token in tokens if token not in _FUNCTION_WORDS]
    return {
        "speaker": speaker,
        "tokens": token_count,
        "unique_tokens": unique_count,
        "type_token_ratio": round(unique_count / token_count, 3) if token_count else 0.0,
        "lexical_density": round(len(lexical_tokens) / token_count, 3)
        if token_count
        else 0.0,
    }


def _tokens_for_turns(turns: list[Turn], disfluency_tokens: set[str]) -> list[str]:
    tokens: list[str] = []
    for turn in turns:
        tokens.extend(_word_tokens(_clean_text(turn.text), disfluency_tokens))
    return tokens


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


def _unique_examples(values) -> list[str]:
    seen: set[str] = set()
    examples: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        examples.append(value)
    return examples


def _count_sentences(text: str) -> int:
    clean_text = _remove_nonverbals(text).strip()
    if not clean_text:
        return 0
    return len([part for part in re.split(r"[.!?]+", clean_text) if part.strip()])


_FUNCTION_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "he",
    "her",
    "him",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "she",
    "that",
    "the",
    "they",
    "this",
    "to",
    "we",
    "with",
    "you",
}
