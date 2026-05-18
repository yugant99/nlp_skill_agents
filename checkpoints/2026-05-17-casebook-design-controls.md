# 2026-05-17 Casebook Design Controls

## Goal

Move the Study Workspace closer to an NVivo-style deterministic setup: researchers define participant count, study conditions, and week count before checking uploaded transcript assignments.

## Files Changed

- `frontend/src/casebookDesign.ts`
- `frontend/tests/casebookDesign.test.mjs`
- `frontend/src/App.tsx`
- `frontend/src/styles.css`
- `frontend/package.json`

## CLI Verification

- `cd frontend && npm run test:casebook`
- `cd frontend && npm run test:batch`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`

## UI Verification

- Launched frontend on `http://127.0.0.1:5173`.
- Set Study Workspace Participants to `1`.
- Uploaded `P1_home_week1.txt` and `P2_lab_week2.docx`.
- Verified the assignment warning: `P2_lab_week2.docx uses participant P2 outside P1.`
- Ran the batch and verified `Batch complete: 2 run(s), 0 failure(s)`.

## Git Commit

- `e732b8a feat: add study casebook design controls`

## Follow-Up Risks

- Current controls warn instead of blocking. A future researcher-facing mode can enforce strict validation once the study schema is finalized.
- Conditions are comma-delimited for now; later this should become a managed condition table with labels, aliases, and custom metadata fields.
