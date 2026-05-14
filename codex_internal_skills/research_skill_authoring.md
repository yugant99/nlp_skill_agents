# Research Skill Authoring

Use this internal skill when converting a professor's study requirements into a valid study skill pack.

## Goal

Create a local, deterministic skill pack that captures the researcher's operational definitions without requiring production code changes.

## Inputs To Ask For Or Infer

- Transcript speaker labels and prefixes.
- Participant or dyad naming convention.
- Concepts the researcher wants counted.
- Terms that should map to each concept.
- Nonverbal cue categories and transcript notation.
- Disfluency tokens to include or exclude.
- Metrics the study needs in the output.
- Example transcript lines that should be counted.

## Output Contract

Return a JSON skill pack with:

- `id`
- `name`
- `version`
- `description`
- `metrics`
- `speaker_roles`
- `disfluency_tokens`
- `concept_lexicons`
- `nonverbal_cues`

## Authoring Rules

- Prefer explicit researcher definitions over general clinical assumptions.
- Do not imply diagnosis or clinical interpretation.
- Keep terms lowercase unless the transcript convention requires casing.
- Include plural and common tense variants when the researcher expects exact token matching.
- Keep categories narrow enough that table outputs remain interpretable.
- Add examples to the checkpoint or PR notes when a term is ambiguous.

## Verification

Before committing a skill pack:

- Run `./.venv/bin/pytest tests/test_skill_packs.py -q`.
- Validate the pack through `POST /api/skill-packs/validate` or the UI.
- Run a pasted transcript through the UI with the pack active.
- Confirm concept and cue rows change when the pack definitions change.

## OpenRouter-Assisted Authoring

When using OpenRouter for V2 skill drafting or refinement:

- Use `openai/gpt-oss-120b` as the default low-cost model.
- Store the key in ignored `.env` as `OPENROUTER_API_KEY`.
- Send only the study brief, current skill-pack schema, and refinement instruction.
- Do not send transcript content unless a future workflow has explicit researcher approval.
- Validate every generated pack before activation.
