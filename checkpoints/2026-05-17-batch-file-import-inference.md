# 2026-05-17 Batch File Import Inference

## Goal

Let researchers select multiple transcript TXT files and automatically infer casebook assignments from filenames before running batch analysis.

## Files Changed

- `frontend/src/batchTranscripts.ts`
- `frontend/tests/batchTranscripts.test.mjs`
- `frontend/src/App.tsx`
- `frontend/src/styles.css`
- `demo_assets/healthcare_demo/README.md`

## Completed

- Added filename inference for `participant_id`, `condition`, and `week`.
- Added `createBatchTranscriptFromTextFile` for importing browser-selected TXT files into the existing batch transcript model.
- Added parser tests for filename inference and uploaded file conversion.
- Added a Study Workspace multi-file TXT picker.
- TXT imports replace the current batch text and populate the editable assignment grid.
- Unsupported batch imports show a clear inline parse error.
- Kept transcript content local in browser state until the user runs analysis.

## Verification

- `cd frontend && npm run test:batch`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`
- Playwright CLI smoke at `http://127.0.0.1:5173/`:
  - selected `/tmp/nlp_skill_agents_upload_smoke/P1_home_week1.txt`.
  - selected `/tmp/nlp_skill_agents_upload_smoke/P2_lab_week2.txt`.
  - confirmed the assignment grid inferred `P1/home/week_1` and `P2/lab/week_2`.
  - ran study batch.
  - confirmed aggregate tables include `P1 home week_1` and `P2 lab week_2` rows with nonzero metrics.

## Follow-Up Risks

- Batch file import currently supports TXT only. DOCX batch import should be added through a backend multipart endpoint so extraction stays consistent with single-file DOCX uploads.
- Filename inference uses known condition tokens: home, lab, clinic, telehealth. A study-level condition vocabulary should drive this later.
- Multi-file upload is wired into the existing workbench, not yet a dedicated study setup wizard.
