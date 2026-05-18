# 2026-05-17 NVivo Gap Slices

## Goal

Close the most demo-relevant gaps identified in the NVivo comparison: reuse saved study workspaces, exchange casebook metadata as CSV, and show matrix-style participant/condition/week comparisons.

## Files Changed

- `frontend/src/App.tsx`
- `frontend/src/casebookDesign.ts`
- `frontend/tests/casebookDesign.test.mjs`
- `frontend/src/casebookCsv.ts`
- `frontend/tests/casebookCsv.test.mjs`
- `frontend/src/matrixView.ts`
- `frontend/tests/matrixView.test.mjs`
- `frontend/src/styles.css`
- `frontend/package.json`

## CLI Verification

- `cd frontend && npm run test:casebook`
- `cd frontend && npm run test:casebook-csv`
- `cd frontend && npm run test:matrix`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`

## UI Verification

- Launched backend on `http://127.0.0.1:8000`.
- Launched frontend on `http://127.0.0.1:5173`.
- Applied the Caregiver mobility template.
- Uploaded `P1_home_week1.txt` and `P2_lab_week2.docx`.
- Imported a metadata-only casebook CSV.
- Ran Study Workspace batch.
- Verified `Study created; schema saved for 4 participant(s); batch complete: 2 run(s), 0 failure(s)`.
- Verified casebook CSV metadata appeared in output rows.
- Verified three `Matrix view:` comparison tables rendered.

## Git Commits

- `31903c3 feat: reuse saved study workspaces`
- `41e6f25 feat: import and export casebook csv`
- `1cf44c6 feat: add metric matrix aggregation helper`
- `a7136c7 feat: show batch metric matrix views`

## Follow-Up Risks

- Matrix views currently pick the first numeric metric column. A future slice should let the researcher choose the value column.
- CSV import updates metadata for matching filenames only; future UI should show unmatched CSV rows.
- Saved study reuse loads existing schema, but batch history drilldown is still minimal.
