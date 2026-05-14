# 2026-05-14 V4 Study Workspace Backend

## Goal

Add the first V4 study-level foundation: local study workspaces, versioned skill-pack artifacts, text batch runs, per-file failure isolation, and aggregate dashboard-ready JSON/CSV exports.

## Files Changed

- `backend/storage/study_store.py`
- `backend/app/main.py`
- `tests/test_study_workspaces.py`
- `tests/test_api.py`
- `checkpoints/README.md`

## CLI Verification

- `.venv/bin/pytest tests/test_study_workspaces.py tests/test_api.py::test_study_workspace_batch_api_creates_aggregate_outputs -q`
- `.venv/bin/pytest -q`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`

## UI Verification

- Started the local backend on `http://127.0.0.1:8000`.
- Created `V4 API Smoke Study` through `POST /api/studies`.
- Added a `v4_smoke_pack-1_0_0` skill-pack version.
- Ran a two-transcript text batch through `POST /api/studies/{study_id}/batches/text`.
- Confirmed `aggregate_results.json` and `question_type_metrics.csv` were written under `local_data/studies/v4-api-smoke-study/batches/<batch_id>/`.

## Git Commit

Pending.

## Follow-Up Risks

- This is backend/API first. The next V4 slice should add a compact frontend study workspace panel and batch result viewer.
