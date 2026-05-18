# 2026-05-17 Study File Assignment Grid

## Goal

Move the Study Workspace closer to an NVivo-style intake flow by showing an editable file assignment grid before batch analysis.

## Files Changed

- `frontend/src/batchTranscripts.ts`
- `frontend/tests/batchTranscripts.test.mjs`
- `frontend/package.json`
- `frontend/src/App.tsx`
- `frontend/src/styles.css`
- `demo_assets/healthcare_demo/README.md`

## Completed

- Extracted batch transcript parsing into a tested TypeScript module.
- Added a frontend parser test harness with `npm run test:batch`.
- Added editable participant, condition, and week fields for every parsed batch transcript.
- Kept paste-block input as the source of truth while serializing grid edits back into the header syntax.
- Updated Study Workspace runs to use the parsed assignment state.
- Added an empty/error state for malformed batch text.
- Verified grid edits propagate into aggregate tables.

## Verification

- `cd frontend && npm run test:batch`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`
- Playwright CLI smoke at `http://127.0.0.1:5173/`:
  - confirmed File assignment grid renders.
  - edited first transcript condition from `home` to `clinic`.
  - ran study batch.
  - confirmed aggregate tables show `P1 clinic week_1` with nonzero base, lexical, and disfluency rows.

## Follow-Up Risks

- This still starts from pasted transcript blocks. The next slice should add true multi-file upload and auto-populate the same assignment grid.
- Conditions/weeks are free-text fields. Later we should add study-level controlled vocabularies and dropdowns.
- Grid edits currently cover first-class casebook fields only. Custom columns can be represented in header text but do not yet have dynamic UI columns.
