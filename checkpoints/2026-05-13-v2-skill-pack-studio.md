# 2026-05-13 V2 Skill Pack Studio

## Goal

Start V2 by making skill authoring agent-assisted: researchers can describe a study in natural language and receive a valid editable skill pack.

## Completed

- Local deterministic skill-pack drafter.
- `POST /api/skill-packs/draft` endpoint.
- Drafted packs include:
  - study name and slug
  - role labels and speaker prefixes
  - default disfluency tokens
  - detected concept lexicons
  - detected nonverbal cue definitions
  - standard V2 metric set
- Frontend Skill Pack Studio:
  - study brief textarea
  - draft name input
  - draft skill pack action
  - refinement request textarea
  - refine active pack action
  - applied-change pills
  - editable generated pack JSON
  - existing validate/run workflow
- Deterministic refinement support for:
  - adding known concepts
  - removing known concepts
  - adding/removing known cue categories
  - splitting `pain` into `acute_pain` and `chronic_pain`
- UI quality pass using installed taste/redesign guidance:
  - removed explicit Inter stack
  - added more intentional background treatment
  - improved tactile hover/active states
  - tightened Studio panel hierarchy

## Verification

- Backend focused: `./.venv/bin/pytest tests/test_skill_builder.py -q`
- Frontend: `npm run lint`
- Frontend build: `npm run build`
- UI: drafted `Caregiver Mobility Study`, ran pasted CG/P transcript, confirmed base, lexical, disfluency, concept-count, and cue-inventory tables rendered.
- UI: refined `Caregiver Mobility Study`, confirmed `acute_pain`, `chronic_pain`, and `sleep` appeared in concept-count output.

## Git

Feature slices are committed and pushed to `master` after verification.

## Follow-Up Risks

- Drafter is deterministic and local; it is not yet using a local LLM.
- Concepts are selected from a curated library, not inferred semantically.
- Next V2 slice should support versioned skill-pack saves and side-by-side comparison.
