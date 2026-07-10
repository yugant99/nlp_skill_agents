# 2026-07-10 Phase 0 Segmentation Provenance

## Goal

Stop labeling arbitrary pasted and uploaded segmentation inputs and their merged
outputs as synthetic while preserving truthful labels for tracked fixtures.

## Files Changed

- `backend/app/main.py`
- `backend/segmentation/pipeline.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/segmentationProvenance.ts`
- `frontend/src/types.ts`
- `frontend/tests/segmentationProvenance.test.mjs`
- `frontend/package.json`
- `tests/test_api.py`
- `tests/test_segmentation_core.py`
- `README.md`
- `checkpoints/README.md`
- `goals/psychology-research-platform-roadmap.md`

## Implemented

- New generic JSON and TXT-upload segmentation runs use
  `researcher_provided` provenance by default.
- Synthetic corpus runs explicitly preserve `synthetic` provenance.
- Provenance survives persistence, history loading, specialist patching,
  verification, final transcript export, and evidence export.
- Merged transcript headers now say either `Researcher-provided transcript` or
  `Synthetic run`.
- The frontend changes a tracked synthetic input to researcher-provided as soon as
  it is edited or replaced by a file, restores stored provenance for recent runs,
  and displays provenance on both the input and run evidence.
- Persisted legacy runs without a source field continue to load as synthetic for
  backward compatibility rather than guessing a new origin.

## CLI Verification

- `.venv/bin/pytest tests/test_segmentation_core.py tests/test_api.py -q` passed:
  54/54.
- `.venv/bin/pytest -q` passed.
- `cd frontend && npm run build` passed.
- Existing frontend helper and privacy suites passed: 22/22.
- `cd frontend && npm run test:provenance` passed: 2/2.
- `git diff --check origin/master...HEAD` passed.

## UI Verification

- Launched the backend with network authoring disabled and the frontend on
  `127.0.0.1:8000` and `127.0.0.1:5173`.
- Playwright showed `Synthetic source` for a tracked fixture.
- Editing the transcript immediately changed the label to
  `Researcher-provided source`; its persisted run and merged header kept that
  provenance.
- Uploading `provenance-upload.txt` produced a separate researcher-provided run
  whose merged header named the uploaded file.
- Selecting and running the second tracked fixture produced a persisted
  `Synthetic source` run with a `Synthetic run` merged header.
- Browser console reported zero errors and zero warnings.

## Git Commits

- `0064848 fix: preserve segmentation source provenance`
- `99d228e fix: show truthful segmentation provenance`
- `650bcbd docs: add provenance regression command`

## Known Limitations And Rollback

- Existing artifacts that already contain an incorrect `synthetic` value cannot
  be relabeled safely because the original input path was not stored.
- Pasted-input provenance is supplied by the trusted local frontend/API client.
  Authenticated ingestion identity and immutable evidence lineage remain Phase 1
  work.
- Revert the feature commits to restore the earlier behavior. The added source
  values are backward-compatible with existing JSON artifacts.
