# 2026-05-17 Study Batch History

## Goal

Make saved studies feel persistent by listing prior batch runs and allowing a researcher to reload aggregate results without rerunning analysis.

## Files Changed

- `backend/storage/study_store.py`
- `backend/app/main.py`
- `tests/test_study_workspaces.py`
- `tests/test_api.py`
- `frontend/src/api.ts`
- `frontend/src/types.ts`
- `frontend/src/App.tsx`

## CLI Verification

- `.venv/bin/pytest tests/test_study_workspaces.py::test_study_workspace_lists_and_loads_batch_history -q`
- `.venv/bin/pytest tests/test_api.py::test_study_batch_history_api_lists_and_loads_results tests/test_study_workspaces.py::test_study_workspace_lists_and_loads_batch_history -q`
- `.venv/bin/pytest -q`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`

## UI Verification

- Launched backend on `http://127.0.0.1:8000`.
- Launched frontend on `http://127.0.0.1:5173`.
- Applied the Caregiver mobility template.
- Uploaded `P1_home_week1.txt` and `P2_lab_week2.docx`.
- Ran Study Workspace batch.
- Selected the saved study with `Use`.
- Verified `Batch history` rendered with `Load` actions.
- Loaded a previous batch and verified aggregate matrix views were still visible.

## Git Commits

- `19f0752 feat: expose study batch history`
- `ab3464d feat: reload study batch history in workbench`

## Follow-Up Risks

- Batch history currently shows the first four runs only.
- The UI reloads aggregate outputs but does not yet expose per-file run JSON drilldown.
