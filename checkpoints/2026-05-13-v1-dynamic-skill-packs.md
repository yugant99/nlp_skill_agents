# 2026-05-13 V1 Dynamic Skill Packs

## Goal

Move the MVP from mostly hardcoded metrics toward researcher-defined study skill packs.

## Completed

- Dynamic skill-pack parser support for:
  - role labels
  - multiple speaker prefixes
  - disfluency tokens
  - concept lexicons
  - nonverbal cue definitions
- Generic concept-count metric.
- Generic cue-inventory metric.
- API endpoint for skill-pack validation.
- Embedded skill-pack execution through text and file run configs.
- Skill-pack provenance in API responses and local `results.json`.
- Frontend JSON skill-pack upload/edit/validate flow.
- Frontend dynamic metric labels and run summaries.
- Built-in dynamic templates:
  - caregiver-participant healthcare
  - psychology interview
  - therapy/open conversation
- Internal Codex skill for research skill authoring.

## Verification

- Backend: `./.venv/bin/pytest -q`
- Frontend: `npm run lint && npm run build`
- UI: validated `caregiver_dynamic_demo`, ran pasted CG/P transcript, confirmed concept-count and cue-inventory tables rendered from the custom skill pack.

## Git

Feature slices were committed and pushed to `master` as they passed verification.

## Follow-Up Risks

- YAML upload is not implemented yet; V1 currently uses JSON.
- UI supports the common caregiver/participant prefix controls; arbitrary role editing should be expanded in V2.
- Concept matching is exact token matching, not stemming or semantic matching.
