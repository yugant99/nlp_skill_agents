# 2026-07-10 Phase 0 Calibrated Confidence

## Goal

Remove synthetic-only numeric confidence and score claims from C-unit segmentation
and state exactly what the current deterministic and synthetic evidence can prove.

## Files Changed

- `backend/segmentation/adjudicator.py`
- `backend/segmentation/evaluator.py`
- `backend/segmentation/models.py`
- `backend/segmentation/pipeline.py`
- `backend/segmentation/rulebook.py`
- `frontend/package.json`
- `frontend/src/App.tsx`
- `frontend/src/segmentationValidation.ts`
- `frontend/src/types.ts`
- `frontend/tests/segmentationValidation.test.mjs`
- `tests/test_api.py`
- `tests/test_segmentation_core.py`
- `README.md`
- `checkpoints/README.md`
- `goals/psychology-research-platform-roadmap.md`

## Implemented

- Replaced the 0-100 segmentation score with configured and passed deterministic
  rule counts.
- Replaced numeric C-unit decision confidence with the explicit
  `not_calibrated` status.
- Added `not_domain_validated` and
  `deterministic_heuristics_and_synthetic_fixtures` to adjudication evidence.
- Compute tracked and generated fixture coverage from the actual corpus rather
  than presenting a manually claimed coverage percentage.
- Added an API validation profile that defines the evidence scope, claim boundary,
  and current limitations.
- Rewrote the UI to say `candidate transcript`, `rule checks passed`, and
  `Not domain validated`; it also states that fixture counts are not accuracy,
  reliability, or validity estimates.
- Preserved compatibility when loading old persisted runs containing numeric
  score or confidence fields, while rewriting them into the current schema.

## CLI Verification

- `.venv/bin/pytest tests/test_segmentation_core.py tests/test_api.py -q` passed:
  54/54.
- `.venv/bin/pytest -q` passed: 129/129.
- `cd frontend && npm run build` passed.
- All frontend helper suites passed: 30/30, including
  `npm run test:validation` at 3/3.
- `git diff --check` passed.
- A live evidence export contained configured and passed rule counts,
  `confidence_status`, `validation_status`, and `evidence_scope`, with no exact
  `score` or numeric `confidence` key.

## UI Verification

- Launched an isolated backend with OpenRouter disabled and the frontend on
  `127.0.0.1:8000` and `127.0.0.1:5173`.
- Playwright proved the initial action says `Generate candidate transcript` and
  the rulebook says `Not domain validated` with its claim boundary.
- The complete synthetic workflow produced `rule checks passed`, a
  `Candidate Transcript` tab, `7/7 rule checks`, and three analysis table sets.
- The candidate panel offered `Use synthetic reference` and stated that the
  candidate passed configured rule checks.
- Browser console reported zero errors and zero warnings.

## Git Commits

- `5a4d813 Calibrate segmentation evidence claims`
- `b8012d0 Show explicit segmentation validation limits`
- `992d484 Use candidate transcript language`

## Known Limitations And Rollback

- No representative psychology sample, human-coded reference, or inter-rater
  comparison has been supplied. The implementation is not domain validated.
- Rule-check and fixture counts do not estimate accuracy, reliability, agreement,
  sensitivity, specificity, or validity.
- Domain validation needs the owner's target study method, representative
  codebook, authorized synthetic or deidentified transcripts, and human coding
  examples before Phase 2/3 claims can be calibrated.
- External consumers of the removed score and numeric confidence fields must move
  to the explicit rule counts and status fields. Legacy artifacts still load.
- Reverting the three implementation commits restores the previous schema and UI;
  persisted artifacts created by this feature should be migrated before such a
  rollback because older code expects the removed numeric fields.
