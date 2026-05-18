# 2026-05-17 NVivo-Style Casebook

## Goal

Add the first NVivo-like casebook layer so study batches can compare transcript outputs by participant, condition, week, and custom file metadata.

## Files Changed

- `backend/app/main.py`
- `backend/storage/study_store.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/types.ts`
- `tests/test_api.py`
- `tests/test_study_workspaces.py`
- `demo_assets/healthcare_demo/README.md`
- `docs/superpowers/plans/2026-05-17-nvivo-style-casebook.md`

## Completed

- Added batch transcript `metadata` support through the API.
- Normalized metadata values before local storage.
- Stored metadata in per-run batch JSON.
- Added metadata columns to aggregate JSON and CSV rows.
- Returned aggregate metric rows from the batch API.
- Let `participant_id` metadata drive default participant-prefix parsing for batches that use `P1_c` / `P1_p` style transcript labels.
- Updated Study Workspace batch parsing to accept:

```text
participant_001.txt | participant_id=P1 | condition=home | week=week_1
```

- Rendered aggregate comparison tables directly in the Study Workspace UI.
- Updated demo assets to show participant/condition/week file assignment.

## Verification

- `.venv/bin/pytest tests/test_study_workspaces.py::test_study_workspace_runs_text_batch_with_aggregate_exports tests/test_api.py::test_study_workspace_batch_api_creates_aggregate_outputs -q`
- `.venv/bin/pytest tests/test_study_workspaces.py::test_batch_participant_metadata_can_drive_default_prefix_parsing -q`
- `.venv/bin/pytest -q`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`
- Browser smoke at `http://127.0.0.1:5173/`: ran Study Workspace batch and confirmed aggregate tables show `participant_id`, `condition`, `week`, nonzero turns, and export paths.

## Follow-Up Risks

- This is still paste-based batch setup. The next slice should add multi-file upload with a file-assignment grid.
- The UI displays aggregate rows but does not yet provide filters/crosstabs like participant x week or condition x metric.
- Casebook metadata is file-level context, not yet a full editable case classification database.
