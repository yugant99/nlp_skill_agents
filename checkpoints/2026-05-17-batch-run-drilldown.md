# 2026-05-17 Batch Run Drilldown

## Goal

Let a researcher move from a loaded study batch into the individual transcript runs that produced the aggregate tables.

This makes the study workflow closer to an NVivo-style evidence path: aggregate comparison first, source transcript evidence second.

## Files Changed

- `backend/storage/study_store.py`
- `backend/app/main.py`
- `tests/test_study_workspaces.py`
- `tests/test_api.py`
- `frontend/src/types.ts`
- `frontend/src/api.ts`
- `frontend/src/App.tsx`

## Completed

- Added backend helpers to list per-transcript run summaries for a batch.
- Added backend helper to load a single stored run payload from a batch.
- Added API endpoints for batch run listing and batch run detail.
- Added frontend API types and client functions for those endpoints.
- Added Transcript drilldown UI under loaded study batches.
- Added Inspect action to render source-level metric tables for one transcript.
- Stored parsed speaker turns in each local batch run artifact.
- Added Source evidence preview for inspected transcript runs.

## CLI Verification

- `.venv/bin/pytest tests/test_api.py::test_study_batch_run_drilldown_api_lists_and_loads_one_run tests/test_study_workspaces.py::test_study_workspace_lists_and_loads_batch_run_drilldown -q`
- `.venv/bin/pytest -q`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`

## UI Verification

- Started backend on `http://127.0.0.1:8000`.
- Started frontend on `http://127.0.0.1:5173`.
- Ran the default three-transcript study batch through the UI.
- Confirmed Transcript drilldown listed 3 source runs.
- Clicked Inspect and confirmed transcript-level metric tables rendered.
- Smoke screenshot saved under ignored local storage: `local_data/tmp/batch-run-drilldown-smoke.png`.
- Re-ran the browser flow after parsed-turn support and confirmed Source evidence rendered.
- Smoke screenshot saved under ignored local storage: `local_data/tmp/source-evidence-smoke.png`.

## Git Commit

- `c302c70 feat: expose batch run drilldown`
- `3a5bfb1 feat: inspect batch transcript drilldowns`
- `0263e0f feat: show source evidence in batch drilldown`

## Follow-Up Risks

- Batch failures should eventually have their own inspectable error detail rows.
- Longitudinal source browsing will need filters when batches contain dozens of files.
- Source evidence currently previews parsed turns only; future versions can link metric rows directly to matching turn spans.
