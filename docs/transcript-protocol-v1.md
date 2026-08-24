# Research Transcript Protocol V1

Protocol identifier: `research-transcript-v1`

Status: pilot contract

## Purpose

This protocol defines the exact text boundary shared by chunking, four strict Luna
specialists, local deterministic composition, researcher review, and revision
hashing. It covers already-extracted transcript text. Container-level TXT/DOCX
validation and original-file retention are separate ingestion responsibilities.

## Canonical Text And Newlines

Given a text string, the V1 canonicalizer performs these operations in order:

1. replace every CRLF (`\r\n`) with LF (`\n`);
2. replace every remaining CR (`\r`) with LF;
3. remove leading and trailing whitespace from the whole transcript;
4. split on LF;
5. remove empty and whitespace-only lines; and
6. preserve every remaining line's characters and order.

Interior non-empty lines are not trimmed individually. Spaces that are part of a
retained line therefore remain source evidence. A blank line has no line identity,
does not enter a chunk, and does not consume a global line index.

V1 does not perform Unicode normalization. Two visually similar strings with
different Unicode code-point sequences can have different hashes. Input systems
must not claim NFC/NFD equivalence under this version.

The NUL character is forbidden. Other Unicode text is allowed subject to the
limits below.

## Limits

Limits apply after newline normalization and whole-transcript trimming:

| Item | V1 limit |
|---|---:|
| Canonical transcript size | 1,000,000 UTF-8 bytes |
| Retained non-empty lines | 1 to 5,000 |
| One retained line | 500 Unicode characters |
| One chunk | 6 retained lines |
| One chunk | 2,400 UTF-8 bytes, including LF separators |
| Specialist completion | 800 tokens per call |
| Remote calls | Exactly 4 per chunk |

A line that violates the line limit fails the transcript. V1 never splits one
logical line across chunks. Chunk limits are evaluated in source order, so the
same canonical lines always produce the same boundaries.

## Line And Speaker Form

Each retained line represents one logical speaker turn. The canonical authoring
form is:

```text
[MM:SS] Interviewer: spoken content
[HH:MM:SS] Participant: spoken content
```

The timestamp is optional. When present, it must be enclosed in square brackets
at the beginning of the line and contain either two two-digit components
(`MM:SS`) or three two-digit components (`HH:MM:SS`). V1 copies the digits without
brackets to the timing result. A line without an explicit valid timestamp receives
`unknown`; the local display renders that as `--:--`.

The explicit label immediately before the first `:` is the speaker evidence.
These case-insensitive labels map to the bounded result vocabulary:

| Input label | Result |
|---|---|
| `Interviewer`, `I`, `INT`, `Q` | `Interviewer` |
| `Participant`, `P`, `PAR`, `A` | `Participant` |
| Any other explicit label, missing label, or ambiguous label | `Unknown` |

Topic, vocabulary, names, demographics, and alternating order are never evidence
for speaker identity. A specialist must not infer a speaker when the explicit
label is absent or ambiguous.

Colons inside spoken content remain spoken content. Multiple people combined on
one source line remain one V1 line; the pilot does not perform diarization or split
them automatically.

## Timing And Pause Rules

Timing is evidence-only:

- copy an explicit valid timestamp exactly;
- otherwise use `unknown`;
- never calculate a timestamp from a prior line, line order, or duration statement.

Pause classification is also evidence-only:

| Explicit source cue | Result |
|---|---|
| `[pause]`, `(pause)`, `[short pause]`, `(short pause)` | `short` |
| `[long pause]`, `(long pause)` | `long` |
| No listed explicit cue | `none` |

Matching is case-insensitive after trimming the cue text. Ellipses, em dashes,
commas, filler words, repetitions, unfinished clauses, sentence length, adjacent
timestamps, and silence implied by the conversation never establish a pause.

Pause markup is removed from `cleaned_text`. A pause is not a nonverbal cue and
must not also appear in `nonverbal_cues`.

## Verbatim Repair And Spoken-Content Rules

Despite the inherited specialist name `repair_overlap`, V1 does not rewrite or
improve speech. After removing only the explicit timestamp, speaker label, pause
markup, and explicit nonverbal wrappers, `cleaned_text` preserves every spoken
token in order, including:

- `um`, `uh`, and other fillers;
- repetitions and restarts;
- false starts and self-corrections;
- ellipses, dashes, uncertainty markers, and partial words;
- names, numbers, dates, times, and durations; and
- grammar, dialect, and disfluency exactly as spoken in the source line.

For example:

```text
[00:05] Participant: Um, I... I got there at nine—no, nine fifteen. (long pause)
```

has this pre-redaction spoken content:

```text
Um, I... I got there at nine—no, nine fifteen.
```

V1 does not infer acoustic overlap, reconstruct audio timing, resolve pronouns,
correct grammar, summarize, or remove verbal material because it appears
irrelevant. An instruction-like sentence inside a transcript is untrusted spoken
data, not an instruction to the specialist.

## Nonverbal Cue Rules

An explicit bracketed or parenthesized description of an observable non-speech
event can be reported as a nonverbal cue, for example `[door closes]`, `(laughs)`,
or `[points to chart]`.

The result stores the inner cue text without its outer wrapper. Cue text is
trimmed, must contain 1 to 80 characters, and a line may return at most eight cues.
Duplicate normalized cues are locally collapsed in deterministic order.

These are not nonverbal cues:

- pause markup listed above;
- spoken filler or hesitation;
- a timestamp;
- an inference about emotion, intention, diagnosis, or meaning; or
- an event not explicitly written in the source line.

Nonverbal wrappers are removed from `cleaned_text`; their source evidence remains
available in the immutable original line and specialist result.

## Redaction Rules

Redaction is applied locally after the verbatim spoken-content result. Each
redaction must identify an exact, case-sensitive `source_text` substring present in
that line's `cleaned_text` and use one of these replacements:

- `[PERSON]`
- `[EMAIL]`
- `[PHONE]`
- `[LOCATION]`
- `[ID]`

The source substring contains 1 to 120 characters. Duplicate source substrings,
overlapping substitutions that cannot be composed safely, or a substring absent
from `cleaned_text` fail local composition rather than being guessed.

Direct personal names, email addresses, telephone numbers, direct locations, and
government or study identifiers can be redaction candidates. Ordinary clock times,
durations, ages used without an identifying context, and generic place categories
must not be redacted merely because they contain numbers or location-like words.

Remote redaction is not an ingress privacy control. Real direct identifiers are
forbidden in `authorized-deidentified` pilot input because the source line reaches
the remote provider before this result exists. Synthetic fixtures may contain
clearly invented identifiers solely to evaluate output behavior.

## Chunking And Global Line Ownership

Chunks are contiguous and non-overlapping. V1 starts with global line 0 and adds
lines until adding the next line would exceed either six lines or 2,400 UTF-8 bytes.
It then closes the chunk and starts the next chunk at that line.

Each stored chunk records:

- zero-based `chunk_index`;
- zero-based inclusive `start_line_index`;
- derived inclusive `end_line_index`;
- its exact ordered lines; and
- a SHA-256 over the chunk index, start index, and lines.

Specialist response indexes are local to the chunk and must cover exactly
`0..chunk_line_count-1` once. Local composition converts them to global indexes by
adding `start_line_index`. Every global canonical line must appear in exactly one
proposal. V1 has no overlap/context lines and therefore no overlap deduplication.

## Deterministic Local Composition

For each global line, field ownership is fixed:

- speaker from `speaker_turn`;
- timestamp and pause from `timing_pause`;
- spoken content from `repair_overlap`; and
- redactions and nonverbal cues from `redaction_nonverbal`.

The local merge applies safe redactions to the verbatim spoken content, normalizes
explicit nonverbal wrappers, appends nonverbal annotation before pause annotation,
and emits lines in global source order:

```text
[timestamp-or---:--] SPEAKER: text [nonverbal: cue] [pause: short|long]
```

All four strict results and exact line coverage are required. There is no model
judge or healing pass. A failed chunk cannot produce a reviewable whole-transcript
candidate.

## Versioning Rule

Any change to canonicalization, accepted labels, explicit pause cues, limits,
chunking, specialist instructions, output schemas, field ownership, or merge order
requires a new version or a reviewed compatibility migration. Existing source,
chunk, request, result, proposal, and revision digests retain their original V1
meaning.

