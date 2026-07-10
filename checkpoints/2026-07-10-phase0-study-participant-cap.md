# 2026-07-10 Phase 0 Study Participant Cap

## Goal

Remove the four-participant demo ceiling so the study casebook can represent the
eight-participant acceptance workflow and larger research studies.

## Files Changed

- `backend/app/main.py`
- `backend/storage/study_store.py`
- `frontend/src/App.tsx`
- `frontend/src/casebookDesign.ts`
- `frontend/tests/casebookDesign.test.mjs`
- `tests/test_api.py`
- `tests/test_study_workspaces.py`
- `checkpoints/README.md`
- `goals/psychology-research-platform-roadmap.md`

## Implemented

- Replaced the four-participant API, storage, option-builder, payload-builder, and
  number-input limits with a shared 10,000-participant resource guard.
- Study schemas now persist participant identifiers beyond `P4` and carry the
  expanded participant count into batch aggregates.
- Requests above the API resource guard fail validation instead of allocating an
  unbounded participant list.

## CLI Verification

- `.venv/bin/pytest tests/test_study_workspaces.py tests/test_api.py -q` passed:
  46/46.
- `.venv/bin/pytest -q` passed.
- `cd frontend && npm run build` passed.
- All frontend suites passed: 25/25.
- Source audit found no remaining backend or frontend four-participant cap.
- `git diff --check origin/master...HEAD` passed.

## UI Verification

- Launched the backend with an isolated data root and network authoring disabled,
  plus the frontend on `127.0.0.1:8000` and `127.0.0.1:5173`.
- Playwright set the casebook participant count to 8 and showed active options
  `P1` through `P8`.
- The full three-transcript study batch completed and created a persistent study.
- The schema API returned `participant_count: 8` and participant identifiers `P1`
  through `P8` after the UI workflow.
- Browser console reported zero errors and zero warnings.

## Git Commits

- `16c6ccf fix: remove four-participant study cap`
- `b0f4f7e fix: expand casebook participant range`

## Known Limitations And Rollback

- The 10,000-participant guard is a defensive prototype limit, not a measured
  project-size promise. Phase 6 must replace it with limits proven on the target
  Alienware workload.
- The UI still materializes participant option identifiers in memory; pagination
  and indexed search remain production-platform work.
- Revert the two feature commits to restore the previous cap. Existing schemas
  above four participants would then be truncated when re-saved by the old code.
