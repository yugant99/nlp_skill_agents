# 2026-05-17 Persistent Study Schema

## Goal

Persist the researcher's casebook design as a study artifact so participant count, conditions, weeks, and custom fields are saved with the study and attached to batch outputs.

## Files Changed

- `backend/storage/study_store.py`
- `backend/app/main.py`
- `tests/test_study_workspaces.py`
- `tests/test_api.py`
- `frontend/src/casebookDesign.ts`
- `frontend/tests/casebookDesign.test.mjs`
- `frontend/src/api.ts`
- `frontend/src/types.ts`
- `frontend/src/App.tsx`
- `frontend/src/styles.css`

## CLI Verification

- `.venv/bin/pytest tests/test_study_workspaces.py::test_study_schema_is_saved_and_attached_to_batch_outputs -q`
- `.venv/bin/pytest tests/test_api.py::test_study_schema_api_persists_casebook_design tests/test_study_workspaces.py::test_study_schema_is_saved_and_attached_to_batch_outputs -q`
- `.venv/bin/pytest -q`
- `cd frontend && npm run test:casebook`
- `cd frontend && npm run test:batch`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`

## UI Verification

- Launched backend on `http://127.0.0.1:8000`.
- Launched frontend on `http://127.0.0.1:5173`.
- Applied the Caregiver mobility template.
- Uploaded `P1_home_week1.txt` and `P2_lab_week2.docx`.
- Ran Study Workspace batch.
- Verified `Schema saved for 4 participant(s); batch complete: 2 run(s), 0 failure(s)`.
- Verified the results panel showed `Persisted schema`.
- Verified `GET /api/studies/{study_id}/schema` returned participants `P1-P4`, conditions `home/lab/clinic`, four weeks, and custom fields `site/study_arm`.

## Git Commits

- `2ecf1c4 feat: persist study casebook schema`
- `c92e05e feat: save study schema from workbench`

## Follow-Up Risks

- Existing studies without `study_schema.json` still run; their aggregate payload has `study_schema: null`.
- The schema currently warns in the UI but does not block invalid assignments. Strict mode should be a later explicit feature.
