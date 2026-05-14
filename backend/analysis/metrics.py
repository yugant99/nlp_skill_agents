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


def calculate_interaction_dynamics_metrics(transcript: Transcript) -> MetricResult:
    disfluency_tokens = set(transcript.config.disfluency_tokens) or DEFAULT_DISFLUENCY_TOKENS
    roles = _ordered_roles(transcript)
    role_rows = [
        _interaction_row_for_role(transcript.turns, role, disfluency_tokens)
        for role in roles
    ]
    total_words = sum(row["total_words"] for row in role_rows)
    rows = [
        _without_private_interaction_fields(row, total_words)
        for row in role_rows
    ]
    total_turns = sum(row["turns"] for row in role_rows)
    return MetricResult(
        metric_id="interaction_dynamics_metrics",
        label="Interaction Dynamics Metrics",
        rows=[
            *rows,
            {
                "speaker": "total",
                "turns": total_turns,
                "word_share": 1.0 if total_words else 0.0,
                "question_turns": sum(row["question_turns"] for row in role_rows),
                "avg_words_per_turn": round(total_words / total_turns, 2)
                if total_turns
                else 0.0,
                "longest_turn_words": max(
                    [row["longest_turn_words"] for row in role_rows],
                    default=0,
                ),
            },
        ],
    )


def calculate_care_plan_commitment_metrics(transcript: Transcript) -> MetricResult:
    rows = [
        _care_plan_commitment_row_for_role(transcript.turns, role)
        for role in _ordered_roles(transcript)
    ]
    total_turns = sum(row["turn_count"] for row in rows)
    total_commitments = sum(row["commitment_count"] for row in rows)
    return MetricResult(
        metric_id="care_plan_commitment_metrics",
        label="Care Plan Commitment Metrics",
        rows=[
            *rows,
            {
                "speaker": "total",
                "commitment_count": total_commitments,
                "turn_count": total_turns,
                "commitment_rate": round(total_commitments / total_turns, 3)
                if total_turns
                else 0.0,
                "examples": _unique_examples(
                    example for row in rows for example in row["examples"]
                ),
            },
        ],
    )


def calculate_question_type_metrics(transcript: Transcript) -> MetricResult:
    rows = [
        _question_type_row_for_role(transcript.turns, role)
        for role in _ordered_roles(transcript)
    ]
    total_turns = sum(row["turns"] for row in rows)
    total_questions = sum(row["question_turns"] for row in rows)
    return MetricResult(
        metric_id="question_type_metrics",
        label="Question Type Metrics",
        rows=[
            *rows,
            {
                "speaker": "total",
                "turns": total_turns,
                "question_turns": total_questions,
                "open_question_turns": sum(row["open_question_turns"] for row in rows),
                "yes_no_question_turns": sum(
                    row["yes_no_question_turns"] for row in rows
                ),
                "question_rate": round(total_questions / total_turns, 3)
                if total_turns
                else 0.0,
                "examples": _unique_examples(
                    example for row in rows for example in row["examples"]
                ),
            },
        ],
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


def _ordered_roles(transcript: Transcript) -> list[str]:
    configured_roles = list(transcript.config.speaker_prefixes)
    observed_roles = [turn.role for turn in transcript.turns]
    roles = _unique_examples([*configured_roles, *observed_roles])
    return roles or ["caregiver", "participant"]


def _care_plan_commitment_row_for_role(
    turns: list[Turn],
    role: str,
) -> dict[str, Any]:
    role_turns = [turn for turn in turns if turn.role == role]
    examples = [
        turn.text.strip()
        for turn in role_turns
        if role == "caregiver" and _looks_like_care_plan_commitment(turn.text)
    ]
    turn_count = len(role_turns)
    return {
        "speaker": role,
        "commitment_count": len(examples),
        "turn_count": turn_count,
        "commitment_rate": round(len(examples) / turn_count, 3) if turn_count else 0.0,
        "examples": examples[:5],
    }


def _looks_like_care_plan_commitment(text: str) -> bool:
    normalized = _remove_nonverbals(text).lower()
    return any(pattern.search(normalized) for pattern in _CARE_PLAN_PATTERNS)


def _question_type_row_for_role(
    turns: list[Turn],
    role: str,
) -> dict[str, Any]:
    role_turns = [turn for turn in turns if turn.role == role]
    question_turns = [turn for turn in role_turns if "?" in turn.text]
    open_questions = [
        turn for turn in question_turns if _classify_question_type(turn.text) == "open"
    ]
    yes_no_questions = [
        turn for turn in question_turns if _classify_question_type(turn.text) == "yes_no"
    ]
    return {
        "speaker": role,
        "turns": len(role_turns),
        "question_turns": len(question_turns),
        "open_question_turns": len(open_questions),
        "yes_no_question_turns": len(yes_no_questions),
        "question_rate": round(len(question_turns) / len(role_turns), 3)
        if role_turns
        else 0.0,
        "examples": [turn.text.strip() for turn in question_turns[:5]],
    }


def _classify_question_type(text: str) -> str:
    normalized = _remove_nonverbals(text).strip().lower()
    if any(pattern.match(normalized) for pattern in _OPEN_QUESTION_PATTERNS):
        return "open"
    if any(pattern.match(normalized) for pattern in _YES_NO_QUESTION_PATTERNS):
        return "yes_no"
    return "other"


def _interaction_row_for_role(
    turns: list[Turn],
    role: str,
    disfluency_tokens: set[str],
) -> dict[str, Any]:
    role_turns = [turn for turn in turns if turn.role == role]
    turn_word_counts = [
        len(_word_tokens(_clean_text(turn.text), disfluency_tokens))
        for turn in role_turns
    ]
    total_words = sum(turn_word_counts)
    turn_count = len(role_turns)
    return {
        "speaker": role,
        "turns": turn_count,
        "total_words": total_words,
        "question_turns": sum(1 for turn in role_turns if "?" in turn.text),
        "avg_words_per_turn": round(total_words / turn_count, 2)
        if turn_count
        else 0.0,
        "longest_turn_words": max(turn_word_counts, default=0),
    }


def _without_private_interaction_fields(
    row: dict[str, Any],
    total_words: int,
) -> dict[str, Any]:
    public_row = dict(row)
    role_words = int(public_row.pop("total_words"))
    public_row["word_share"] = round(role_words / total_words, 3) if total_words else 0.0
    return public_row


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


_CARE_PLAN_PATTERNS = [
    re.compile(r"\b(i|we)\s+(will|can|could|should)\s+\w+"),
    re.compile(r"\b(i'm|we're)\s+going\s+to\s+\w+"),
    re.compile(r"\b(i|we)\s+plan\s+to\s+\w+"),
    re.compile(r"\b(let's|let us)\s+\w+"),
    re.compile(
        r"\b(schedule|call|contact|refer|arrange|coordinate|review|order|send|"
        r"follow\s+up|check\s+in)\b"
    ),
]


_OPEN_QUESTION_PATTERNS = [
    re.compile(r"\b(what|when|where|who|whom|whose|why|how)\b"),
    re.compile(r"\b(tell me|describe|walk me through)\b"),
]


_YES_NO_QUESTION_PATTERNS = [
    re.compile(
        r"\b(am|are|is|was|were|do|does|did|can|could|will|would|should|"
        r"have|has|had)\b"
    ),
]
