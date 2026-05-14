# 2026-05-14 V6 Reproducibility Bundles

## Goal

Add the first institutional reproducibility primitive: export a study bundle manifest with all study artifact paths, byte sizes, and SHA-256 hashes.

## Files Changed

- `backend/storage/study_store.py`
- `backend/app/main.py`
- `tests/test_study_workspaces.py`
- `tests/test_api.py`
- `checkpoints/README.md`

## CLI Verification

Pending final verification before commit:

- `.venv/bin/pytest tests/test_study_workspaces.py tests/test_api.py::test_study_workspace_batch_api_creates_aggregate_outputs -q`
- `.venv/bin/pytest -q`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`

## UI Verification

API-level smoke is sufficient for this backend foundation:

- Create a study.
- Add a skill-pack version.
- Run a batch.
- Export a bundle through `POST /api/studies/{study_id}/bundle`.
- Confirm the manifest contains relative paths and SHA-256 hashes.

## Git Commit

Pending.

## Follow-Up Risks

- This exports a manifest, not a zip/archive import flow yet. The next V6 slices should add import into a clean profile and matching-output validation.
