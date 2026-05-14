# 2026-05-14 V4 Study Workspace UI

## Goal

Expose the V4 study workspace backend in the workbench: create a local study, save the active skill pack as a study version, run multiple pasted transcript blocks, and show aggregate output paths.

## Files Changed

- `frontend/src/types.ts`
- `frontend/src/api.ts`
- `frontend/src/App.tsx`
- `checkpoints/README.md`

## CLI Verification

- `.venv/bin/pytest -q`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`

## UI Verification

- Started local backend and frontend.
- Used browser automation against `http://127.0.0.1:5173/`.
- Used the Study Workspace panel's default three-transcript batch.
- Confirmed the UI shows `Batch complete: 3 run(s), 0 failure(s)`.
- Confirmed the UI shows `AGGREGATE JSON` and `base_metrics.csv`.
- Captured `/tmp/nlp_skill_agents_v4_study_workspace.png`.

## Git Commit

Pending.

## Follow-Up Risks

- This panel uses pasted transcript blocks for fast demo coverage. File-based multi-upload can build on the same API later.
