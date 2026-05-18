# 2026-05-17 DOCX Study Batch Upload

## Goal

Let researchers select mixed TXT and DOCX transcript files in the Study Workspace, edit participant/condition/week assignments, and run the same local aggregate analysis used by pasted batch text.

## Files Changed

- `backend/app/main.py`
- `tests/test_api.py`
- `frontend/src/api.ts`
- `frontend/src/App.tsx`
- `frontend/src/batchTranscripts.ts`
- `frontend/tests/batchTranscripts.test.mjs`

## CLI Verification

- `.venv/bin/pytest tests/test_api.py::test_study_workspace_file_batch_api_accepts_txt_and_docx tests/test_api.py::test_study_workspace_batch_api_creates_aggregate_outputs -q`
- `.venv/bin/pytest -q`
- `cd frontend && npm run test:batch`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`

## UI Verification

- Launched backend on `http://127.0.0.1:8000`.
- Launched frontend on `http://127.0.0.1:5173`.
- Used Playwright to select `P1_home_week1.txt` and `P2_lab_week2.docx`.
- Ran Study Workspace batch from the UI.
- Verified `Batch complete: 2 run(s), 0 failure(s)` and aggregate Base, Lexical, and Disfluency tables with `P2_lab_week2.docx` metadata.

## Git Commits

- `a775ac4 feat: accept study batch file uploads`
- `eaee349 feat: run study batch file uploads from UI`

## Follow-Up Risks

- Browser preview for DOCX intentionally shows a placeholder; backend extracts real DOCX content during the run.
- Larger batches should get progress feedback and per-file validation in a future slice.
