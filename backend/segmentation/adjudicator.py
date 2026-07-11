from __future__ import annotations

import re
from collections import Counter

from backend.evidence.identifiers import (
    cunit_evidence_id,
    passage_evidence_id,
    transcript_evidence_identity,
)
from backend.segmentation.models import (
    CUnitAdjudication,
    CUnitBoundaryDecision,
    RawTranscriptEvent,
)


PARTICIPANT_SPEAKERS = {"P", "PN"}
EXAMINER_SPEAKERS = {"Av", "AvN"}
DEPENDENT_STARTERS = {
    "because",
    "when",
    "if",
    "although",
    "though",
    "while",
    "after",
    "before",
    "since",
    "that",
    "who",
    "which",
}
SUBJECT_PRONOUNS = {"i", "we", "he", "she", "they", "it", "you"}
NOMINAL_SUBJECT_STARTERS = {
    "a",
    "an",
    "the",
    "this",
    "that",
    "these",
    "those",
    "my",
    "your",
    "his",
    "her",
    "our",
    "their",
}
MINIMAL_RESPONSES = {
    "yes",
    "yeah",
    "yep",
    "no",
    "nope",
    "okay",
    "ok",
    "sure",
    "thanks",
    "thank you",
}
FORMULAIC_CUNITS = {
    "good morning",
    "good afternoon",
    "good night",
    "hello",
    "hi",
    "bye",
    "goodbye",
}
FINITE_VERB_TERMS = {
    "am",
    "are",
    "is",
    "was",
    "were",
    "be",
    "been",
    "being",
    "can",
    "could",
    "do",
    "did",
    "does",
    "feel",
    "felt",
    "feels",
    "find",
    "found",
    "forgot",
    "go",
    "got",
    "had",
    "has",
    "have",
    "hear",
    "heard",
    "held",
    "hold",
    "keep",
    "kept",
    "know",
    "like",
    "look",
    "make",
    "makes",
    "move",
    "moved",
    "need",
    "picked",
    "pick",
    "place",
    "placed",
    "prefer",
    "put",
    "reach",
    "said",
    "say",
    "see",
    "show",
    "slide",
    "start",
    "stop",
    "think",
    "touch",
    "turn",
    "want",
    "went",
    "will",
    "would",
    "may",
    "might",
    "must",
    "shall",
    "should",
}

_TOKEN_PATTERN = re.compile(r"[A-Za-z']+")
_FILLED_PAUSE_PATTERN = re.compile(r"\b(uh|um|erm|hmm)\b,?", re.IGNORECASE)
_UNINTELLIGIBLE_PATTERN = re.compile(
    r"\[(?:unintelligible|inaudible|xxx)\]|\b(?:xxx|unintelligible|inaudible)\b",
    re.IGNORECASE,
)


def adjudicate_cunit_boundaries(
    events: list[RawTranscriptEvent],
) -> CUnitAdjudication:
    fallback_revision_id = transcript_evidence_identity(
        "\n".join(
            f"{event.timestamp_seconds}\0{event.speaker}\0{event.text}"
            for event in events
        )
    ).transcript_revision_id
    decisions = [
        _classify_event(index, event, fallback_revision_id)
        for index, event in enumerate(events)
    ]
    boundary_counts = Counter(decision.boundary_type for decision in decisions)
    return CUnitAdjudication(
        total_event_count=len(events),
        participant_turn_count=sum(1 for event in events if event.speaker in PARTICIPANT_SPEAKERS),
        examiner_turn_count=sum(1 for event in events if event.speaker in EXAMINER_SPEAKERS),
        counted_cunit_count=sum(decision.cunit_count for decision in decisions),
        needs_review_count=sum(1 for decision in decisions if decision.needs_human_review),
        boundary_type_counts=dict(boundary_counts),
        decisions=decisions,
    )


def _classify_event(
    index: int,
    event: RawTranscriptEvent,
    fallback_revision_id: str,
) -> CUnitBoundaryDecision:
    raw_text = event.text.strip()
    cleaned_text, excluded_maze, maze_terms = _clean_maze_material(raw_text)
    normalized = _normalize(cleaned_text)
    tokens = _tokens(cleaned_text)

    if event.speaker in EXAMINER_SPEAKERS:
        return _decision(
            index,
            event,
            cleaned_text,
            "examiner-prompt",
            "not-counted",
            0,
            "Examiner/caregiver prompt is retained for context but not counted as participant C-unit production.",
            False,
            excluded_maze,
            ["speaker-role"],
            fallback_revision_id,
        )

    if _UNINTELLIGIBLE_PATTERN.search(raw_text):
        return _decision(
            index,
            event,
            cleaned_text,
            "maze-revision-unintelligible",
            "needs-review",
            0,
            "Contains maze/revision or unintelligible material; exclude marked maze material and route the turn for human C-unit adjudication.",
            True,
            excluded_maze,
            [*maze_terms, "unintelligible-material"],
            fallback_revision_id,
        )

    if tokens and tokens[0].lower() in DEPENDENT_STARTERS:
        return _decision(
            index,
            event,
            cleaned_text,
            "dependent-clause-attachment",
            "attached-dependent",
            0,
            "Starts with a dependent clause marker; attach to the prior C-unit unless a reviewer confirms it stands alone.",
            True,
            excluded_maze,
            [tokens[0].lower()],
            fallback_revision_id,
        )

    if normalized in MINIMAL_RESPONSES:
        return _decision(
            index,
            event,
            cleaned_text,
            "ellipsis-minimal-response",
            "counted-elliptical-cunit",
            1,
            "Minimal response is an elliptical answer in discourse context, so count one C-unit and keep it inspectable.",
            False,
            excluded_maze,
            [normalized],
            fallback_revision_id,
        )

    if _is_formulaic_cunit(normalized):
        return _decision(
            index,
            event,
            cleaned_text,
            "formulaic-communicative-unit",
            "counted-cunit",
            1,
            "Formulaic social response is a communicative unit in the discourse context, so count one C-unit.",
            False,
            excluded_maze,
            [normalized],
            fallback_revision_id,
        )

    if _has_coordinate_clause(cleaned_text):
        return _decision(
            index,
            event,
            cleaned_text,
            "coordination-split",
            "counted-cunit",
            2,
            "Contains coordinated independent clauses; count each coordinate clause as a separate C-unit candidate.",
            False,
            excluded_maze,
            ["and + subject"],
            fallback_revision_id,
        )

    if _has_independent_clause(tokens):
        return _decision(
            index,
            event,
            cleaned_text,
            "independent-clause",
            "counted-cunit",
            1,
            "Contains a subject plus finite predicate, so it is counted as one independent C-unit.",
            False,
            excluded_maze,
            ["subject-predicate"],
            fallback_revision_id,
        )

    return _decision(
        index,
        event,
        cleaned_text,
        "fragment-review",
        "needs-review",
        0,
        "Fragment lacks enough clause evidence for deterministic C-unit counting.",
        True,
        excluded_maze,
        ["fragment"],
        fallback_revision_id,
    )


def _decision(
    index: int,
    event: RawTranscriptEvent,
    cleaned_text: str,
    boundary_type: str,
    decision: str,
    cunit_count: int,
    rationale: str,
    needs_review: bool,
    excluded_maze: str,
    evidence_terms: list[str],
    fallback_revision_id: str,
) -> CUnitBoundaryDecision:
    passage_id = event.passage_id or passage_evidence_id(
        fallback_revision_id,
        index,
    )
    return CUnitBoundaryDecision(
        event_index=index,
        speaker=event.speaker,
        raw_text=event.text,
        cleaned_text=cleaned_text,
        boundary_type=boundary_type,
        decision=decision,
        cunit_count=cunit_count,
        rationale=rationale,
        confidence_status="not_calibrated",
        needs_human_review=needs_review,
        excluded_maze=excluded_maze,
        evidence_terms=[term for term in evidence_terms if term],
        passage_id=passage_id,
        cunit_ids=[
            cunit_evidence_id(passage_id, ordinal)
            for ordinal in range(cunit_count)
        ],
    )


def _clean_maze_material(text: str) -> tuple[str, str, list[str]]:
    excluded: list[str] = []
    terms: list[str] = []

    def filled_pause_replacer(match: re.Match[str]) -> str:
        excluded.append(match.group(0).strip(" ,"))
        terms.append("filled-pause")
        return ""

    cleaned = _FILLED_PAUSE_PATTERN.sub(filled_pause_replacer, text)
    words = _tokens(cleaned)
    for index in range(len(words) - 1):
        current = words[index]
        following = words[index + 1]
        if len(current) >= 2 and following.lower().startswith(current.lower()):
            excluded.append(current)
            terms.append("partial-word-revision")
            cleaned = re.sub(rf"\b{re.escape(current)}\b\s+", "", cleaned, count=1)
            break
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
    return cleaned, ", ".join(excluded), terms


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z' ]+", "", text.lower()).strip()


def _tokens(text: str) -> list[str]:
    return _TOKEN_PATTERN.findall(text)


def _has_coordinate_clause(text: str) -> bool:
    return bool(re.search(r"\b(?:and|but|so)\s+(?:i|we|he|she|they|it)\s+\w+", text, re.IGNORECASE))


def _is_formulaic_cunit(normalized: str) -> bool:
    return any(
        normalized == formula or normalized.startswith(f"{formula} ")
        for formula in FORMULAIC_CUNITS
    )


def _has_independent_clause(tokens: list[str]) -> bool:
    lowered = [token.lower() for token in tokens]
    if not lowered:
        return False
    for index, token in enumerate(lowered):
        if _is_finite_verb_candidate(token):
            preceding = lowered[:index]
            if any(candidate in SUBJECT_PRONOUNS for candidate in preceding):
                return True
            if any(candidate in NOMINAL_SUBJECT_STARTERS for candidate in preceding):
                return True
    return False


def _is_finite_verb_candidate(token: str) -> bool:
    return token in FINITE_VERB_TERMS or token.endswith("ed") or token.endswith("ing")
